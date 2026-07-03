#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""rtime assistant gateway: Obsidian plugin backend.

Receives the plugin's AssistantRequestBody (schema_version=1), unlocks the
brain files referenced by the active note's frontmatter, runs claude-kimi in
non-interactive mode inside the brain workspace, and returns
{"answer": str, "sources": [...]}.

By default the gateway stays read-only. Set GATEWAY_ACCESS_MODE=full on trusted
local/private endpoints to allow write/edit tools for explicitly requested
organization and intake work.

v0.3 session protocol: one request runs at a time, busy requests join a short
FIFO queue instead of getting 503 (queue-full is the only 503 left); streaming
waiters get 排队中… heartbeats, and a failed heartbeat write dequeues the
abandoned request. Bodies may carry an optional conversation_id plus
context.history [{role,content}] which build_prompt folds into a clearly
non-instruction 此前对话回顾 section (newest-first char budget).

Stdlib only. Designed for systemd user service on orangepi, bound to the
Tailscale interface. See apps/assistant-gateway/README.md.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from attachments import (  # noqa: E402,F401
    build_attachments_section,
    request_has_image_attachments,
    request_has_file_attachments,
    request_has_archive_attachments,
    materialize_image_attachments,
)

from providers import (  # noqa: E402,F401
    openai_message_content,
    _openai_secret_for_provider,
    _multipart_form_data,
    _attachment_bytes,
    _moonshot_upload_file,
    _moonshot_file_content,
    moonshot_file_extract_messages,
    run_openai_chat,
    _openai_chat_request,
    stream_openai_chat,
)

from models import (  # noqa: E402,F401
    model_selection_supports_images,
    model_selection_supports_file_extract,
    MODEL_PROTOCOLS,
    _capabilities,
    _model,
    static_model_catalog,
    _fetch_openai_models,
    _replace_provider_models,
    refresh_model_catalog,
    get_model_catalog,
    resolve_model_selection,
)

from memory import (  # noqa: E402,F401
    build_memory_section,
    _memory_disabled,
    _memory_query_text,
    _memory_cards_fingerprint,
    _approved_memory_cards,
    _rank_memory_cards,
    approved_memory_injection,
    _memory_candidate_requested,
    _memory_candidate_claim,
    _candidate_slug,
    _write_review_queue_candidate,
    merge_memory_events,
    memory_events_for_request,
    memory_events_from_context,
    _MEMORY_CARDS_CACHE,
)

from _common import (  # noqa: E402,F401
    SCHEMA_VERSION,
    EXCLUDED_TOP_DIRS,
    FRONTMATTER_PATH_KEYS,
    SOURCE_LINE,
    ZERO_HIT,
    MAX_IMAGE_ATTACHMENT_BYTES,
    MAX_FILE_ATTACHMENT_BYTES,
    IMAGE_ATTACHMENT_MIME_PREFIXES,
    FILE_ATTACHMENT_KINDS,
    ARCHIVE_ATTACHMENT_KINDS,
    TOOL_ATTACHMENT_KINDS,
    MOONSHOT_FILE_EXTRACT_KINDS,
    RUNTIME_ERROR_QUERY,
    RUNTIME_ERROR_CODE,
    INVESTIGATION_QUERY,
    WEB_INTENT_QUERY,
    PERMISSION_MODES,
    DEFAULT_PERMISSION_MODE,
    DEFAULT_WEB_ALLOWED_TOOLS,
    FULL_ACCESS_MODES,
    FULL_ACCESS_ALLOWED_TOOLS,
    BEIJING_TZ,
    SENSITIVE_TEXT_RE,
    MEMORY_INTENT_RE,
    env_bool,
    sanitize_permission_mode,
    bool_option,
    access_mode,
    full_access_enabled,
    extract_frontmatter,
    safe_brain_path,
    _safe_attachment_name,
    _parse_memory_frontmatter,
    _memory_terms,
    _today_beijing,
    _beijing_date,
    csv_env,
    _read_secret,
    runtime_error_context,
    request_is_runtime_error_question,
)
from pathlib import Path
from gateway_config import load_config  # noqa: E402
from gateway_queue import RequestQueue  # noqa: E402
from gateway_intake import process_intake  # noqa: E402
from gateway_prompt import (  # noqa: E402
    HISTORY_ROLES,
    TASK_HINTS,
    allowed_tools,
    build_history_section,
    build_prompt,
    build_related_prefetch_section,
    build_runtime_diagnostic_section,
    enforce_agent_tool_model,
    related_edges_for_request,
    request_needs_investigation_budget,
    request_needs_web_budget,
    request_requires_agent_tools,
    runtime_diagnostic_records,
    _candidate_relation_sources,
)
# 动态上下文源子系统在 context_sources.py;re-import 回本命名空间,使
# gateway.context_source_injection 等调用点与测试无需改动(prompt 抽出后保留此再导出)。
from context_sources import context_source_injection  # noqa: E402,F401
from gateway_runner import (  # noqa: E402
    ClaudeStreamIncomplete,
    cfg_for_request,
    iter_stream_events,
    log_prewarm_event,
    parse_sources,
    prepare_context_key,
    run_claude,
    run_claude_stream,
    start_model_prewarm,
    _LIVE_PREWARM_LOCK,
    _LIVE_PREWARM_STATE,
    _PREWARM_LOCK,
    _PREWARM_STATE,
    _append_turn_limit,
    _claude_cmd,
    _claude_live_cmd,
    _claude_live_input,
    _trace_ms,
)
from gateway_unlocks import (  # noqa: E402
    collect_unlocks,
    manifest_lookup,
    public_unlocks,
    resolve_pdf_unlocks,
    resolve_request_unlocks,
    _MANIFEST_CACHE,
    _brain_rel,
)

