# ustc-kb

中国科学技术大学校内**公开**资料的抓取 / 归档 / 索引模块。确定性脚本流水线，**抓取零 LLM**；
原始文件落地归档并可按名检索，供 rtime 助手取用并发送给用户。覆盖：10 个手工核心部处 +
自动发现的 51 学院 / 40 管理机构 / 9 科研机构 + 独立站种子（共 100+ 单位）。

## 设计原则
- **脚本优先**：USTC 站点是少数几种统一 CMS，确定性解析，不一页一个 agent。
- **原始信息无罪**：原始 HTML 存 `sources/`、原始文件存 `files/`，永久留底再结构化。
- **忠实优先**：笔记保全页面真实内容，不硬套模板。
- **记录留痕**：`sites.json`/`colleges.json` 站点清单、`WORKLOG.md`、每部门 `data/<dept>.jsonl` 台账。
- **质量门**：确定性 `audit`（去重 / 校准隐私扫描 / 结构 / 覆盖）优先于模型自报。
- **公开/个人分流**：公开资料进 `knowledge/`；个人数据（成绩/选课/财务，需登录）按规范走 `personal-data/`（照抓、换区存，不省略）。

## 数据落点（仓库外，`USTC_KB_DATA`，默认 `~/Desktop/ustc-kb-data`；runtime 主机可用 `/var/lib/rtime-assistant/ustc-kb-data`）
```
sources/<dept>/<slug>.html                                   原始HTML
files/<dept>/<name>                                          原始附件（按sha256去重）
notes/knowledge/institutions/ustc/<topic>/<dept>_<slug>.md   忠实笔记（镜像 brain 结构）
  topic: 手工部处=procedures/academics/...; 学院=colleges/<sub>; 管理机构=orgs/<sub>; 科研=research/<sub>
data/<dept>.jsonl                                            每部门抓取台账（增量据此跳过）
index/files_index.jsonl / master-index.md / contacts.md      文件索引 + 总索引 + 联系表
WORKLOG.md / worklog.jsonl                                   工作记录
```

## 用法
```
# 站点 / 发现
python -m ustc_kb sites                       # 手工部处清单(sites.json)
python -m ustc_kb colleges-discover           # 联网发现学院/管理机构/科研机构 -> colleges.json

# 抓取（depts 可填具体 id，或关键词）
python -m ustc_kb crawl baowei                # 单个
python -m ustc_kb crawl all                   # 全部手工部处
python -m ustc_kb crawl colleges|orgs|research|units   # 学院/管理机构/科研/全部自动单位
python -m ustc_kb crawl <ids...> --incremental --workers 8   # 增量 + 条目级并发(默认8)
python -m ustc_kb crawl baowei --since 2026-06-01 --limit 20 # 按日期截 / 限量
python -m ustc_kb crawl-job                    # 就业处(ahbys JSON-API，独立流程)

# 质量 / 索引 / 取用
python -m ustc_kb audit                        # 去重/隐私/结构/覆盖
python -m ustc_kb assemble                     # 总索引 + 联系总表
python -m ustc_kb find-file 无犯罪记录          # 按名找已归档原始文件 -> 本地路径
```

## 站点来源与 CMS 适配
- **手工核心部处**：`src/ustc_kb/sites.json`（基址 / topic / 公开联系方式 / 栏目 + cms）。
- **自动发现**：`src/ustc_kb/colleges.py` → `colleges.json`。来源：`yxjs.htm`(学院,kind=college)、
  `xxgk/gljg.htm`(管理机构,kind=admin)、`_RESEARCH_SEED`(科研机构,kind=research)、`_SEED_UNITS`(注册页外独立站)。
  按子域名去重、跳过手工部处。**新增独立站往 `_SEED_UNITS`/`_RESEARCH_SEED` 加。**
- **CMS 适配**（`cms.py` + `colleges.discover_columns` 自动识别）：
  - `siyuan` `/cNNNNaNNNN/page.htm` + `/col/list.htm`（多数）；列页用 `/col/main.htm` 的会归一到 list.htm。
  - `teach` 教务 `/service/svc-*/N.html`；`lib` 图书馆中文 permalink。
  - `wordpress` `wp-content`→分类法 `/(news-)category/`（如 oic 国合部）。
  - `column` `/column/N` + `/article/N`，`/column/N_k` 翻页（如 gradschool 研究生院）。
  - `ahbys` 厂商 JSON-API（就业处 `job.py`，需 XHR 头）。
- **结构页捕获**：栏目首页本身是内容页（组织架构/现任领导/概况）时，正文≥200字或含图就单独建笔记。

## 并发提速（三层；瓶颈是网络 I/O + USTC WAF，非 CPU）
1. **条目级**：`crawl_dept` 用线程池并发抓正文+下附件+写笔记（`--workers`/`USTC_KB_WORKERS`，默认8）。
2. **栏目枚举级**：多栏目并发枚举（`_enum_column`）。
3. **进程级 fan-out**：`unit_ids()` round-robin 切 N 组，N 个 `crawl <ids> --incremental` 进程并行。
**安全甜点 ≈ 20 聚合并发**（如 5进程×4workers）；超过 ~24 会触发瞬时 403，但 403 不进台账 → 增量自动重试自愈。

## 入库 brain（在 orangepi 本机，暂存与 brain 同盘）
```
python -m ustc_kb assemble
python ../../scripts/publish_ustc_to_brain.py --staging $USTC_KB_DATA --brain <brain-root>
python ../../scripts/ocr_attachments.py --root <brain>/.../sources/files --files-index <brain>/.../_files_index.jsonl
bash ../../scripts/rebuild-brain-index.sh
```
- `publish_ustc_to_brain.py`：笔记/文件 add-only rsync 进 brain，合并 `_files_index`（保留教务通知附件/jw 等其他管线条目）+ 备份。
- `ocr_attachments.py`：PDF/Office/图片 → 同目录旁车 `.md`（pdftotext/ocrmypdf/pandoc/soffice/tesseract，图片默认跳过 `--images` 才做），让检索命中文件内容；`--files-index` 给旁车配可读标题。
- 重建索引后 brain-library 混合检索（BM25+向量）即可命中全部笔记 + 附件正文。

## 已知缺口（栏目0 / 待适配）
- **wbtree `/info` CMS**（宣传部 xcb / 纪检 jjjc / lfo + 主站 www 新闻）：列表藏 JS，需浏览器抓真实请求逆向。
- **SPA**（ceni 未来网络 / iat 先进技术院）需找 JSON API；**aspx 壳**（hr）；**实验室目录门户**（institution）；
  **jsp+登录**（aga 校友会 / ef 基金会）；偶发站点下线（tj 体育中心 502）。
- **个人数据**（jw 成绩/选课、财务、i.ustc）需用户登录会话，存 `personal-data/`。
- nsrl 国家同步辐射 / hfnl 微尺度（返 HTTPError）可后补进 `_RESEARCH_SEED`。
