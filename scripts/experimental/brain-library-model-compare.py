#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Head-to-head: bge-small vs Qwen3-0.6B for brain-library vector/hybrid search.

Builds one index per embedding model over a representative Chinese physics + USTC
campus-affairs corpus, runs the same paraphrase/synonym query set through bm25 /
vector / hybrid, and reports Recall@1, Recall@3, MRR@10, and latency. The query
wording deliberately avoids the gold doc's exact terms so lexical-only BM25 is
stressed and semantic recall is what differentiates the models.

Run (Mac, with both models fetched locally):
  BGE auto-detected from HF cache; point Qwen3 at its dir:
  QWEN3_DIR=/tmp/qwen3eval PYTHONPATH=packages/brain-library/src \
      python3 scripts/experimental/brain-library-model-compare.py
"""
import os
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "packages" / "brain-library" / "src"
sys.path.insert(0, str(SRC))
from brain_library import embed, indexer  # noqa: E402

# (filename stem, body). Title is the stem. ~40 docs with overlapping topics so
# distractors are real (multiple physics notes, multiple campus-affairs notices).
CORPUS = {
    # --- physics course notes ---
    "自由电子论": "自由电子论把金属中的价电子近似看作自由电子气，用于解释金属的电导和热导。",
    "布里渊区": "布里渊区是倒易空间中的基本原胞，用来描述能带结构和晶格的周期性。",
    "声子与热容": "晶格振动量子化为声子，德拜模型用声子谱解释低温固体的热容随温度的变化。",
    "能带论": "能带论由布洛赫定理出发，把电子在周期势场中的本征态写成布洛赫波，区分导体绝缘体半导体。",
    "费米面": "费米面是动量空间中费米能对应的等能面，决定金属的输运和低温性质。",
    "量子力学测不准": "海森堡不确定关系给出位置与动量不能同时精确测量的下限。",
    "薛定谔方程": "薛定谔方程描述量子态随时间的演化，定态方程给出能量本征值。",
    "氢原子能级": "氢原子的能级由主量子数决定，能量与主量子数平方成反比。",
    "热力学第二定律": "热力学第二定律指出孤立系统的熵不会自发减少，决定了过程的方向性。",
    "麦克斯韦方程组": "麦克斯韦方程组统一了电场与磁场，预言了电磁波的存在。",
    "狭义相对论": "狭义相对论基于光速不变原理，给出时间膨胀和长度收缩。",
    "角动量耦合": "多电子原子中轨道与自旋角动量耦合形成总角动量，决定光谱精细结构。",
    "黑体辐射": "普朗克为解释黑体辐射谱引入能量量子化，开启量子理论。",
    "晶体结构": "晶体由原胞按晶格平移对称排列，布拉伐格子描述其平移对称性。",
    "超导现象": "超导体在临界温度以下电阻为零并排斥磁场，BCS理论用库珀对解释常规超导。",
    # --- USTC campus affairs ---
    "选课时间": "每学期选课在开学前两周开始，分初选、抽签和补退选三个阶段。",
    "转专业流程": "本科生转专业需在学期初提交申请，由转入学院考核后办理学籍变动。",
    "奖学金评定": "本科生奖学金按学业成绩和综合表现综合评定，每学年评选一次。",
    "缓考申请": "因病或突发事件无法参加考试的，可在考前申请缓考并提交相关证明。",
    "学位授予": "授予学士学位须修满培养方案规定学分且平均绩点达到要求。",
    "大创项目经费": "大学生创新创业训练计划项目获得资助，每个项目经费一般为一到两万元。",
    "宿舍管理": "学生公寓实行门禁管理，调换宿舍须提交申请并经公寓中心审批。",
    "请假规定": "学生请假须履行请假手续，超过规定天数需由学院负责人审批。",
    "保研政策": "推荐免试攻读研究生依据综合排名确定资格，名额按学院分配。",
    "补考与重修": "考试不及格的课程可参加补考，补考仍不通过的须重修该课程。",
    "实验室安全": "进入实验室须完成安全培训并通过考核，危险化学品须登记领用。",
    "图书馆借阅": "本科生可借阅图书若干册，借期内可续借，逾期将暂停借阅权限。",
    "体测要求": "学生须参加每学年体质健康测试，成绩计入毕业要求。",
    "学费缴纳": "学生须在每学年开学前按规定标准缴纳学费，困难学生可申请缓缴。",
    "出国交流": "本科生可申请国家公派或校际交流项目赴境外高校学习一学期。",
    "课程重修登记": "重修须在重修选课阶段登记，重修成绩按实际取得记载。",
    "评教": "每学期期末学生须完成对所修课程的网上评教方可查询成绩。",
    "档案转递": "毕业生档案由学校按就业去向统一转递至接收单位。",
    "诚信考试": "考试违纪作弊将按学术诚信规定处理，情节严重者取消学位资格。",
    "心理咨询": "学校提供免费心理咨询服务，学生可预约心理健康中心面询。",
    "勤工助学": "家庭经济困难学生可申请校内勤工助学岗位获得报酬。",
    "学生医保": "在校学生纳入城镇居民基本医疗保险，就医可按比例报销。",
    "成绩复核": "学生对考试成绩有异议的，可在公布后规定期限内申请成绩复核。",
    "毕业设计": "毕业设计需在导师指导下完成并通过答辩方可获得相应学分。",
}

# (query, gold-doc-stem). Queries paraphrase / use synonyms / colloquial wording with
# little lexical overlap with the gold doc, to separate semantic from lexical recall.
QUERIES = [
    ("金属里的电子是怎么导电的", "自由电子论"),
    ("倒空间里描述能带周期性的区域叫什么", "布里渊区"),
    ("固体在低温下的比热怎么来的", "声子与热容"),
    ("为什么有的材料是导体有的是绝缘体", "能带论"),
    ("动量空间里区分金属性质的那个面", "费米面"),
    ("位置和动量为什么不能同时测准", "量子力学测不准"),
    ("描述微观粒子波函数随时间变化的方程", "薛定谔方程"),
    ("氢原子里电子能量是怎么分层的", "氢原子能级"),
    ("为什么热量不会自己从冷处流向热处", "热力学第二定律"),
    ("把电和磁统一起来的那组方程", "麦克斯韦方程组"),
    ("速度很快时时间会变慢吗", "狭义相对论"),
    ("普朗克为什么提出能量一份一份的", "黑体辐射"),
    ("电阻突然变成零还能排斥磁铁的现象", "超导现象"),
    ("什么时候可以开始抢课", "选课时间"),
    ("不想读现在的专业想换一个怎么办", "转专业流程"),
    ("成绩好能拿到什么钱的奖励", "奖学金评定"),
    ("生病了考不了试该怎么处理", "缓考申请"),
    ("拿到毕业证学位证需要满足什么条件", "学位授予"),
    ("做创新创业项目能拿多少钱", "大创项目经费"),
    ("想换个寝室住要走什么手续", "宿舍管理"),
    ("有事不能上课要怎么报备", "请假规定"),
    ("不用考试直接读研是怎么选上的", "保研政策"),
    ("挂科了还有没有补救机会", "补考与重修"),
    ("进实验室之前要做什么准备", "实验室安全"),
    ("书借了之后到期还想接着看怎么办", "图书馆借阅"),
    ("跑步测试不达标会影响毕业吗", "体测要求"),
    ("交不起钱上学能晚点交吗", "学费缴纳"),
    ("想去国外的大学读一学期", "出国交流"),
    ("觉得分数打错了能不能查卷", "成绩复核"),
    ("没钱的学生能在学校打工吗", "勤工助学"),
]


def _model_dir_env(model_key):
    if model_key == "qwen3-0.6b":
        return {"BRAIN_LIBRARY_EMBED_MODEL": "qwen3-0.6b",
                "BRAIN_LIBRARY_EMBED_MODEL_DIR": os.environ.get("QWEN3_DIR", "/tmp/qwen3eval")}
    return {"BRAIN_LIBRARY_EMBED_MODEL": "bge-small", "BRAIN_LIBRARY_EMBED_MODEL_DIR": ""}


def build_corpus(root):
    k = root / "knowledge"
    k.mkdir(parents=True)
    for stem, body in CORPUS.items():
        (k / f"{stem}.md").write_text(f"# {stem}\n{body}", encoding="utf-8")


def metrics(index, mode):
    ranks = []
    t0 = time.time()
    for q, gold in QUERIES:
        res = indexer.query_index(index, q, limit=10, mode=mode)
        paths = [Path(r["path"]).stem for r in res["results"]]
        rank = paths.index(gold) + 1 if gold in paths else 0
        ranks.append(rank)
    dt = (time.time() - t0) / len(QUERIES) * 1000
    n = len(ranks)
    r1 = sum(1 for r in ranks if r == 1) / n
    r3 = sum(1 for r in ranks if 1 <= r <= 3) / n
    mrr = sum((1.0 / r) for r in ranks if r) / n
    return r1, r3, mrr, dt


def run_model(model_key, tmp):
    for key, val in _model_dir_env(model_key).items():
        os.environ[key] = val
    embed._INSTANCES.clear()  # force re-resolve with the new env
    e = embed.get_embedder()
    if e is None:
        print(f"\n## {model_key}: SKIP (model not available)")
        return None
    root = tmp / model_key
    build_corpus(root)
    out = tmp / f"{model_key}.sqlite"
    t0 = time.time()
    build = indexer.build_index(root, out, force=True, embed=True)
    build_dt = time.time() - t0
    assert build["ok"] and build["embedded"], build
    doc_s = build["vectors_written"] / build_dt
    print(f"\n## {model_key}  (dim={e.dim}, {build['vectors_written']} docs embedded "
          f"in {build_dt:.1f}s = {doc_s:.0f} doc/s)")
    print(f"  {'mode':7} {'R@1':>6} {'R@3':>6} {'MRR@10':>7} {'q-latency':>10}")
    rows = {}
    for mode in ("bm25", "vector", "hybrid"):
        r1, r3, mrr, dt = metrics(out, mode)
        rows[mode] = (r1, r3, mrr, dt)
        print(f"  {mode:7} {r1:6.2f} {r3:6.2f} {mrr:7.3f} {dt:8.0f}ms")
    return rows


def main():
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    print(f"corpus: {len(CORPUS)} docs, {len(QUERIES)} paraphrase queries")
    results = {}
    for model_key in ("bge-small", "qwen3-0.6b"):
        rows = run_model(model_key, tmp)
        if rows:
            results[model_key] = rows
    if len(results) == 2:
        print("\n## Δ (qwen3 − bge), hybrid mode")
        b = results["bge-small"]["hybrid"]
        q = results["qwen3-0.6b"]["hybrid"]
        print(f"  R@1 {q[0]-b[0]:+.2f}  R@3 {q[1]-b[1]:+.2f}  MRR {q[2]-b[2]:+.3f}  "
              f"latency {q[3]-b[3]:+.0f}ms ({q[3]/max(b[3],1e-9):.1f}x)")


if __name__ == "__main__":
    main()
