# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""web-chat HTTP server — stdlib ThreadingHTTPServer, JSON-over-SSE chat.

Endpoints (design §5.2):
    GET  /               static index.html (fetch()+ReadableStream client)
    GET  /api/profiles   web-enabled profiles (real loader, see web_chat.profiles)
    POST /api/chat       {profile, message, session_id?} -> SSE stream
    GET  /healthz        liveness

Every chat turn goes through rtime-chat-runtime: ToolPolicy (allow/disallow +
hints + read-only hard door), the shared SessionStore (multi-turn --resume by
session_id), append_run_event (redacted run log) and run_claude (the unified CLI
model runner). web-chat NEVER talks to LiteLLM / model endpoints directly.

SSE event shapes match apps/assistant-gateway (the proven web-ready protocol):
    {"type":"status","text":...}   progress / tool activity
    {"type":"delta","text":...}    streamed answer text
    {"type":"done","answer":...,"session_id":...,"profile":...}
    {"type":"error","message":...}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from rtime_chat_runtime.model_runner import run_claude
from rtime_chat_runtime.run_log import (
    append_run_event,
    hash_value,
    new_run_id,
    summarize_text,
)
from rtime_chat_runtime.session_store import SessionStore
from rtime_chat_runtime.sse import CORS_HEADERS, start_sse, write_sse_frame

from rtime_chat_runtime.archive import make_archive_func

from .config import WebChatConfig
from .profiles import get_profile, load_profiles, public_view
from .tool_policy import READONLY_PERMISSION_MODE, policy_for_profile

log = logging.getLogger("web_chat.server")

_STATIC_DIR = Path(__file__).resolve().parent / "static"

# 本轮固定匿名访客（内网，design §5.2）。公网化时鉴权在 _resolve_actor 落位：
# 把认证结果映射成 actor 字符串，其余管线（session key、run_log hash、access
# tier）已按 actor 参数穿好线，不需要再改。
ANONYMOUS_ACTOR = "web:anonymous"


