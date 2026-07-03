# scripts/brain-intake/ — 链路脚本目录（模块契约）

本目录是`docs/tasks/pipeline/`各模块的脚本落点。Codex按本契约实现；已实现并测试过的文件**不要重写**。

## 状态表（2026-06-12 run-12后更新）

| 文件 | 模块 | 状态 |
|---|---|---|
| `vector_spike.py` | M9/exec-02c参考实现 | 已实现并双机验证（见下） |
| `memory_schema.py` | M9记忆卡校验 | 已实现+8单测 |
| `intake_common.py` | 共用库 | **已实现（Codex run-01）** |
| `m0_triage.py` | M0分诊 | **已实现，run-01实跑：扫描1402项** |
| `m1_registry.py` | M1登记归位 | **已实现，run-01实跑：manifest 33→234全覆盖、2组四件套改名+132处引用重写零残留、67组重复sha标记、热统vault目录归档+symlink切换** |
| `m2_convert.sh` / `m2_convert.py` | M2转换 | **已实现**：run-01按scope跳过；run-02支持advanced-photonics的MinerU讲义整转、PDF页图/text层、PPT/PPTX转PDF+MarkItDown素材、伴生md与manifest补录 |
| `m3_frontmatter.py` | M3回填 | **已实现，run-01实跑：1126个md回填frontmatter，全库md_without_frontmatter=0** |
| `m4_link.py` / `m4_zotero.py` | M4互通 | **已实现**：`m4_link.py`按同一份view manifest维护Mac/Windows/Orange Pi本地vault视图入口和`.stignore`；课程资料默认`materialize`为真实同步目录，`brain_rels`可合并多个正本目录，`mode=absent`可安全退役错误入口，只有`sync:false`的`01 每日`等写入口保留本地symlink并写入`.stignore`；`--verify`验收Obsidian显示层，非`ok: true`视为未完成；zotero侧只dry-run对账（42KB CSV） |
| `m5_index.sh` | M5索引 | **已实现，run-01实跑**：本地+orangepi重建+三组查询留档 |
| `m6_validate.py` | M6校验 | **已实现，run-01实跑**：9项检查全绿（含改名零残留、vault红线、sha抽查） |
| `run02_thermal_holds.py` | R1遗留处置 | **已实现**：对run-01热统hold做sha/size/mtime/page诊断，输出resolved-by-archive与待用户拍板清单 |
| `intake_ticket.py` | run-12多入口收件/ticket | **已实现**：Obsidian/WebDAV/Feishu/CLI入口只生成`_inbox/<source>/<date>/` ticket与报告，不写最终knowledge/Zotero/memory |
| `m9_extract.py` | M9-L2提取器 | exec-02a实现 |
| `m10_relations.py` | run-17关系图谱 | **已实现**：生成`_indexes/relations.jsonl`，关系类型含wikilink/manifest-sibling/same-course/bm25-topic/citekey；apply时幂等刷新伴生md“相关材料”节 |

配套测试：`tests/test_brain_intake_pipeline.py`、`tests/test_memory_schema.py`、`tests/test_assistant_gateway.py`。
run-01产物与各模块报告：`work/pipeline/run-01/`（总报告自评通过，主助手2026-06-11审阅确认，41条热统同名异内容hold为正确行为，处置规则见EXECUTE的run-02节）。

## 全目录通用约束

1. 计划/执行两段式：所有改brain的脚本必须有`--plan`（只产计划JSON）与`--apply --approved-plan <path>`两个入口；无approved-plan拒绝破坏性动作。
2. 幂等：执行前按RUN.md状态判定表检查后置条件，已满足即跳过；重复apply同一计划安全。
3. 日志：机器日志JSON落`work/pipeline/<run-id>/`，不写brain、不进git；日志含动作流水可供回放回滚。
4. 路径：BRAIN_ROOT等一律环境变量/参数注入，不硬编码Mac或orangepi路径（默认值可给，两机皆可跑）。
5. 依赖：优先stdlib；需要三方包的（jieba/fastembed/sqlite-vec）在脚本头注释写明安装命令与已验证版本。
6. 退出码：0成功、1校验失败、2环境缺失；stderr只给人读，stdout给机器（JSON）。
7. 单测：每个`m*.py`至少配tests/test_<name>.py覆盖计划生成与安全拒绝路径，进repo标准pytest。

M4 Obsidian显示层额外要求：维护课程入口时必须执行
`m4_link.py --plan` → `m4_link.py --apply --approved-plan ...` →
`m4_link.py --verify`。`--verify`检查manifest、`.stignore`、真实vault目录、
受控入口名、派生/source-only目录过滤和待执行动作；返回非零时继续修复，不得向
用户报告“Obsidian已经好了”。

## vector_spike.py 双机验证记录（2026-06-11）

| 项 | Mac (Apple Silicon) | orangepi (aarch64) |
|---|---|---|
| 安装 | sqlite-vec 0.1.9+fastembed 0.8.0，pip直装成功 | 同版本，onnxruntime 1.23.2，aarch64 wheel直装成功 |
| 模型获取 | HF被墙→fastembed自动回退Qdrant源成功 | 同；首跑总耗时10.6秒含下载 |
| 嵌入速度 | 2ms/条（512维） | 13ms/条（1000块≈13秒，夜间增量无压力） |
| 同义召回证明 | 查询"电子比热"：BM25零命中，向量通道把"电子热容"卡排第一（距离0.799 vs 次名1.05） | 结果与Mac完全一致 |

结论：exec-02c技术栈无风险；运行时设`HF_ENDPOINT=https://hf-mirror.com`双保险。生产实现以本spike的serialize/vec0/RRF模式为基准，融合排序再叠recency×importance×access（见docs/memory-loop.zh-CN.md第六节）。

## m10_relations.py 关系图谱入口（run-17）

只写派生索引和生成节，不移动原件，不读`personal-data/`：

```bash
python3 scripts/brain-intake/m10_relations.py --plan \
  --brain-root <brain-root> \
  --run-dir ~/.local/state/rtime-assistant/pipeline/run-17 \
  --state-dir ~/.local/state/rtime-assistant/relations

python3 scripts/brain-intake/m10_relations.py --apply \
  --approved-plan ~/.local/state/rtime-assistant/pipeline/run-17/m10-relations-plan.json
```

查询出口：

```bash
python3 scripts/rtime-vault.py related knowledge/courses/example/lesson.md
```

夜间模板：`deploy/bin/rtime-relations-job` +
`deploy/systemd/user/rtime-relations-nightly.{service,timer}`。Kimi 或其他
agent 可执行同一入口；如果需要模型标注关系，只能在现有边表上追加派生证据，
不能把正文、secret 或 personal-data 写入仓库日志。
