# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""The process_event seam: one directly-callable QQ processing chain (T4 地基).

Design (docs/design/mainline-profiles-and-entries-2026-07.zh-CN.md §3.1): the
injection point sits after the OneBot JSON decode and before the access gate.
``QQEventPipeline.process_event(event) -> list[OutboundAction]`` wraps the whole
chain — decoded OneBot event → actor-tier gate → debounce → direct reply → model
run → render → structured outbound actions — as a plain async call. The reverse-WS
server (``onebot.ws_server``) and the simulation harness both run THIS pipeline;
the WS adapter is just a thin consumer that forwards each ``OutboundAction`` as a
OneBot action frame, so simulated and live traffic cannot drift apart.

``OutboundAction`` mirrors the OneBot action wire pair exactly
(``send_private_msg`` / ``send_group_msg`` / ``upload_*_file`` /
``set_group_add_request`` / ``set_group_leave`` …): the harness asserts on it,
the adapter serializes it — no second vocabulary in between.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .onebot.protocol import parse_message_event, reply_action


@dataclass(frozen=True)
class OutboundAction:
    """One structured outbound OneBot action produced by the processing chain."""

    action: str  # OneBot action name, e.g. "send_private_msg"
    params: dict[str, Any]

    @property
    def message_text(self) -> str:
        """The plain-text body of a send_*_msg action ("" for segment arrays)."""
        message = self.params.get("message")
        return message if isinstance(message, str) else ""


# Called for each OutboundAction as it is produced (live path: send it on the ws).
EmitFunc = Callable[[OutboundAction], Awaitable[None]]


class QQEventPipeline:
    """The single implementation of the decoded-event → outbound-actions chain.

    Handlers are the existing ``build_*_handler`` closures from ``qq_bridge.app``
    (message is required; request/notice optional). ``process_event`` accepts a raw
    decoded OneBot event dict (message / request / notice post types), runs the
    identical chain the live bridge runs, and returns every outbound action in
    emission order. When ``emit`` is given, each action is also awaited out to it
    the moment it is produced — that is how the WS adapter keeps streaming
    semantics (partial replies go out mid-run, not after the model finishes).
    """

    def __init__(
        self,
        *,
        on_message: Callable[..., Awaitable[None]],
        on_request: Callable[..., Awaitable[None]] | None = None,
        on_notice: Callable[..., Awaitable[None]] | None = None,
        group_reply_at_sender: Callable[[], bool] | bool = False,
    ) -> None:
        if on_message is None:
            raise ValueError("QQEventPipeline requires an on_message handler")
        self.on_message = on_message
        self.on_request = on_request
        self.on_notice = on_notice
        if callable(group_reply_at_sender):
            self._group_reply_at_sender = group_reply_at_sender
        else:
            self._group_reply_at_sender = lambda: bool(group_reply_at_sender)

    async def process_event(
        self, event: dict[str, Any], *, emit: EmitFunc | None = None
    ) -> list[OutboundAction]:
        """Run one decoded OneBot event through the full chain; return its actions."""
        actions: list[OutboundAction] = []

        async def dispatch(action: str, params: dict[str, Any]) -> None:
            outbound = OutboundAction(action=action, params=params)
            actions.append(outbound)
            if emit is not None:
                await emit(outbound)

        post_type = event.get("post_type")
        if post_type == "message":
            msg = parse_message_event(event)
            at_sender = bool(self._group_reply_at_sender())

            async def reply(text: str) -> None:
                action, params = reply_action(msg, text, at_sender=at_sender)
                await dispatch(action, params)

            await self.on_message(msg, reply, dispatch)
        elif post_type == "request" and self.on_request is not None:
            await self.on_request(event, dispatch)
        elif post_type == "notice" and self.on_notice is not None:
            await self.on_notice(event, dispatch)
        return actions


__all__ = ["EmitFunc", "OutboundAction", "QQEventPipeline"]
