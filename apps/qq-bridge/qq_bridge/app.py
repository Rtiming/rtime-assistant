# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""M1 echo spike handlers + the group-join gate (入群审核).

The message handler proves the end-to-end path with the *real* shared primitives:
the tiered access gate (``_actor_tier``) and the redacted run log. It echoes the
owner's text back; M2 replaces the body with the Claude+brain pipeline.

Access model (用户分级, see ``_actor_tier``): blocked_users rejected everywhere
(highest priority, 含公开群内、开放模式内); private chats serve admins (QQ_ADMIN_IDS,
default = owner_ids — full features incl. all slash commands + free model choice) and
the QQ_ALLOWED_USERS whitelist or ``private_access``-opened friends/temporary chats
(questions + basic commands only; admin commands get a friendly refusal, model
pinned to the instance default). Groups: either a
``QQ_PUBLIC_GROUPS`` allowlist (default) OR — when ``QQ_OPEN_PUBLIC`` is on — EVERY
group (owner: "默认所有群所有人都能使用"). In both, only @-mention messages trigger a
run; a group member is served at tier "user" (or "admin" if in admin_ids). blocked
still wins in open mode; open mode does NOT itself open private chat (that is
``private_access``) and does NOT weaken the read_only / library-scope hard doors
(those apply per-run regardless of who asks).

Slash commands go through the modular tier registry (``commands.py``): BASIC commands
(/new /reset /stream /help) work for any servable user in private AND groups; ADMIN
commands (/model) are admin-only anywhere (non-admin => friendly refusal, no run).

The request/notice handlers enforce the group-join policy so the account stops
being pulled into spam groups (skipped entirely in open mode — the bot stays put):
  - group *invite* requests are answered per ``group_invite_policy`` (default reject);
  - whenever the bot *ends up* in a group (invite auto-accepted, or direct add) and
    that group is not in ``group_allowlist``, it is left immediately.
