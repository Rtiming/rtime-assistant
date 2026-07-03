# ustc-kb 路线图 / 待办

## 已完成（2026-06-20）
- 确定性脚本流水线：`crawl` / `extract(notes)` / `audit` / `assemble` / `files` / `worklog` / `cli`（抓取零 LLM token）
- CMS 适配器：`siyuan`（/cNNNNaNNNN/page.htm，多数部处）、`teach`（教务 /service/svc-*/N.html）、`lib`（图书馆 WordPress 中文 permalink）
- 6 个部门入库 `brain/knowledge/institutions/ustc/`：教务 / 财务 / 保卫 / 团委 / 学工 / 图书馆 = 217 笔记 + 252 原始文件
- `find-file <名字>` 按名取原始文件（审批表 / 表格 / 流程图）→ 本地路径，供 rtime 助手取来发用户

## 待办 · Edge 登录批次（计划中，之后做）
以下站点**静态脚本够不到**，需 Edge 登录态 / 浏览器渲染，单列处理：
- **研究生院** gradschool.ustc.edu.cn —— `/column/N` 是 JS 渲染（前端取数），需浏览器或其 JSON API
- **招生就业** job.ustc.edu.cn —— ASP.NET（.aspx），列表页多为栏目导航、文章详情藏更深，需补 aspx 适配器或浏览器
- **网络信息中心** ustcnet —— 服务指南是嵌套子栏目，当前仅抓到顶层，需深一层遍历
- 财务等站的 **CAS 登录墙**在线申请项（信息发布 / 公众号推送等）—— 需登录态

## 其他待办
- `benke`（本科生院）= 教务处镜像，已按来源 URL 去重、未单列；如需其独有内容再补
- 通知公告（必带 发布日期 + 时限 deadline，做冲突交叉验证）—— 规划中的下一类内容
- 装饰图过滤已加；个别站点 logo 命名特殊可继续调阈值/关键词

## 怎么加一个新部门 / 新站点
1. 在 `src/ustc_kb/sites.json` 的 `departments` 加一条：`name/base/topic/contact/columns`（每栏目带 `cms` 类型）
2. 若是新 CMS，在 `cms.py` 的 `LINK_PATTERNS` 加 link 模式（必要时补 `parse_article` 选择器）
3. `python -m ustc_kb crawl <id>` → `audit` → `assemble`

## 数据落点（已入库）
brain（orangepi `<brain-root>`）`/knowledge/institutions/ustc/`：
- `<topic>/<dept>_<标题>.md` 笔记
- `sources/files/<dept>/...` 原始文件
- `_master-index.md` / `_contacts.md` / `_files_index.jsonl`

本地工作副本：`~/Desktop/ustc-kb-data`（环境变量 `USTC_KB_DATA` 可改）。
注意 brain 是 SMB 库、不同步到各设备、不进 Obsidian（见 brain `_meta/使用指南.md`）。
