# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Claude runner + model prewarm for the assistant gateway.

Carved out of gateway.py (P6, see docs/maintainability-standards.zh-CN.md §三).
Owns everything about invoking claude and parsing its output: command building,
blocking run, stream-json parsing (tool/approval/result events), the live-prewarm
subprocess pool, model prewarm, and the prepare/prewarm context keys. One-way deps
only — _common, gateway_prompt (budget + tool gating), models, providers; no
back-edge to the unlock/prepare-cache/handler orchestration that stays in
gateway.py, so importing this module never cycles. Behavior-invariant move.

Shared mutable prewarm state (_PREWARM_STATE/_LIVE_PREWARM_STATE and their locks)
lives here and is re-exported by gateway.py so existing call sites and tests that
poke gateway._PREWARM_STATE keep mutating the same objects.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from _common import (
    DEFAULT_PERMISSION_MODE,
    SOURCE_LINE,
    bool_option,
    full_access_enabled,
    request_is_runtime_error_question,
    sanitize_permission_mode,
)
from gateway_prompt import (
    allowed_tools,
    enforce_agent_tool_model,
    request_needs_investigation_budget,
    request_needs_web_budget,
)
from models import resolve_model_selection
from providers import run_openai_chat


class ClaudeStreamIncomplete(RuntimeError):
    """Raised when Claude stream-json ends before a usable final answer."""

    def __init__(self, message: str, *, partial: str = ""):
        super().__init__(message)
        self.partial = partial

_PREWARM_LOCK = threading.Lock()

_PREWARM_STATE: dict[str, object] = {
    "running_key": "",
    "items": {},
}

_LIVE_PREWARM_LOCK = threading.Lock()

_LIVE_PREWARM_STATE: dict[str, object] = {
    "items": {},
}

def prepare_context_key(body: dict) -> str:
    """Stable key for a prepared Obsidian location; excludes user message text."""
    context = body.get("context") or {}
    options = body.get("options") or {}
    active = context.get("active_file") or {}
    pdf = context.get("pdf") or {}
    selection = context.get("selection") or {}
    key = {
        "active_path": active.get("path") or "",
        "active_mtime": active.get("mtime") or 0,
        "pdf_page": pdf.get("page"),
        "selection_chars": selection.get("chars") or 0,
        "context_mode": options.get("context_mode") or context.get("requested_mode"),
        "target_module": options.get("target_module") or "",
        "target_folder": options.get("target_folder") or "",
        "model_provider_id": options.get("model_provider_id") or "",
        "model_id": options.get("model_id") or "",
        "model_protocol": options.get("model_protocol") or "",
        "permission_mode": options.get("permission_mode") or "",
    }
    return json.dumps(key, sort_keys=True, ensure_ascii=False)

def prewarm_context_key(body: dict, cfg: dict, model_selection: dict | None) -> str:
    """Stable key for model-side warmup; includes the effective route."""
    try:
        prepare_key = json.loads(prepare_context_key(body))
    except json.JSONDecodeError:
        prepare_key = {"raw": prepare_context_key(body)}
    if model_selection:
        route = {
            "provider_id": model_selection.get("provider_id") or "",
            "model_id": model_selection.get("model_id") or "",
            "protocol": model_selection.get("protocol") or "",
            "cli_model": model_selection.get("cli_model") or "",
        }
    else:
        route = {
            "provider_id": "gateway-default",
            "model_id": "",
            "protocol": "claude-wrapper/agent-tools",
            "cli_model": "",
        }
    key = {
        "prepare": prepare_key,
        "route": route,
        "claude_bin": cfg.get("claude_bin") or "",
        "permission_mode": cfg.get("claude_permission_mode") or DEFAULT_PERMISSION_MODE,
    }
    return json.dumps(key, sort_keys=True, ensure_ascii=False)

def _append_turn_limit(cmd: list[str], cfg: dict) -> None:
    """Append --max-turns only when an explicit positive cap is configured.

    Default is empty/unset → uncapped (bounded by claude_timeout). A cap is
    opt-in via CLAUDE_MAX_TURNS (global) or options.max_tool_turns (per-request
    plugin setting). Non-positive/blank values leave the run uncapped."""
    try:
        turns = int(str(cfg.get("claude_max_turns") or "").strip())
    except (TypeError, ValueError):
        return
    if turns > 0:
        cmd.extend(["--max-turns", str(turns)])

