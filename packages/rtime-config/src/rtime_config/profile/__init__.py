# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""``rtime_config.profile`` — the git-declared profile layer (design §二).

A profile.yaml is a human-written, nested declaration of an instance's model /
system prompt / permissions / plugins / library scope / user tiers / channel
bindings / output rendering. This package:

  - :class:`ProfileConfig` — the pydantic model of that file shape (``schema``);
  - :data:`PROJECTIONS` — the EXPLICIT mapping table that compiles the nested
    profile to the flat ``module.field`` key space the admin core addresses
    (``mapping``);
  - :func:`load_profile` — parse + single-level ``extends`` merge + file-ref
    resolution + projection + x-secret rejection + optional registry validation
    (``loader``), returning a :class:`CompiledProfile` whose ``.layer`` is injected
    as ``ConfigStore(profile_layer=...)``.

The compiled layer sits BELOW the admin-core store and ABOVE schema defaults in
the four-layer read precedence (env > store > profile > default), resolved at read
time — nothing is seeded (design §2.4).
"""

from __future__ import annotations

from .doctor import (
    check_profile_policy_file,
    cross_check_scope,
    mount_target_to_scope,
)
from .library_policy import (
    POLICY_SCHEMA_VERSION,
    build_library_policy,
    render_library_policy_json,
)
from .loader import (
    CompiledProfile,
    ProfileError,
    ProfileSecretError,
    load_profile,
)
from .mapping import PROJECTIONS, Projection
from .schema import SUPPORTED_SCHEMA_VERSION, ProfileConfig

__all__ = [
    "ProfileConfig",
    "SUPPORTED_SCHEMA_VERSION",
    "PROJECTIONS",
    "Projection",
    "load_profile",
    "CompiledProfile",
    "ProfileError",
    "ProfileSecretError",
    "POLICY_SCHEMA_VERSION",
    "build_library_policy",
    "render_library_policy_json",
    "cross_check_scope",
    "mount_target_to_scope",
    "check_profile_policy_file",
]
