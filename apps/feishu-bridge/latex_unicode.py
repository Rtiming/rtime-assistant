# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Render LaTeX math into faithful Unicode for Feishu chat.

Feishu chat surfaces (text / post / interactive-card markdown) have NO formula
component, so LaTeX cannot be typeset natively in a chat bubble. Rather than
fall back to images (forbidden) or a linked doc, this module converts common
LaTeX math into readable Unicode *inline*, keeping the reply in a normal card.

Design principles:
- Lossless-or-source: constructs Unicode cannot faithfully express (matrices,
  cases, arrays) keep their original ``$...$`` source so nothing is silently
  mangled. The goal is "render what renders cleanly, never fake the rest".
- Markdown-safe: fenced code blocks and inline code are left untouched; only
  real math delimiters ($...$, $$...$$, \\(...\\), \\[...\\]) are converted.
"""

from __future__ import annotations

import re


# --- character maps -------------------------------------------------------

GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "Gamma": "Γ", "delta": "δ",
    "Delta": "Δ", "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ", "eta": "η",
    "theta": "θ", "Theta": "Θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "Lambda": "Λ", "mu": "μ", "nu": "ν", "xi": "ξ", "Xi": "Ξ",
    "pi": "π", "Pi": "Π", "rho": "ρ", "varrho": "ϱ", "sigma": "σ", "Sigma": "Σ",
    "tau": "τ", "upsilon": "υ", "Upsilon": "Υ", "phi": "φ", "varphi": "φ",
    "Phi": "Φ", "chi": "χ", "psi": "ψ", "Psi": "Ψ", "omega": "ω", "Omega": "Ω",
}

# Operators, relations, arrows, set/logic symbols, named functions.
SYMBOLS = {
    "cdot": "·", "cdots": "⋯", "ldots": "…", "dots": "…", "vdots": "⋮",
    "times": "×", "div": "÷", "ast": "∗", "star": "⋆", "circ": "∘",
    "pm": "±", "mp": "∓", "oplus": "⊕", "otimes": "⊗", "odot": "⊙",
    "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "ll": "≪", "gg": "≫",
    "neq": "≠", "ne": "≠", "equiv": "≡", "approx": "≈", "simeq": "≃",
    "sim": "∼", "cong": "≅", "propto": "∝", "asymp": "≍", "doteq": "≐",
    "to": "→", "rightarrow": "→", "longrightarrow": "⟶", "leftarrow": "←",
    "Rightarrow": "⇒", "Leftarrow": "⇐", "Leftrightarrow": "⇔",
    "leftrightarrow": "↔", "mapsto": "↦", "uparrow": "↑", "downarrow": "↓",
    "infty": "∞", "partial": "∂", "nabla": "∇", "hbar": "ℏ", "ell": "ℓ",
    "Re": "ℜ", "Im": "ℑ", "wp": "℘", "aleph": "ℵ", "emptyset": "∅",
    "varnothing": "∅", "forall": "∀", "exists": "∃", "nexists": "∄",
    "neg": "¬", "lnot": "¬", "land": "∧", "wedge": "∧", "lor": "∨",
    "vee": "∨", "in": "∈", "notin": "∉", "ni": "∋", "subset": "⊂",
    "subseteq": "⊆", "supset": "⊃", "supseteq": "⊇", "cup": "∪", "cap": "∩",
    "setminus": "∖", "mid": "∣", "parallel": "∥", "perp": "⊥", "angle": "∠",
    "int": "∫", "iint": "∬", "iiint": "∭", "oint": "∮", "sum": "∑",
    "prod": "∏", "coprod": "∐", "bigcup": "⋃", "bigcap": "⋂",
    "bigoplus": "⨁", "bigotimes": "⨂", "sqrt_sym": "√", "surd": "√",
    "langle": "⟨", "rangle": "⟩", "lfloor": "⌊", "rfloor": "⌋",
    "lceil": "⌈", "rceil": "⌉", "nabla2": "∇²",
    "degree": "°", "prime": "′", "dagger": "†", "ddagger": "‡",
    "hbar2": "ℏ", "Box": "□", "diamond": "⋄", "bullet": "•", "checkmark": "✓",
    # named functions / operators kept as words
    "lim": "lim", "sin": "sin", "cos": "cos", "tan": "tan", "cot": "cot",
    "sec": "sec", "csc": "csc", "arcsin": "arcsin", "arccos": "arccos",
    "arctan": "arctan", "sinh": "sinh", "cosh": "cosh", "tanh": "tanh",
    "log": "log", "ln": "ln", "lg": "lg", "exp": "exp", "det": "det",
    "dim": "dim", "ker": "ker", "deg": "deg", "gcd": "gcd", "max": "max",
    "min": "min", "sup": "sup", "inf": "inf", "arg": "arg", "Tr": "Tr",
    "tr": "tr", "mod": "mod", "bmod": "mod",
}

MATHCAL = {
    "A": "𝒜", "B": "ℬ", "C": "𝒞", "D": "𝒟", "E": "ℰ", "F": "ℱ", "G": "𝒢",
    "H": "ℋ", "I": "ℐ", "J": "𝒥", "K": "𝒦", "L": "ℒ", "M": "ℳ", "N": "𝒩",
    "O": "𝒪", "P": "𝒫", "Q": "𝒬", "R": "ℛ", "S": "𝒮", "T": "𝒯", "U": "𝒰",
    "V": "𝒱", "W": "𝒲", "X": "𝒳", "Y": "𝒴", "Z": "𝒵",
}

MATHBB = {
    "A": "𝔸", "B": "𝔹", "C": "ℂ", "D": "𝔻", "E": "𝔼", "F": "𝔽", "G": "𝔾",
    "H": "ℍ", "I": "𝕀", "J": "𝕁", "K": "𝕂", "L": "𝕃", "M": "𝕄", "N": "ℕ",
    "O": "𝕆", "P": "ℙ", "Q": "ℚ", "R": "ℝ", "S": "𝕊", "T": "𝕋", "U": "𝕌",
    "V": "𝕍", "W": "𝕎", "X": "𝕏", "Y": "𝕐", "Z": "ℤ",
}

# Combining diacritics for accents over a single base char.
ACCENTS = {
    "hat": "̂", "widehat": "̂", "bar": "̄", "overline": "̄",
    "vec": "⃗", "dot": "̇", "ddot": "̈", "tilde": "̃",
    "widetilde": "̃", "check": "̌", "acute": "́", "grave": "̀",
}

VULGAR = {
    ("1", "2"): "½", ("1", "3"): "⅓", ("2", "3"): "⅔", ("1", "4"): "¼",
    ("3", "4"): "¾", ("1", "5"): "⅕", ("1", "6"): "⅙", ("1", "8"): "⅛",
}

SUPERSCRIPT_MAP = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶",
    "7": "⁷", "8": "⁸", "9": "⁹", "+": "⁺", "-": "⁻", "=": "⁼", "(": "⁽",
    ")": "⁾", "n": "ⁿ", "i": "ⁱ", "a": "ᵃ", "b": "ᵇ", "c": "ᶜ", "d": "ᵈ",
    "e": "ᵉ", "f": "ᶠ", "g": "ᵍ", "h": "ʰ", "j": "ʲ", "k": "ᵏ", "l": "ˡ",
    "m": "ᵐ", "o": "ᵒ", "p": "ᵖ", "r": "ʳ", "s": "ˢ", "t": "ᵗ", "u": "ᵘ",
    "v": "ᵛ", "w": "ʷ", "x": "ˣ", "y": "ʸ", "z": "ᶻ",
}
SUBSCRIPT_MAP = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅", "6": "₆",
    "7": "₇", "8": "₈", "9": "₉", "+": "₊", "-": "₋", "=": "₌", "(": "₍",
    ")": "₎", "a": "ₐ", "e": "ₑ", "h": "ₕ", "i": "ᵢ", "j": "ⱼ", "k": "ₖ",
    "l": "ₗ", "m": "ₘ", "n": "ₙ", "o": "ₒ", "p": "ₚ", "r": "ᵣ", "s": "ₛ",
    "t": "ₜ", "u": "ᵤ", "v": "ᵥ", "x": "ₓ",
}
SUPERSCRIPT = str.maketrans(SUPERSCRIPT_MAP)
SUBSCRIPT = str.maketrans(SUBSCRIPT_MAP)

_FALLBACK_ENVS = re.compile(r"\\begin\{(?:[bvBpV]?matrix|array|cases|aligned|alignedat|split|gathered)\b")

_MATH_CMD = re.compile(
    r"\\(?:frac|dfrac|tfrac|sqrt|sum|int|prod|lim|nabla|partial|hbar|infty|"
    r"alpha|beta|gamma|delta|theta|lambda|mu|omega|sigma|phi|psi|pi|rho|"
    r"cdot|times|pm|leq|geq|neq|approx|rightarrow|to|mathbf|mathrm|mathcal|"
    r"mathbb|vec|hat|bar|dot|left|right|begin|text)\b"
)


def contains_latex_math(text: str) -> bool:
    """Heuristic: does the text contain LaTeX-style math worth converting?"""
    value = text or ""
    if "$$" in value or "\\[" in value or "\\(" in value:
        return True
    if _MATH_CMD.search(value):
        return True
    # Paired single-dollar inline math with non-space content.
    return bool(re.search(r"(?<![\\$])\$(?!\s)(?:[^$\n]|\\\$){1,400}?(?<!\\)\$(?!\$)", value))


# --- public entry ---------------------------------------------------------

def render_math_for_feishu(text: str) -> str:
    """Convert LaTeX math in *text* to inline Unicode, preserving Markdown.

    Display math ($$...$$ / \\[...\\]) becomes its own line; inline math
    ($...$ / \\(...\\)) is converted in place. Fenced code blocks are left
    verbatim. Anything that cannot be faithfully rendered keeps its TeX source.
    """
    if not contains_latex_math(text):
        return text or ""

    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if _is_fence(line):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        if in_fence:
            out.append(line)
            i += 1
            continue

        block, next_i = _consume_display(lines, i)
        if block is not None:
            rendered = _render_display(block)
            if out and out[-1].strip():
                out.append("")
            out.extend(rendered)
            out.append("")
            i = next_i
            continue

        out.append(_render_inline_line(line))
        i += 1

    return "\n".join(out).strip("\n")


def _is_fence(line: str) -> bool:
    s = line.strip()
    return s.startswith("```") or s.startswith("~~~")


def _consume_display(lines: list[str], start: int) -> tuple[str | None, int]:
    stripped = lines[start].strip()
    for opener, closer in (("$$", "$$"), ("\\[", "\\]")):
        if not stripped.startswith(opener):
            continue
        rest = stripped[len(opener):]
        if rest.endswith(closer) and len(rest) >= len(closer):
            inner = rest[: len(rest) - len(closer)]
            return inner.strip(), start + 1
        collected = [rest] if rest else []
        j = start + 1
        while j < len(lines):
            s = lines[j].rstrip()
            if s.strip().endswith(closer):
                before = s[: s.rfind(closer)]
                if before.strip():
                    collected.append(before)
                return "\n".join(collected).strip(), j + 1
            collected.append(lines[j])
            j += 1
        return "\n".join(collected).strip(), j
    return None, start


def _render_display(block: str) -> list[str]:
    rows = re.split(r"\\\\", block)
    out: list[str] = []
    for row in rows:
        row = row.strip()
        if not row:
            continue
        converted, ok = _convert_expr(row)
        out.append(converted if ok else f"$$ {row} $$")
    return out or [""]


def _looks_mathy(s: str) -> bool:
    """Guard against converting non-math ``$...$`` spans (e.g. prices)."""
    s = (s or "").strip()
    if not s:
        return False
    if "\\" in s or re.search(r"[\^_]", s):
        return True
    if re.search(r"[A-Za-z0-9)\]][\s]*[=+\-*/<>][\s]*[A-Za-z0-9(\[\\]", s):
        return True
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,2}", s))


def _render_inline_line(line: str) -> str:
    result: list[str] = []
    i = 0
    n = len(line)
    while i < n:
        if line.startswith("\\(", i):
            end = line.find("\\)", i + 2)
            if end != -1:
                inner = line[i + 2:end]
                converted, ok = _convert_expr(inner)
                result.append(converted if ok else f"\\({inner}\\)")
                i = end + 2
                continue
        if line.startswith("$$", i) and not _escaped(line, i):
            end = line.find("$$", i + 2)
            if end != -1 and end > i + 2:
                inner = line[i + 2:end]
                converted, ok = _convert_expr(inner)
                result.append(converted if ok else f"$${inner}$$")
                i = end + 2
                continue
        if line[i] == "$" and not _escaped(line, i) and not line.startswith("$$", i):
            end = _next_dollar(line, i + 1)
            if end != -1 and end > i + 1:
                inner = line[i + 1:end]
                if _looks_mathy(inner):
                    converted, ok = _convert_expr(inner)
                    result.append(converted if ok else f"${inner}$")
                    i = end + 1
                    continue
        result.append(line[i])
        i += 1
    return "".join(result)


def _next_dollar(text: str, start: int) -> int:
    i = start
    while i < len(text):
        if text[i] == "$" and not _escaped(text, i):
            return i
        i += 1
    return -1


def _escaped(text: str, idx: int) -> bool:
    n = 0
    i = idx - 1
    while i >= 0 and text[i] == "\\":
        n += 1
        i -= 1
    return n % 2 == 1


# --- core expression converter -------------------------------------------

def _convert_expr(expr: str) -> tuple[str, bool]:
    """Convert one math expression to Unicode.

    Returns ``(text, ok)``. ``ok`` is False when the expression contains
    constructs we refuse to fake (matrices/cases/array) — callers then keep the
    TeX source.
    """
    value = (expr or "").strip()
    if not value:
        return "", True
    if _FALLBACK_ENVS.search(value):
        return value, False

    # spacing + delimiters
    value = re.sub(r"\\(?:quad|qquad)", " ", value)
    value = re.sub(r"\\[,;:!> ]", " ", value)
    value = value.replace("\\left", "").replace("\\right", "")
    value = value.replace("\\notag", "").replace("\\nonumber", "")
    value = value.replace("\\displaystyle", "").replace("\\limits", "")

    # braced commands (recursive): \frac, \sqrt, accents, styles, \text
    value = _replace_braced(value)

    # \sqrt[n]{x} handled in _replace_braced; standalone surd symbol
    value = value.replace("\\sqrt", "√")

    # named commands -> unicode (greek + symbols); longest names first
    value = _replace_commands(value)

    # scripts ^ and _
    value = _replace_scripts(value)

    # leftover braces and cleanup
    value = value.replace("{", "").replace("}", "")
    value = re.sub(r"[ \t]+", " ", value).strip()
    return value, True


def _parse_group(s: str, start: int) -> tuple[str, int]:
    """Given s[start] == '{', return (inner, index_after_closing_brace)."""
    assert s[start] == "{"
    depth = 0
    i = start
    while i < len(s):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return s[start + 1:i], i + 1
        i += 1
    return s[start + 1:], len(s)  # unbalanced: take the rest


def _next_token(s: str, i: int) -> tuple[str, int]:
    """Read a single script/arg token starting at i: {group}, \\cmd, or 1 char."""
    if i >= len(s):
        return "", i
    if s[i] == "{":
        inner, j = _parse_group(s, i)
        return inner, j
    if s[i] == "\\":
        m = re.match(r"\\[A-Za-z]+", s[i:])
        if m:
            return s[i:i + m.end()], i + m.end()
        return s[i:i + 2], i + 2
    return s[i], i + 1


def _replace_braced(value: str) -> str:
    out: list[str] = []
    i = 0
    n = len(value)
    while i < n:
        if value[i] == "\\":
            m = re.match(r"\\([A-Za-z]+)", value[i:])
            if m:
                name = m.group(1)
                j = i + m.end()
                # \frac{a}{b}, \dfrac, \tfrac
                if name in ("frac", "dfrac", "tfrac") and j < n and value[j] == "{":
                    num, j = _parse_group(value, j)
                    if j < n and value[j] == "{":
                        den, j = _parse_group(value, j)
                        out.append(_format_fraction(num, den))
                        i = j
                        continue
                # \sqrt[n]{x} or \sqrt{x}
                if name == "sqrt":
                    root = ""
                    if j < n and value[j] == "[":
                        k = value.find("]", j)
                        if k != -1:
                            root = value[j + 1:k]
                            j = k + 1
                    if j < n and value[j] == "{":
                        inner, j = _parse_group(value, j)
                        rinner = _replace_braced(inner)
                        pre = _to_superscript(root) if root else ""
                        out.append(f"{pre}√({rinner})")
                        i = j
                        continue
                # accents \hat{x} etc.
                if name in ACCENTS and j < n and value[j] == "{":
                    inner, j = _parse_group(value, j)
                    out.append(_apply_accent(name, _replace_braced(inner)))
                    i = j
                    continue
                # styles
                if name in ("mathbf", "boldsymbol", "bm", "mathrm", "text",
                            "operatorname", "mathcal", "mathbb", "mathit", "mathsf") and j < n and value[j] == "{":
                    inner, j = _parse_group(value, j)
                    out.append(_apply_style(name, inner))
                    i = j
                    continue
        out.append(value[i])
        i += 1
    return "".join(out)


def _format_fraction(num: str, den: str) -> str:
    num_r = _replace_braced(num).strip()
    den_r = _replace_braced(den).strip()
    if (num_r, den_r) in VULGAR:
        return VULGAR[(num_r, den_r)]
    num_s = num_r if _atomic(num_r) else f"({num_r})"
    den_s = den_r if _atomic(den_r) else f"({den_r})"
    return f"{num_s}/{den_s}"


def _atomic(s: str) -> bool:
    """A single number or single symbol — safe in a fraction without parens."""
    return bool(re.fullmatch(r"\d+|[A-Za-zα-ωΑ-Ω∂ℏ]", s or ""))


def _apply_accent(name: str, inner: str) -> str:
    inner = inner.strip()
    if len(inner) == 1:
        return inner + ACCENTS[name]
    if name in ("bar", "overline"):
        return f"‾{inner}"  # overline-ish prefix for multi-char
    if name == "vec":
        return inner + ACCENTS["vec"]
    return inner + ACCENTS[name]


def _apply_style(name: str, inner: str) -> str:
    inner_r = _replace_braced(inner)
    if name in ("text", "mathrm", "operatorname", "mathsf"):
        return inner_r
    if name == "mathcal":
        return "".join(MATHCAL.get(c, c) for c in inner_r)
    if name == "mathbb":
        return "".join(MATHBB.get(c, c) for c in inner_r)
    # mathbf / boldsymbol / bm / mathit -> keep plain (Unicode bold italic is noisy)
    return inner_r


_CMD_RE = re.compile(r"\\([A-Za-z]+)")


def _replace_commands(value: str) -> str:
    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name in GREEK:
            return GREEK[name]
        if name in SYMBOLS:
            return SYMBOLS[name]
        # unknown command: drop the backslash, keep the name (best effort)
        return name

    return _CMD_RE.sub(repl, value)


def _replace_scripts(value: str) -> str:
    out: list[str] = []
    i = 0
    n = len(value)
    while i < n:
        ch = value[i]
        if ch in "^_" and i + 1 < n:
            token, j = _next_token(value, i + 1)
            token = _replace_scripts(_replace_braced(token))
            if ch == "^":
                out.append(_to_superscript(token))
            else:
                out.append(_to_subscript(token))
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _to_superscript(token: str) -> str:
    if token and all(c in SUPERSCRIPT_MAP for c in token):
        return token.translate(SUPERSCRIPT)
    return f"^({token})" if len(token) > 1 else f"^{token}"


def _to_subscript(token: str) -> str:
    if token and all(c in SUBSCRIPT_MAP for c in token):
        return token.translate(SUBSCRIPT)
    return f"_({token})" if len(token) > 1 else f"_{token}"
