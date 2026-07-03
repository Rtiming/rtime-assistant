# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""按需补码:飞书侧触发词识别 / owner 门 / 写触发文件 (qr_request 模块)。"""

import json
import os

import pytest

import _shared_runtime  # noqa: F401
import qr_request


@pytest.mark.parametrize("text", [
    "补码", " 补码 ", "qq码", "QQ码", "qq 码", "qq二维码", "QQ二维码",
    "qqcode", "QQCode", "/qqcode", "/QQCode", " /qq code ", "qq code",
])
def test_trigger_words_match(text):
    assert qr_request.is_qr_request(text) is True


@pytest.mark.parametrize("text", [
    "", "补一下码", "帮我补码好吗", "qq码是多少", "扫码", "codeqq",
    "补个码谢谢", "请补码", "hello", "/stop", "码",
])
def test_non_trigger_words_do_not_match(text):
    assert qr_request.is_qr_request(text) is False


def test_owner_gate():
    admins = {"ou_owner"}
    assert qr_request.is_owner("ou_owner", admins) is True
    assert qr_request.is_owner("ou_other", admins) is False
    # 未配置 admin => 一律拒(不会误触发)。
    assert qr_request.is_owner("ou_owner", set()) is False


def test_qr_request_file_env_override(monkeypatch):
    monkeypatch.delenv("RTIME_QQ_QR_REQUEST_FILE", raising=False)
    assert qr_request.qr_request_file() == qr_request.DEFAULT_QR_REQUEST_FILE
    monkeypatch.setenv("RTIME_QQ_QR_REQUEST_FILE", "/x/y/req")
    assert qr_request.qr_request_file() == "/x/y/req"


def test_write_qr_request_creates_file_with_audit(tmp_path):
    target = tmp_path / "sub" / "qq-qr-request"  # 父目录也要被创建
    written = qr_request.write_qr_request("ou_owner", path=str(target))
    assert written == str(target)
    assert target.exists()
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["requester_open_id"] == "ou_owner"
    assert payload["source"] == "feishu-bridge"
    assert isinstance(payload["requested_at"], (int, float))


def test_write_qr_request_uses_env_path(monkeypatch, tmp_path):
    target = tmp_path / "qq-qr-request"
    monkeypatch.setenv("RTIME_QQ_QR_REQUEST_FILE", str(target))
    written = qr_request.write_qr_request("ou_owner")
    assert written == str(target)
    assert os.path.exists(target)
