# sync 集成模块使用参考(Syncthing 笔记同步)

状态:已建(K3,通用可选模块)。schema:packages/rtime-admin-core/src/rtime_admin_core/schemas.py 的 SyncIntegrationConfig,经 registry.py:default_registry() 恒注册为模块 sync。字段清单(机器生成)见 docs/config/sync.md。模块声明:deploy/modules.json 的 integration-sync。设计:docs/design/module-system-and-open-source-2026-07.zh-CN.md(OB/notes/sync 通用模块表)。

## 一、这个模块是什么、不是什么
Syncthing 是外部服务:用户自装、自配设备与共享,同步的内容(笔记目录)**永不进本仓库**。本模块只是**助手侧的指针**——这台机器上被同步的笔记目录在哪、本机 Syncthing 的 REST API 怎么连,好让面板(K5"模块"页)能渲染配置表单、亮同步健康灯。

不在这里的东西:Syncthing 自己的设备/共享/忽略规则/版本控制配置(在 Syncthing GUI/config.xml,是它的数据);笔记内容本身(用户自己的,不入仓、不进面板)。

## 二、字段(4 个,全部 hot,scope=write:library)
| 字段 | env | 说明 |
|---|---|---|
| notes_root | RTIME_SYNC_NOTES_ROOT | 被同步笔记目录的本机路径;None=本机不参与 |
| api_url | RTIME_SYNC_API_URL | 本机 Syncthing REST 地址,默认 http://127.0.0.1:8384 |
| api_key(秘密) | RTIME_SYNC_API_KEY | GUI→操作→设置→API 密钥;None=只无鉴权探活 |
| folder_id | RTIME_SYNC_FOLDER_ID | 笔记共享的 folder id;None=不查同步完成度 |

面板表单由 schema 自动渲染(三层配置第二层);文件/CLI/API 用户直接写 env 或 PATCH /v1/config(第三层)。健康灯消费(K5):api_key 缺→GET {api_url}/rest/noauth/health 探活;有 key→/rest/system/status + /rest/db/status?folder={folder_id} 查完成度。

## 三、教程:接自己的 Syncthing(从零)
1. 装 Syncthing:https://syncthing.net/downloads/(Linux 常见 `apt install syncthing`;树莓派/服务器建议 systemd 用户服务 `systemctl --user enable --now syncthing`)。
2. 建笔记共享:GUI(默认 http://127.0.0.1:8384)→添加文件夹,路径选你的笔记目录(如 ~/notes),记下 folder id。
3. 加设备互信:两台设备交换 Device ID(GUI→操作→显示 ID),双向接受共享。
4. 忽略规则(.stignore,放在共享根):**首个匹配生效**,反向排除(!)必须放在对应通配前。常用模板:
   ```
   !.env.example
   (?d).DS_Store
   (?d)Thumbs.db
   .obsidian/workspace*.json
   .trash
   ```
   注意每台机器的 .stignore 是本机文件、不被同步——每机各写。
5. 填本模块配置(面板 sync 表单,或 env):notes_root=你的笔记目录;api_key 从 GUI→操作→设置复制;folder_id=第 2 步的 id。
6. 验证:`curl -s http://127.0.0.1:8384/rest/noauth/health` 应回 `{"status":"OK"}`;带 key 查同步度 `curl -s -H "X-API-Key: <key>" 'http://127.0.0.1:8384/rest/db/status?folder=<folder_id>'`。

## 四、与 Obsidian 集成(integration-obsidian)的关系
两个独立可选模块,常一起用:Syncthing 把笔记目录同步到各设备,Obsidian 把该目录当 vault 打开,obsidian-rtime-assistant 插件(config_module=assistant-gateway)再接本机助手网关。都不装也完全不影响核心(渠道/网关/面板)。owner 部署的既有事实(brain 不同步、brain-notes 是 vault)是**一种配置**,不是硬编码——别人接自己的目录即可。

## 五、数据边界(硬规矩)
notes_root 指向的目录、Syncthing 的实例配置、API key:全部在仓库外。本仓库只有 schema(字段名+默认值)与本文档。开源打包(publish 白名单)含代码与文档,永不含任何被同步内容。

## 六、改这个模块时
加字段照 SyncIntegrationConfig 现有纹理(config_field/secret_field+env_aliases+HOT+write:library);改完重生成字段文档:
```bash
PYTHONPATH=packages/rtime-config/src:packages/rtime-admin-core/src \
  uv run --project packages/rtime-admin-core python -m rtime_config \
  rtime_admin_core.schemas:SyncIntegrationConfig --title 'sync 配置项' --out docs/config/sync.md
```
并跑 packages/rtime-admin-core/tests/(test_registry 的核心模块清单断言含 sync)。

配套件:deploy/bin/rtime-sync-health 及其 systemd timer 归本模块(modules.json setup_notes 已登记)。
