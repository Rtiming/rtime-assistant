# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""ustc_kb —— 中科大校内事务公开资料抓取/归档/索引模块。

确定性流水线（零LLM抓取）：
  registry(sites.json 站点清单) -> crawl(抓列表+正文，存原始HTML，下载原始附件文件)
  -> notes(忠实正文规范 .md) -> audit(去重/隐私/结构/覆盖台账)
  -> assemble(总索引/联系总表) ；全程写 worklog 工作记录。

原始文件(审批表/通知PDF等)归档到 DATA_ROOT/files 并建 files_index，
供 rtime 助手按名检索并发送（见 files.find）。
"""

__version__ = "0.1.0"
