# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Configuration for the QQ bridge — schema-driven (pydantic-settings).

No secrets in the repo: the owner QQ id(s) and any OneBot access token come from
env (or orange pi local config), never from a committed file. Access is tiered
(see ``app._actor_tier``): QQ_BLOCKED_USERS > admin (QQ_ADMIN_IDS, default =
QQ_OWNER_IDS) > QQ_ALLOWED_USERS / QQ_PRIVATE_ACCESS for private chats, and
QQ_PUBLIC_GROUPS or QQ_OPEN_PUBLIC for group Q&A. With no admin/whitelist/private
policy configured the bridge rejects ALL private messages — never allow-all on QQ
by accident.

P2 stage ① config pilot (see docs/development-plan.zh-CN.md §四):
``QQBridgeConfig`` is now a pydantic-settings model — one source of truth for
validation, defaults, descriptions and JSON Schema (docs/config/qq-bridge.md is
generated from it). This is BEHAVIOUR-PRESERVING: every field default matches the
old dataclass, every legacy env name still loads the value, and ``from_env`` /
attribute access / the ``QQBridgeConfig(...)`` constructor are unchanged. The
env-parsing quirks (comma/space id splitting, ``~`` expansion, ``QQ_DEBUG``
override, ``~/.qq-claude`` fallback) are reproduced by field validators + a thin
``from_env`` so both the direct-construct and load-from-env paths match the
legacy code exactly.
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import NoDecode, SettingsConfigDict
from rtime_config import RtimeBaseSettings, config_field, secret_field
from rtime_config.fields import Reload

log = logging.getLogger("qq_bridge.config")

# Env var naming the git profile to consume (design §2.6: binding = per-service env
# ``RTIME_PROFILE=<id>``). Unset => the legacy env-only ``from_env`` path, unchanged.
PROFILE_ENV = "RTIME_PROFILE"
# Where the compose bind-mounts the git ``profiles/`` tree read-only (design §2.6).
# Overridable for local dev / the console harness.
PROFILES_ROOT_ENV = "RTIME_PROFILES_ROOT"
DEFAULT_PROFILES_ROOT = "/etc/rtime/profiles"

# Legacy prompt/flag env names that, when set AND a profile also supplies the same
# key, mean "env still wins but warn once" (design §2.8 transition rule). Kept
# small: only the keys a profile is expected to own where a lingering env value is
# a migration foot-gun (last-wins docker.env prompt, read-only flag, MCP surface,
# or public-instance private-chat policy).
_LEGACY_PROFILE_OVERRIDE_ENVS = (
    "QQ_SYSTEM_PROMPT",
    "QQ_READ_ONLY",
    "QQ_MCP_CONFIG",
    "QQ_PRIVATE_ACCESS",
)
# Map those legacy env names to the compiled ``qq.<field>`` key they shadow, so the
# migration warning only fires when the profile actually supplies that key.
_ENV_TO_QQ_PATH = {
    "QQ_SYSTEM_PROMPT": "qq.system_prompt",
    "QQ_READ_ONLY": "qq.read_only",
    "QQ_MCP_CONFIG": "qq.mcp_config",
    "QQ_PRIVATE_ACCESS": "qq.private_access",
}

# --- SECURITY: fail-closed restriction fields -----------------------------------
# Most fields resolve env > store > profile > default (env last-wins). Security
# RESTRICTION fields are a DELIBERATE EXCEPTION to last-wins: they are MONOTONIC —
# any layer that asserts a restriction wins, and env can only STRENGTHEN a
# restriction, NEVER weaken one a lower layer (profile/store) declared. This is the
# general class "修通用类": the generic last-wins would let compose's env defaults
# (``QQ_READ_ONLY=${QQ_READ_ONLY:-0}``, ``QQ_BLOCKED_USERS=${…:-}``) SILENTLY WEAKEN a
# profile's restriction — a fail-OPEN. Restrictions must fail CLOSED.
#
# Two kinds, both monotonic:
#   * boolean restriction (read_only): union = OR; env=="1" strengthens, env "0"/""
#     cannot pull a profile/store True down to False.
#   * id-set restriction (blocked_users): union = ∪; env ADDS blocked ids, an empty
#     env can never REMOVE ids a profile/store blocked (an empty ``QQ_BLOCKED_USERS``
#     — the compose default — must not un-block a profile's blocklist).
# The complementary read-only ENFORCEMENT (ToolPolicy) already ORs env=="1"; this
# fixes the config-LAYERING so the field value itself fails closed (design §2.9).
_RESTRICTION_BOOL_FIELDS_ENV = {
    "read_only": "QQ_READ_ONLY",
}
_RESTRICTION_IDSET_FIELDS_ENV = {
    "blocked_users": "QQ_BLOCKED_USERS",
}
# Convenience: every field that gets the special (non-last-wins) resolution.
_RESTRICTION_FIELDS = frozenset(_RESTRICTION_BOOL_FIELDS_ENV) | frozenset(
    _RESTRICTION_IDSET_FIELDS_ENV
)


def _layer_value(field: str, store: object, profile_layer: dict) -> object:
    """The profile-or-store value for ``qq.<field>`` WITHOUT the env overlay.

    Reads the compiled profile layer and the persisted store backend directly, so an
    env value the store's ``get`` would treat as winning cannot mask a lower layer.
    Store (L2) takes precedence over profile when both set it (store > profile).
    """
    path = f"qq.{field}"
    module, fld = path.split(".", 1)
    stored = store.backend.load_config().get(module, {}).get(fld)  # type: ignore[attr-defined]
    if stored is not None:
        return stored
    return profile_layer.get(path)