def _claude_cmd(prompt: str, cfg: dict, model_selection: dict | None = None, *, stream: bool = False) -> list[str]:
    cmd = [
        cfg["claude_bin"],
        "-p",
        prompt,
        "--permission-mode",
        sanitize_permission_mode(cfg.get("claude_permission_mode"), DEFAULT_PERMISSION_MODE),
        "--allowedTools",
        allowed_tools(cfg),
    ]
    _append_turn_limit(cmd, cfg)
    extra_tools = str(cfg.get("extra_allowed_tools") or "")
    # `--bare` cuts Claude Code startup overhead substantially for this gateway
    # route. Avoid it when the live config declares MCP tools, because those may
    # depend on user/project MCP discovery rather than built-in tools only.
    if bool(cfg.get("claude_bare", True)) and "mcp__" not in extra_tools:
        cmd.append("--bare")
    if bool(cfg.get("claude_no_session_persistence", True)):
        cmd.append("--no-session-persistence")
    if bool(cfg.get("claude_exclude_dynamic_sections", True)):
        cmd.append("--exclude-dynamic-system-prompt-sections")
    if model_selection and model_selection.get("protocol") in {"claude-wrapper/agent-tools", "anthropic-compatible"}:
        cli_model = str(model_selection.get("cli_model") or "").strip()
        if cli_model:
            cmd.extend(["--model", cli_model])
    if stream:
        cmd.extend(["--output-format", "stream-json", "--verbose", "--include-partial-messages"])
    return cmd

def _claude_live_cmd(cfg: dict, model_selection: dict | None = None) -> list[str]:
    cmd = [
        cfg["claude_bin"],
        "-p",
        "--permission-mode",
        sanitize_permission_mode(cfg.get("claude_permission_mode"), DEFAULT_PERMISSION_MODE),
        "--allowedTools",
        allowed_tools(cfg),
    ]
    _append_turn_limit(cmd, cfg)
    extra_tools = str(cfg.get("extra_allowed_tools") or "")
    if bool(cfg.get("claude_bare", True)) and "mcp__" not in extra_tools:
        cmd.append("--bare")
    if bool(cfg.get("claude_no_session_persistence", True)):
        cmd.append("--no-session-persistence")
    if bool(cfg.get("claude_exclude_dynamic_sections", True)):
        cmd.append("--exclude-dynamic-system-prompt-sections")
    if model_selection and model_selection.get("protocol") in {"claude-wrapper/agent-tools", "anthropic-compatible"}:
        cli_model = str(model_selection.get("cli_model") or "").strip()
        if cli_model:
            cmd.extend(["--model", cli_model])
    cmd.extend(
        [
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--permission-prompt-tool",
            "stdio",
            "--replay-user-messages",
        ]
    )
    return cmd

def _claude_live_input(prompt: str) -> str:
    return json.dumps(
        {
            "type": "user",
            "session_id": "",
            "parent_tool_use_id": None,
            "message": {"role": "user", "content": prompt},
        },
        ensure_ascii=False,
    ) + "\n"

def _live_prewarm_key(cfg: dict, model_selection: dict | None = None) -> str:
    payload = {
        "cmd": _claude_live_cmd(cfg, model_selection),
        "cwd": str(cfg.get("brain_root") or ""),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)