# Archives are tool-model inputs, not file-extract provider inputs.
# Pre-filter for "why did the assistant/runtime just fail" questions. Kept to
# explicit failure references (报错/错误/后端/网关/超时/没回答/error codes) and
# deliberately NOT bare 怎么回事 / 为什么.*错, which collide with subject-matter
# study questions and would misroute them into runtime diagnosis on stale context.


PLUGIN_RELEASE_ROUTE = "/api/obsidian/plugin-release"
PLUGIN_RELEASE_FILES = {
    "release.json": "application/json; charset=utf-8",
    "manifest.json": "application/json; charset=utf-8",
    "main.js": "application/javascript; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
}


def plugin_release_file(path: str, cfg: dict) -> tuple[Path, str] | None:
    """Resolve a private Obsidian plugin release asset from a narrow allowlist."""
    route = urllib.parse.urlparse(path).path
    if route.rstrip("/") == PLUGIN_RELEASE_ROUTE:
        name = "release.json"
    elif route.startswith(f"{PLUGIN_RELEASE_ROUTE}/"):
        name = route.removeprefix(f"{PLUGIN_RELEASE_ROUTE}/").strip("/")
    else:
        return None
    if "/" in name or name not in PLUGIN_RELEASE_FILES:
        return None
    root = Path(cfg.get("plugin_release_dir") or "")
    target = root / name
    if not target.is_file():
        return None
    return target, PLUGIN_RELEASE_FILES[name]


_PREPARE_CACHE: dict[str, dict] = {}
_PREPARE_LOCK = threading.Lock()


def _prepare_cache_store(record: dict, cfg: dict) -> None:
    ttl = max(1, int(cfg.get("prepare_cache_ttl", 180)))
    max_items = max(1, int(cfg.get("prepare_cache_max", 64)))
    record["expires_at"] = time.time() + ttl
    with _PREPARE_LOCK:
        now = time.time()
        stale = [key for key, item in _PREPARE_CACHE.items() if item.get("expires_at", 0) <= now]
        for key in stale:
            _PREPARE_CACHE.pop(key, None)
        while len(_PREPARE_CACHE) >= max_items:
            oldest = min(_PREPARE_CACHE, key=lambda key: _PREPARE_CACHE[key].get("created_at", 0))
            _PREPARE_CACHE.pop(oldest, None)
        _PREPARE_CACHE[record["prepare_id"]] = record


def _prepare_cache_get(body: dict) -> dict | None:
    prepare_id = body.get("prepare_id")
    if not isinstance(prepare_id, str) or not prepare_id:
        return None
    with _PREPARE_LOCK:
        record = _PREPARE_CACHE.get(prepare_id)
        if not record:
            return None
        if record.get("expires_at", 0) <= time.time():
            _PREPARE_CACHE.pop(prepare_id, None)
            return None
        if record.get("context_key") != prepare_context_key(body):
            return None
        return dict(record)