def _restriction_bool_union(
    field: str, env_name: str, store: object, profile_layer: dict
) -> bool:
    """True iff ANY layer asserts the boolean restriction (fail-closed OR; §2.9).

    profile OR store OR env(=="1"). env=="1" STRENGTHENS; any other env value
    ("0"/unset/garbage) contributes nothing — it can never pull a lower layer's True
    down to False.
    """
    if os.getenv(env_name, "").strip() == "1":
        return True
    return bool(_layer_value(field, store, profile_layer))


def _restriction_idset_union(
    field: str, env_name: str, store: object, profile_layer: dict
) -> frozenset[str]:
    """Union of the id-set restriction across all layers (env ∪ store ∪ profile).

    env ADDS ids; an empty/unset env can never REMOVE ids a lower layer set. This is
    the analog of the boolean OR for a blocklist (a monotonic-grow restriction).
    """
    ids: set[str] = set()
    ids |= _parse_ids(os.getenv(env_name, ""))
    lower = _layer_value(field, store, profile_layer)
    if lower is not None:
        ids |= _parse_ids(lower)
    return frozenset(ids)


# frozenset id fields are populated from a comma/space env string, NOT JSON;
# NoDecode stops pydantic-settings from json.loads()-ing the raw value before our
# ``mode="before"`` validator splits it. (Direct construction passes a frozenset.)
IdSet = Annotated[frozenset[str], NoDecode]

PRIVATE_ACCESS_ADMIN_ALLOWED = "admin_allowed"
PRIVATE_ACCESS_FRIENDS = "friends"
PRIVATE_ACCESS_FRIENDS_AND_TEMPORARY = "friends_and_temporary"
PRIVATE_ACCESS_VALUES = frozenset(
    {
        PRIVATE_ACCESS_ADMIN_ALLOWED,
        PRIVATE_ACCESS_FRIENDS,
        PRIVATE_ACCESS_FRIENDS_AND_TEMPORARY,
    }
)


def _parse_ids(raw: object) -> frozenset[str]:
    """Split a comma/space separated id list into a normalized set of strings.

    Accepts an already-normalized ``frozenset`` (direct construction / tests) and
    passes it through unchanged; a string is split like the legacy ``from_env``.
    """
    if isinstance(raw, frozenset):
        return raw
    if raw is None:
        return frozenset()
    if isinstance(raw, (set, list, tuple)):
        return frozenset(str(tok).strip() for tok in raw if str(tok).strip())
    return frozenset(
        tok.strip() for tok in str(raw).replace(",", " ").split() if tok.strip()
    )


# Steers the model toward a chat assistant, overriding /mnt/brain/CLAUDE.md's
# library-operator framing (which makes kimi-code go agentic on plain chat).
QQ_CHAT_SYSTEM_PROMPT = (
    "你是用户在 QQ 私聊里的个人助手，底层模型 Kimi，能访问用户的 brain 个人知识库"
    "（课程/科研/个人档案/记忆）。原则："
    "① 直接、简洁地回答，像聊天，别啰嗦；"
    "② 闲聊就正常聊，绝不要为一句问候或简单问题去读库规范、整理或维护知识库；"
    "③ 需要查 brain 时，**优先用 MCP 工具 lib_search（已建索引，最快）**一次拿到命中，"
    "再用 lib_read 或读绝对路径 /mnt/brain/… 取原文；只有 lib_search 不可用时才退用 Grep"
    "（缩到相关子目录，别对整个 /mnt/brain 做 grep -r）；拿到原文再答，简要标出处；"
    "④ 绝不要为简单问题启动子代理（Agent/Task）做多步任务，直接回答；"
    "⑤ QQ 是纯文本，不渲染 markdown/LaTeX，公式用 Unicode 近似或文字表达。"
)