def cfg_for_request(body: dict, cfg: dict) -> dict:
    """Per-request knobs. Tool turns are uncapped; the profile only selects the
    per-request timeout (longer for investigation/web) and a tracing label.
    Turn count is bounded by claude_timeout, not a hard ceiling."""
    options = body.get("options") or {}
    task_mode = (options.get("task_mode") or "ask")
    next_cfg = dict(cfg)
    next_cfg["claude_permission_mode"] = sanitize_permission_mode(
        options.get("permission_mode"),
        str(cfg.get("claude_permission_mode") or DEFAULT_PERMISSION_MODE),
    )
    if full_access_enabled(next_cfg):
        next_cfg["claude_permission_mode"] = "bypassPermissions"
    next_cfg["approval_forwarding_enabled"] = bool_option(
        options.get("approval_forwarding"),
        bool(cfg.get("approval_forwarding_enabled", True)),
    )
    # Optional per-request tool-turn cap (plugin setting). Default unset/0 = uncapped.
    if str(options.get("max_tool_turns") or "").strip() not in {"", "0"}:
        next_cfg["claude_max_turns"] = str(options.get("max_tool_turns")).strip()
    if request_is_runtime_error_question(body):
        next_cfg["claude_timeout"] = max(
            int(cfg.get("claude_timeout", 110)),
            int(cfg.get("claude_runtime_diag_timeout", cfg.get("claude_timeout", 110))),
        )
        next_cfg["budget_profile"] = "runtime-diagnosis"
    elif request_needs_investigation_budget(body):
        next_cfg["claude_timeout"] = max(
            int(cfg.get("claude_timeout", 110)),
            int(cfg.get("claude_investigation_timeout", cfg.get("claude_timeout", 110))),
        )
        next_cfg["budget_profile"] = "investigation"
    elif request_needs_web_budget(body):
        next_cfg["claude_timeout"] = max(
            int(cfg.get("claude_timeout", 110)),
            int(cfg.get("claude_web_timeout", cfg.get("claude_timeout", 110))),
        )
        next_cfg["budget_profile"] = "web"
    elif task_mode in {"related", "citation-review"}:
        next_cfg["budget_profile"] = "deep"
    else:
        next_cfg["budget_profile"] = "fast"
    return next_cfg

def run_claude(prompt: str, cfg: dict, model_selection: dict | None = None) -> str:
    cmd = _claude_cmd(prompt, cfg, model_selection)
    proc = subprocess.run(
        cmd,
        cwd=str(cfg["brain_root"]),
        capture_output=True,
        text=True,
        timeout=cfg["claude_timeout"],
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-400:]
        raise RuntimeError(f"claude exited {proc.returncode}: {detail}")
    return proc.stdout.strip()

def _prewarm_model_fields(model_selection: dict | None) -> dict[str, str]:
    if model_selection:
        return {
            "model_provider_id": str(model_selection.get("provider_id") or ""),
            "model_id": str(model_selection.get("model_id") or ""),
            "model_protocol": str(model_selection.get("protocol") or ""),
        }
    return {
        "model_provider_id": "gateway-default",
        "model_id": "",
        "model_protocol": "claude-wrapper/agent-tools",
    }

def _close_live_prewarm_item(item: dict, reason: str = "close") -> None:
    proc = item.get("proc")
    if not isinstance(proc, subprocess.Popen):
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
    item["closed_reason"] = reason
    item["closed_at"] = time.time()

def _live_prewarm_item_alive(item: dict) -> bool:
    proc = item.get("proc")
    return isinstance(proc, subprocess.Popen) and proc.poll() is None and bool(item.get("stdin_open", True))

def _cleanup_live_prewarm_items(cfg: dict) -> None:
    ttl = max(1, int(cfg.get("live_prewarm_idle_seconds", cfg.get("prewarm_ttl_seconds", 240))))
    now = time.time()
    items = _LIVE_PREWARM_STATE.setdefault("items", {})
    if not isinstance(items, dict):
        _LIVE_PREWARM_STATE["items"] = {}
        return
    for key, item in list(items.items()):
        if not isinstance(item, dict):
            items.pop(key, None)
            continue
        if not _live_prewarm_item_alive(item) or now - float(item.get("created_at") or 0.0) > ttl:
            _close_live_prewarm_item(item, "stale")
            items.pop(key, None)

