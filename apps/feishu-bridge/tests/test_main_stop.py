# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import os
import sys
import types
import unittest
from unittest import mock

os.environ.setdefault("FEISHU_APP_ID", "test-app-id")
os.environ.setdefault("FEISHU_APP_SECRET", "test-app-secret")


def _install_fake_lark():
    if "lark_oapi" in sys.modules:
        return

    class _Builder:
        def app_id(self, *_args, **_kwargs):
            return self

        def app_secret(self, *_args, **_kwargs):
            return self

        def log_level(self, *_args, **_kwargs):
            return self

        def request_body(self, *_args, **_kwargs):
            return self

        def receive_id_type(self, *_args, **_kwargs):
            return self

        def receive_id(self, *_args, **_kwargs):
            return self

        def msg_type(self, *_args, **_kwargs):
            return self

        def content(self, *_args, **_kwargs):
            return self

        def message_id(self, *_args, **_kwargs):
            return self

        def event_handler(self, *_args, **_kwargs):
            return self

        def register_p2_im_message_receive_v1(self, *_args, **_kwargs):
            return self

        def register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(self, *_args, **_kwargs):
            return self

        def register_p2_im_message_message_read_v1(self, *_args, **_kwargs):
            return self

        def register_p2_card_action_trigger(self, *_args, **_kwargs):
            return self

        def build(self):
            return self

    class _Client:
        @staticmethod
        def builder():
            return _Builder()

    class _WsClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            return None

    fake_lark = types.ModuleType("lark_oapi")
    fake_lark.Client = _Client
    fake_lark.LogLevel = types.SimpleNamespace(INFO="INFO", WARNING="WARNING")
    fake_lark.ws = types.SimpleNamespace(Client=_WsClient)
    fake_lark.EventDispatcherHandler = types.SimpleNamespace(builder=lambda *_args, **_kwargs: _Builder())

    model_mod = types.ModuleType("lark_oapi.api.im.v1.model")
    for name in (
        "P2ImMessageReceiveV1",
        "CreateMessageRequest",
        "CreateMessageRequestBody",
        "PatchMessageRequest",
        "PatchMessageRequestBody",
        "ReplyMessageRequest",
        "ReplyMessageRequestBody",
    ):
        setattr(model_mod, name, type(name, (), {"builder": staticmethod(lambda: _Builder())}))

    callback_mod = types.ModuleType("lark_oapi.event.callback.model.p2_card_action_trigger")
    for name in (
        "P2CardActionTrigger",
        "P2CardActionTriggerResponse",
        "CallBackToast",
    ):
        setattr(callback_mod, name, type(name, (), {}))

    sys.modules["lark_oapi"] = fake_lark
    sys.modules["lark_oapi.api"] = types.ModuleType("lark_oapi.api")
    sys.modules["lark_oapi.api.im"] = types.ModuleType("lark_oapi.api.im")
    sys.modules["lark_oapi.api.im.v1"] = types.ModuleType("lark_oapi.api.im.v1")
    sys.modules["lark_oapi.api.im.v1.model"] = model_mod
    sys.modules["lark_oapi.event"] = types.ModuleType("lark_oapi.event")
    sys.modules["lark_oapi.event.callback"] = types.ModuleType("lark_oapi.event.callback")
    sys.modules["lark_oapi.event.callback.model"] = types.ModuleType("lark_oapi.event.callback.model")
    sys.modules["lark_oapi.event.callback.model.p2_card_action_trigger"] = callback_mod


_install_fake_lark()

import main  # noqa: E402


class MainStopTests(unittest.IsolatedAsyncioTestCase):
    def test_redact_log_text_masks_lark_url_tokens(self):
        text = "connect?access_key=abc123&ticket=secret-ticket&foo=bar"

        redacted = main._redact_log_text(text)

        self.assertIn("access_key=[REDACTED]", redacted)
        self.assertIn("ticket=[REDACTED]", redacted)
        self.assertNotIn("abc123", redacted)
        self.assertNotIn("secret-ticket", redacted)
        self.assertIn("foo=bar", redacted)

    def test_health_payload_contains_non_sensitive_process_state(self):
        payload = main._health_payload()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["service"], "feishu-bridge")
        self.assertIsInstance(payload["uptime_seconds"], int)
        self.assertIsInstance(payload["idle_seconds"], int)
        self.assertIsInstance(payload["watchdog_max_uptime_seconds"], int)
        self.assertIsInstance(payload["watchdog_forced_restart_enabled"], bool)
        self.assertIsInstance(payload["chat_lock_count"], int)
        self.assertNotIn("FEISHU_APP_SECRET", payload)
        self.assertNotIn("ALLOWED_USERS", payload)

    def test_watchdog_max_uptime_env_can_disable_forced_restart(self):
        with mock.patch.dict(os.environ, {"WATCHDOG_MAX_UPTIME_SECONDS": "0"}):
            self.assertEqual(main._load_watchdog_max_uptime_seconds(), 0)

    def test_watchdog_max_uptime_env_parses_seconds(self):
        with mock.patch.dict(os.environ, {"WATCHDOG_MAX_UPTIME_SECONDS": "7200"}):
            self.assertEqual(main._load_watchdog_max_uptime_seconds(), 7200)

    def test_watchdog_max_uptime_invalid_env_uses_default(self):
        with mock.patch.dict(os.environ, {"WATCHDOG_MAX_UPTIME_SECONDS": "bad"}):
            self.assertEqual(
                main._load_watchdog_max_uptime_seconds(),
                main.DEFAULT_WATCHDOG_MAX_UPTIME_SECONDS,
            )

    def test_ignored_lark_event_updates_last_event_without_scheduling_work(self):
        new_time = main._last_event + 10

        with mock.patch.object(main.time, "time", return_value=new_time), mock.patch.object(
            main.asyncio,
            "run_coroutine_threadsafe",
        ) as schedule_mock:
            result = main.on_ignored_lark_event(object())

        self.assertIsNone(result)
        self.assertEqual(main._last_event, new_time)
        schedule_mock.assert_not_called()

    async def test_handle_stop_command_returns_no_active_run_message(self):
        with mock.patch.object(main, "stop_run", mock.AsyncMock(return_value=False)):
            reply = await main._handle_stop_command("user-1")

        self.assertIn("没有正在运行", reply)

    async def test_handle_stop_command_requests_stop_for_active_run(self):
        active_run = mock.Mock(stop_requested=False)

        with mock.patch.object(
            main._active_runs,
            "get_run",
            return_value=active_run,
        ), mock.patch.object(
            main,
            "stop_run",
            mock.AsyncMock(return_value=True),
        ) as stop_run_mock:
            reply = await main._handle_stop_command("user-1")

        stop_run_mock.assert_awaited_once()
        self.assertIn("已发送停止请求", reply)


if __name__ == "__main__":
    unittest.main()