"""

from __future__ import annotations

import asyncio
import logging
import os

from rtime_chat_runtime.chat_queue import (
    PendingChatMessage,
    get_chat_debounce_queue,
    get_chat_lock,
    merge_pending_messages,
)
from rtime_chat_runtime.direct_reply import DirectReplyProvider
from rtime_chat_runtime.model_routing import model_can_see
from rtime_chat_runtime.run_log import (
    append_run_event,
    hash_value,
    new_run_id,
    summarize_text,
)

from .channel_response import QQChannelResponse
from .commands import CommandContext
from .commands import dispatch as dispatch_command
from .config import (
    PRIVATE_ACCESS_FRIENDS,
    PRIVATE_ACCESS_FRIENDS_AND_TEMPORARY,
    ConfigProvider,
    QQBridgeConfig,
)
from .events import OutboundAction, QQEventPipeline
from .media import (
    build_media_prompt,
    download_url,
    extract_file_text,
    face_label,
    fetch_napcat_file,
    stem_for,
    sticker_label,
    transcribe_voice,
)
from .model_runner import run_claude
from .onebot.ws_server import EventHandler, MessageHandler
from .sessions import SessionStore
from .tool_policy import (
    READONLY_PERMISSION_MODE,
    policy_for_config,
)

log = logging.getLogger("qq_bridge.app")


def _actor_tier(config: QQBridgeConfig, msg) -> str | None:
    """Resolve the actor's access tier; ``None`` = rejected. 判定顺序(用户分级):

    1. ``blocked_users`` 优先级最高 —— 命中一律拒,**包括公开群内、开放模式内**
       (防捣乱的关键;blocked > 一切,开放模式也压不过它);
    2. 群聊两种模式:
       - 开放模式(``open_public=True``):任何群里的任何非黑名单成员都放行 ——
         admin(在 ``admin_ids`` 里)判为 "admin",其余判为 "user"(答疑),
         **不再要求群在 ``public_groups`` 白名单里**(owner:"默认所有群所有人都能用");
       - 白名单模式(默认):仅 ``QQ_PUBLIC_GROUPS`` 里的群放行,其余群一律拒(含 admin);
       两种模式下群成员都按其身份定级(admin 仍是 admin,模型不受限)。命令门控在
       handler 里按 tier 判(见命令注册表:basic 命令群里也能用,admin 命令仅 admin);
    3. 私聊:admin(``QQ_ADMIN_IDS``,默认=owner_ids,向后兼容)→ "admin"(全功能+命令);
       ``QQ_ALLOWED_USERS`` 白名单 → "user";再按 ``private_access`` 放开好友
       (``sub_type=friend``)和/或群临时会话(``sub_type=group``)。这些都只是普通 user
       档:可问、basic 命令、模型固定实例默认。admin/allowed/private_access 都不命中
       => 拒。**注意:``open_public`` 只放开群答疑准入;私聊由 ``private_access`` 等
       私聊规则单独控制**。
    """
    # blocked_users is a fail-closed UNION of env ∪ store ∪ profile (config.py
    # _restriction_idset_union): an empty QQ_BLOCKED_USERS can never un-block a
    # profile's blocklist, env only ADDS blocked ids — so this check sees the
    # accumulated restriction across all layers (same monotonic class as read_only,
    # design §2.9). No env-downgrade path reaches here. blocked 在 open_public 之前判,
    # 所以开放模式绝不会绕过黑名单(硬约束)。
    if msg.user_id in config.blocked_users:
        return None
    admin_ids = config.admin_ids or frozenset()
    if msg.is_group:
        # 开放模式:任何群放行(准入范围放开);否则仅 public_groups 白名单群放行。
        if not (config.open_public or msg.chat_id in config.public_groups):
            return None
        return "admin" if msg.user_id in admin_ids else "user"
    if msg.user_id in admin_ids:
        return "admin"
    if msg.user_id in config.allowed_users:
        return "user"
    if config.private_access == PRIVATE_ACCESS_FRIENDS and msg.sub_type == "friend":
        return "user"
    if config.private_access == PRIVATE_ACCESS_FRIENDS_AND_TEMPORARY and msg.sub_type in (
        "friend",
        "group",
    ):
        return "user"
    return None


def _is_admin(config: QQBridgeConfig, msg) -> bool:
    """Admin check for per-run privileges (模型自选等);blocked 永远压过 admin。"""
    admin_ids = config.admin_ids or frozenset()
    return msg.user_id not in config.blocked_users and msg.user_id in admin_ids


def _group_message_triggered(msg) -> bool:
    """In groups the bot only responds when it is @-mentioned (防刷屏: a public Q&A
    bot must not answer every line of group chatter). NapCat delivers ALL group
    messages; the at-segments are parsed into ``msg.mentions`` and the plain text
    already has the [CQ:at] stripped."""
    return bool(msg.self_id) and msg.self_id in (msg.mentions or [])


def build_echo_handler(config: QQBridgeConfig) -> MessageHandler:
    async def on_message(msg, reply, send_action=None) -> None:
        if _actor_tier(config, msg) is None:
            append_run_event(
                "qq_message_rejected",
                entry="qq",
                actor_hash=hash_value(msg.user_id),
                is_group=msg.is_group,
            )
            return
        if msg.is_group and not _group_message_triggered(msg):
            return  # allowed group but not @bot: stay silent (no event spam)

        text = msg.text.strip()
        run_id = new_run_id("qq")
        append_run_event(
            "qq_echo",
            run_id=run_id,
            entry="qq",
            actor_hash=hash_value(msg.user_id),
            chat_hash=hash_value(msg.chat_id),
            is_group=msg.is_group,
            message_chars=len(text),
            message_preview=summarize_text(text),
        )
        await reply(text or "(空消息)")

    return on_message


def build_request_handler(config: QQBridgeConfig) -> EventHandler:
    """Answer group *invite* requests per policy (default: reject -> never auto-join)."""

    async def on_request(event, call_action) -> None:
        if event.get("request_type") != "group":
            return
        sub_type = str(event.get("sub_type", ""))  # "invite" | "add"
        inviter = str(event.get("user_id", ""))
        group_id = str(event.get("group_id", ""))
        policy = config.group_invite_policy
        approve = policy == "allow" or (
            policy == "owner" and inviter in config.owner_ids
        )
        await call_action(
            "set_group_add_request",
            {
                "flag": event.get("flag", ""),
                "sub_type": sub_type,
                "approve": approve,
                "reason": "" if approve else "bot 不自动入群",
            },
        )
        append_run_event(
            "qq_group_request",
            entry="qq",
            actor_hash=hash_value(inviter),
            group_hash=hash_value(group_id),
            sub_type=sub_type,
            approved=approve,
            policy=policy,
        )

    return on_request


def build_notice_handler(config: QQBridgeConfig) -> EventHandler:
    """Leave any group the bot lands in unless it is allowlisted (catches direct-adds)."""

    async def on_notice(event, call_action) -> None:
        if config.open_public:
            return  # 开放答疑模式:bot 留在被拉进的每个群里,绝不自动退(与准入放开一致)
        if not config.group_autoleave:
            return  # 自动退群总开关关闭(公开答疑实例:管理员手动管群)
        if event.get("notice_type") != "group_increase":
            return
        self_id = str(event.get("self_id", ""))
        joined = str(event.get("user_id", ""))
        if joined != self_id:
            return  # someone else joined, not the bot
        group_id = str(event.get("group_id", ""))
        if group_id and group_id in config.group_allowlist:
            return  # allowed to stay
        await call_action(
            "set_group_leave",
            {"group_id": int(group_id) if group_id.isdigit() else group_id},
        )
        append_run_event(
            "qq_group_autoleave",
            entry="qq",
            group_hash=hash_value(group_id),
        )

    return on_notice


def _inbound_media_dir(config: QQBridgeConfig) -> str:
    if config.media_dir:
        return config.media_dir
    base = config.sessions_dir or os.path.expanduser("~/.qq-claude")
    return os.path.join(base, "inbound")


async def _download_inbound(
    msg, config: QQBridgeConfig, send_action=None
) -> tuple[list[str], list[str], list[str], list[str], int, int]:
    """Fetch inbound media to local files. Returns
    (image_paths, inline_notes, file_notes, voice_texts, voice_count, video_count).
    voice_texts = locally-transcribed speech (sherpa-onnx); voice_count = voices that
    could not be transcribed (STT off / model missing / decode failed). Download or
    transcribe failures degrade to a text label so the model still knows something was sent."""
    media_dir = _inbound_media_dir(config)
    images: list[str] = []
    inline_notes: list[str] = []
    file_notes: list[str] = []
    voice_texts: list[str] = []
    voice_count = video_count = 0

    for i, seg in enumerate(msg.media):
        if seg.kind in ("image", "sticker"):
            if seg.url:
                try:
                    path = await download_url(
                        seg.url,
                        media_dir,
                        stem=stem_for(msg.message_id, i),
                        suggested_name=seg.name,
                        max_bytes=config.max_download_bytes,
                    )
                    images.append(path)
                    continue
                except Exception as exc:
                    log.warning("inbound %s download failed: %s", seg.kind, exc)
            inline_notes.append(
                sticker_label(seg) if seg.kind == "sticker" else "[图片(下载失败)]"
            )
        elif seg.kind == "face":
            inline_notes.append(face_label(seg))
        elif seg.kind == "file":
            path = None
            if seg.url:
                try:
                    path = await download_url(
                        seg.url,
                        media_dir,
                        stem=stem_for(msg.message_id, i),
                        suggested_name=seg.name,
                        max_bytes=config.max_download_bytes,
                    )
                except Exception as exc:
                    log.warning("inbound file download failed: %s", exc)
            if path is None:
                # NapCat file segments carry no url; fetch via get_file + shared temp mount.
                try:
                    path = await fetch_napcat_file(
                        seg,
                        napcat_file_dir=config.napcat_file_dir,
                        dest_dir=media_dir,
                        stem=stem_for(msg.message_id, i),
                        send_action=send_action,
                        max_bytes=config.max_download_bytes,
                    )
                except Exception as exc:
                    log.warning("inbound file fetch (get_file) failed: %s", exc)
            name = seg.name or (os.path.basename(path) if path else "未知文件")
            if path:
                text, note = extract_file_text(path)
                if text:
                    file_notes.append(f"文件「{name}」（{note}）内容：\n{text}")
                else:
                    file_notes.append(f"文件「{name}」（{note}）")
            else:
                file_notes.append(f"文件「{name}」（无法获取文件内容）")
        elif seg.kind == "voice":
            transcript = None
            try:
                transcript = await transcribe_voice(seg, config=config)
            except Exception as exc:  # noqa: BLE001 — STT best-effort, never crash a turn
                log.warning("voice transcription failed: %s", exc)
            if transcript:
                voice_texts.append(transcript)
            else:
                voice_count += 1
        elif seg.kind == "video":
            video_count += 1

    return images, inline_notes, file_notes, voice_texts, voice_count, video_count


def build_model_handler(
    config: QQBridgeConfig,
    *,
    model_runner=None,
    config_provider: ConfigProvider | None = None,
) -> MessageHandler:
    """M2/M3: owner message -> real Claude+brain answer (replaces echo).

    Reuses the model runner (kimi via the claude-rtime wrapper), tool policy, and a
    slim session store. When streaming is on (config / per-chat /stream), partial text
    is sent at paragraph/long-text boundaries and tool calls surface as short status
    lines — like the Feishu bridge's intermediate output, adapted to QQ's no-edit model.
    M3 multimodal: inbound images / stickers / files are downloaded and Read by the
    model (vision guard per decision 3 — if the chosen model can't see, the user is told
    rather than the image silently dropped); outbound [[rtime-send-image/file]] directives
    are sent back via OneBot as base64 segments. ``send_action`` (3rd arg from ws_server)
    is the raw OneBot sender used for outbound media; None in unit tests => media skipped.

    ``model_runner`` is the EXPLICIT model-side injection point for the simulation
    harness (T4, design §3.1): pass ``rtime_chat_runtime.testing.FakeModelRunner``
    (same call signature as ``run_claude``) to run the full chain with zero network /
    subprocess. Default None keeps the module-level ``run_claude`` (resolved at call
    time, so existing monkeypatch-based tests keep working).

    T8 hot reload (design §2.10): pass a ``config_provider`` and the HOT fields
    (user lists, system prompt, model default, direct-rules file) are read from
    ``config_provider.current()`` at each session build — so a profile edit / an
    admin-api ``:reload`` takes effect on the next message WITHOUT a container
    restart, and with no per-message latency cost (the provider stat-caches; see
    ``ConfigProvider``). Omit it (or the env-only path) and the frozen ``config``
    is used, exactly as before. RESTART-level fields (read_only hard door, MCP
    config, channel wiring) are captured ONCE below and are NOT hot (§2.10:
    security/wiring from strict — a restart is required for those).
    """
    # A hot config source: the provider when given (profile path), else a frozen
    # provider wrapping the passed-in config (env path / tests) so ``_current()``
    # is a single call shape everywhere.
    provider = (
        config_provider if config_provider is not None else ConfigProvider(config)
    )

    def _current() -> QQBridgeConfig:
        return provider.current()

    # RESTART-LEVEL fields captured ONCE (design §2.10: not hot). The SessionStore's
    # sessions_dir / cwd / permission_mode and the read_only tool policy are built
    # from the STARTUP config; changing them needs a restart. read_only in particular
    # is a security hard door held from strict — never hot-swapped.
    store = SessionStore(
        config.sessions_dir,
        default_model=config.model,
        default_permission_mode=config.permission_mode,
        default_cwd=config.default_cwd or os.path.expanduser("~"),
    )
    # Per-config tool policy: read_only is driven by config.read_only (env > store >
    # profile > default) AND still by QQ_READ_ONLY env (either True => the hard door).
    # A profile permissions.read_only:true thus forces the SAME dontAsk + write-tool
    # deny set the QQ_READ_ONLY env flag did (T2 consumption chain / defect #5).
    # RESTART-LEVEL (§2.10): read_only is a security hard door — captured once.
    policy = policy_for_config(config)
    # 块5 正则直答 (HOT, §2.10): fixed asks (bus timetable / FAQ) answered from rules
    # WITHOUT the model. The provider rebuilds the engine only when the rules FILE
    # changes (by mtime) — an operator edit / profile reload takes effect on the next
    # message with no per-message file read (empty path => quietly disabled).
    direct_reply_provider = DirectReplyProvider(config.direct_rules_path)
    # Per-chat serialization (different chats run concurrently; same chat serializes) +
    # burst debounce — reuses the shared P5 primitives the Feishu bridge already uses.
    _chat_locks: dict[str, asyncio.Lock] = {}
    _chat_debounce_queues: dict = {}

    def _run_model(*args, **kwargs):
        # Explicit model-runner injection point (T4 seam). Default: the module-level
        # run_claude, resolved at call time so monkeypatch(app_mod, "run_claude", …)
        # keeps working; tests/harness pass a FakeModelRunner via model_runner=.
        runner = model_runner if model_runner is not None else run_claude
        return runner(*args, **kwargs)

    async def on_message(msg, reply, send_action=None) -> None:
        # HOT config read (§2.10): resolve the effective config ONCE per message via
        # the provider (stat-cached — no rebuild unless a profile file changed). The
        # gate (user lists), direct-rules engine, model default and system prompt all
        # come from THIS snapshot so a profile edit / reload takes effect next message.
        cfg = _current()
        tier = _actor_tier(cfg, msg)
        if tier is None:
            append_run_event(
                "qq_message_rejected",
                entry="qq",
                actor_hash=hash_value(msg.user_id),
                is_group=msg.is_group,
            )
            return
        if msg.is_group and not _group_message_triggered(msg):
            # Public-group mode: only @bot messages trigger a run; everything else in
            # the group is normal chatter — stay silent, and don't spam the run log.
            return

        text = (msg.text or "").strip()
        media = list(getattr(msg, "media", None) or [])

        # Slash commands are text-only controls; ignore them when media is attached
        # (an image + "/model" is treated as an ordinary media message). Dispatch
        # goes through the modular registry (commands.py): basic commands (/new,
        # /reset, /stream, /help) work for ANY servable user in private AND groups;
        # admin commands (/model) are admin-only anywhere and a non-admin gets a
        # friendly refusal (no model run). An unknown /word falls through to Q&A.
        # tier is "admin" | "user" here (None was rejected above).
        if not media and text.startswith("/"):

            def _ctx_factory(arg: str) -> CommandContext:
                return CommandContext(
                    user_id=msg.user_id,
                    chat_id=msg.chat_id,
                    arg=arg,
                    reply=reply,
                    store=store,
                    default_stream=cfg.stream_output,
                    default_model=cfg.model,
                )

            handled = await dispatch_command(
                text,
                caller_tier=tier,
                ctx_factory=_ctx_factory,
                reply=reply,
            )
            if handled:
                return
            # not a known command -> fall through and treat it as ordinary text.

        if not text and not media:
            await reply("（收到空消息）")
            return
        # 块5 正则直答: text-only messages try the rule engine BEFORE the model queue
        # (and strictly AFTER the owner gate above — direct answers obey the same
        # access policy). A hit short-circuits the model; any rule failure inside the
        # engine degrades to None and the message falls through to the normal path.
        # HOT: the engine comes from the provider (rebuilt only on rules-file change).
        direct_reply = direct_reply_provider.current()
        if not media and direct_reply.enabled:
            hit = await asyncio.to_thread(direct_reply.match_rule, text)
            if hit is not None:
                rule_name, direct_text = hit
                log.info("direct reply rule=%s chars=%d", rule_name, len(direct_text))
                append_run_event(
                    "qq_direct_reply",
                    run_id=new_run_id("qq"),
                    entry="qq",
                    actor_hash=hash_value(msg.user_id),
                    chat_hash=hash_value(msg.chat_id),
                    rule=rule_name,
                    message_chars=len(text),
                    message_preview=summarize_text(text),
                    reply_chars=len(direct_text),
                )
                await reply(direct_text)
                return
        # Media goes straight to the per-chat lock (never debounce-merged); plain text is
        # debounced so a split question becomes one run. Either way, different chats run
        # concurrently (ws_server dispatches each message as its own task).
        # Group messages also bypass the debounce: the queue is keyed by chat_id, so in
        # a public group it would merge DIFFERENT members' questions into one run.
        if media or msg.is_group:
            await _run_locked(msg, reply, send_action, text, media)
        else:
            await _enqueue_text(msg, reply, send_action, text)

    async def _run_locked(msg, reply, send_action, text, media) -> None:
        lock = get_chat_lock(msg.chat_id, _chat_locks, config.max_chat_locks)
        async with lock:
            try:
                await _process(msg, reply, send_action, text, media)
            except Exception:
                log.exception("qq process failed (locked)")
                await reply("⚠️ 处理消息时出错了")

    async def _enqueue_text(msg, reply, send_action, text) -> None:
        queue = get_chat_debounce_queue(
            msg.chat_id, _chat_debounce_queues, config.max_chat_locks
        )
        queue.append(
            PendingChatMessage(
                user_id=msg.user_id,
                chat_id=msg.chat_id,
                is_group=msg.is_group,
                message_id=msg.message_id,
                text=text,
                raw_message=(msg, reply, send_action),
            )
        )
        if queue.worker_active:
            return  # an active worker will drain this message
        queue.worker_active = True
        try:
            while True:
                if config.debounce_seconds > 0:
                    await asyncio.sleep(config.debounce_seconds)
                batch = queue.drain(config.debounce_max_messages)
                if not batch:
                    return
                merged, _overflow = merge_pending_messages(
                    batch, max_chars=config.debounce_max_chars
                )
                rmsg, rreply, rsend = merged.raw_message
                lock = get_chat_lock(msg.chat_id, _chat_locks, config.max_chat_locks)
                async with lock:
                    try:
                        await _process(rmsg, rreply, rsend, merged.text, [])
                    except Exception:
                        log.exception("qq process failed (debounce)")
                        await rreply("⚠️ 处理消息时出错了")
        finally:
            queue.worker_active = False

    async def _process(msg, reply, send_action, text, media) -> None:
        # HOT config read (§2.10): re-resolve here too — _process can run much later
        # than on_message (debounce worker), so read the live snapshot for the model
        # default, system prompt and admin list rather than a stale build-time config.
        cfg = _current()
        session = store.get(msg.user_id, msg.chat_id)
        # 普通用户固定用实例默认模型：即使 session 里存有历史 /model 选择（比如曾是
        # admin 时设的）也强制回实例默认；admin 不受限。None => wrapper 默认(kimi)。
        model = (session.model or None) if _is_admin(cfg, msg) else (cfg.model or None)

        # --- inbound media (M3): download images/files, then build the model prompt ---
        images: list[str] = []
        file_notes: list[str] = []
        if media:
            await reply("📎 收到附件，正在下载理解…")
            (
                images,
                inline_notes,
                file_notes,
                voice_texts,
                voice_count,
                video_count,
            ) = await _download_inbound(msg, config, send_action)
            # Vision guard (decision 3): don't force-route; if the chosen model can't see,
            # tell the user rather than silently dropping the image.
            if images and not model_can_see(model):
                model_name = model or "默认(kimi)"
                if text:
                    inline_notes.append("[图片已忽略：当前模型看不了图]")
                    images = []
                    await reply(
                        f"⚠️ 当前模型 {model_name} 看不了图片，这次先按你的文字回答；"
                        "要分析图片请 /model 切到 kimi/opus 等能读图的模型再发。"
                    )
                else:
                    await reply(
                        f"⚠️ 当前模型 {model_name} 看不了图片。请 /model 切到 "
                        "kimi/opus 等能读图的模型后再发一次。"
                    )
                    return
            prompt_text = build_media_prompt(
                text,
                images,
                inline_notes=inline_notes,
                file_notes=file_notes,
                voice_texts=voice_texts,
                voice_count=voice_count,
                video_count=video_count,
            )
        else:
            prompt_text = text

        if not prompt_text:
            await reply("（收到，但这条没有可处理的内容）")
            return

        run_id = new_run_id("qq")
        streaming = cfg.stream_output if session.stream is None else session.stream
        prompt = policy.add_runtime_policy_hints(prompt_text)
        allowed = policy.allowed_tools_for_text(prompt_text)
        # When local image/file paths are referenced, ensure Read is permitted even if a
        # narrowing allowlist (e.g. web intent) was selected; None already = all tools.
        if (images or file_notes) and allowed is not None and "Read" not in allowed:
            allowed = list(allowed) + ["Read"]
        disallowed = policy.disallowed_tools_for_text(prompt_text)
        append_run_event(
            "run_started",
            run_id=run_id,
            entry="qq",
            actor_hash=hash_value(msg.user_id),
            chat_hash=hash_value(msg.chat_id),
            model=model or "wrapper-default",
            streaming=streaming,
            message_chars=len(prompt_text),
            media_items=len(media),
            message_preview=summarize_text(text or "(附件)"),
        )

        log.info(
            "run %s start: chars=%d media=%d streaming=%s model=%s",
            run_id,
            len(prompt_text),
            len(media),
            streaming,
            model or "wrapper-default",
        )
        # Render the turn through the shared output port (QQ adapter).
        resp = QQChannelResponse(reply, send_action, msg, config)

        async def on_chunk(chunk: str) -> None:
            await resp.segment(chunk)

        async def on_tool(name: str, inp: dict) -> None:
            await resp.tool(name, inp)

        if streaming:
            await resp.progress("⏳ 思考中…")
        # read-only 硬门（config.read_only：env QQ_READ_ONLY / profile permissions.
        # read_only 任一为真）：权限模式在代码里强制为 dontAsk，不信任 session/profile
        # 里存的 bypassPermissions —— 只读 allowlist 的收窄效果依赖非 bypass 模式
        # （详见 rtime_chat_runtime.tool_policy 的语义说明）。
        permission_mode = (
            READONLY_PERMISSION_MODE
            if policy.is_read_only()
            else session.permission_mode
        )
        try:
            full_text, new_sid, used_fresh = await _run_model(
                prompt,
                cli=config.claude_cli,
                permission_mode=permission_mode,
                session_id=session.session_id,
                model=model,
                cwd=session.cwd or None,
                # HOT (§2.10): system prompt from the live snapshot — a profile edit /
                # reload changes it on the next run without a restart.
                system_prompt=cfg.system_prompt,
                # RESTART-LEVEL (§2.10): mcp_config change requires a restart.
                mcp_config=config.mcp_config,
                allowed_tools=allowed,
                disallowed_tools=disallowed,
                on_text_chunk=on_chunk if streaming else None,
                on_tool_use=on_tool if streaming else None,
                max_seconds=config.run_timeout_seconds,
            )
        except Exception as exc:
            log.exception("run %s failed", run_id)  # full traceback to stdout for debug
            append_run_event(
                "run_failed",
                run_id=run_id,
                entry="qq",
                error_type=type(exc).__name__,
                error_preview=summarize_text(str(exc)),
            )
            await resp.error(f"模型出错：{type(exc).__name__}: {exc}")
            return

        if new_sid:
            store.on_response(msg.user_id, msg.chat_id, new_sid)
        # Streaming fed the buffer (finalize drains the tail); non-streaming passes the
        # whole result. Either way finalize emits remaining text + any outbound attachments.
        await resp.finalize(full_text)
        if used_fresh:
            await resp.progress("（注：上一段会话已失效，已自动新开一段对话）")
        log.info(
            "run %s done: output_chars=%d attachments=%d",
            run_id,
            resp.output_chars,
            resp.attachments_sent,
        )
        append_run_event(
            "run_completed",
            run_id=run_id,
            entry="qq",
            session_hash=hash_value(new_sid),
            actor_hash=hash_value(msg.user_id),
            output_chars=resp.output_chars,
            attachments_sent=resp.attachments_sent,
            used_fresh_session_fallback=used_fresh,
            # 出站回复预览(截断;run_log 的 _is_sensitive_key/脱敏对值仍生效)——
            # 用于审计"bot 到底回了谁什么",补上此前只记字数看不到内容的盲区。
            reply_preview=summarize_text(full_text, 600),
        )

    return on_message


def build_pipeline(
    config: QQBridgeConfig,
    *,
    model_runner=None,
    echo: bool = False,
    config_provider: ConfigProvider | None = None,
) -> QQEventPipeline:
    """Assemble the full decoded-event → outbound-actions chain for ``config``.

    This is the T4 seam entry both the live WS server and the simulation harness use:
    build it once, then ``await pipeline.process_event(decoded_event)`` runs the exact
    same actor-tier gate → debounce → direct-reply → model run → render chain. Pass
    ``model_runner=FakeModelRunner(...)`` (design §3.1 model-side double) to run it with
    zero network / subprocess; ``echo=True`` wires the M1 echo handler instead of the
    model handler (dev/no-CLI parity with ``__main__``).

    ``config_provider`` (T8, §2.10) makes the HOT fields (user lists, system prompt,
    model default, direct-rules file) re-read live: the model handler reads them from
    ``config_provider.current()`` on each message (stat-cached, no per-message rebuild),
    so a profile edit / an admin-api ``:reload`` takes effect without a restart. Omit it
    and the frozen ``config`` is used (env path / tests). RESTART-level fields (read_only
    hard door, MCP config, channel gate = invite/allowlist/autoleave) stay on ``config``.
    """
    if echo:
        on_message = build_echo_handler(config)
    else:
        on_message = build_model_handler(
            config, model_runner=model_runner, config_provider=config_provider
        )
    group_reply_at_sender = (
        (lambda: bool(config_provider.current().group_reply_at_sender))
        if config_provider is not None
        else bool(config.group_reply_at_sender)
    )
    return QQEventPipeline(
        on_message=on_message,
        on_request=build_request_handler(config),
        on_notice=build_notice_handler(config),
        group_reply_at_sender=group_reply_at_sender,
    )


__all__ = [
    "OutboundAction",
    "QQEventPipeline",
    "build_echo_handler",
    "build_model_handler",
    "build_notice_handler",
    "build_pipeline",
    "build_request_handler",
]