class WebChatHandler(BaseHTTPRequestHandler):
    """One request per connection (HTTP/1.0 close semantics, same as the gateway)."""

    server_version = "rtime-web-chat/0.1"
    # Injected by build_server() onto a per-server subclass:
    cfg: WebChatConfig
    store: SessionStore
    profiles: list[dict]
    archive: object | None = None  # A1.5: make_archive_func(...) 或 None(零归档)

    # --- plumbing -----------------------------------------------------------
    def log_message(self, fmt: str, *args) -> None:  # route to logging, not stderr
        log.debug("%s %s", self.address_string(), fmt % args)

    def _cors_headers(self) -> None:
        for name, value in CORS_HEADERS:
            self.send_header(name, value)

    def _respond_json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def _resolve_actor(self) -> str:
        """Auth slot-in point (kept threaded through the whole run path)."""
        return ANONYMOUS_ACTOR

    def _sse(self, obj: dict) -> bool:
        """Emit one frame; False once the client is gone (never raises).

        The model run is not killed on disconnect this round — it finishes and is
        still run-logged; we just stop writing. TODO(T5b+): FIN watcher + child
        kill like assistant-gateway for prompt slot release."""
        if getattr(self, "_client_gone", False):
            return False
        try:
            write_sse_frame(self.wfile, obj)
            return True
        except OSError:
            self._client_gone = True
            return False

    # --- routing ------------------------------------------------------------
    def do_OPTIONS(self) -> None:  # noqa: N802 — CORS preflight for fetch() streaming
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path != "/":
            path = path.rstrip("/")
        if path == "/":
            self._serve_index()
        elif path == "/healthz":
            self._respond_json(200, {"ok": True, "service": "web-chat"})
        elif path == "/api/profiles":
            self._respond_json(
                200,
                {
                    "profiles": public_view(self.profiles),
                    "default": self.profiles[0]["id"],
                },
            )
        else:
            self._respond_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path != "/api/chat":
            self._respond_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
        except (ValueError, json.JSONDecodeError):
            self._respond_json(400, {"error": "请求不是有效JSON对象"})
            return
        self._handle_chat(body)

    def _serve_index(self) -> None:
        index = _STATIC_DIR / "index.html"
        try:
            data = index.read_bytes()
        except OSError:
            self._respond_json(404, {"error": "index.html missing"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    # --- chat ----------------------------------------------------------------
    def _handle_chat(self, body: dict) -> None:
        # A1.5 归档先于一切业务判定(与 QQ _dispatch 顶部同构):body 原样入 raw 层。
        if self.archive is not None:
            self.archive({"endpoint": "/api/chat", "body": body})
        message = body.get("message")
        if not isinstance(message, str) or not message.strip():
            self._respond_json(400, {"error": "message 不能为空"})
            return
        message = message.strip()
        profile_id = str(body.get("profile") or self.profiles[0]["id"])
        profile = get_profile(self.profiles, profile_id)
        if profile is None:
            self._respond_json(400, {"error": f"未知profile：{profile_id!r}"})
            return
        raw_session = body.get("session_id")
        session_id = (
            str(raw_session).strip()
            if isinstance(raw_session, (str, int)) and str(raw_session).strip()
            else uuid.uuid4().hex
        )
        actor = self._resolve_actor()
        # Per-(actor, profile, conversation) continuity: switching profile must
        # never --resume the other profile's CLI session.
        chat_key = f"{profile_id}:{session_id}"
        session = self.store.get(actor, chat_key)

        policy = policy_for_profile(profile)
        prompt = policy.add_runtime_policy_hints(message)
        allowed = policy.allowed_tools_for_text(message)
        disallowed = policy.disallowed_tools_for_text(message)
        # Per-profile MCP gateway (web = gateway-only consumer): the profile's
        # channels.web.mcp_servers picks the gateway this session reaches (e.g. the
        # studentunion scoped 8781 gateway, whose library-policy enforces the same
        # scope + personal-data denial as the QQ session). None => process default.
        mcp_config = profile.get("mcp_config") or self.cfg.mcp_config
        # read-only 硬门：权限模式在代码里强制 dontAsk，不信任配置里的
        # bypassPermissions（allowlist 的收窄效果依赖非 bypass 模式，语义见
        # rtime_chat_runtime.tool_policy）。
        permission_mode = (
            READONLY_PERMISSION_MODE
            if policy.is_read_only()
            else session.permission_mode
        )

        run_id = new_run_id("web")
        append_run_event(
            "run_started",
            run_id=run_id,
            entry="web",
            profile=profile_id,
            actor_hash=hash_value(actor),
            chat_hash=hash_value(chat_key),
            model=session.model or "wrapper-default",
            read_only=policy.is_read_only(),
            message_chars=len(message),
            message_preview=summarize_text(message),
        )
        log.info(
            "run %s start: profile=%s chars=%d read_only=%s",
            run_id,
            profile_id,
            len(message),
            policy.is_read_only(),
        )

        start_sse(self)
        self._sse({"type": "status", "text": "已接收请求…"})

        sent_generic_tool_status = False

        # web = RenderPolicy.MARKDOWN (passthrough): raw markdown streams verbatim and
        # the frontend renders (marked + KaTeX). No server-side render() call needed —
        # that policy is a no-op. profile.output.render feeds this mapping (T2/T5b).
        def on_chunk(chunk: str) -> None:
            self._sse({"type": "delta", "text": chunk})

        def on_tool(name: str, _inp: dict) -> None:
            nonlocal sent_generic_tool_status
            if self.cfg.show_tool_calls:
                self._sse({"type": "status", "text": f"使用工具 {name}…"})
            elif not sent_generic_tool_status:
                sent_generic_tool_status = True
                self._sse({"type": "status", "text": "查阅中…"})

        try:
            if self.cfg.model_enabled:
                answer, new_sid, used_fresh = asyncio.run(
                    run_claude(
                        prompt,
                        cli=self.cfg.claude_cli,
                        permission_mode=permission_mode,
                        session_id=session.session_id,
                        model=session.model or None,
                        cwd=session.cwd or None,
                        system_prompt=profile["system_prompt"],
                        mcp_config=mcp_config,
                        allowed_tools=allowed,
                        disallowed_tools=disallowed,
                        on_text_chunk=on_chunk,
                        on_tool_use=on_tool,
                        max_seconds=self.cfg.run_timeout_seconds,
                    )
                )
            else:
                # Dev fallback (no claude CLI): proves the protocol end to end.
                answer, new_sid, used_fresh = f"(echo) {message}", None, False
                self._sse({"type": "status", "text": "模型未配置，echo模式"})
                self._sse({"type": "delta", "text": answer})
        except Exception as exc:  # noqa: BLE001 — surface as an SSE error frame
            log.exception("run %s failed", run_id)
            append_run_event(
                "run_failed",
                run_id=run_id,
                entry="web",
                profile=profile_id,
                error_type=type(exc).__name__,
                error_preview=summarize_text(str(exc)),
            )
            self._sse(
                {"type": "error", "message": f"模型出错：{type(exc).__name__}: {exc}"}
            )
            return

        if new_sid:
            self.store.on_response(actor, chat_key, new_sid)
        done: dict = {
            "type": "done",
            "answer": answer,
            "session_id": session_id,
            "profile": profile_id,
        }
        if used_fresh:
            done["used_fresh_session_fallback"] = True
        self._sse(done)
        append_run_event(
            "run_completed",
            run_id=run_id,
            entry="web",
            profile=profile_id,
            actor_hash=hash_value(actor),
            session_hash=hash_value(new_sid),
            output_chars=len(answer),
            used_fresh_session_fallback=used_fresh,
            client_disconnected=getattr(self, "_client_gone", False),
            reply_preview=summarize_text(answer, 600),
        )
        log.info("run %s done: output_chars=%d", run_id, len(answer))


def build_server(cfg: WebChatConfig) -> ThreadingHTTPServer:
    """Build a ready-to-serve ThreadingHTTPServer with its own store/profiles.

    A per-server Handler subclass keeps state isolated (tests run many servers
    in one process). load_profiles() is called once here — fail fast on a
    malformed profile override instead of 500ing per request."""
    profiles = load_profiles()
    store = SessionStore(
        os.path.join(cfg.state_dir, "sessions"),
        default_model=cfg.model,
        default_permission_mode=cfg.permission_mode,
        default_cwd=cfg.default_cwd,
    )
    handler = type(
        "BoundWebChatHandler",
        (WebChatHandler,),
        {
            "cfg": cfg,
            "store": store,
            "profiles": profiles,
            "archive": make_archive_func(cfg.archive_root, "web", cfg.archive_mode),
        },
    )
    httpd = ThreadingHTTPServer((cfg.bind, cfg.port), handler)
    httpd.daemon_threads = True
    return httpd
