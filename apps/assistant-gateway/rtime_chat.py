#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""rtime-chat：assistant-gateway的命令行对话客户端（agent测试接口）。

与Obsidian插件走同一contract（AssistantRequestBody schema_version=1），
让任何agent（Claude Code/Codex/kimi/qwen/deepseek）或用户在终端直接与
助手网关对话：支持笔记/PDF上下文、SSE流式、JSON输出（脚本断言用）。

stdlib零依赖。示例：
  python3 rtime_chat.py --health
  python3 rtime_chat.py "固体物理的能带怎么理解"
  python3 rtime_chat.py --pdf lesson2-main.pdf --page 5 --stream "这页讲什么"
  python3 rtime_chat.py --note "/path/to/笔记.md" --task summarize --json
  python3 rtime_chat.py --conversation conv-1 --history-file 历史.json "它的低温极限呢"
（历史.json格式：[{"role":"user","content":"…"},{"role":"assistant","content":"…"}]）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_ENDPOINT = os.environ.get("RTIME_GATEWAY_URL", "http://127.0.0.1:8765")
MAX_NOTE_CHARS = 20000
TASK_MODES = ("ask", "summarize", "explain", "related", "citation-review")

# 网关直连时不走系统HTTP代理（Mac常见的本地代理会把 Tailnet/CGNAT 地址也代理出去导致502）。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def build_body(
    message: str | None,
    *,
    note_path: str | None = None,
    note_text: str | None = None,
    pdf: str | None = None,
    page: int | None = None,
    selection: str | None = None,
    task_mode: str = "ask",
    stream: bool = False,
    max_note_chars: int = MAX_NOTE_CHARS,
    conversation_id: str | None = None,
    history: list | None = None,
) -> dict:
    """组装AssistantRequestBody（与插件同构）。纯函数，便于单测。"""
    context: dict = {}
    if pdf:
        context["active_file"] = {"path": pdf}
        if page:
            context["pdf"] = {"page": int(page)}
    elif note_path:
        context["active_file"] = {"path": str(note_path)}
    if note_text is not None:
        context["note"] = {
            "text": note_text[:max_note_chars],
            "truncated": len(note_text) > max_note_chars,
        }
    if selection:
        context["selection"] = {"text": selection}
    if history:
        context["history"] = clean_history(history)
    body = {
        "schema_version": 1,
        "entry": "rtime-chat",
        "message": message or "",
        "context": context,
        "options": {"task_mode": task_mode},
    }
    if conversation_id:
        body["conversation_id"] = conversation_id
    if stream:
        body["stream"] = True
    return body


def clean_history(history: list) -> list[dict]:
    """history白名单校验：仅保留{role: user|assistant, content: 非空str}。"""
    cleaned: list[dict] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            cleaned.append({"role": role, "content": content})
    return cleaned


def iter_sse_events(fp):
    """解析SSE字节流：空行结束一帧，data:行的JSON载荷逐帧yield。

    兼容结尾缺空行的最后一帧；注释行与非JSON载荷跳过不抛错。"""

    def flush(lines: list[str]):
        if not lines:
            return
        try:
            yield json.loads("\n".join(lines))
        except json.JSONDecodeError:
            return

    data_lines: list[str] = []
    for raw in fp:
        line = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else raw
        line = line.rstrip("\r\n")
        if not line:
            yield from flush(data_lines)
            data_lines = []
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    yield from flush(data_lines)


def render_final(payload: dict) -> str:
    answer = (payload.get("answer") or "").rstrip()
    lines = [answer] if answer else []
    sources = payload.get("sources") or []
    if sources:
        lines.extend(["", "来源："])
        for src in sources:
            path = src.get("path", "")
            page = src.get("page")
            lines.append(f"- {path}#page={page}" if page else f"- {path}")
    return "\n".join(lines)


def http_post_chat(endpoint: str, body: dict, timeout: float):
    url = endpoint.rstrip("/") + "/api/obsidian/chat"
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if body.get("stream"):
        headers["Accept"] = "text/event-stream"
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    return _OPENER.open(req, timeout=timeout)  # noqa: S310 — Tailscale内网