class QQBridgeConfig(RtimeBaseSettings):
    # env_prefix="" (not "QQ_") on purpose: every field declares its COMPLETE set
    # of accepted env names via env_aliases, so the accepted env surface equals
    # exactly what is declared (and what x-env-aliases documents) — no implicit
    # prefix-derived names silently widening it. This keeps the pilot strictly
    # behaviour-preserving: the legacy QQ_* / QQ_BRIDGE_* / QQ_ONEBOT_* / shared
    # unprefixed (DEFAULT_MODEL, PERMISSION_MODE, …) names each load as before,
    # and nothing new is accepted unless explicitly listed.
    model_config = SettingsConfigDict(
        env_prefix="",
        extra="ignore",
        populate_by_name=True,
        validate_default=True,
        case_sensitive=False,
    )

    owner_ids: IdSet = config_field(
        default_factory=frozenset,
        description="Owner QQ id(s), comma/space separated. Private-message hard "
        "gate baseline; also the default admin set.",
        reload=Reload.HOT,
        env_aliases=["QQ_OWNER_IDS"],
    )
    # --- 用户分级(公开答疑实例) ---
    # 名单四件套标 hot:设计 §2.10 把用户名单列为热调项(store 可调+profile reload
    # 即生效);桥内按会话构建时读取的落地在 T8,元数据先行成为真相。
    admin_ids: Annotated[frozenset[str] | None, NoDecode] = config_field(
        default=None,
        description="管理员:私聊全功能(含斜杠命令、自选模型)。None(默认)=沿用 "
        "owner_ids(向后兼容);显式给出时以其为准。",
        reload=Reload.HOT,
        env_aliases=["QQ_ADMIN_IDS"],
    )
    allowed_users: IdSet = config_field(
        default_factory=frozenset,
        description="普通用户私聊白名单:可私聊提问,但无斜杠命令、模型固定为实例默认。"
        "空(默认)=私聊仅 admin。",
        reload=Reload.HOT,
        env_aliases=["QQ_ALLOWED_USERS"],
    )
    private_access: str = config_field(
        default=PRIVATE_ACCESS_ADMIN_ALLOWED,
        description="私聊开放策略: admin_allowed(默认)=仅 admin+allowed_users; "
        "friends=所有好友私聊可问; friends_and_temporary=好友私聊 + 群临时会话可问。"
        "黑名单仍最高优先级;好友请求是否通过不由此字段控制。",
        reload=Reload.HOT,
        env_aliases=["QQ_PRIVATE_ACCESS"],
    )
    blocked_users: IdSet = config_field(
        default_factory=frozenset,
        description="黑名单,优先级最高:命中一律拒 —— 包括公开群内(公开群防捣乱的关键)。",
        reload=Reload.HOT,
        env_aliases=["QQ_BLOCKED_USERS"],
    )
    ws_host: str = config_field(
        default="0.0.0.0",
        description="Reverse-WebSocket bind host.",
        env_aliases=["QQ_BRIDGE_WS_HOST"],
    )
    ws_port: int = config_field(
        default=8080,
        description="Reverse-WebSocket bind port.",
        env_aliases=["QQ_BRIDGE_WS_PORT"],
    )
    ws_path: str = config_field(
        default="/onebot/v11",
        description="Reverse-WebSocket path NapCat connects to.",
        env_aliases=["QQ_BRIDGE_WS_PATH"],
    )
    access_token: str | None = secret_field(
        default=None,
        description="OneBot access token shared with NapCat. None => no auth.",
        env_aliases=["QQ_ONEBOT_ACCESS_TOKEN"],
    )
    archive_path: str | None = config_field(
        default=None,
        description="Full-content archive of incoming events (JSONL). None disables it. "
        "Legacy 平铺单文件;archive_root 设置时被 envelope 分片归档取代。",
        env_aliases=["QQ_BRIDGE_ARCHIVE"],
    )
    archive_root: str | None = config_field(
        default=None,
        description="通道无关聊天归档根(design chat-archive-storage §1):设置即启用 "
        "rtime_chat_runtime.archive 的按日分片 envelope 归档(<root>/raw/qq/YYYY/MM/DD/"
        "events.jsonl),优先于 legacy archive_path;None=沿用 legacy。",
        env_aliases=["QQ_ARCHIVE_ROOT", "RTIME_CHAT_ARCHIVE_ROOT"],
    )
    archive_mode: str = config_field(
        default="events",
        description="归档模式 off|events|full(design 配置面):off=不落盘;events=raw "
        "envelope 层;full=预留(A2 normalized transcript 落地后与 events 才有差异)。"
        "仅当 archive_root 设置时生效。",
        env_aliases=["QQ_ARCHIVE_MODE"],
    )
    group_invite_policy: str = config_field(
        default="reject",
        description="Group-join gate for group *invite* requests: reject (default) "
        "— never auto-join; allow — auto-join; owner — only if the inviter is an owner.",
        env_aliases=["QQ_GROUP_INVITE_POLICY"],
    )
    group_allowlist: IdSet = config_field(
        default_factory=frozenset,
        description="Groups the bot is allowed to stay in. Empty => auto-leave any "
        "group it joins (only when group_autoleave is on).",
        env_aliases=["QQ_GROUP_ALLOWLIST"],
    )
    group_autoleave: bool = config_field(
        default=True,
        description="自动退非白名单群总开关(默认开=历史行为)。设 0 关闭:bot 待在任何"
        "被拉进的群里,不自作主张退群——公开答疑实例(管理员手动管群)应设 0。",
        env_aliases=["QQ_GROUP_AUTOLEAVE"],
    )
    public_groups: IdSet = config_field(
        default_factory=frozenset,
        description="公开答疑群:群内 ANY 成员 @bot 提问都会被回答。空(默认)=关。"
        "注意:这些群必须同时写进 QQ_GROUP_ALLOWLIST,否则防拉群逻辑会让 bot 自动退群。"
        "被 open_public 完全覆盖(开放模式下不再看这个白名单)。",
        reload=Reload.HOT,
        env_aliases=["QQ_PUBLIC_GROUPS"],
    )
    open_public: bool = config_field(
        default=False,
        description="开放答疑模式:True 时任何群里任何非黑名单成员 @bot 都能提问"
        "(不再要求群在 public_groups 白名单里)。black > admin > user 的次序不变:"
        "黑名单仍一律拒,admin 仍是 admin。仅放开【群答疑的准入范围】;私聊由 "
        "admin/allowed_users/private_access 单独控制;不放开 read_only/库 scope"
        "(那些是独立的硬门,与谁在问无关)。配合 group_autoleave=false"
        "(开放模式下 bot 留在每个群)。",
        reload=Reload.HOT,
        env_aliases=["QQ_OPEN_PUBLIC"],
    )
    group_reply_at_sender: bool = config_field(
        default=False,
        description="群聊回复是否在文本消息开头 @ 提问者。默认关(兼容旧行为);公开答疑"
        "实例可打开,让群内多人提问时每条回复都明确指向触发者。",
        reload=Reload.HOT,
        env_aliases=["QQ_GROUP_REPLY_AT_SENDER"],
    )
    # --- M2 model integration (mirrors apps/feishu-bridge/bot_config.py) ---
    claude_cli: str = config_field(
        default="",
        description="The claude CLI / claude-rtime wrapper. Empty + not on PATH => "
        "model disabled (echo only). from_env resolves CLAUDE_CLI_PATH then PATH.",
        env_aliases=["CLAUDE_CLI_PATH"],
    )
    model: str = config_field(
        default="",
        description='Default model; "" => wrapper default (kimi-code).',
        reload=Reload.HOT,
        env_aliases=["DEFAULT_MODEL"],
    )
    permission_mode: str = config_field(
        default="default",
        description="Model CLI permission mode.",
        env_aliases=["PERMISSION_MODE"],
    )
    # read-only 硬门的 schema 真相位(profile permissions.read_only 的编译目标)。
    # 生效点仍是 tool_policy.read_only_enabled()(每次调用读 QQ_READ_ONLY env);
    # 桥改读本字段属 T2/T8 接线。安全项从严:restart 级,不做热切(设计 §2.10)。
    read_only: bool = config_field(
        default=False,
        description="实例只读硬门:True 时强制 READONLY 权限模式+禁所有写工具"
        "(公开只读实例如学生会答疑开,owner 实例关)。重启级,不热切。",
        env_aliases=["QQ_READ_ONLY"],
    )
    default_cwd: str = config_field(
        default="",
        description='Where the model runs; "" => $HOME (brain dir in prod). '
        "~ expanded.",
        env_aliases=["DEFAULT_CWD"],
    )
    sessions_dir: str = config_field(
        default="",
        description="Session-id store; independent from the Feishu bridge. from_env "
        "default = ~/.qq-claude (expanded).",
        env_aliases=["QQ_SESSIONS_DIR"],
    )
    stream_output: bool = config_field(
        default=True,
        description="Stream intermediate output (partial text + tool-call status).",
        env_aliases=["QQ_STREAM_OUTPUT"],
    )
    show_tool_calls: bool = config_field(
        default=False,
        description="Reveal which tools/commands ran. Off => a single generic "
        '"查阅中…" ping while streaming.',
        env_aliases=["QQ_SHOW_TOOL_CALLS"],
    )
    system_prompt: str = config_field(
        default=QQ_CHAT_SYSTEM_PROMPT,
        description="Chat system prompt steering the model toward a QQ chat assistant."
        " 设计 §2.10 列为热调:内容改动下条会话即生效(桥内按内容 hash 缓存,T8 接线)。",
        reload=Reload.HOT,
        env_aliases=["QQ_SYSTEM_PROMPT"],
    )
    mcp_config: str | None = config_field(
        default='{"mcpServers": {}}',
        description="MCP config for the model CLI (inline JSON or a path). Default / "
        "empty => no MCP servers (skips ~1.4s cold-start; QQ reaches brain via the "
        "/mnt/brain mount). 重启级(设计 §2.10:mcp_servers 改动需重启)。",
        env_aliases=["QQ_MCP_CONFIG"],
    )
    log_level: str = config_field(
        default="INFO",
        description="Log level. QQ_DEBUG=1 forces DEBUG (overrides QQ_LOG_LEVEL) in "
        "from_env.",
        env_aliases=["QQ_LOG_LEVEL"],
    )
    # --- M3 multimodal ---
    media_dir: str = config_field(
        default="",
        description="Where inbound images/files are downloaded. "
        '"" => <sessions_dir>/inbound. ~ expanded.',
        env_aliases=["QQ_MEDIA_DIR"],
    )
    max_download_bytes: int = config_field(
        default=20 * 1024 * 1024,
        description="Per inbound file size cap (bytes). from_env reads QQ_MAX_DOWNLOAD_MB.",
        env_aliases=["QQ_MAX_DOWNLOAD_BYTES"],
    )
    send_media: bool = config_field(
        default=True,
        description="Honor model [[rtime-send-image:…]] / [[rtime-send-file:…]] "
        "directives (outbound media).",
        env_aliases=["QQ_SEND_MEDIA"],
    )
    napcat_file_dir: str = config_field(
        default="",
        description="Host mount of NapCat's file temp dir, to read inbound url-less "
        'files. "" => such files dropped. ~ expanded.',
        env_aliases=["QQ_NAPCAT_FILE_DIR"],
    )
    # --- stability (A1) ---
    run_timeout_seconds: float = config_field(
        default=600.0,
        description="Hard wall-clock ceiling per model run; a hung run is killed + "
        "reported instead of wedging the bot.",
        env_aliases=["QQ_RUN_TIMEOUT_SECONDS"],
    )
    max_chat_locks: int = config_field(
        default=256,
        description="Cap on the per-chat lock/queue maps (idle cleanup above).",
        env_aliases=["QQ_MAX_CHAT_LOCKS"],
    )
    debounce_seconds: float = config_field(
        default=0.0,
        description="Burst debounce window: merge near-simultaneous text messages "
        "from one chat into one run. 0 = off.",
        env_aliases=["QQ_DEBOUNCE_SECONDS"],
    )
    debounce_max_messages: int = config_field(
        default=20,
        description="Max messages merged in one debounce window.",
        env_aliases=["QQ_DEBOUNCE_MAX_MESSAGES"],
    )
    debounce_max_chars: int = config_field(
        default=12000,
        description="Max chars merged in one debounce window.",
        env_aliases=["QQ_DEBOUNCE_MAX_CHARS"],
    )
    replay_grace_seconds: float = config_field(
        default=5.0,
        description="Drop NapCat replay/backlog message events whose OneBot time is "
        "older than the current reverse-WS connection by more than this many seconds. "
        "Raw archive still records them before this filter. 0 disables the filter.",
        env_aliases=["QQ_REPLAY_GRACE_SECONDS"],
    )
    suppress_sends_when_offline: bool = config_field(
        default=True,
        description="When heartbeat says the QQ account is offline, suppress outbound "
        "send/upload actions instead of continuing to call sendMsg and timing out.",
        env_aliases=["QQ_SUPPRESS_SENDS_WHEN_OFFLINE"],
    )
    # --- voice STT (D) ---
    stt_model_dir: str = config_field(
        default="",
        description="Local sherpa-onnx Paraformer model dir. Empty => STT off. "
        "~ expanded.",
        env_aliases=["QQ_STT_MODEL_DIR"],
    )
    napcat_http: str = config_field(
        default="http://127.0.0.1:3000",
        description="NapCat OneBot HTTP control API — used for get_record (SILK->WAV).",
        env_aliases=["QQ_NAPCAT_HTTP"],
    )
    # --- 风控/掉线告警 (D) ---
    alert_webhook: str = config_field(
        default="",
        description="Optional webhook for offline/disconnect alerts (out-of-band; the "
        "account being offline means QQ cannot notify). Empty => log + run_log only.",
        env_aliases=["QQ_ALERT_WEBHOOK"],
    )
    # --- 块5 正则直答 ---
    direct_rules_path: str = config_field(
        default="",
        description="JSON rule file for fixed FAQs answered WITHOUT the model. Empty "
        "(default) => off. ~ expanded. from_env reads QQ_DIRECT_RULES. 设计 §2.10 列为"
        "热调:改文件下条会话即生效(引擎从'构建一次'改为按 mtime 失效重建,T8 接线)。",
        reload=Reload.HOT,
        env_aliases=["QQ_DIRECT_RULES"],
    )

    # --- validators: reproduce the legacy from_env parsing so direct construction
    #     AND load-from-env both match the old dataclass byte-for-byte. ---

    @field_validator(
        "owner_ids",
        "allowed_users",
        "blocked_users",
        "group_allowlist",
        "public_groups",
        mode="before",
    )
    @classmethod
    def _coerce_ids(cls, v: object) -> frozenset[str]:
        # None / "" / "a, b" all normalize to a frozenset of trimmed strings.
        return _parse_ids(v)

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _coerce_admin_ids(cls, v: object) -> frozenset[str] | None:
        # admin_ids is special: None OR empty-string-from-env means "unset" => the
        # after-validator falls back to owner_ids (向后兼容). An explicit empty
        # frozenset (direct construction) is honored as "no admins", matching the
        # legacy dataclass __post_init__ (which only falls back when admin_ids is
        # None). A non-empty value normalizes like the other id fields.
        if v is None:
            return None
        if isinstance(v, str) and v.strip() == "":
            return None
        return _parse_ids(v)

    @field_validator("private_access", mode="before")
    @classmethod
    def _coerce_private_access(cls, v: object) -> str:
        mode = str(v or PRIVATE_ACCESS_ADMIN_ALLOWED).strip().lower().replace("-", "_")
        if mode in ("", "default", "closed", "whitelist", "admin"):
            return PRIVATE_ACCESS_ADMIN_ALLOWED
        if mode not in PRIVATE_ACCESS_VALUES:
            raise ValueError(
                "private_access must be one of: "
                + ", ".join(sorted(PRIVATE_ACCESS_VALUES))
            )
        return mode

    @model_validator(mode="after")
    def _admin_defaults_to_owner(self) -> "QQBridgeConfig":
        # admin_ids未显式给出 => 沿用 owner_ids(向后兼容:老配置/老测试只传 owner_ids)。
        if self.admin_ids is None:
            object.__setattr__(self, "admin_ids", self.owner_ids)
        return self

    @property
    def model_enabled(self) -> bool:
        return bool(self.claude_cli)

    @classmethod
    def from_env(cls) -> "QQBridgeConfig":
        """Load from process env, reproducing the legacy parsing exactly.

        pydantic-settings handles the plain QQ_* / alias reads; the handful of
        transforms it cannot express declaratively (PATH lookup for the CLI,
        QQ_DEBUG->DEBUG override, ~/.qq-claude fallback, QQ_MAX_DOWNLOAD_MB->bytes,
        ~ expansion) are applied here so behaviour is byte-identical to before.
        """
        claude_cli = (
            os.getenv("CLAUDE_CLI_PATH", "").strip() or shutil.which("claude") or ""
        )
        admin_raw = os.getenv("QQ_ADMIN_IDS", "").strip()
        return cls(
            owner_ids=_parse_ids(os.getenv("QQ_OWNER_IDS", "")),
            # 未设 QQ_ADMIN_IDS => None => model_validator 回落到 owner_ids。
            admin_ids=_parse_ids(admin_raw) if admin_raw else None,
            allowed_users=_parse_ids(os.getenv("QQ_ALLOWED_USERS", "")),
            private_access=os.getenv(
                "QQ_PRIVATE_ACCESS", PRIVATE_ACCESS_ADMIN_ALLOWED
            ),
            blocked_users=_parse_ids(os.getenv("QQ_BLOCKED_USERS", "")),
            ws_host=os.getenv("QQ_BRIDGE_WS_HOST", "0.0.0.0"),
            ws_port=int(os.getenv("QQ_BRIDGE_WS_PORT", "8080")),
            ws_path=os.getenv("QQ_BRIDGE_WS_PATH", "/onebot/v11"),
            access_token=(os.getenv("QQ_ONEBOT_ACCESS_TOKEN", "").strip() or None),
            archive_path=(os.getenv("QQ_BRIDGE_ARCHIVE", "").strip() or None),
            archive_root=(
                os.getenv("QQ_ARCHIVE_ROOT", "").strip()
                or os.getenv("RTIME_CHAT_ARCHIVE_ROOT", "").strip()
                or None
            ),
            archive_mode=(
                os.getenv("QQ_ARCHIVE_MODE", "").strip().lower() or "events"
            ),
            group_invite_policy=(
                os.getenv("QQ_GROUP_INVITE_POLICY", "reject").strip().lower()
                or "reject"
            ),
            group_allowlist=_parse_ids(os.getenv("QQ_GROUP_ALLOWLIST", "")),
            group_autoleave=os.getenv("QQ_GROUP_AUTOLEAVE", "1").strip() != "0",
            public_groups=_parse_ids(os.getenv("QQ_PUBLIC_GROUPS", "")),
            open_public=os.getenv("QQ_OPEN_PUBLIC", "0").strip() not in ("", "0"),
            group_reply_at_sender=os.getenv("QQ_GROUP_REPLY_AT_SENDER", "0")
            .strip()
            not in ("", "0"),
            # read-only 硬门:env QQ_READ_ONLY 载入 config.read_only,让代码硬门统一
            # 由 config.read_only 驱动(T2:profile permissions.read_only 走同一字段)。
            read_only=os.getenv("QQ_READ_ONLY", "0").strip() not in ("", "0"),
            claude_cli=claude_cli,
            model=os.getenv("DEFAULT_MODEL", "").strip(),
            permission_mode=os.getenv("PERMISSION_MODE", "default").strip()
            or "default",
            default_cwd=os.path.expanduser(os.getenv("DEFAULT_CWD", "").strip())
            if os.getenv("DEFAULT_CWD", "").strip()
            else "",
            sessions_dir=os.path.expanduser(
                os.getenv("QQ_SESSIONS_DIR", "~/.qq-claude")
            ),
            stream_output=os.getenv("QQ_STREAM_OUTPUT", "1") != "0",
            show_tool_calls=os.getenv("QQ_SHOW_TOOL_CALLS", "0") != "0",
            system_prompt=os.getenv("QQ_SYSTEM_PROMPT", "").strip()
            or QQ_CHAT_SYSTEM_PROMPT,
            # Unset OR empty (compose passes "") => no MCP servers (skip the ~1.4s
            # cold-start + the unrelated ~/.claude.json MCPs). Set to gateway JSON to opt in.
            mcp_config=(os.getenv("QQ_MCP_CONFIG", "").strip() or '{"mcpServers": {}}'),
            log_level=(
                "DEBUG"
                if os.getenv("QQ_DEBUG", "0") != "0"
                else os.getenv("QQ_LOG_LEVEL", "INFO").strip().upper() or "INFO"
            ),
            media_dir=os.path.expanduser(os.getenv("QQ_MEDIA_DIR", "").strip()),
            max_download_bytes=int(
                float(os.getenv("QQ_MAX_DOWNLOAD_MB", "20")) * 1024 * 1024
            ),
            send_media=os.getenv("QQ_SEND_MEDIA", "1") != "0",
            napcat_file_dir=os.path.expanduser(
                os.getenv("QQ_NAPCAT_FILE_DIR", "").strip()
            ),
            run_timeout_seconds=float(os.getenv("QQ_RUN_TIMEOUT_SECONDS", "600")),
            debounce_seconds=float(os.getenv("QQ_DEBOUNCE_SECONDS", "0")),
            debounce_max_messages=int(os.getenv("QQ_DEBOUNCE_MAX_MESSAGES", "20")),
            debounce_max_chars=int(os.getenv("QQ_DEBOUNCE_MAX_CHARS", "12000")),
            replay_grace_seconds=float(os.getenv("QQ_REPLAY_GRACE_SECONDS", "5")),
            suppress_sends_when_offline=os.getenv(
                "QQ_SUPPRESS_SENDS_WHEN_OFFLINE", "1"
            ).strip()
            not in ("", "0"),
            stt_model_dir=os.path.expanduser(os.getenv("QQ_STT_MODEL_DIR", "").strip()),
            napcat_http=os.getenv("QQ_NAPCAT_HTTP", "http://127.0.0.1:3000").strip()
            or "http://127.0.0.1:3000",
            alert_webhook=os.getenv("QQ_ALERT_WEBHOOK", "").strip(),
            direct_rules_path=os.path.expanduser(
                os.getenv("QQ_DIRECT_RULES", "").strip()
            ),
        )

    @classmethod
    def load(cls) -> "QQBridgeConfig":
        """Load effective config: profile-aware when ``RTIME_PROFILE`` is set, else env.

        This is the single entry point the live bridge (``__main__``) uses. When
        ``RTIME_PROFILE`` is unset it is EXACTLY ``from_env()`` (backward compatible —
        the 195 existing tests and the current prod deploy are untouched). When set,
        it consumes the git profile layer (design §2, the T2 keystone): the effective
        value of every field is resolved ``env > store > profile > default`` and fed
        into the same ``QQBridgeConfig`` — so ``permissions.read_only: true`` in a
        profile reaches ``config.read_only`` and drives the code hard door, while a
        legacy env (``QQ_READ_ONLY`` / ``QQ_SYSTEM_PROMPT`` / …) still wins (env is
        the top layer) with a one-time migration warning.
        """
        profile_id = os.getenv(PROFILE_ENV, "").strip()
        if not profile_id:
            return cls.from_env()
        return cls.from_profile(profile_id)

    @classmethod
    def from_profile(
        cls,
        profile_id: str,
        *,
        profiles_root: str | None = None,
    ) -> "QQBridgeConfig":
        """Build config from the git profile ``profile_id`` (design §2 consumption chain).

        Precedence is ``env > store > profile > default``, delegated to the admin-core
        ``ConfigStore`` (which resolves exactly that order at read time). The compiled
        profile layer is injected as the store's read-only profile layer; there is no
        persistent store on disk for the bridge yet, so the store layer is empty
        (a live L2 store can be wired later without changing this precedence).

        env still wins: any ``qq.*`` field whose env alias is set takes the env value,
        and if that field is one the profile also supplies we emit a one-time
        ``legacy … env in use`` warning (§2.8 transition rule). File-ref fields
        (system prompt content, direct-rules path) come pre-resolved in the compiled
        layer; the remaining env-only transforms (CLI PATH lookup, ``QQ_DEBUG`` ->
        DEBUG, ``QQ_MAX_DOWNLOAD_MB`` -> bytes, ``~`` expansion) are layered from
        ``from_env`` for fields the env owns, so behaviour stays byte-identical to the
        env path for every env-set field.
        """
        cfg, _watch = cls._build_from_profile(profile_id, profiles_root=profiles_root)
        return cfg

    @classmethod
    def _build_from_profile(
        cls,
        profile_id: str,
        *,
        profiles_root: str | None = None,
    ) -> tuple["QQBridgeConfig", list[str]]:
        """The body of :meth:`from_profile`, also returning the SOURCE files to watch.

        Returns ``(config, watch_files)`` where ``watch_files`` are the on-disk
        profile sources whose ``mtime`` changing means the compiled config changed:
        the ``profile.yaml``, its ``extends`` parent (if any), and every resolved
        file reference (system-prompt file, direct-rules file). :class:`ConfigProvider`
        stats exactly these to decide when a hot re-read is needed (design §2.10) —
        so an unchanged profile costs one stat per file and NO recompile.
        """
        # Lazy imports: admin-core + the profile loader are only needed on the
        # profile path, keeping the plain env path free of the dependency.
        from pathlib import Path

        from rtime_admin_core import (
            ConfigStore,
            InMemoryHistory,
            MemoryBackend,
            default_registry,
            validate_state,
        )
        from rtime_config.profile import load_profile

        root = Path(
            profiles_root
            or os.getenv(PROFILES_ROOT_ENV, "").strip()
            or DEFAULT_PROFILES_ROOT
        )
        profile_dir = root / profile_id
        registry = default_registry(include_qq=True)
        compiled = load_profile(
            profile_dir,
            registry=registry,
            profiles_root=root,
            validate=validate_state,
        )
        # The files a hot re-read must watch: the profile doc, its parent (extends),
        # and the resolved file refs (compiled.files values are absolute paths for
        # content refs / resolved paths for path refs).
        watch_files: list[str] = [str(Path(compiled.source))]
        if compiled.parent_id:
            child_raw = _read_profile_yaml_head(profile_dir / "profile.yaml")
            extends = (child_raw.get("profile") or {}).get("extends")
            if extends:
                rel = (
                    extends
                    if extends.endswith((".yaml", ".yml"))
                    else f"{extends}.yaml"
                )
                watch_files.append(str((root / rel).resolve()))
        watch_files.extend(str(p) for p in compiled.files.values())
        store = ConfigStore(
            registry,
            MemoryBackend(),  # no persistent store yet: store layer is empty
            InMemoryHistory(),
            profile_layer=compiled.layer,
        )

        # One-time migration warning: a legacy env value that shadows a profile key.
        _bool_restriction_envs = set(_RESTRICTION_BOOL_FIELDS_ENV.values())
        for env_name in _LEGACY_PROFILE_OVERRIDE_ENVS:
            raw = os.getenv(env_name, "").strip()
            if not raw:
                continue
            target = _ENV_TO_QQ_PATH.get(env_name)
            if not (target and target in compiled.layer):
                continue
            if env_name in _bool_restriction_envs:
                # Boolean restriction: env CANNOT downgrade the profile (fail-closed
                # union). A redundant/misleading env value (e.g. QQ_READ_ONLY=0 while
                # the profile is read_only) is a no-op — warn so ops removes the
                # misleading key, but make clear it did NOT weaken the restriction.
                if raw != "1":
                    log.warning(
                        "legacy %s=%r is a no-op — it CANNOT disable the profile "
                        "%r restriction (fail-closed union; §2.9). Remove this "
                        "misleading env key (see deploy/PROFILE-CUTOVER.md).",
                        env_name,
                        raw,
                        profile_id,
                    )
            else:
                log.warning(
                    "legacy %s env in use — it overrides the profile %r value "
                    "(env still wins for one version; move it into the profile "
                    "and unset the env; design §2.8)",
                    env_name,
                    profile_id,
                )

        # env transforms for the fields the env path massages beyond a plain read.
        # We take the env-computed base and override, per field, with the store's
        # env>store>profile>default resolution EXCEPT where env is the winning layer
        # for that field (then the from_env-transformed value is authoritative).
        env_cfg = cls.from_env()
        values: dict[str, object] = {}
        for fld in cls.model_fields:
            # SECURITY exception: restriction fields resolve as a fail-closed union
            # (monotonic; env can only strengthen, never weaken a lower layer).
            if fld in _RESTRICTION_BOOL_FIELDS_ENV:
                values[fld] = _restriction_bool_union(
                    fld, _RESTRICTION_BOOL_FIELDS_ENV[fld], store, compiled.layer
                )
                continue
            if fld in _RESTRICTION_IDSET_FIELDS_ENV:
                values[fld] = _restriction_idset_union(
                    fld, _RESTRICTION_IDSET_FIELDS_ENV[fld], store, compiled.layer
                )
                continue
            path = f"qq.{fld}"
            prov = store.provenance(path)
            if prov == "env":
                # env owns this field: keep from_env's fully-transformed value.
                values[fld] = getattr(env_cfg, fld)
            else:
                # store/profile/default: take the resolved (typed) value.
                values[fld] = store.get(path)
        # admin_ids is special: model_validator falls back to owner_ids when None.
        # The store returns the compiled/default value; keep None -> fallback intact.
        return cls(**values), watch_files