def cached_or_resolved_unlocks(body: dict, cfg: dict) -> tuple[list[tuple[str, Path]], str | None]:
    record = _prepare_cache_get(body)
    if record:
        unlocks: list[tuple[str, Path]] = []
        for item in record.get("unlocks", []):
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "资料")
            raw = str(item.get("path") or "")
            path = safe_brain_path(raw, cfg["brain_root"])
            if path is not None:
                unlocks.append((label, path))
        if unlocks:
            return unlocks, record.get("prepare_id")
    return resolve_request_unlocks(body, cfg), None


def process_prepare(body: dict, cfg: dict) -> tuple[int, dict]:
    """Warm context assembly and optionally start a background model prewarm."""
    started = time.time()
    unlocks = resolve_request_unlocks(body, cfg)
    request_cfg = cfg_for_request(body, cfg)
    related = related_edges_for_request(body, unlocks, request_cfg)
    memory_events = memory_events_for_request(body, request_cfg)
    catalog = get_model_catalog(cfg)
    prewarm = start_model_prewarm(body, request_cfg, unlocks)
    ttl = max(1, int(cfg.get("prepare_cache_ttl", 180)))
    prepare_id = f"prep-{uuid.uuid4().hex[:12]}"
    record = {
        "prepare_id": prepare_id,
        "created_at": time.time(),
        "context_key": prepare_context_key(body),
        "unlocks": [
            {"label": label, "path": _brain_rel(path, cfg)}
            for label, path in unlocks
        ],
        "related_count": len(related),
        "memory_events": memory_events,
    }
    _prepare_cache_store(record, cfg)
    payload = {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "prepare_id": prepare_id,
        "cache_ttl_seconds": ttl,
        "dur_ms": int((time.time() - started) * 1000),
        "unlock_count": len(unlocks),
        "unlocks": public_unlocks(unlocks, cfg)[:12],
        "related_count": len(related),
        "memory_referenced_count": int((memory_events or {}).get("referenced_count") or 0),
        "model_catalog_cached": Path(cfg.get("model_catalog_path", "")).is_file(),
        "model_provider_count": len(catalog.get("providers", [])),
    }
    if prewarm.get("status") != "not_requested":
        payload["prewarm_status"] = prewarm.get("status")
        payload["prewarm_reason"] = prewarm.get("reason")
        if prewarm.get("model_provider_id"):
            payload["prewarm_model_provider_id"] = prewarm.get("model_provider_id")
        if prewarm.get("model_id") is not None:
            payload["prewarm_model_id"] = prewarm.get("model_id")
        if prewarm.get("model_protocol"):
            payload["prewarm_model_protocol"] = prewarm.get("model_protocol")
    return 200, payload


QUEUE_FULL_ANSWER = "助手繁忙且排队已满，请稍后重试。"
QUEUE_TIMEOUT_ANSWER = "排队等待超时，助手仍在处理较早的请求，请稍后重试。"


def _resolve_gateway_revision() -> str:
    """Best-effort short commit of the deployed gateway tree so /healthz, /version
    and every request log can prove WHICH code is actually running. This is the
    cheap fix for the stale-staged-copy deploy-drift class: a deploy can assert
    the live revision == git HEAD instead of trusting a bare 'ok'."""
    env_rev = (os.environ.get("GATEWAY_REVISION") or "").strip()
    if env_rev:
        return env_rev[:40]
    repo_root = Path(__file__).resolve().parents[2]
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:  # not a git tree (e.g. a copied dir): fall back to this file's mtime
        return "mtime-" + time.strftime("%Y%m%d-%H%M%S", time.localtime(Path(__file__).stat().st_mtime))
    except OSError:
        return "unknown"


GATEWAY_REVISION = _resolve_gateway_revision()
GATEWAY_STARTED_AT = time.strftime("%Y-%m-%dT%H:%M:%S%z")