def start_live_prewarm_process(cfg: dict, model_selection: dict | None) -> dict:
    started = time.time()
    key = _live_prewarm_key(cfg, model_selection)
    fields = _prewarm_model_fields(model_selection)
    with _LIVE_PREWARM_LOCK:
        _cleanup_live_prewarm_items(cfg)
        items = _LIVE_PREWARM_STATE.setdefault("items", {})
        if not isinstance(items, dict):
            items = {}
            _LIVE_PREWARM_STATE["items"] = items
        existing = items.get(key)
        if isinstance(existing, dict) and _live_prewarm_item_alive(existing):
            return {"status": "skipped", "reason": "live_warm", **fields}
        try:
            proc = subprocess.Popen(
                _claude_live_cmd(cfg, model_selection),
                cwd=str(cfg["brain_root"]),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except Exception as exc:  # noqa: BLE001 - prewarm must not block prepare.
            return {"status": "skipped", "reason": f"live_start_failed:{type(exc).__name__}", **fields}
        items[key] = {
            "proc": proc,
            "created_at": time.time(),
            "model_provider_id": fields["model_provider_id"],
            "model_id": fields["model_id"],
            "model_protocol": fields["model_protocol"],
            "stdin_open": True,
        }
    log_prewarm_event(cfg, key, "live_idle", int((time.time() - started) * 1000), model_selection)
    return {"status": "started", "reason": "live_idle", **fields}

def claim_live_prewarm_process(cfg: dict, model_selection: dict | None) -> dict | None:
    if not bool(cfg.get("live_prewarm_enabled", False)):
        return None
    if model_selection and model_selection.get("protocol") == "openai-chat":
        return None
    key = _live_prewarm_key(cfg, model_selection)
    with _LIVE_PREWARM_LOCK:
        _cleanup_live_prewarm_items(cfg)
        items = _LIVE_PREWARM_STATE.get("items")
        if not isinstance(items, dict):
            return None
        item = items.pop(key, None)
        if not isinstance(item, dict) or not _live_prewarm_item_alive(item):
            if isinstance(item, dict):
                _close_live_prewarm_item(item, "dead")
            return None
        item["claimed_at"] = time.time()
        return item

def replenish_live_prewarm_process(cfg: dict, model_selection: dict | None) -> None:
    if not bool(cfg.get("live_prewarm_enabled", False)):
        return
    if model_selection and model_selection.get("protocol") == "openai-chat":
        return

    def worker() -> None:
        start_live_prewarm_process(cfg, model_selection)

    thread = threading.Thread(target=worker, name="rtime-gateway-live-prewarm", daemon=True)
    try:
        thread.start()
    except RuntimeError:
        pass

def start_model_prewarm(body: dict, cfg: dict, unlocks: list[tuple[str, Path]]) -> dict:
    options = body.get("options") if isinstance(body.get("options"), dict) else {}
    if not bool_option(options.get("prewarm_model"), False):
        return {"status": "not_requested", "reason": "request_disabled"}
    if not bool(cfg.get("prewarm_enabled", True)):
        return {"status": "skipped", "reason": "config_disabled"}

    model_selection, model_warning = resolve_model_selection(body, cfg)
    model_selection, model_warning = enforce_agent_tool_model(body, unlocks, model_selection, model_warning)
    if bool(cfg.get("live_prewarm_enabled", False)) and not (
        model_selection and model_selection.get("protocol") == "openai-chat"
    ):
        return start_live_prewarm_process(cfg, model_selection)

    prewarm_cfg = dict(cfg)
    prewarm_cfg["claude_timeout"] = max(1, int(cfg.get("prewarm_timeout", 30)))
    prewarm_cfg["budget_profile"] = "prewarm"
    key = prewarm_context_key(body, prewarm_cfg, model_selection)
    fields = _prewarm_model_fields(model_selection)
    ttl = max(1, int(cfg.get("prewarm_ttl_seconds", 240)))
    now = time.time()

    with _PREWARM_LOCK:
        items = _PREWARM_STATE.setdefault("items", {})
        if not isinstance(items, dict):
            items = {}
            _PREWARM_STATE["items"] = items
        for stale_key, stale in list(items.items()):
            if not isinstance(stale, dict) or stale.get("running"):
                continue
            if now - float(stale.get("finished_at") or 0.0) > ttl * 2:
                items.pop(stale_key, None)
        running_key = str(_PREWARM_STATE.get("running_key") or "")
        if running_key:
            reason = "inflight" if running_key == key else "busy"
            return {"status": "skipped", "reason": reason, **fields}
        item = items.get(key) if isinstance(items.get(key), dict) else {}
        if (
            item.get("status") == "ok"
            and now - float(item.get("finished_at") or 0.0) < ttl
        ):
            return {"status": "skipped", "reason": "warm", **fields}
        items[key] = {
            "running": True,
            "started_at": now,
            "finished_at": 0.0,
            "status": "running",
            "model_provider_id": fields["model_provider_id"],
            "model_id": fields["model_id"],
            "model_protocol": fields["model_protocol"],
        }
        _PREWARM_STATE["running_key"] = key

    thread = threading.Thread(
        target=run_model_prewarm,
        args=(key, prewarm_cfg, model_selection),
        name="rtime-gateway-prewarm",
        daemon=True,
    )
    try:
        thread.start()
    except RuntimeError:
        with _PREWARM_LOCK:
            items = _PREWARM_STATE.get("items")
            if isinstance(items, dict) and isinstance(items.get(key), dict):
                items[key].update(running=False, finished_at=time.time(), status="error")
            if _PREWARM_STATE.get("running_key") == key:
                _PREWARM_STATE["running_key"] = ""
        return {"status": "skipped", "reason": "thread_start_failed", **fields}
    return {"status": "started", "reason": "queued", **fields}

def run_model_prewarm(key: str, cfg: dict, model_selection: dict | None) -> None:
    started = time.time()
    result = "ok"
    error_type = ""
    try:
        prompt = "预热 rtime 助手模型连接。请只回复 OK，不要调用工具。"
        if model_selection and model_selection.get("protocol") == "openai-chat":
            run_openai_chat(prompt, cfg, model_selection, attachments=None)
        else:
            run_claude(prompt, cfg, model_selection)
    except subprocess.TimeoutExpired:
        result = "timeout"
        error_type = "TimeoutExpired"
    except Exception as exc:  # noqa: BLE001 - prewarm must not break prepare/chat.
        result = "error"
        error_type = type(exc).__name__
    finally:
        dur_ms = int((time.time() - started) * 1000)
        with _PREWARM_LOCK:
            items = _PREWARM_STATE.setdefault("items", {})
            if isinstance(items, dict):
                item = items.get(key) if isinstance(items.get(key), dict) else {}
                item.update(running=False, finished_at=time.time(), status=result)
                items[key] = item
            if _PREWARM_STATE.get("running_key") == key:
                _PREWARM_STATE["running_key"] = ""
        log_prewarm_event(cfg, key, result, dur_ms, model_selection, error_type)

def log_prewarm_event(
    cfg: dict,
    key: str,
    result: str,
    dur_ms: int,
    model_selection: dict | None,
    error_type: str = "",
) -> None:
    try:
        log_dir = Path(cfg.get("log_dir") or "")
        log_dir.mkdir(parents=True, exist_ok=True)
        status = 200 if result in {"ok", "live_idle"} else (504 if result == "timeout" else 500)
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "endpoint": "prewarm",
            "status": status,
            "prewarm_status": result,
            "dur_ms": dur_ms,
            "prewarm_key_hash": hashlib.sha256(key.encode("utf-8")).hexdigest()[:16],
            "budget_profile": cfg.get("budget_profile"),
            "permission_mode": cfg.get("claude_permission_mode"),
            **_prewarm_model_fields(model_selection),
        }
        if error_type:
            entry["error_type"] = error_type
        with (log_dir / "requests.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass

def _trace_ms(trace: dict | None, started: float) -> dict[str, int]:
    if not isinstance(trace, dict):
        return {}
    origin = trace.get("request_received")
    try:
        origin_f = float(origin)
    except (TypeError, ValueError):
        origin_f = started
    result: dict[str, int] = {}
    for key in (
        "queue_acquired",
        "first_sse_emit",
        "claude_spawned",
        "live_prewarm_claimed",
        "first_stdout_event",
        "process_exit",
        "done_emit",
    ):
        value = trace.get(key)
        try:
            value_f = float(value)
        except (TypeError, ValueError):
            continue
        result[f"{key}_ms"] = max(0, int((value_f - origin_f) * 1000))
    queued = trace.get("queued_ms")
    if isinstance(queued, (int, float)):
        result["queued_ms"] = int(queued)
    live_age = trace.get("live_prewarm_age_ms")
    if isinstance(live_age, (int, float)):
        result["live_prewarm_age_ms"] = int(live_age)
    return result

def _approval_request_text(obj: dict) -> str | None:
    """Best-effort bridge for Claude-style permission/approval request events.

    CLI event shapes vary by runner/version. Keep this conservative: only
    forward request-looking permission/approval events, and summarize structured
    fields instead of dumping whole raw objects.
    """
    candidates = [obj]
    event = obj.get("event")
    if isinstance(event, dict):
        candidates.append(event)
    for item in candidates:
        kind = str(item.get("type") or item.get("subtype") or item.get("event_type") or "").lower()
        if not (("approval" in kind or "permission" in kind) and ("request" in kind or "ask" in kind or "prompt" in kind)):
            continue
        tool = str(item.get("tool_name") or item.get("tool") or item.get("name") or "").strip()
        message = str(item.get("message") or item.get("text") or item.get("prompt") or item.get("description") or "").strip()
        command = str(item.get("command") or item.get("input") or "").strip()
        parts = ["需要批准"]
        if tool:
            parts.append(f"工具：{tool}")
        if command and len(command) <= 160:
            parts.append(f"请求：{command}")
        if message and len(message) <= 220:
            parts.append(message)
        if len(parts) == 1:
            return "需要批准：模型请求继续执行受限操作。"
        return "；".join(parts)
    return None

def _status_detail_text(value, max_chars: int = 120) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace(str(Path.home()), "~")
    text = re.sub(r"\s+", " ", text)
    lowered = text.lower()
    if any(marker in lowered for marker in ("authorization", "bearer ", "api_key", "apikey", "password", "token=", "secret")):
        return ""
    if len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "…"
    return text

def _tool_input_detail(tool_input) -> str:
    if not isinstance(tool_input, dict):
        return _status_detail_text(tool_input)
    sensitive_keys = {"authorization", "cookie", "password", "secret", "token", "api_key", "apikey", "key"}
    for key in ("query", "url", "pattern", "path", "file_path", "command", "cmd"):
        if key not in tool_input:
            continue
        if key.lower() in sensitive_keys:
            continue
        detail = _status_detail_text(tool_input.get(key))
        if detail:
            return detail
    for key, value in tool_input.items():
        if str(key).lower() in sensitive_keys:
            continue
        detail = _status_detail_text(value)
        if detail:
            return detail
    return ""

def _parse_tool_input_fragments(fragments: list[str]):
    if not fragments:
        return None
    raw = "".join(fragments).strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw

def _tool_status_text(name, tool_input=None) -> str:
    tool_name = str(name or "工具").strip() or "工具"
    lowered = tool_name.lower()
    detail = _tool_input_detail(tool_input)
    action = f"正在使用{tool_name}"
    if lowered == "websearch":
        action = "正在搜索网页"
    elif lowered == "webfetch":
        action = "正在读取网页"
    elif lowered == "read":
        action = "正在读取资料"
    elif lowered == "glob":
        action = "正在扫描文件"
    elif lowered == "grep":
        action = "正在检索资料"
    elif lowered == "bash" and "rtime-web-fetch" in detail:
        action = "正在搜索网页" if " search " in f" {detail} " else "正在读取网页"
    elif "browser" in lowered or "playwright" in lowered or "chrome" in lowered:
        action = "正在控制浏览器"
    if detail:
        return f"{action}：{detail}…"
    return f"{action}…"

def _log_result_anomaly(result_obj: dict) -> None:
    """Record a non-fatal claude result anomaly to stderr (→ systemd journal).

    Fires when a result frame carries is_error=true but its subtype is NOT a
    terminal error (typically subtype="success" with a transient MCP/hook error
    mid-run). We keep the answer; this line makes the event diagnosable without
    relying on the subtype leaking into the user-facing message string."""
    try:
        print(
            "gateway: claude result is_error=true on non-error subtype "
            f"(subtype={result_obj.get('subtype')!r}, "
            f"api_error_status={result_obj.get('api_error_status')!r}); answer kept",
            file=sys.stderr,
            flush=True,
        )
    except Exception:  # noqa: BLE001 — diagnostics must never break the stream
        pass

def iter_stream_events(lines, deadline: float):
    """Parse claude --output-format stream-json lines into gateway events.

    Yields ("delta", text) | ("status", text) | ("final", full_answer).
    Handles both token-level shapes (--include-partial-messages wraps
    Anthropic events as {"type":"stream_event","event":{...}}) and
    whole-message shapes ({"type":"assistant","message":{...}}) so the CLI
    version on orangepi can vary without breaking the plugin."""
    collected: list[str] = []
    text_after_tool: list[str] = []
    saw_partial = False
    saw_thinking_status = False
    saw_tool_use = False
    final: str | None = None
    result_error: str | None = None
    tool_blocks: dict[int, dict] = {}
    last_status_text: str | None = None
    for line in lines:
        if time.time() > deadline:
            raise subprocess.TimeoutExpired("claude", 0)
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        approval_text = _approval_request_text(obj)
        if approval_text:
            yield ("approval_request", approval_text)
            continue
        kind = obj.get("type")
        if kind == "stream_event":
            event = obj.get("event") or {}
            etype = event.get("type")
            if etype == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    saw_partial = True
                    collected.append(delta["text"])
                    if saw_tool_use:
                        text_after_tool.append(delta["text"])
                    yield ("delta", delta["text"])
                elif delta.get("type") == "input_json_delta":
                    index = int(event.get("index") or 0)
                    block_state = tool_blocks.get(index)
                    if block_state is not None:
                        block_state["fragments"].append(str(delta.get("partial_json") or ""))
                elif "thinking" in str(delta.get("type") or "") and not saw_thinking_status:
                    saw_thinking_status = True
                    if last_status_text != "思考中…":
                        last_status_text = "思考中…"
                        yield ("status", "思考中…")
            elif etype == "content_block_start":
                block = event.get("content_block") or {}
                index = int(event.get("index") or 0)
                if block.get("type") == "tool_use":
                    saw_tool_use = True
                    tool_blocks[index] = {
                        "name": block.get("name") or "工具",
                        "fragments": [],
                        "last_status": None,
                    }
                    status = _tool_status_text(block.get("name"), block.get("input"))
                    tool_blocks[index]["last_status"] = status
                    if status != last_status_text:
                        last_status_text = status
                        yield ("status", status)
                elif "thinking" in str(block.get("type") or "") and not saw_thinking_status:
                    saw_thinking_status = True
                    if last_status_text != "思考中…":
                        last_status_text = "思考中…"
                        yield ("status", "思考中…")
            elif etype == "content_block_stop":
                index = int(event.get("index") or 0)
                block_state = tool_blocks.get(index)
                if block_state is not None:
                    tool_input = _parse_tool_input_fragments(block_state.get("fragments") or [])
                    status = _tool_status_text(block_state.get("name"), tool_input)
                    if status != block_state.get("last_status"):
                        block_state["last_status"] = status
                        if status != last_status_text:
                            last_status_text = status
                            yield ("status", status)
        elif kind == "assistant":
            for block in ((obj.get("message") or {}).get("content") or []):
                if block.get("type") == "text" and block.get("text"):
                    if not saw_partial:
                        collected.append(block["text"])
                        if saw_tool_use:
                            text_after_tool.append(block["text"])
                        yield ("delta", block["text"])
                elif block.get("type") == "tool_use":
                    saw_tool_use = True
                    status = _tool_status_text(block.get("name"), block.get("input"))
                    if status != last_status_text:
                        last_status_text = status
                        yield ("status", status)
        elif kind == "result":
            final = obj.get("result") if isinstance(obj.get("result"), str) else None
            subtype = str(obj.get("subtype") or "")
            is_error = obj.get("is_error") is True
            # `subtype` is the authoritative terminal status. The Claude CLI can
            # report is_error=true on an otherwise-successful run (e.g. a transient
            # brain-MCP / hook error mid-stream) while subtype stays "success".
            # Treating that as fatal discarded a complete answer and surfaced the
            # nonsensical "模型流以非成功状态结束：success". Only the error_* subtypes
            # (or is_error with no subtype at all) are genuine terminal failures.
            if subtype.startswith("error"):
                result_error = subtype
            elif is_error and not subtype:
                result_error = "error"
            elif is_error:
                _log_result_anomaly(obj)
            break
    partial = "".join(collected).strip()
    final_text = (final or "").strip()
    if result_error:
        raise ClaudeStreamIncomplete(
            f"模型流以非成功状态结束：{result_error}。请重试，或缩小问题范围。",
            partial=final_text or partial,
        )
    if saw_tool_use and not "".join(text_after_tool).strip():
        if not final_text or final_text == partial:
            raise ClaudeStreamIncomplete(
                "模型在工具调用后没有返回最终回答；已阻止把中间计划误当成完整答案。",
                partial=final_text or partial,
            )
    yield ("final", (final_text or partial).strip())

def run_claude_stream(
    prompt: str,
    cfg: dict,
    emit,
    on_spawn=None,
    trace: dict | None = None,
    model_selection: dict | None = None,
) -> str:
    """Spawn claude in stream-json mode; forward events via emit(type, data).

    Returns the full answer text. emit raising (client gone) kills claude.
    on_spawn(proc) lets the caller's disconnect watcher kill claude directly —
    write-failure alone detects departure only at the next emit, which can lag
    by many seconds during tool use or bursty generation."""
    live_item = claim_live_prewarm_process(cfg, model_selection)
    live_used = live_item is not None
    if live_item:
        proc = live_item["proc"]
    else:
        cmd = _claude_cmd(prompt, cfg, model_selection, stream=True)
        proc = subprocess.Popen(
            cmd, cwd=str(cfg["brain_root"]),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    if on_spawn is not None:
        on_spawn(proc)
    if trace is not None:
        trace["claude_spawned"] = time.time()
        if live_used:
            trace["live_prewarm_claimed"] = trace["claude_spawned"]
            created_at = live_item.get("created_at") if isinstance(live_item, dict) else None
            if isinstance(created_at, (int, float)):
                trace["live_prewarm_age_ms"] = int((trace["claude_spawned"] - created_at) * 1000)
    deadline = time.time() + cfg["claude_timeout"]
    answer = ""
    forward_approvals = bool(cfg.get("approval_forwarding_enabled", True))
    try:
        if live_used:
            if proc.stdin is None:
                raise RuntimeError("live claude stdin is unavailable")
            proc.stdin.write(_claude_live_input(prompt))
            proc.stdin.flush()
        for etype, data in iter_stream_events(proc.stdout, deadline):
            if etype == "final":
                answer = data
            elif etype == "approval_request" and not forward_approvals:
                continue
            else:
                if trace is not None and "first_stdout_event" not in trace:
                    trace["first_stdout_event"] = time.time()
                emit(etype, data)
        if live_used:
            if trace is not None:
                trace["process_exit"] = time.time()
            if proc.returncode not in (0, None) and not answer:
                detail = (proc.stderr.read() or "")[-400:]
                raise RuntimeError(f"claude exited {proc.returncode}: {detail}")
        else:
            proc.wait(timeout=10)
            if trace is not None:
                trace["process_exit"] = time.time()
            if proc.returncode not in (0, None) and not answer:
                detail = (proc.stderr.read() or "")[-400:]
                raise RuntimeError(f"claude exited {proc.returncode}: {detail}")
        return answer
    finally:
        # Kill AND reap on every path — an unreaped child stays as a zombie
        # under the long-lived gateway process (seen in run-10 acceptance).
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        try:
            proc.wait(timeout=2)
        except (subprocess.TimeoutExpired, OSError):
            pass
        if live_used:
            replenish_live_prewarm_process(cfg, model_selection)

def parse_sources(answer: str) -> list[dict]:
    """Best-effort parse of the trailing '来源：' block into plugin sources."""
    sources: list[dict] = []
    in_block = False
    for line in answer.splitlines():
        stripped = line.strip()
        if not in_block:
            if stripped.rstrip(":：") == "来源" or stripped.startswith(("来源：", "来源:")):
                in_block = True
            continue
        if not stripped:
            continue
        match = SOURCE_LINE.match(stripped.strip("`"))
        if not match:
            continue
        source: dict = {"path": match.group(1).strip("`")}
        if match.group(2):
            source["page"] = int(match.group(2))
        sources.append(source)
    return sources[:12]
