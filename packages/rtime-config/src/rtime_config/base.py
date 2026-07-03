# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""``RtimeBaseSettings`` — the common pydantic-settings base for rtime configs.

Shared model config so every component behaves the same:

    - ``extra="ignore"``: the process env is full of unrelated vars; a settings
      model must never choke on them.
    - ``populate_by_name=True``: fields can be set by their Python name in
      addition to their env alias(es) — needed because our code and tests
      construct configs directly with keyword args (e.g.
      ``QQBridgeConfig(owner_ids=...)``), not only from env.
    - ``validate_default=True``: run validators on defaults too, so a bad default
      is caught in tests, not in prod.
    - ``case_sensitive=False``: env names are conventionally UPPER_SNAKE.

Subclasses set their own ``env_prefix`` (e.g. ``QQ_``) via ``model_config``.
Fields whose legacy name does NOT share that prefix (shared cross-process names
like ``DEFAULT_MODEL``) pin an explicit ``validation_alias`` with
``AliasChoices`` so both the prefixed-new and the legacy-unprefixed name resolve.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class RtimeBaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        populate_by_name=True,
        validate_default=True,
        case_sensitive=False,
    )
