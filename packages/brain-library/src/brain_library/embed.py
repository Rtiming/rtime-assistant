# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Pluggable text-embedding backends for brain-library vector search.

Design: the embedding model is *pluggable*. The retrieval architecture (sqlite-vec
KNN + RRF fusion in :mod:`indexer`) never imports a concrete model — it goes through
the :class:`Embedder` interface and :func:`get_embedder`. Switching from bge-small to
Qwen3 is a config change (env ``BRAIN_LIBRARY_EMBED_MODEL``) plus one re-embed; no
retrieval code changes.

Heavy deps (numpy / onnxruntime / tokenizers) are imported lazily so the module — and
the "is a model available?" probe — works on a host that lacks them. With no model files
or no deps, :func:`get_embedder` returns ``None`` and callers degrade to pure BM25.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np

DEFAULT_MODEL = "bge-small"
ENV_MODEL = "BRAIN_LIBRARY_EMBED_MODEL"
ENV_MODEL_DIR = "BRAIN_LIBRARY_EMBED_MODEL_DIR"
_HF_CACHE = Path("~/.cache/huggingface/hub").expanduser()


@dataclass(frozen=True)
class ModelSpec:
    """Static description of an ONNX embedding model.

    ``dim`` is the *stored* dimension after optional MRL truncation: bge-small emits
    512-d and keeps all of it; Qwen3-0.6B emits 1024-d and is truncated (MRL) to 256.
    """

    key: str
    pooling: str  # "cls" (bge) | "last" (qwen3, last non-pad token)
    query_prefix: str
    dim: int
    max_length: int
    hf_hint: str  # substring used to locate the model in the HF cache


_SPECS: dict[str, ModelSpec] = {
    "bge-small": ModelSpec(
        key="bge-small",
        pooling="cls",
        query_prefix="为这个句子生成表示以用于检索相关文章：",
        dim=512,
        max_length=256,
        hf_hint="bge-small-zh",
    ),
    "qwen3-0.6b": ModelSpec(
        key="qwen3-0.6b",
        pooling="last",
        # Qwen3 embeddings are instruction-aware; the retrieval task is encoded as an
        # instruction prefix on the query side only (documents are embedded raw).
        query_prefix=(
            "Instruct: Given a web search query, retrieve relevant passages that "
            "answer the query\nQuery: "
        ),
        # Full 1024-d. Local A/B (scripts/experimental/brain-library-model-compare.py)
        # showed MRL truncation to 256 noticeably hurt recall (R@1 0.97→0.83) while 512
        # already saturated; opting into the heavy model should keep its full quality.
        # Lower this (512 is as good here, smaller storage) only if vec storage matters.
        dim=1024,
        max_length=512,
        hf_hint="Qwen3-Embedding-0.6B",
    ),
}


class Embedder:
    """Embedding backend interface.

    ``embed(texts, is_query=False)`` returns an ``(n, dim)`` float32 array of
    L2-normalized row vectors. ``is_query=True`` applies the model's query-side
    instruction/prefix (asymmetric retrieval).
    """

    spec: ModelSpec

    @property
    def model_name(self) -> str:
        return self.spec.key

    @property
    def dim(self) -> int:
        return self.spec.dim

    def embed(self, texts: list[str], *, is_query: bool = False, batch: int = 32) -> "np.ndarray":  # noqa: D401
        raise NotImplementedError


