# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""按需补码:飞书桥前置钩子 (main.handle_message_async 拦截)。

验证:owner 私聊触发词 -> 写触发文件 + 回提示 + **不进模型**;
非 owner / 群聊 / 普通消息 -> 不触发,照常走后续(模型)流程。
"""

import json
import os
import types
from unittest import mock

import pytest

import _shared_runtime  # noqa: F401
import main
import qr_request


def _post_content(text: str) -> dict:
    return {
        "title": "",
        "content": [[{"tag": "text", "text": text}]],
    }


def _localized_post_content(text: str) -> dict:
    return {
        "post": {
            "zh_cn": {
                "title": "",
                "content": [[{"tag": "text", "text": text}]],
            }
        }
    }


def _make_event(
    open_id: str,
    text: str,
    chat_type: str = "p2p",
    chat_id: str | None = None,
    message_type: str = "text",
    content: dict | None = None,
):
    """构造一个最小可用的飞书文本消息 event(仿真钩子链需要的字段)。"""
    msg = types.SimpleNamespace()
    msg.message_type = message_type
    msg.content = json.dumps(content if content is not None else {"text": text})
    msg.message_id = "om_test_msg"
    msg.chat_type = chat_type
    msg.chat_id = chat_id or open_id
    msg.mentions = []

    sender = types.SimpleNamespace()
    sender.sender_id = types.SimpleNamespace(open_id=open_id)

    event = types.SimpleNamespace()
    event.event = types.SimpleNamespace(message=msg, sender=sender)
    return event


@pytest.fixture
def owner_env(monkeypatch, tmp_path):
    """owner 白名单 + 触发文件指向 tmp。"""
    monkeypatch.setattr(main.config, "ALLOWED_USERS", {"ou_owner"}, raising=False)
    monkeypatch.setattr(main.config, "ADMIN_USERS", {"ou_owner"}, raising=False)
    monkeypatch.setattr(main.config, "REQUIRE_MENTION_IN_GROUP", True, raising=False)
    target = tmp_path / "qq-qr-request"
    monkeypatch.setenv("RTIME_QQ_QR_REQUEST_FILE", str(target))
    return target


@pytest.mark.asyncio
async def test_owner_trigger_writes_file_and_replies_without_model(owner_env):
    target = owner_env
    event = _make_event("ou_owner", "补码")

    fake_feishu = mock.MagicMock()
    fake_feishu.send_card_to_user = mock.AsyncMock(return_value="card_id")

    with mock.patch.object(main, "feishu", fake_feishu), \
         mock.patch.object(main, "_process_message", mock.AsyncMock()) as process:
        await main.handle_message_async(event)

    # 写了触发文件 + 内容含请求者。
    assert os.path.exists(target)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["requester_open_id"] == "ou_owner"
    # 回了一句提示,且**没有**进模型处理。
    fake_feishu.send_card_to_user.assert_awaited_once()
    reply = fake_feishu.send_card_to_user.await_args.kwargs.get("content", "")
    assert "二维码" in reply
    process.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_post_trigger_writes_file_and_replies_without_model(owner_env):
    """飞书富文本 post 里的 QQ码 也应当触发补码,避免被当成不支持消息类型。"""
    target = owner_env
    event = _make_event(
        "ou_owner",
        "QQ码",
        message_type="post",
        content=_post_content("QQ码"),
    )

    fake_feishu = mock.MagicMock()
    fake_feishu.send_card_to_user = mock.AsyncMock(return_value="card_id")

    with mock.patch.object(main, "feishu", fake_feishu), \
         mock.patch.object(main, "_process_message", mock.AsyncMock()) as process:
        await main.handle_message_async(event)

    assert os.path.exists(target)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["requester_open_id"] == "ou_owner"
    fake_feishu.send_card_to_user.assert_awaited_once()
    process.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_localized_post_trigger_writes_file(owner_env):
    """飞书 post 的 zh_cn 包裹结构也要能识别触发词。"""
    target = owner_env
    event = _make_event(
        "ou_owner",
        "QQ 码",
        message_type="post",
        content=_localized_post_content("QQ 码"),
    )

    fake_feishu = mock.MagicMock()
    fake_feishu.send_card_to_user = mock.AsyncMock(return_value="card_id")

    with mock.patch.object(main, "feishu", fake_feishu), \
         mock.patch.object(main, "_process_message", mock.AsyncMock()) as process:
        await main.handle_message_async(event)

    assert os.path.exists(target)
    process.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_owner_trigger_does_not_intercept(owner_env):
    """非 owner 发同样的词:不拦截(不写文件),照常进后续流程(_process_message)。"""
    target = owner_env
    # ou_other 需要在 ALLOWED_USERS 里才不会被 access 门挡掉,才能验证"进模型"。
    main.config.ALLOWED_USERS = {"ou_owner", "ou_other"}
    event = _make_event("ou_other", "补码")

    fake_feishu = mock.MagicMock()
    fake_feishu.send_card_to_user = mock.AsyncMock(return_value="card_id")

    with mock.patch.object(main, "feishu", fake_feishu), \
         mock.patch.object(main, "_process_message", mock.AsyncMock()) as process:
        await main.handle_message_async(event)

    assert not os.path.exists(target)  # 没写触发文件
    process.assert_awaited_once()      # 进了模型流程


@pytest.mark.asyncio
async def test_ordinary_message_goes_to_model(owner_env):
    """owner 发普通消息(非触发词):不拦截,进模型。"""
    target = owner_env
    event = _make_event("ou_owner", "帮我算一下今天的复习计划")

    fake_feishu = mock.MagicMock()
    fake_feishu.send_card_to_user = mock.AsyncMock(return_value="card_id")

    with mock.patch.object(main, "feishu", fake_feishu), \
         mock.patch.object(main, "_process_message", mock.AsyncMock()) as process:
        await main.handle_message_async(event)

    assert not os.path.exists(target)
    process.assert_awaited_once()


@pytest.mark.asyncio
async def test_group_trigger_not_intercepted(owner_env):
    """群聊里 owner @bot 发触发词:不走按需补码(补码是私聊 owner 动作),照常进模型。"""
    target = owner_env
    main.config.ALLOWED_CHATS = {"oc_group"}
    event = _make_event("ou_owner", "补码", chat_type="group", chat_id="oc_group")
    # 群聊需要 @mention 才处理;给一个 mention 让它通过 REQUIRE_MENTION_IN_GROUP。
    event.event.message.mentions = [types.SimpleNamespace(key="@_user_1")]

    fake_feishu = mock.MagicMock()
    fake_feishu.send_card_to_user = mock.AsyncMock(return_value="card_id")

    with mock.patch.object(main, "feishu", fake_feishu), \
         mock.patch.object(main, "_process_message", mock.AsyncMock()) as process:
        await main.handle_message_async(event)

    assert not os.path.exists(target)   # 没触发补码
    process.assert_awaited_once()        # 进了模型流程