def consume_stream(resp, as_json: bool) -> int:
    done: dict | None = None
    error: str | None = None
    for event in iter_sse_events(resp):
        etype = event.get("type")
        if etype == "delta":
            if not as_json:
                sys.stdout.write(event.get("text") or "")
                sys.stdout.flush()
        elif etype == "status":
            print(f"[{event.get('text')}]", file=sys.stderr)
        elif etype == "done":
            done = event
        elif etype == "error":
            error = event.get("message") or "未知错误"
    if not as_json and done is not None:
        sys.stdout.write("\n")
    if error is not None:
        print(f"网关错误：{error}", file=sys.stderr)
        return 1
    if done is None:
        print("流提前结束，未收到done事件。", file=sys.stderr)
        return 1
    if as_json:
        payload = {k: v for k, v in done.items() if k != "type"}
        print(json.dumps(payload, ensure_ascii=False))
    return 0


def handle_http_error(exc: urllib.error.HTTPError, as_json: bool) -> int:
    try:
        payload = json.loads(exc.read().decode("utf-8"))
    except (ValueError, OSError):
        payload = {"answer": f"HTTP {exc.code}", "sources": []}
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"HTTP {exc.code}：{payload.get('answer', '')}", file=sys.stderr)
    return 1


def run_health(endpoint: str, timeout: float) -> int:
    url = endpoint.rstrip("/") + "/healthz"
    try:
        with _OPENER.open(url, timeout=min(timeout, 10)) as resp:  # noqa: S310
            text = resp.read().decode("utf-8", "replace").strip()
    except (urllib.error.URLError, OSError) as exc:
        print(f"healthz失败：{exc}", file=sys.stderr)
        return 1
    print(text)
    return 0 if text == "ok" else 1


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rtime-chat",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("message", nargs="?", help="发给助手的内容；可与--note/--pdf组合")
    parser.add_argument(
        "--endpoint",
        default=DEFAULT_ENDPOINT,
        help=f"网关地址（默认{DEFAULT_ENDPOINT}，环境变量RTIME_GATEWAY_URL覆盖）",
    )
    parser.add_argument("--note", help="本地md笔记路径：读取其内容作为当前笔记上下文")
    parser.add_argument("--pdf", help="PDF路径或文件名：模拟正在阅读该PDF（经manifest解锁正本）")
    parser.add_argument("--page", type=int, help="配合--pdf：当前页码")
    parser.add_argument("--selection", help="选中文本上下文")
    parser.add_argument("--conversation", help="会话id：网关日志与记忆素材按它聚合")
    parser.add_argument(
        "--history-file",
        help='历史JSON文件：[{"role":"user|assistant","content":"…"}]，作为续聊上下文',
    )
    parser.add_argument("--task", choices=TASK_MODES, default="ask", dest="task_mode")
    parser.add_argument("--stream", action="store_true", help="SSE流式输出（status进stderr）")
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="输出最终{'answer','sources'}单行JSON，供脚本断言",
    )
    parser.add_argument("--timeout", type=float, default=130.0, help="秒，默认130")
    parser.add_argument("--health", action="store_true", help="只查/healthz")
    return parser


def main(argv=None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    if args.health:
        return run_health(args.endpoint, args.timeout)
    if not (args.message or args.note or args.pdf or args.selection):
        parser.error("需要message或--note/--pdf/--selection之一（或--health）")
    note_text = None
    if args.note:
        try:
            note_text = Path(args.note).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"读不到笔记：{exc}", file=sys.stderr)
            return 2
    history = None
    if args.history_file:
        try:
            history = json.loads(Path(args.history_file).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"读不到历史文件：{exc}", file=sys.stderr)
            return 2
        if not isinstance(history, list):
            print("历史文件必须是JSON数组：[{role, content}, …]", file=sys.stderr)
            return 2
    body = build_body(
        args.message,
        note_path=args.note,
        note_text=note_text,
        pdf=args.pdf,
        page=args.page,
        selection=args.selection,
        task_mode=args.task_mode,
        stream=args.stream,
        conversation_id=args.conversation,
        history=history,
    )
    try:
        resp = http_post_chat(args.endpoint, body, args.timeout)
    except urllib.error.HTTPError as exc:
        return handle_http_error(exc, args.as_json)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        print(f"网关连不上：{exc}", file=sys.stderr)
        return 1
    with resp:
        if args.stream:
            return consume_stream(resp, args.as_json)
        payload = json.loads(resp.read().decode("utf-8"))
    print(json.dumps(payload, ensure_ascii=False) if args.as_json else render_final(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
