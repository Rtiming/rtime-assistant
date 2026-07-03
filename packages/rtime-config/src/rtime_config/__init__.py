# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Schema-driven configuration foundation for rtime-assistant.

One pydantic-settings model per component = validation + defaults + description +
JSON Schema. That single source of truth feeds three consumers without ever
maintaining two truths (see docs/development-plan.zh-CN.md §四/§五 L0):

    - the running process (env / config-file loading);
    - the docs (``model_json_schema()`` -> docs/config/<name>.md);
    - the future admin API / panel (react-jsonschema-form off the same schema).

This package deliberately stays thin: it holds the base class + the field
metadata vocabulary (``x-secret`` / ``x-reload`` / ``x-scope``) + the
schema->markdown exporter. Component packages (qq-bridge, and later
feishu-bridge / gateway) depend on it; the zero-dependency runtime primitives
package (rtime-chat-runtime) does NOT, so the runtime stays light.

P2 stage ① rule: migrations are behaviour-preserving. Every field carries the
SAME default as the legacy ``from_env`` it replaces, and every legacy env name
keeps working via ``AliasChoices`` (see ``config_field``). New names may be added
alongside, but old names are never dropped in this stage.
"""

from __future__ import annotations

from .base import RtimeBaseSettings
from .fields import Reload, config_field, secret_field
from .schema_doc import schema_to_markdown

__all__ = [
    "RtimeBaseSettings",
    "Reload",
    "config_field",
    "secret_field",
    "schema_to_markdown",
]
