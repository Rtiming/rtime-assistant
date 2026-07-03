# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Shared model routing: base aliases, alias resolution, passthrough, vision guard."""

from rtime_chat_runtime.model_routing import (
    base_aliases,
    default_model,
    model_choice_by_name,
    model_choices,
    model_can_see,
    numbered_model_choice,
    resolve_alias,
)


def test_base_aliases_has_opus_sonnet_haiku():
    assert {"opus", "sonnet", "haiku"} <= set(base_aliases())


def test_resolve_alias_known_is_case_insensitive():
    assert resolve_alias("opus").startswith("claude-opus")
    assert resolve_alias("OPUS").startswith("claude-opus")


def test_resolve_alias_passthrough():
    assert resolve_alias("kimi-code") == "kimi-code"
    assert resolve_alias("") == ""


def test_resolve_alias_extra_overrides():
    assert resolve_alias("fast", {"fast": "kimi-code"}) == "kimi-code"


def test_default_model_is_str():
    assert isinstance(default_model(), str)


def test_model_choices_are_numbered_and_resolvable():
    choices = model_choices()
    assert choices
    assert numbered_model_choice("1") == choices[0]
    assert numbered_model_choice("0") is None
    assert numbered_model_choice("bad") is None
    assert model_choice_by_name("kimi") is not None


def test_model_can_see_default_and_vision_models():
    # wrapper default (kimi-code) sees images via the Read tool (empirical override)
    assert model_can_see("") is True
    assert model_can_see("kimi-code") is True
    assert model_can_see("opus") is True  # alias -> vision-capable Claude
    assert model_can_see("claude-sonnet-4-6") is True


def test_model_can_see_text_only_model_is_false_when_registry_present():
    # A registry model explicitly marked vision:false must be blocked (decision 3).
    # Skip if the registry package can't be imported here (optimistic fallback => True).
    import pytest

    pytest.importorskip("rtime_models")
    assert model_can_see("deepseek-v4-flash-ascend") is False
    assert model_can_see("ds") is False
