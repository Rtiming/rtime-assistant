#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Hybrid retrieval spike: FTS5(BM25) + sqlite-vec + bge-small-zh, RRF fusion.

Reference implementation for exec-02c (docs/memory-loop.zh-CN.md section 6).
Self-contained: builds a throwaway index over built-in Chinese samples,
then answers a query both lexically and semantically and fuses with RRF.

Run (Mac or orangepi; first run downloads the 0.09GB ONNX model):
  HF_ENDPOINT=https://hf-mirror.com python3 vector_spike.py --query 电子比热怎么算
Expected: the synonym sample (电子热容) is missed by BM25 but recovered by
the vector channel — that is the value the vector layer must prove before
it earns a place in production (see startup conditions in memory-loop doc).
"""

from __future__ import annotations

import argparse
import sqlite3
import struct
import time

SAMPLES = [
    ("mem-001", "用户在固体物理复习中偏好先看页图再读公式转写，公式拿不准时回看PDF原页。"),
    ("mem-002", "电子热容在低温下与温度成正比，系数正比于费米面附近态密度。"),
    ("mem-003", "声子热容的德拜模型在低温区给出T三次方定律。"),
    ("mem-004", "用户的期末考试集中在六月底到七月初，复习以历年试卷为主线。"),
    ("mem-005", "布里渊区是倒易点阵的维格纳-赛茨原胞，用于描述能带结构。"),
    ("mem-006", "仿星器HTS线圈的绕线路径规划是用户的科研方向之一。"),
    ("mem-007", "晶格振动的简正坐标变换把耦合振子化为独立振子，是态密度计算的基础。"),
    ("mem-008", "用户要求所有AI生成的笔记标注generated_by和日期，公式不确定要标needs_review。"),
]


def serialize_f32(vec) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def rrf(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: -kv[1])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default="电子比热怎么算")
    parser.add_argument("--model", default="BAAI/bge-small-zh-v1.5")
    parser.add_argument("--top", type=int, default=4)
    args = parser.parse_args()

    import sqlite_vec
    from fastembed import TextEmbedding

    t0 = time.time()
    model = TextEmbedding(model_name=args.model)
    t_load = time.time() - t0

    t0 = time.time()
    passage_vecs = list(model.embed([text for _, text in SAMPLES]))
    t_embed = time.time() - t0
    dim = len(passage_vecs[0])

    db = sqlite3.connect(":memory:")
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)

    db.execute("CREATE VIRTUAL TABLE fts USING fts5(id UNINDEXED, body)")
    db.execute(f"CREATE VIRTUAL TABLE vec USING vec0(id TEXT PRIMARY KEY, embedding float[{dim}])")
    for (doc_id, text), v in zip(SAMPLES, passage_vecs):
        db.execute("INSERT INTO fts(id, body) VALUES (?, ?)", (doc_id, text))
        db.execute("INSERT INTO vec(id, embedding) VALUES (?, ?)", (doc_id, serialize_f32(v)))

    # --- lexical channel (token AND-match degrades to OR on zero hits) ---
    tokens = [t for t in args.query.replace("？", " ").replace("?", " ").split() if t] or [args.query]
    fts_query = " OR ".join(f'"{t}"' for t in tokens)
    bm25_rows = db.execute(
        "SELECT id FROM fts WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT ?",
        (fts_query, args.top),
    ).fetchall()
    bm25_ids = [r[0] for r in bm25_rows]

    # --- semantic channel ---
    t0 = time.time()
    qvec = list(model.query_embed(args.query))[0]
    t_query = time.time() - t0
    vec_rows = db.execute(
        "SELECT id, distance FROM vec WHERE embedding MATCH ? AND k = ? ORDER BY distance",
        (serialize_f32(qvec), args.top),
    ).fetchall()
    vec_ids = [r[0] for r in vec_rows]

    fused = rrf([bm25_ids, vec_ids])
    texts = dict(SAMPLES)

    print(f"query: {args.query}")
    print(f"model load {t_load:.1f}s | embed {len(SAMPLES)} passages {t_embed:.2f}s "
          f"({t_embed/len(SAMPLES)*1000:.0f}ms/条, dim={dim}) | query embed {t_query*1000:.0f}ms")
    print(f"\nBM25通道:   {bm25_ids or '（零命中）'}")
    print(f"向量通道:   {[(i, round(d, 3)) for i, d in vec_rows]}")
    print("\nRRF融合结果:")
    for doc_id, score in fused[: args.top]:
        channel = ("both" if doc_id in bm25_ids and doc_id in vec_ids
                   else "bm25" if doc_id in bm25_ids else "vec")
        print(f"  {score:.4f} [{channel}] {doc_id}: {texts[doc_id]}")


if __name__ == "__main__":
    main()