class _OnnxEmbedder(Embedder):
    """ONNX-runtime embedder shared by all models; behavior driven by ``spec``."""

    def __init__(self, spec: ModelSpec, onnx_path: Path, tokenizer_path: Path):
        import onnxruntime as ort  # lazy: keep module importable without the dep
        from tokenizers import Tokenizer

        self.spec = spec
        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._tokenizer.enable_truncation(max_length=spec.max_length)
        self._session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        inputs = self._session.get_inputs()
        self._input_names = {i.name for i in inputs}
        # Decoder/KV-cache exports (e.g. the Qwen3-Embedding ONNX) need position_ids and
        # empty past_key_values fed alongside input_ids/attention_mask. Introspect the
        # graph so one code path drives both the plain encoder (bge) and these exports.
        # Each past key/value input has shape [batch, n_kv_heads, past_len, head_dim];
        # we feed an empty (past_len=0) tensor per such input.
        self._past_kv: list[tuple[str, int, int]] = []
        for inp in inputs:
            if inp.name.startswith("past_key_values"):
                shape = inp.shape
                n_heads = shape[1] if isinstance(shape[1], int) else 0
                head_dim = shape[3] if len(shape) > 3 and isinstance(shape[3], int) else 0
                self._past_kv.append((inp.name, n_heads, head_dim))
        self._needs_position_ids = "position_ids" in self._input_names
        # Prefer the token-level hidden states output by name; fall back to output 0
        # (true for bge, whose first output is last_hidden_state).
        out_names = [o.name for o in self._session.get_outputs()]
        self._hidden_output = next(
            (n for n in out_names if n in ("last_hidden_state", "token_embeddings", "last_hidden")),
            out_names[0],
        )

    def _pool(self, last_hidden: "np.ndarray", attention_mask: "np.ndarray") -> "np.ndarray":
        import numpy as np

        if self.spec.pooling == "cls":
            return last_hidden[:, 0]
        # last-token pooling: the last non-padding position of each row.
        lengths = attention_mask.sum(axis=1) - 1
        return last_hidden[np.arange(last_hidden.shape[0]), lengths]

    def embed(self, texts: list[str], *, is_query: bool = False, batch: int = 32) -> "np.ndarray":
        import numpy as np

        if not texts:
            return np.zeros((0, self.spec.dim), dtype=np.float32)
        if is_query and self.spec.query_prefix:
            texts = [self.spec.query_prefix + t for t in texts]
        out: list[np.ndarray] = []
        for start in range(0, len(texts), batch):
            chunk = texts[start : start + batch]
            encs = [self._tokenizer.encode(t) for t in chunk]
            max_len = max(len(e.ids) for e in encs)
            ids = np.array([e.ids + [0] * (max_len - len(e.ids)) for e in encs], dtype=np.int64)
            mask = np.array(
                [e.attention_mask + [0] * (max_len - len(e.attention_mask)) for e in encs],
                dtype=np.int64,
            )
            feed: dict[str, np.ndarray] = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._input_names:
                feed["token_type_ids"] = np.zeros_like(ids)
            if self._needs_position_ids:
                # 0-based positions for real tokens; cumsum handles either padding side
                # (pad positions are clipped to 0 and masked out by attention anyway).
                feed["position_ids"] = np.clip(np.cumsum(mask, axis=1) - 1, 0, None).astype(np.int64)
            for name, n_heads, head_dim in self._past_kv:
                feed[name] = np.zeros((ids.shape[0], n_heads, 0, head_dim), dtype=np.float32)
            outputs = self._session.run([self._hidden_output], feed)
            last_hidden = np.asarray(outputs[0])
            emb = self._pool(last_hidden, mask)
            # MRL truncation: take the first `dim` dims (no-op when dim == hidden size),
            # then normalize the truncated vector.
            emb = emb[:, : self.spec.dim]
            emb = emb / (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9)
            out.append(emb.astype(np.float32))
        return np.vstack(out)


class BgeSmallEmbedder(_OnnxEmbedder):
    """Xenova/bge-small-zh-v1.5 quantized ONNX (CLS pooling, 512-d, query prefix)."""

    def __init__(self, onnx_path: Path, tokenizer_path: Path):
        super().__init__(_SPECS["bge-small"], onnx_path, tokenizer_path)


class Qwen3Embedder(_OnnxEmbedder):
    """Qwen/Qwen3-Embedding-0.6B ONNX (last-token, instruction-aware, MRL→256-d)."""

    def __init__(self, onnx_path: Path, tokenizer_path: Path):
        super().__init__(_SPECS["qwen3-0.6b"], onnx_path, tokenizer_path)


