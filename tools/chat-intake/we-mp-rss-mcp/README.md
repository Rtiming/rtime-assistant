# wechat-mp MCP

给 AI 助手（Claude / Codex / Kimi）提供"搜索微信公众号文章"的 MCP server。
后端是自托管的 [we-mp-rss](../we-mp-rss)（默认 `http://127.0.0.1:8001`）。

## 工具

| 工具 | 作用 |
|---|---|
| `search_articles(keyword, account="", limit=10, start_date="", end_date="")` | 按关键词检索（标题/摘要），可选限定公众号与**时间范围**；keyword 可留空只按时间/号筛选 |
| `articles_in_range(start_date="", end_date="", account="", keyword="", limit=50)` | **按时间范围**翻某段时期的推文（可叠加关键词、限定号），适合"把某号某年某月的推文都翻出来" |
| `latest_articles(account="", limit=10)` | 最新文章（可按号过滤） |
| `get_article(article_id, fetch_if_missing=True, include_images=False)` | 取单篇正文（保留段落）。库里有正文就用，没有则**直连微信原文兜底**；`include_images=True` 附图片链接；返回 `source` 标明来源 |
| `list_subscriptions()` | 列出已订阅的公众号 |
| `find_official_accounts(keyword)` | 在微信上搜公众号（找新号准备订阅） |
| `subscribe_account(mp_name, mp_id)` | 订阅一个号（mp_id=fakeid） |
| `refresh_account(account, start_page=0, end_page=1)` | 抓某个号的文章；调大 `end_page` 可**往回深爬历史**（每页约5篇），返回该号现有篇数 |

> **时间范围** `start_date` / `end_date` 支持 `2024-06-01`、`2024/06/30`、`2024-06`（整月）、`2024`（整年）；`end_date` 只给到日期/月/年时自动补到区间末尾 23:59:59。后端按发布时间倒序，时间过滤在 MCP 端分页扫描完成。
> **深爬历史**：新订阅只抓最新一批，要回溯旧文章用 `refresh_account(号, end_page=15)` 之类；抓取异步入库，`articles_now` 没变可稍等再查。微信对历史深度有上限。
> **正文兜底**：后端 `refresh` 常补不出老文章正文，`get_article` 会自动改抓微信原文 URL（公开可取），所以老文章也能拿到全文。

## 实现要点 / 优化

- 共享 `httpx.Client` 连接池；订阅号列表 60s TTL 缓存（账号名→id 解析高频，避免每次请求），缓存用**独立锁**保护（`threading.Lock` 不可重入，不能复用登录锁）。
- `_req` 在 401 自动重登的基础上，对**瞬时网络错误**（连接/超时）重试 1 次。
- 后端单次 `limit` 上限 **100**（>100 返回空），分页与各工具均按此夹紧。
- `refresh_account` / `archive_account` 用 `_resolve_known` 校验：解析不到真实订阅号即报错，不把乱字符串塞给后端。
- 检索结果带 `summary`（取文章 description 预览），AI 不必每篇都 `get_article` 就能判断相关性。
- 正文提取 `_html_to_text`：保留段落、解码 HTML 实体、去注释、图片转 `[图片]` 占位；`_extract_body` 用"`</div>` 后跟 `<script>`/工具栏"作边界，正文里嵌套 `<div>` 不会被提前截断。
- `get_article` 校验空 `article_id`；时间范围支持开放式（只给 `start_date` 或只给 `end_date`）。
- 测试：`test_suite.py` 覆盖 11 个工具 + 两轮边界用例（日期解析含闰年跨年/HTML提取去注释/嵌套div/分页/时间过滤/开放区间/正文双路径兜底/防呆/8线程并发/缓存），对接真实后端 **61 项断言全过**。跑法：`WEMP_BASE_URL=http://127.0.0.1:8001 "C:/Users/<username>/anaconda3/envs/wechat-mcp/python.exe" test_suite.py`。

## 运行环境

- conda env：`wechat-mcp`（python 3.12 + `mcp` + `httpx`）
- 启动命令：`C:/Users/<username>/anaconda3/envs/wechat-mcp/python.exe C:/Users/<username>/Documents/claude/we-mp-rss-mcp/server.py`

环境变量（可选，均有默认值）：
`WEMP_BASE_URL=http://127.0.0.1:8001`、`WEMP_USERNAME=admin`、`WEMP_PASSWORD=<你的口令>`

## 注册进三个助手（用同步工具）

```powershell
& "$HOME\.ai-skills\_setup-mcp.ps1" -Name wechat-mp `
  -Command "C:/Users/<username>/anaconda3/envs/wechat-mcp/python.exe" `
  -CommandArgs @("C:/Users/<username>/Documents/claude/we-mp-rss-mcp/server.py") `
  -EnvVars @{ WEMP_BASE_URL="http://127.0.0.1:8001"; WEMP_USERNAME="admin"; WEMP_PASSWORD="<你的口令>" }
```

注册后重启对应助手即可加载。前提：we-mp-rss 容器在跑（`docker compose -f ../we-mp-rss/docker-compose.yml up -d`）。
