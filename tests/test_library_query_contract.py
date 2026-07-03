# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""泳道 I-Q1 库查询服务兼容合同(design: library-query-service-2026-07 §三)。

保住 owner 点名的开发路径:核心查询动词的**返回形状**与 **stdio↔HTTP 双传输等价**
被 golden 锁定——改形状/改传输语义=本测试红=有意识的版本决策,不是顺手改。
工具面(名称集合/wire 命名)已由 test_rtime_library_gateway_mcp.py 锁定,此处不重复。
"""

from __future__ import annotations

import http.client
import json
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_SRC = ROOT / "packages" / "rtime-library-gateway" / "src"
LIBRARY_SRC = ROOT / "packages" / "brain-library" / "src"


def _load_mcp():
    for src in (GATEWAY_SRC, LIBRARY_SRC):
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))
    import importlib

    return importlib.import_module("rtime_library_gateway.mcp_server")


def _make_brain(tmp_path, monkeypatch):
    from brain_library import indexer

    brain = tmp_path / "brain"
    (brain / "knowledge").mkdir(parents=True)
    (brain / "knowledge" / "stellarator.md").write_text(
        "---\nstatus: active\nsource: https://example.edu/s\n---\n"
        "# 仿星器\n仿星器 HTS 线圈 与等离子体物理研究,这是合同测试文档。\n",
        encoding="utf-8",
    )
    idx = tmp_path / "idx.sqlite"
    assert indexer.build_index(brain, idx, force=True)["ok"]
    monkeypatch.setenv("RTIME_ASSISTANT_ROOT", str(ROOT))
    monkeypatch.setenv("BRAIN_ROOT", str(brain))
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_PREWARM", "0")
    return brain, idx


# ---- 合同 1:核心查询动词的返回形状(键必须存在;允许新增键,不允许改名/删除) ----

SEARCH_CONTRACT_KEYS = {"ok", "method", "query", "results", "result_count", "index"}
SEARCH_HIT_KEYS = {"path", "title", "snippet", "score"}
READ_CONTRACT_KEYS = {"ok", "path"}
TREE_CONTRACT_KEYS = {"ok"}
STAT_CONTRACT_KEYS = {"ok", "path"}


def test_search_result_shape_contract(tmp_path, monkeypatch):
    _, idx = _make_brain(tmp_path, monkeypatch)
    server = _load_mcp().RtimeLibraryGatewayMCP()
    data = server.invoke("lib.search", {"query": "仿星器", "index": str(idx)})
    assert data["ok"] is True
    missing = SEARCH_CONTRACT_KEYS - set(data)
    assert not missing, f"lib.search 合同键缺失: {missing}"
    assert data["result_count"] >= 1
    hit = data["results"][0]
    missing_hit = SEARCH_HIT_KEYS - set(hit)
    assert not missing_hit, f"lib.search 命中项合同键缺失: {missing_hit}"
    assert hit["path"] == "knowledge/stellarator.md"


def test_read_tree_stat_shape_contract(tmp_path, monkeypatch):
    _make_brain(tmp_path, monkeypatch)
    server = _load_mcp().RtimeLibraryGatewayMCP()
    read = server.invoke("lib.read", {"path": "knowledge/stellarator.md"})
    assert read["ok"] is True and not (READ_CONTRACT_KEYS - set(read))
    tree = server.invoke("lib.tree", {"path": "knowledge"})
    assert tree["ok"] is True and not (TREE_CONTRACT_KEYS - set(tree))
    stat = server.invoke("lib.stat", {"path": "knowledge/stellarator.md"})
    assert stat["ok"] is True and not (STAT_CONTRACT_KEYS - set(stat))


# ---- 合同 2:stdio ↔ HTTP 双传输等价(同一请求,同一 result) ----


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _rpc(name: str, arguments: dict, rpc_id: int) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def test_stdio_and_http_transports_return_identical_result(tmp_path, monkeypatch):
    _, idx = _make_brain(tmp_path, monkeypatch)
    mcp = _load_mcp()
    server = mcp.RtimeLibraryGatewayMCP()
    request = _rpc("lib_search", {"query": "仿星器", "index": str(idx)}, rpc_id=7)

    # stdio 语义 = 直接 handle_message
    stdio_response = server.handle_message(json.loads(json.dumps(request)))

    # HTTP 壳:同一个 server 实例起在临时端口(与生产 serve_http 同一代码路径)
    port = _free_port()
    t = threading.Thread(
        target=mcp.serve_http, args=("127.0.0.1", port, server), daemon=True
    )
    t.start()
    body = json.dumps(request).encode()
    last_exc: Exception | None = None
    for _ in range(50):  # 等服务起监听
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "POST", "/mcp", body=body, headers={"Content-Type": "application/json"}
            )
            resp = conn.getresponse()
            http_response = json.loads(resp.read())
            conn.close()
            break
        except OSError as exc:
            last_exc = exc
            time.sleep(0.1)
    else:
        raise AssertionError(f"HTTP transport never came up: {last_exc}")

    assert http_response["id"] == stdio_response["id"] == 7
    # 合同核心:两壳的 result 完全一致(传输壳只做传输,不改语义)
    assert http_response["result"] == stdio_response["result"]
    payload = json.loads(stdio_response["result"]["content"][0]["text"])
    assert payload["ok"] is True and payload["result_count"] >= 1
