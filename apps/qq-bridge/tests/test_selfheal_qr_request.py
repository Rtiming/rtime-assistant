# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""按需补码守护侧:触发文件监听 (qq_selfheal.handle_qr_request)。

覆盖:缺文件无操作 / 存在则取码发+删 / mtime 去抖不重复 / 发送失败不崩且不重发。
qq_selfheal.py 住在 ops/(不在测试 sys.path),按路径加载。
"""

import importlib.util
import os
import time
from pathlib import Path
from unittest import mock

import pytest

_OPS = Path(__file__).resolve().parents[1] / "ops" / "qq_selfheal.py"
_spec = importlib.util.spec_from_file_location("qq_selfheal", _OPS)
qq_selfheal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qq_selfheal)


def _cfg(tmp_path) -> "qq_selfheal.Config":
    cfg = qq_selfheal.Config()
    cfg.qr_request_file = str(tmp_path / "qq-qr-request")
    return cfg


def test_no_file_is_noop(tmp_path):
    cfg = _cfg(tmp_path)
    with mock.patch.object(qq_selfheal, "copy_qr_out") as copy_out, \
         mock.patch.object(qq_selfheal, "send_qr") as send_qr:
        result = qq_selfheal.handle_qr_request(cfg, last_seen_mtime=0.0)
    assert result == 0.0
    copy_out.assert_not_called()
    send_qr.assert_not_called()


def test_present_file_delivers_and_deletes(tmp_path):
    cfg = _cfg(tmp_path)
    Path(cfg.qr_request_file).write_text('{"requester_open_id": "ou_x"}', encoding="utf-8")
    with mock.patch.object(qq_selfheal, "copy_qr_out", return_value="/tmp/qr.png") as copy_out, \
         mock.patch.object(qq_selfheal, "send_qr") as send_qr, \
         mock.patch.object(qq_selfheal, "get_online", return_value=False), \
         mock.patch.object(qq_selfheal, "qr_mtime", return_value=time.time()), \
         mock.patch.object(qq_selfheal, "qr_decode_url", return_value="https://txz"):
        new_mtime = qq_selfheal.handle_qr_request(cfg, last_seen_mtime=0.0)
    copy_out.assert_called_once()
    send_qr.assert_called_once()
    # 触发文件被删除,返回的 mtime 记录已处理的这次请求(非 0)。
    assert not os.path.exists(cfg.qr_request_file)
    assert new_mtime != 0.0


def test_same_mtime_not_resent(tmp_path):
    """同一个请求(mtime 未变)不重复发送(去抖):即使文件因删除失败还在。"""
    cfg = _cfg(tmp_path)
    p = Path(cfg.qr_request_file)
    p.write_text("{}", encoding="utf-8")
    mtime = os.stat(cfg.qr_request_file).st_mtime
    with mock.patch.object(qq_selfheal, "copy_qr_out") as copy_out, \
         mock.patch.object(qq_selfheal, "send_qr") as send_qr, \
         mock.patch.object(qq_selfheal, "qr_decode_url", return_value=""):
        # 传入 last_seen_mtime == 文件当前 mtime => 视为已处理过,不再发。
        result = qq_selfheal.handle_qr_request(cfg, last_seen_mtime=mtime)
    copy_out.assert_not_called()
    send_qr.assert_not_called()
    assert result == mtime


def test_new_request_after_processed_resends(tmp_path):
    """处理过一次后,来了新请求(新 mtime)应再次发送。"""
    cfg = _cfg(tmp_path)
    p = Path(cfg.qr_request_file)

    # 第一次请求。
    p.write_text("{}", encoding="utf-8")
    with mock.patch.object(qq_selfheal, "copy_qr_out", return_value="/tmp/qr.png"), \
         mock.patch.object(qq_selfheal, "send_qr") as send_qr1, \
         mock.patch.object(qq_selfheal, "get_online", return_value=False), \
         mock.patch.object(qq_selfheal, "qr_mtime", return_value=time.time()), \
         mock.patch.object(qq_selfheal, "qr_decode_url", return_value=""):
        seen = qq_selfheal.handle_qr_request(cfg, last_seen_mtime=0.0)
    send_qr1.assert_called_once()

    # 第二次请求(确保 mtime 严格变大)。
    new_mtime = seen + 10.0
    p.write_text("{}", encoding="utf-8")
    os.utime(cfg.qr_request_file, (new_mtime, new_mtime))
    with mock.patch.object(qq_selfheal, "copy_qr_out", return_value="/tmp/qr.png"), \
         mock.patch.object(qq_selfheal, "send_qr") as send_qr2, \
         mock.patch.object(qq_selfheal, "get_online", return_value=False), \
         mock.patch.object(qq_selfheal, "qr_mtime", return_value=time.time()), \
         mock.patch.object(qq_selfheal, "qr_decode_url", return_value=""):
        seen2 = qq_selfheal.handle_qr_request(cfg, last_seen_mtime=seen)
    send_qr2.assert_called_once()
    assert seen2 == new_mtime


def test_send_failure_does_not_raise_and_warns_owner(tmp_path):
    """取码/发送失败:不抛异常(不拖垮守护),给 owner 发一条文字告警,文件已删(靠 mtime 去抖)。"""
    cfg = _cfg(tmp_path)
    Path(cfg.qr_request_file).write_text("{}", encoding="utf-8")
    with mock.patch.object(qq_selfheal, "copy_qr_out", side_effect=RuntimeError("no qr")), \
         mock.patch.object(qq_selfheal, "send_qr") as send_qr, \
         mock.patch.object(qq_selfheal, "notify_text") as notify_text, \
         mock.patch.object(qq_selfheal, "get_online", return_value=False), \
         mock.patch.object(qq_selfheal, "qr_mtime", return_value=time.time()), \
         mock.patch.object(qq_selfheal, "qr_decode_url", return_value=""):
        # 不应抛。
        new_mtime = qq_selfheal.handle_qr_request(cfg, last_seen_mtime=0.0)
    send_qr.assert_not_called()
    notify_text.assert_called_once()  # 告警了 owner
    assert not os.path.exists(cfg.qr_request_file)  # 已删,避免下轮重复触发
    assert new_mtime != 0.0  # 记为已处理


def test_config_defaults_from_env(monkeypatch):
    monkeypatch.setenv("SELFHEAL_QR_REQUEST_FILE", "/custom/path/req")
    monkeypatch.setenv("SELFHEAL_QR_REQUEST_CHECK_SECONDS", "7")
    cfg = qq_selfheal.Config()
    assert cfg.qr_request_file == "/custom/path/req"
    assert cfg.qr_request_check_seconds == 7


# ---------- 按需补码"新鲜码"三档语义(2026-07-03 修:不再发过期旧码) ----------

def _trigger(cfg):
    Path(cfg.qr_request_file).write_text("{}", encoding="utf-8")


def test_online_replies_text_no_qr(tmp_path):
    """在线时:告知免扫,绝不发码、不重启。"""
    cfg = _cfg(tmp_path)
    _trigger(cfg)
    with mock.patch.object(qq_selfheal, "get_online", return_value=True), \
         mock.patch.object(qq_selfheal, "notify_text") as notify, \
         mock.patch.object(qq_selfheal, "send_qr") as send_qr, \
         mock.patch.object(qq_selfheal, "restart_napcat") as restart:
        qq_selfheal.handle_qr_request(cfg, last_seen_mtime=0.0)
    send_qr.assert_not_called()
    restart.assert_not_called()
    assert "在线" in notify.call_args[0][1]


def test_stale_qr_waits_for_self_refresh_no_restart(tmp_path):
    """码过期但 NapCat 登录界面在自刷:等到新码就发,绝不重启(重启作废正扫的码)。"""
    cfg = _cfg(tmp_path)
    cfg.qr_refresh_wait_seconds = 30
    _trigger(cfg)
    stale = time.time() - 600
    fresh_after_wait = [stale, time.time() + 5]  # 第一次查=旧码,循环里查=自刷新码

    def fake_qr_mtime(_cfg):
        return fresh_after_wait.pop(0) if len(fresh_after_wait) > 1 else fresh_after_wait[0]

    with mock.patch.object(qq_selfheal, "get_online", return_value=False), \
         mock.patch.object(qq_selfheal, "qr_mtime", side_effect=fake_qr_mtime), \
         mock.patch.object(qq_selfheal, "copy_qr_out", return_value="/tmp/qr.png"), \
         mock.patch.object(qq_selfheal, "send_qr") as send_qr, \
         mock.patch.object(qq_selfheal, "notify_text"), \
         mock.patch.object(qq_selfheal, "restart_napcat") as restart, \
         mock.patch.object(qq_selfheal, "qr_decode_url", return_value=""), \
         mock.patch("time.sleep"):
        qq_selfheal.handle_qr_request(cfg, last_seen_mtime=0.0)
    send_qr.assert_called_once()
    restart.assert_not_called()
    # 按需场景的 caption 不许谎称"被风控踢下线"
    assert "按需补码" in send_qr.call_args.kwargs["caption"]


def test_stuck_login_restarts_then_delivers(tmp_path):
    """码过期且 NapCat 不自刷(卡死):重启容器强制出码,并更新共享 LAST_HEAL_TS。"""
    cfg = _cfg(tmp_path)
    cfg.qr_refresh_wait_seconds = 1
    cfg.qr_wait_seconds = 30
    _trigger(cfg)
    stale = time.time() - 600
    state = {"restarted": False}

    def fake_qr_mtime(_cfg):
        return time.time() + 5 if state["restarted"] else stale

    def fake_restart(_cfg):
        state["restarted"] = True

    qq_selfheal.LAST_HEAL_TS = 0.0
    with mock.patch.object(qq_selfheal, "get_online", return_value=False), \
         mock.patch.object(qq_selfheal, "qr_mtime", side_effect=fake_qr_mtime), \
         mock.patch.object(qq_selfheal, "restart_napcat", side_effect=fake_restart), \
         mock.patch.object(qq_selfheal, "copy_qr_out", return_value="/tmp/qr.png"), \
         mock.patch.object(qq_selfheal, "send_qr") as send_qr, \
         mock.patch.object(qq_selfheal, "notify_text"), \
         mock.patch.object(qq_selfheal, "qr_decode_url", return_value=""), \
         mock.patch("time.sleep"):
        qq_selfheal.handle_qr_request(cfg, last_seen_mtime=0.0)
    assert state["restarted"] is True
    send_qr.assert_called_once()
    assert qq_selfheal.LAST_HEAL_TS > 0.0  # auto-heal 冷却能看到这次重启


# ---------- A3 决策3: 管理员上报队列 (handle_notify_queue) ----------
def _ncfg(tmp_path):
    cfg = qq_selfheal.Config()
    cfg.notify_queue_dir = str(tmp_path / "notify-queue")
    return cfg


def test_notify_queue_sends_and_deletes(tmp_path):
    import json as _json
    cfg = _ncfg(tmp_path)
    os.makedirs(cfg.notify_queue_dir, exist_ok=True)
    for i in range(2):
        Path(cfg.notify_queue_dir, f"{i}.json").write_text(
            _json.dumps({"text": f"上报{i}"}), encoding="utf-8"
        )
    with mock.patch.object(qq_selfheal, "notify_text") as nt:
        sent = qq_selfheal.handle_notify_queue(cfg)
    assert sent == 2
    assert nt.call_count == 2
    assert not list(Path(cfg.notify_queue_dir).glob("*.json"))  # 发完删


def test_notify_queue_empty_is_noop(tmp_path):
    cfg = _ncfg(tmp_path)
    with mock.patch.object(qq_selfheal, "notify_text") as nt:
        assert qq_selfheal.handle_notify_queue(cfg) == 0
    nt.assert_not_called()


def test_notify_queue_send_failure_keeps_file_for_retry(tmp_path):
    import json as _json
    cfg = _ncfg(tmp_path)
    os.makedirs(cfg.notify_queue_dir, exist_ok=True)
    Path(cfg.notify_queue_dir, "a.json").write_text(_json.dumps({"text": "x"}), encoding="utf-8")
    with mock.patch.object(qq_selfheal, "notify_text", side_effect=RuntimeError("feishu down")):
        sent = qq_selfheal.handle_notify_queue(cfg)
    assert sent == 0
    assert list(Path(cfg.notify_queue_dir).glob("*.json"))  # 留待重试


def test_notify_queue_bad_file_removed(tmp_path):
    cfg = _ncfg(tmp_path)
    os.makedirs(cfg.notify_queue_dir, exist_ok=True)
    Path(cfg.notify_queue_dir, "bad.json").write_text("not json", encoding="utf-8")
    with mock.patch.object(qq_selfheal, "notify_text") as nt:
        qq_selfheal.handle_notify_queue(cfg)
    nt.assert_not_called()
    assert not list(Path(cfg.notify_queue_dir).glob("*.json"))  # 坏文件删除
