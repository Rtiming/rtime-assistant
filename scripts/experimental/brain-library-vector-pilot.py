#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""第二档 pilot：sqlite-vec/向量 + RRF 混合检索 vs 纯 BM25。
用 bge-small-zh 量化 ONNX 嵌入文档(标题+正文片段)与查询，对改写/同义查询对比召回。"""
import glob
import os
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

HF = os.path.expanduser("~/.cache/huggingface/hub")


def _find(pat):
    g = glob.glob(os.path.join(HF, "**", pat), recursive=True)
    return g[0] if g else None


MODEL = _find("model_quantized.onnx")
TOKJSON = _find("tokenizer.json")
assert MODEL and TOKJSON, ("model/tokenizer not found", MODEL, TOKJSON)

tok = Tokenizer.from_file(TOKJSON)
tok.enable_truncation(max_length=256)
sess = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])
INAMES = {i.name for i in sess.get_inputs()}


def embed(texts, batch=32):
    out = []
    for i in range(0, len(texts), batch):
        encs = [tok.encode(t) for t in texts[i:i + batch]]
        ml = max(len(e.ids) for e in encs)
        ids = np.array([e.ids + [0] * (ml - len(e.ids)) for e in encs], dtype=np.int64)
        am = np.array([e.attention_mask + [0] * (ml - len(e.attention_mask)) for e in encs], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": am}
        if "token_type_ids" in INAMES:
            feed["token_type_ids"] = np.zeros_like(ids)
        last = sess.run(None, feed)[0]
        emb = last[:, 0]  # bge: CLS pooling
        emb = emb / (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9)
        out.append(emb.astype(np.float32))
    return np.vstack(out)


con = sqlite3.connect("/tmp/idx_perf.db")
con.row_factory = sqlite3.Row
rows = con.execute("SELECT id, path, title, substr(body,1,256) AS snip FROM documents").fetchall()
docs = [(r["path"], (r["title"] or "") + " " + (r["snip"] or "")) for r in rows]
print("docs", len(docs), flush=True)
t0 = time.time()
mat = embed([d[1] for d in docs])
print("embedded %d in %.1fs (%.1f doc/s)" % (len(docs), time.time() - t0, len(docs) / (time.time() - t0)), flush=True)

sys.path.insert(0, os.path.expanduser("~/Desktop/rtime-assistant/packages/brain-library/src"))
from brain_library import indexer  # noqa: E402

QPREFIX = "为这个句子生成表示以用于检索相关文章："
QUERIES = [
    "创新创业项目能拿多少经费",
    "什么时候开始选课",
    "怎么转专业",
    "本科生奖学金怎么评",
    "应用物理学要学哪些数学课",
]


def rrf(rank_lists, k=60):
    score = {}
    for rl in rank_lists:
        for rank, key in enumerate(rl):
            score[key] = score.get(key, 0.0) + 1.0 / (k + rank + 1)
    return sorted(score, key=lambda x: -score[x])


def short(p):
    return p.split("/")[-1].replace(".md", "")[:26]


for q in QUERIES:
    bm = indexer.query_index(Path("/tmp/idx_perf.db"), q, limit=8)
    bm_paths = [r["path"] for r in bm["results"]]
    qv = embed([QPREFIX + q])[0]
    sims = mat @ qv
    vec_paths = [docs[i][0] for i in np.argsort(-sims)[:8]]
    hyb = rrf([bm_paths, vec_paths])[:8]
    print("\n=== %s ===" % q, flush=True)
    print("BM25:", [short(p) for p in bm_paths[:5]], flush=True)
    print("Vec :", [short(p) for p in vec_paths[:5]], flush=True)
    print("RRF :", [short(p) for p in hyb[:5]], flush=True)