class GatewayHandler(BaseHTTPRequestHandler):
    server_version = "rtime-assistant-gateway/0.5"
    cfg: dict = {}
    queue = RequestQueue(2)  # main() rebuilds with cfg["queue_max"]

    def do_GET(self) -> None:  # noqa: N802
        release_asset = plugin_release_file(self.path, self.cfg)
        if release_asset is not None:
            self._respond_file(*release_asset)
        elif self.path.rstrip("/") == "/api/obsidian/models":
            self._respond(200, get_model_catalog(self.cfg))
        elif self.path.rstrip("/") == "/version":
            self._respond(200, {
                "revision": GATEWAY_REVISION,
                "server_version": self.server_version,
                "started_at": GATEWAY_STARTED_AT,
            })
        elif self.path.rstrip("/") == "/healthz" or self.path == "/":
            # Keep a plain 2xx body (the plugin treats any 2xx as healthy) but stamp
            # the revision so "ok" alone can't hide stale code. Starts with "ok" for
            # backward compatibility; scripts should prefer /version.
            self._respond(200, f"ok {GATEWAY_REVISION}", raw_text=True)
        else:
            self._respond(404, {"answer": "not found", "sources": []})

    def _handle_intake(self) -> None:
        started = time.time()
        max_mb = int(self.cfg.get("intake_max_mb", 64))
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        # base64 + json overhead ≈ 1.4x of the raw file cap
        if length > int(max_mb * 1024 * 1024 * 1.4):
            self._respond(413, {"ok": False, "error": f"request exceeds {max_mb}MB intake limit"})
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._respond(400, {"ok": False, "error": "请求不是有效JSON。"})
            return
        if body.get("schema_version") != SCHEMA_VERSION:
            self._respond(400, {"ok": False, "error": f"schema_version不支持：{body.get('schema_version')!r}"})
            return
        try:
            status, payload = process_intake(body, self.cfg)
        except OSError as exc:
            status, payload = 500, {"ok": False, "error": f"intake write failed: {type(exc).__name__}"}
        self._log_intake(status, started, payload)
        self._respond(status, payload)

    def _log_intake(self, status: int, started: float, payload: dict) -> None:
        ticket = payload.get("ticket") or {}
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "endpoint": "intake",
            "status": status,
            "dur_ms": int((time.time() - started) * 1000),
            "size": ticket.get("size"),
            "sha8": (ticket.get("sha256") or "")[:8],
            "class": ticket.get("class"),
            "decision": ticket.get("decision"),
            "needs_confirm": payload.get("needs_confirm"),
            "notify": payload.get("notify"),
        }
        try:
            log_dir = Path(self.cfg["log_dir"])
            log_dir.mkdir(parents=True, exist_ok=True)
            with (log_dir / "requests.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _handle_prepare(self) -> None:
        started = time.time()
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._respond(400, {"ok": False, "error": "请求不是有效JSON。"})
            return
        if body.get("schema_version") != SCHEMA_VERSION:
            self._respond(400, {"ok": False, "error": f"schema_version不支持：{body.get('schema_version')!r}"})
            return
        try:
            status, payload = process_prepare(body, self.cfg)
        except OSError as exc:
            status, payload = 500, {"ok": False, "error": f"prepare failed: {type(exc).__name__}"}
        except Exception as exc:  # noqa: BLE001 - prepare should always return JSON
            status, payload = 500, {"ok": False, "error": f"prepare failed: {type(exc).__name__}"}
        self._log_prepare(body, status, started, payload)
        self._respond(status, payload)

    def _log_prepare(self, body: dict, status: int, started: float, payload: dict) -> None:
        try:
            log_dir = self.cfg["log_dir"]
            log_dir.mkdir(parents=True, exist_ok=True)
            context = body.get("context") or {}
            active = (context.get("active_file") or {}).get("path")
            pdf_page = (context.get("pdf") or {}).get("page")
            entry = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "endpoint": "prepare",
                "status": status,
                "dur_ms": int((time.time() - started) * 1000),
                "note": active,
                "pdf_page": pdf_page,
                "unlock_count": payload.get("unlock_count"),
                "related_count": payload.get("related_count"),
                "prewarm_status": payload.get("prewarm_status"),
                "prewarm_reason": payload.get("prewarm_reason"),
            }
            with (log_dir / "requests.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/api/obsidian/intake":
            self._handle_intake()
            return
        if self.path.rstrip("/") == "/api/obsidian/prepare":
            self._handle_prepare()
            return
        if self.path.rstrip("/") == "/api/obsidian/models/refresh":
            self._respond(200, refresh_model_catalog(self.cfg))
            return
        if self.path.rstrip("/") != "/api/obsidian/chat":
            self._respond(404, {"answer": "not found", "sources": []})
            return
        started = time.time()
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._respond(400, {"answer": "请求不是有效JSON。", "sources": []})
            return
        if body.get("schema_version") != SCHEMA_VERSION:
            self._respond(
                400,
                {"answer": f"schema_version不支持：{body.get('schema_version')!r}", "sources": []},
            )
            return
        stream = bool(body.get("stream")) or "text/event-stream" in (self.headers.get("Accept") or "")
        kind, ticket = GatewayHandler.queue.try_enter()
        if kind == "full":
            payload = {"answer": QUEUE_FULL_ANSWER, "sources": []}
            self._log_request(body, 503, started, payload)
            self._respond(503, payload)
            return
        # Streaming requests get a FIN watcher: it notices the client closing
        # the socket instantly (write-failure detection alone lags during tool
        # use / bursty generation, especially over Tailscale buffering).
        watch: dict | None = None
        client_gone: threading.Event | None = None
        if stream:
            self._start_sse()
            # Show "received" the instant the stream opens, before the (possibly
            # multi-hundred-ms) unlock + prompt-assembly work, so the spinner is
            # never blank waiting for the first real status event.
            try:
                self._sse({"type": "status", "text": "已接收请求…"})
            except OSError:
                pass
            watch = {"proc": None, "finished": False}
            client_gone = threading.Event()
            threading.Thread(
                target=self._watch_client_disconnect,
                args=(client_gone, watch),
                daemon=True,
            ).start()
        queued_ms = 0
        if kind == "wait":
            if stream:

                def queue_heartbeat() -> None:
                    if client_gone is not None and client_gone.is_set():
                        raise BrokenPipeError("client left while queued")
                    self._sse({"type": "status", "text": "排队中…"})

                got = GatewayHandler.queue.wait_turn(
                    ticket,
                    heartbeat=queue_heartbeat,
                    heartbeat_interval=float(self.cfg.get("queue_heartbeat_secs", 3.0)),
                )
                if not got:
                    if watch is not None:
                        watch["finished"] = True
                    self._log_request(
                        body, 499, started, {"answer": "(queued client left)", "sources": []}
                    )
                    return
            else:
                got = GatewayHandler.queue.wait_turn(
                    ticket, timeout=float(self.cfg.get("queue_wait_timeout", 30))
                )
                if not got:
                    payload = {"answer": QUEUE_TIMEOUT_ANSWER, "sources": []}
                    self._log_request(body, 503, started, payload)
                    self._respond(503, payload)
                    return
            queued_ms = int((time.time() - started) * 1000)
        attachment_tmp_dir: Path | None = None
        try:
            if request_is_runtime_error_question(body):
                unlocks, prepare_id = [], None
            else:
                unlocks, prepare_id = cached_or_resolved_unlocks(body, self.cfg)
            request_cfg = cfg_for_request(body, self.cfg)
            attachment_tmp_dir = materialize_image_attachments(body, request_cfg)
            prompt = build_prompt(body, unlocks, request_cfg)
            memory_events = memory_events_for_request(body, request_cfg)
            model_selection, model_warning = resolve_model_selection(body, self.cfg)
            model_selection, model_warning = enforce_agent_tool_model(body, unlocks, model_selection, model_warning)
            attachments = (body.get("context") or {}).get("attachments")
            if stream:
                trace = {
                    "request_received": started,
                    "queue_acquired": time.time(),
                    "queued_ms": queued_ms,
                }
                if model_warning:
                    self._sse({"type": "status", "text": model_warning})
                if model_selection and model_selection.get("protocol") == "openai-chat":
                    payload, status = self._stream_openai_chat(
                        prompt, watch, trace, model_selection, memory_events, request_cfg, attachments
                    )
                else:
                    payload, status = self._stream_claude(prompt, watch, trace, memory_events, model_selection, request_cfg)
                if model_warning:
                    payload["model_warning"] = model_warning
                if prepare_id:
                    payload["prepare_id"] = prepare_id
            else:
                if model_selection and model_selection.get("protocol") == "openai-chat":
                    answer = run_openai_chat(prompt, request_cfg, model_selection, attachments)
                else:
                    answer = run_claude(prompt, request_cfg, model_selection)
                payload = {"answer": answer, "sources": parse_sources(answer)}
                if prepare_id:
                    payload["prepare_id"] = prepare_id
                if model_warning:
                    payload["model_warning"] = model_warning
                if memory_events is not None:
                    payload["memory_events"] = memory_events
                status = 200
        except subprocess.TimeoutExpired:
            payload = {"answer": "模型响应超时，请重试或缩小问题范围。", "sources": []}
            status = 504
        except Exception as exc:  # noqa: BLE001 — surface as answer, never crash
            payload = {"answer": f"网关错误：{exc}", "sources": []}
            status = 500
        finally:
            if watch is not None:
                watch["finished"] = True
            GatewayHandler.queue.release()
            if attachment_tmp_dir is not None:
                shutil.rmtree(attachment_tmp_dir, ignore_errors=True)
        self._log_request(body, status, started, payload, queued_ms=queued_ms)
        if not stream:
            self._respond(status, payload)
        elif not getattr(self, "_sse_started", False):
            self._respond(status, payload)  # streaming failed before headers went out

    def _watch_client_disconnect(self, client_gone: threading.Event, watch: dict) -> None:
        """Block on recv until the client's FIN/RST arrives, then kill claude.

        The request body is already consumed and our clients never pipeline,
        so any recv result means the connection is gone (b"" on FIN, OSError
        on RST). Killing the child unblocks the stdout reader loop, which
        ends the request promptly and frees the queue slot."""
        try:
            while True:
                data = self.connection.recv(64)
                if not data:
                    break
        except OSError:
            pass
        client_gone.set()
        if watch.get("finished"):
            return  # request already over; never touch a later request's child
        proc = watch.get("proc")
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    def do_OPTIONS(self) -> None:  # noqa: N802 — CORS preflight for window.fetch streaming
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")

    def _start_sse(self) -> None:
        """Send SSE headers once; queued streams start before claude runs."""
        if getattr(self, "_sse_started", False):
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self._cors_headers()
        self.end_headers()
        self._sse_started = True

    def _stream_claude(
        self,
        prompt: str,
        watch: dict | None = None,
        trace: dict | None = None,
        memory_events: dict | None = None,
        model_selection: dict | None = None,
        request_cfg: dict | None = None,
    ) -> tuple[dict, int]:
        """SSE response: status/delta events while claude runs, done at end."""
        self._start_sse()

        def emit(etype: str, data: str) -> None:
            if trace is not None and "first_sse_emit" not in trace:
                trace["first_sse_emit"] = time.time()
            self._sse({"type": etype, "text": data})

        emit("status", "已接收请求，正在启动模型…")

        def on_spawn(proc) -> None:
            if watch is not None:
                watch["proc"] = proc

        try:
            answer = run_claude_stream(prompt, request_cfg or self.cfg, emit, on_spawn, trace, model_selection)
            payload = {"answer": answer, "sources": parse_sources(answer)}
            if memory_events is not None:
                payload["memory_events"] = memory_events
            if trace is not None and self.cfg.get("stream_trace_enabled"):
                trace["done_emit"] = time.time()
                payload["trace"] = trace
            self._sse({"type": "done", **payload})
            return payload, 200
        except subprocess.TimeoutExpired:
            payload = {"answer": "模型响应超时，请重试或缩小问题范围。", "sources": []}
            try:
                self._sse({"type": "error", "message": payload["answer"]})
            except OSError:
                return {"answer": "(client disconnected)", "sources": []}, 499
            return payload, 504
        except ClaudeStreamIncomplete as exc:
            payload = {"answer": f"网关错误：{exc}", "sources": [], "partial_answer": exc.partial}
            try:
                self._sse({"type": "error", "code": "incomplete_answer", "message": payload["answer"]})
            except OSError:
                return {"answer": "(client disconnected)", "sources": []}, 499
            return payload, 502
        except ConnectionError:  # BrokenPipeError/ConnectionResetError——客户端已走
            return {"answer": "(client disconnected)", "sources": []}, 499
        except Exception as exc:  # noqa: BLE001
            payload = {"answer": f"网关错误：{exc}", "sources": []}
            try:
                self._sse({"type": "error", "message": payload["answer"]})
            except OSError:
                # 连错误事件都写不出去——按客户端断开记账（如FIN监视已杀子进程的场景）
                return {"answer": "(client disconnected)", "sources": []}, 499
            return payload, 500

    def _stream_openai_chat(
        self,
        prompt: str,
        watch: dict | None,
        trace: dict | None,
        model_selection: dict,
        memory_events: dict | None = None,
        request_cfg: dict | None = None,
        attachments=None,
    ) -> tuple[dict, int]:
        self._start_sse()
        self._sse({"type": "status", "text": "已接收请求，正在调用chat-only模型…"})
        if trace is not None:
            trace["claude_spawned"] = time.time()
        cfg = request_cfg or self.cfg
        parts: list[str] = []

        def mark_first() -> None:
            if trace is not None and "first_sse_emit" not in trace:
                now = time.time()
                trace.setdefault("first_stdout_event", now)
                trace["first_sse_emit"] = now

        try:
            try:
                for piece in stream_openai_chat(prompt, cfg, model_selection, attachments):
                    if not piece:
                        continue
                    mark_first()
                    parts.append(piece)
                    self._sse({"type": "delta", "text": piece})
            except Exception:  # noqa: BLE001 — keep any partial text; fall back below only if empty
                pass
            if not parts:
                # Streaming produced nothing (early error or empty stream): one
                # blocking call so the user still always gets a complete answer.
                answer = run_openai_chat(prompt, cfg, model_selection, attachments)
                mark_first()
                parts.append(answer)
                self._sse({"type": "delta", "text": answer})
            answer = "".join(parts)
            if trace is not None:
                trace["process_exit"] = time.time()
            payload = {"answer": answer, "sources": parse_sources(answer)}
            if memory_events is not None:
                payload["memory_events"] = memory_events
            if trace is not None and self.cfg.get("stream_trace_enabled"):
                trace["done_emit"] = time.time()
                payload["trace"] = trace
            self._sse({"type": "done", **payload})
            return payload, 200
        except Exception as exc:  # noqa: BLE001
            payload = {"answer": f"网关错误：{exc}", "sources": []}
            try:
                self._sse({"type": "error", "message": payload["answer"]})
            except OSError:
                return {"answer": "(client disconnected)", "sources": []}, 499
            return payload, 500

    def _sse(self, obj: dict) -> None:
        self.wfile.write(f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode())
        self.wfile.flush()

    def _respond(self, status: int, payload, raw_text: bool = False) -> None:
        data = payload.encode() if raw_text else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain" if raw_text else "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def _respond_file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def _log_request(
        self, body: dict, status: int, started: float, payload: dict, queued_ms: int = 0
    ) -> None:
        try:
            log_dir = self.cfg["log_dir"]
            log_dir.mkdir(parents=True, exist_ok=True)
            active = ((body.get("context") or {}).get("active_file") or {}).get("path")
            dur_ms = int((time.time() - started) * 1000)
            request_cfg = cfg_for_request(body, self.cfg)
            entry = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "rev": GATEWAY_REVISION,
                "status": status,
                "dur_ms": dur_ms,
                "queued_ms": queued_ms,
                "note": active,
                "task_mode": (body.get("options") or {}).get("task_mode"),
                "message_chars": len(str(body.get("message") or "")),
                "answer_chars": len(str(payload.get("answer") or "")),
                "source_count": len(payload.get("sources") or []),
                "budget_profile": payload.get("budget_profile") or request_cfg.get("budget_profile"),
                "permission_mode": request_cfg.get("claude_permission_mode"),
                "approval_forwarding": request_cfg.get("approval_forwarding_enabled"),
                "gateway_access_mode": request_cfg.get("gateway_access_mode"),
            }
            if payload.get("trace"):
                entry["trace_ms"] = _trace_ms(payload.get("trace"), started)
            if status >= 400:
                answer = str(payload.get("answer") or "")
                if "模型响应超时" in answer:
                    entry["error_type"] = "timeout"
                elif "工具调用后没有返回最终回答" in answer or "incomplete" in answer.lower():
                    entry["error_type"] = "incomplete_answer"
                elif answer:
                    entry["error_type"] = "gateway_error"
            options = body.get("options") or {}
            for src_key, dst_key in (
                ("model_provider_id", "model_provider_id"),
                ("model_id", "model_id"),
                ("model_protocol", "model_protocol"),
            ):
                value = options.get(src_key)
                if isinstance(value, str) and value:
                    entry[dst_key] = value
            conversation_id = body.get("conversation_id")
            if isinstance(conversation_id, str) and conversation_id:
                entry["conversation_id"] = conversation_id
            with (log_dir / "requests.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._log_memory_async(body, payload, status, dur_ms)
        except OSError:
            pass

    def _log_memory_async(self, body: dict, payload: dict, status: int, dur_ms: int) -> None:
        if not (
            self.cfg.get("memory_capture_enabled")
            or self.cfg.get("memory_failed_query_log_enabled")
            or self.cfg.get("memory_access_log_enabled")
            or self.cfg.get("memory_candidate_write_enabled")
        ):
            return
        thread = threading.Thread(
            target=self._write_memory_logs,
            args=(body, payload, status, dur_ms),
            daemon=True,
        )
        thread.start()

    def _clip(self, value: str | None) -> str:
        limit = int(self.cfg.get("memory_capture_max_chars") or 800)
        text = (value or "").strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "..."

    def _failed_query_reason(self, payload: dict, status: int) -> str | None:
        if status == 504:
            return "timeout"
        if status >= 500:
            return "error"
        sources = payload.get("sources") or []
        answer = payload.get("answer") or ""
        if status == 200 and not sources:
            return "zero_sources"
        if status == 200 and ZERO_HIT.search(answer):
            return "zero_hit_text"
        return None

    def _write_memory_logs(self, body: dict, payload: dict, status: int, dur_ms: int) -> None:
        try:
            log_dir = self.cfg["log_dir"]
            log_dir.mkdir(parents=True, exist_ok=True)
            context = body.get("context") or {}
            active = (context.get("active_file") or {}).get("path")
            options = body.get("options") or {}
            sources = payload.get("sources") or []
            today = time.strftime("%Y-%m-%d")
            base = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "entry": body.get("entry") or "obsidian",
                "task_mode": options.get("task_mode"),
                "active_file": active,
                "status": status,
                "dur_ms": dur_ms,
                "source_count": len(sources),
            }
            conversation_id = body.get("conversation_id")
            if isinstance(conversation_id, str) and conversation_id:
                base["conversation_id"] = conversation_id  # M9可按会话聚合素材
            message_excerpt = self._clip(body.get("message"))
            if self.cfg.get("memory_capture_enabled"):
                material_dir = log_dir / "memory-session-materials"
                material_dir.mkdir(parents=True, exist_ok=True)
                material = {
                    **base,
                    "message_excerpt": message_excerpt,
                    "answer_excerpt": self._clip(payload.get("answer")),
                    "sources": sources[:8],
                }
                with (material_dir / f"{today}.jsonl").open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(material, ensure_ascii=False) + "\n")
            reason = self._failed_query_reason(payload, status)
            if reason and self.cfg.get("memory_failed_query_log_enabled"):
                failed = {
                    **base,
                    "reason": reason,
                    "query_excerpt": message_excerpt,
                }
                with (log_dir / "failed-queries.jsonl").open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(failed, ensure_ascii=False) + "\n")
            memory_events = payload.get("memory_events") if isinstance(payload, dict) else None
            refs = memory_events.get("referenced_cards") if isinstance(memory_events, dict) else None
            source_refs = memory_events.get("referenced_context_sources") if isinstance(memory_events, dict) else None
            if self.cfg.get("memory_access_log_enabled") and (
                (isinstance(refs, list) and refs) or (isinstance(source_refs, list) and source_refs)
            ):
                access = {
                    **base,
                    "referenced_cards": [item for item in refs or [] if isinstance(item, str)][:8],
                    "referenced_context_sources": [
                        {
                            "id": str(item.get("id") or ""),
                            "path": str(item.get("path") or ""),
                            "kind": str(item.get("kind") or ""),
                        }
                        for item in (source_refs or [])
                        if isinstance(item, dict)
                    ][:8],
                }
                with (log_dir / "memory-access.jsonl").open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(access, ensure_ascii=False) + "\n")
            candidate = _write_review_queue_candidate(body, self.cfg) if status < 500 else None
            if candidate and self.cfg.get("memory_access_log_enabled"):
                candidate_log = {
                    **base,
                    "memory_candidate": {
                        key: value
                        for key, value in candidate.items()
                        if key in {"ok", "action", "reason", "written", "path", "entry", "claim_chars"}
                    },
                }
                with (log_dir / "memory-candidates.jsonl").open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(candidate_log, ensure_ascii=False) + "\n")
        except OSError:
            return

    def log_message(self, *_args) -> None:  # quiet default stderr access log
        return


def main() -> None:
    cfg = load_config()
    GatewayHandler.cfg = cfg
    GatewayHandler.queue = RequestQueue(cfg["queue_max"])
    server = ThreadingHTTPServer((cfg["bind"], cfg["port"]), GatewayHandler)
    print(f"assistant-gateway listening on {cfg['bind']}:{cfg['port']} brain={cfg['brain_root']} rev={GATEWAY_REVISION}")
    server.serve_forever()


if __name__ == "__main__":
    main()