_CLASSES: dict[str, Callable[[Path, Path], Embedder]] = {
    "bge-small": BgeSmallEmbedder,
    "qwen3-0.6b": Qwen3Embedder,
}

# Process-wide instance cache: loading an ONNX session is expensive, and the MCP
# server is long-lived, so reuse one embedder per model key. Only successful loads
# are cached, so a later install of the model files is still picked up.
_INSTANCES: dict[str, Embedder] = {}


def _default_models_dir() -> Path:
    """Convention path that ``scripts/fetch-embed-model.sh`` writes to, so a fetched
    model is found with no env var on any machine (mirrors ``default_index()``)."""
    state = Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser()
    return state / "rtime-assistant" / "brain-library" / "models"


def _tokenizer_near(onnx: Path) -> Path | None:
    """Find tokenizer.json belonging to the same model as ``onnx`` (walk up from the
    onnx file to its model root, e.g. <root>/onnx/model.onnx -> <root>/tokenizer.json).
    Scoping to the model root keeps bge and qwen3 from cross-wiring when both exist."""
    for parent in onnx.parents:
        cand = parent / "tokenizer.json"
        if cand.is_file():
            return cand
    return None


def _match_in_base(base: Path, spec: ModelSpec, *, require_hint: bool) -> tuple[Path | None, Path | None]:
    pattern_onnx = sorted(
        glob.glob(os.path.join(str(base), "**", "model_quantized.onnx"), recursive=True)
    ) or sorted(glob.glob(os.path.join(str(base), "**", "model.onnx"), recursive=True))
    for hit in pattern_onnx:
        if require_hint and spec.hf_hint.lower() not in hit.lower():
            continue
        onnx = Path(hit)
        tok = _tokenizer_near(onnx)
        if tok is not None:
            return onnx, tok
    return None, None


def _locate_files(spec: ModelSpec) -> tuple[Path | None, Path | None]:
    """Find ``(onnx, tokenizer.json)`` for a model.

    Search order: ``BRAIN_LIBRARY_EMBED_MODEL_DIR`` (explicit, no hint required) ->
    the default fetched-models dir (by convention) -> the Hugging Face cache. For the
    last two, the model dir/snapshot path must contain the model's ``hf_hint`` so the
    right model is picked when several are installed side by side.
    """
    env_dir = os.environ.get(ENV_MODEL_DIR)
    bases: list[tuple[Path, bool]] = []
    if env_dir:
        bases.append((Path(env_dir).expanduser(), False))
    bases.append((_default_models_dir(), True))
    if _HF_CACHE.is_dir():
        bases.append((_HF_CACHE, True))
    for base, require_hint in bases:
        if not base.is_dir():
            continue
        onnx, tok = _match_in_base(base, spec, require_hint=require_hint)
        if onnx and tok:
            return onnx, tok
    return None, None


def get_embedder(model_key: str | None = None) -> Embedder | None:
    """Return an :class:`Embedder`, or ``None`` if unavailable (→ degrade to BM25).

    ``model_key`` selects the backend; defaults to ``BRAIN_LIBRARY_EMBED_MODEL`` then
    ``bge-small``. Returns ``None`` (never raises) when the key is unknown, the model
    files are missing, the embedding deps are not installed, or the ONNX session fails
    to load — every caller treats ``None`` as "no vectors, use BM25".
    """
    key = (model_key or os.environ.get(ENV_MODEL) or DEFAULT_MODEL).strip()
    if key in _INSTANCES:
        return _INSTANCES[key]
    spec = _SPECS.get(key)
    cls = _CLASSES.get(key)
    if spec is None or cls is None:
        return None
    onnx_path, tokenizer_path = _locate_files(spec)
    if onnx_path is None or tokenizer_path is None:
        return None
    try:
        import numpy  # noqa: F401  (ensure the array dep is present before loading)
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
    except ImportError:
        return None
    try:
        embedder = cls(onnx_path, tokenizer_path)
    except Exception:  # pragma: no cover - corrupt/incompatible model files
        return None
    _INSTANCES[key] = embedder
    return embedder