def _read_profile_yaml_head(path) -> dict:
    """Parse just enough of a profile.yaml to read its ``profile.extends`` ref.

    Used only on the (rare) profile-rebuild path to locate the parent file to
    watch; a parse failure degrades to ``{}`` (the parent is then simply not
    watched, which at worst means an extends-only edit needs a restart — never a
    crash). Not on the hot per-message path.
    """
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — best-effort parent discovery, never crash
        return {}


class ConfigProvider:
    """Live, hot-reloading source of the effective :class:`QQBridgeConfig` (T8, §2.10).

    The bridge used to build one frozen config at startup, so every hot field (user
    lists, system prompt, model default, direct-rules path, …) needed a container
    restart to change. This provider re-reads the profile-backed config WITHOUT a
    restart, on the next session build, when — and ONLY when — the underlying
    profile source files change.

    PERF (owner hard constraint — no per-message latency regression): :meth:`current`
    on the steady state (config unchanged) does at most ONE ``os.stat`` per watched
    profile file (profile.yaml + parent + prompt/rules files — a handful) and returns
    the cached config object; it does NOT re-parse YAML, re-run the loader, or rebuild
    the ConfigStore. A recompile happens only when a stat shows a file moved (an
    operator edit or an admin-api ``:reload`` that rewrote the store/profile files).

    Env-only mode (``RTIME_PROFILE`` unset): there is no profile file to watch, so
    the config is read once from env and returned as a frozen constant — identical to
    the pre-T8 behaviour, zero stat, zero overhead (env changes still need a restart,
    which was always true for the env path).
    """

    def __init__(
        self,
        config: QQBridgeConfig,
        *,
        watch_files: list[str] | None = None,
        profile_id: str = "",
        profiles_root: str | None = None,
    ) -> None:
        self._config = config
        self._profile_id = profile_id
        self._profiles_root = profiles_root
        self._watch = list(watch_files or [])
        self._sig = self._stat_all()

    @classmethod
    def load(cls) -> "ConfigProvider":
        """Build a provider mirroring :meth:`QQBridgeConfig.load` (profile-aware).

        ``RTIME_PROFILE`` set => a hot provider watching that profile's source files;
        unset => a frozen env-only provider (backward compatible).
        """
        profile_id = os.getenv(PROFILE_ENV, "").strip()
        if not profile_id:
            return cls(QQBridgeConfig.from_env())
        profiles_root = os.getenv(PROFILES_ROOT_ENV, "").strip() or None
        config, watch = QQBridgeConfig._build_from_profile(
            profile_id, profiles_root=profiles_root
        )
        return cls(
            config,
            watch_files=watch,
            profile_id=profile_id,
            profiles_root=profiles_root,
        )

    def _stat_all(self) -> tuple[tuple[float, int] | None, ...]:
        """A signature of every watched file — one ``os.stat`` each, missing => None."""
        sig: list[tuple[float, int] | None] = []
        for path in self._watch:
            try:
                st = os.stat(path)
                sig.append((st.st_mtime, st.st_size))
            except OSError:
                sig.append(None)
        return tuple(sig)

    def current(self) -> QQBridgeConfig:
        """The live effective config, recompiled only when a watched file changed.

        The fast path (no profile, or all watched files unchanged) returns the cached
        config after at most one stat per watched file — no parse, no rebuild.
        """
        if not self._profile_id:
            return self._config  # env-only: frozen, no stat
        sig = self._stat_all()
        if sig != self._sig:
            try:
                config, watch = QQBridgeConfig._build_from_profile(
                    self._profile_id, profiles_root=self._profiles_root
                )
            except Exception:  # noqa: BLE001 — a broken edit must not brick the bridge
                log.exception(
                    "profile %r hot re-read failed; keeping the last-good config",
                    self._profile_id,
                )
                # refresh the signature so we don't retry every message on a
                # persistently-broken file; the next real change re-triggers.
                self._sig = sig
                return self._config
            self._config = config
            self._watch = watch
            self._sig = self._stat_all()
        return self._config
