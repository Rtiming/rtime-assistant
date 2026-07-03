# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Channel-level output rendering strategy (the per-channel renderer choke point).

Problem class: models do not reliably obey a "don't use markdown" *prompt*
instruction, so the deterministic downgrade must happen at the channel egress
choke point — the prompt only sets tone. This module is that choke point,
sibling of ``channel_display.py`` (which handles streaming segmentation).

Three policies, one entry ``render(text, policy)``:

- ``plain_text`` (QQ): downgrade common markdown to plain text (QQ renders none
  of it). This is the generalized ``strip_markdown_for_qq`` that landed in the
  QQ bridge (T0); the exact regex behavior is preserved here.
- ``rich`` (Feishu): route the channel's existing rich/LaTeX rendering through
  here. The runtime stays dependency-light and app-agnostic, so the concrete
  Feishu renderer (``latex_unicode.render_math_for_feishu``) is passed in via the
  ``rich_renderer`` argument; with no renderer the text passes through unchanged.
- ``markdown`` (web): passthrough — the web frontend renders markdown/KaTeX.

The policy value comes from a profile's ``output.render`` (fed by T2/T5b); this
module takes the policy explicitly so it has zero profile/config coupling.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from enum import Enum

__all__ = ["RenderPolicy", "render", "strip_markdown_plain_text"]


class RenderPolicy(str, Enum):
    """Per-channel output rendering strategy (from profile ``output.render``)."""

    PLAIN_TEXT = "plain_text"  # QQ: markdown -> plain text
    RICH = "rich"  # Feishu: LaTeX/rich rendering
    MARKDOWN = "markdown"  # web: passthrough (frontend renders)


# --- plain_text: markdown -> plain-text downgrade (QQ renders no markdown) --------
# Behavior preserved verbatim from qq_bridge.output_qq.strip_markdown_for_qq (T0).
_CODE_FENCE = re.compile(r"^[ \t]*```[^\n]*$", re.M)  # drop fence lines, keep body
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")  # [label](url) -> label（url）
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.S)  # **x** / __x__ -> x
_ITALIC_STAR = re.compile(r"\*([^*\n]+?)\*")  # *x* -> x (single '_' left alone: snake_case)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")  # `x` -> x
_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*", re.M)  # ## H -> H
_QUOTE = re.compile(r"^[ \t]{0,3}>[ \t]?", re.M)  # > q -> q
_HR = re.compile(r"^[ \t]{0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$", re.M)  # --- / *** rule
_BULLET = re.compile(r"^([ \t]*)[*+][ \t]+", re.M)  # normalize * / + bullets to -
_BLANKS = re.compile(r"\n{3,}")


def strip_markdown_plain_text(text: str) -> str:
    """Downgrade common markdown to plain text (for channels that render none of it).

    Kills ``**``/``__`` bold, ``*x*`` italics, ``## `` headings, ``> `` quotes,
    ``---``/``***`` rules, code fences and inline ``` `` code, turns ``[t](u)``
    into ``t（u）`` and ``*``/``+`` bullets into ``- ``. Leaves snake_case paths,
    single ``_``, ``2*3``, bare numbers/phones and emoji untouched.
    """
    if not text:
        return text
    t = _CODE_FENCE.sub("", text)
    t = _LINK.sub(lambda m: f"{m.group(1)}（{m.group(2)}）", t)
    t = _BOLD.sub(lambda m: m.group(1) or m.group(2) or "", t)
    t = _ITALIC_STAR.sub(r"\1", t)
    t = _INLINE_CODE.sub(r"\1", t)
    t = _HEADING.sub("", t)
    t = _QUOTE.sub("", t)
    t = _HR.sub("", t)
    t = _BULLET.sub(r"\1- ", t)
    t = t.replace("**", "")  # kill any orphan bold markers (e.g. split across stream segments)
    t = _BLANKS.sub("\n\n", t)
    return t


def render(
    text: str,
    policy: RenderPolicy | str = RenderPolicy.MARKDOWN,
    *,
    rich_renderer: Callable[[str], str] | None = None,
) -> str:
    """Render ``text`` for a channel per ``policy``.

    - ``plain_text`` -> :func:`strip_markdown_plain_text`.
    - ``rich`` -> ``rich_renderer(text)`` if given, else ``text`` unchanged (the
      concrete Feishu LaTeX renderer is injected by the Feishu bridge so the
      runtime keeps no app dependency).
    - ``markdown`` -> passthrough (web frontend renders).

    ``policy`` accepts a :class:`RenderPolicy` or its string value.
    """
    policy = RenderPolicy(policy)
    if policy is RenderPolicy.PLAIN_TEXT:
        return strip_markdown_plain_text(text)
    if policy is RenderPolicy.RICH:
        return rich_renderer(text) if rich_renderer is not None else (text or "")
    # RenderPolicy.MARKDOWN: passthrough
    return text or ""
