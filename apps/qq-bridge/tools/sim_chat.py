#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""End-to-end message simulator for the QQ bridge — a reusable 检测节点 (test checkpoint).

Connects to the *running* bridge's reverse-WS as a SECOND fake NapCat client, injects one
owner message (text / image-by-url / file-by-id / face), and captures the bridge's full
streamed reply by reading action frames until the stream goes quiet. The real model + brain
run; replies come back to THIS client, so the real QQ user is never disturbed.

The owner id must match the bridge's QQ_OWNER_IDS, else the gate silently rejects.

Usage (run where the bridge listens, e.g. on the orange pi host):
  python sim_chat.py --text "配分函数一句话"
  python sim_chat.py --text "这是什么" --image-url https://.../x.jpg
  python sim_chat.py --file-id <id> --file-name report.pdf --text "讲了什么"
  python sim_chat.py --face-id 28 --text "懂我意思吗"
Exit code 0 if a non-status reply was captured, 2 if the turn produced only status/no reply.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

import aiohttp

# Status-ping prefixes the bridge streams before/around the real answer.
_STATUS_PREFIXES = ("⏳", "🔍", "📎", "📚", "📄", "🔎", "🔧", "🌐", "⚙️", "🆕")


def _build_message(args: argparse.Namespace) -> list[dict]:
    segs: list[dict] = []
    if args.text:
        segs.append({"type": "text", "data": {"text": args.text}})
    if args.image_url:
        segs.append(
            {
                "type": "image",
                "data": {
                    "url": args.image_url,
                    "file": args.image_name or "sim.jpg",
                    "sub_type": args.image_subtype,
                    "summary": args.image_summary,
                },
            }
        )
    if args.file_id:
        segs.append(
            {
                "type": "file",
                "data": {"file": args.file_name or "sim.bin", "file_id": args.file_id},
            }
        )
    if args.face_id:
        segs.append({"type": "face", "data": {"id": str(args.face_id)}})
    if not segs:
        raise SystemExit("nothing to send: pass --text / --image-url / --file-id / --face-id")
    return segs


async def _run(args: argparse.Namespace) -> int:
    ws_url = f"ws://{args.host}:{args.port}{args.path}"
    event = {
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "user_id": int(args.owner),
        "self_id": int(args.self_id),
        "message_id": int(time.time()) % 2_000_000_000,
        "message": _build_message(args),
        "raw_message": "[sim]",
        "sender": {"user_id": int(args.owner), "nickname": "sim"},
        "message_format": "array",
    }
    frames: list[tuple[float, dict]] = []
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(ws_url, heartbeat=None, autoping=True) as ws:
            await ws.send_str(
                json.dumps(
                    {
                        "post_type": "meta_event",
                        "meta_event_type": "lifecycle",
                        "sub_type": "connect",
                        "self_id": int(args.self_id),
                    }
                )
            )
            await ws.send_str(json.dumps(event))
            t0 = time.monotonic()
            last = t0
            seen_reply = False  # the model thinks silently before the first token — be
            # patient until a real (non-status) reply arrives, then end on a short quiet.
            while True:
                now = time.monotonic()
                if now - t0 > args.overall:
                    break
                if seen_reply and (now - last) > args.quiet:
                    break
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    action = data.get("action")
                    if action:
                        frames.append((round(time.monotonic() - t0, 1), data))
                        last = time.monotonic()
                        message = (data.get("params") or {}).get("message")
                        is_status = (
                            isinstance(message, str) and message[:1] in _STATUS_PREFIXES
                        )
                        if (not is_status) and (message is not None or "file" in action):
                            seen_reply = True
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.ERROR,
                ):
                    break

    # Render the captured turn.
    reply_text: list[str] = []
    print(f"\n=== captured {len(frames)} action frame(s) ===")
    for elapsed, d in frames:
        action = d.get("action")
        params = d.get("params", {})
        message = params.get("message")
        if isinstance(message, list):  # media segment (image/file)
            kinds = [s.get("type") for s in message]
            print(f"  +{elapsed}s {action} [segments={kinds}]")
            reply_text.append(f"[media:{kinds}]")
        elif isinstance(message, str):
            tag = "status" if message[:1] in _STATUS_PREFIXES else "REPLY"
            print(f"  +{elapsed}s {action} ({tag}): {message[:160]}")
            if tag == "REPLY":
                reply_text.append(message)
        elif action and "file" in action:  # upload_*_file
            print(f"  +{elapsed}s {action} name={params.get('name')}")
            reply_text.append(f"[file:{params.get('name')}]")
        else:
            print(f"  +{elapsed}s {action}: {str(params)[:120]}")
    has_reply = bool(reply_text)
    print(f"\n=== reply ({'OK' if has_reply else 'NO REPLY'}) ===")
    print("\n".join(reply_text) if has_reply else "(only status pings / nothing)")
    return 0 if has_reply else 2


def main() -> int:
    p = argparse.ArgumentParser(description="Simulate a QQ message into the running bridge.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--path", default="/onebot/v11")
    p.add_argument("--owner", default="10001", help="must match QQ_OWNER_IDS(填自己的)")
    p.add_argument("--self-id", default="10000")  # 占位:模拟台不需要真实bot号
    p.add_argument("--text", default="")
    p.add_argument("--image-url", default="")
    p.add_argument("--image-name", default="")
    p.add_argument("--image-summary", default="")
    p.add_argument("--image-subtype", default="0")
    p.add_argument("--file-id", default="")
    p.add_argument("--file-name", default="")
    p.add_argument("--face-id", default="")
    p.add_argument("--quiet", type=float, default=12.0, help="after the first real reply, end the turn this many idle seconds later")
    p.add_argument("--overall", type=float, default=180.0, help="hard cap on the whole turn (also the patience for the first reply token)")
    return asyncio.run(_run(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
