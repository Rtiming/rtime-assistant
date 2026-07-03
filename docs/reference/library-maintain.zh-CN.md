# 库维护写动词使用参考(H M1/M2/M3)

设计: docs/design/library-lifecycle-maintenance-2026-07.zh-CN.md §四(M2)/§六-七(M1)/§九(M3)。
代码: packages/brain-library/src/brain_library/{annotate,edit,maintain}.py;网关接线:
rtime-library-gateway mcp_server(in-process)+ gate(权限)。

## 一、动词清单
| 动词 | 层 | 干什么 | 两段式 |
|---|---|---|---|
| `lib.annotate` | write | 只改 frontmatter 合同字段(status/review_after/superseded_by/source/tags) | plan→apply |
| `lib.edit` | write | 改**正文**(frontmatter 除 version 逐字节保留,version 自增) | plan→apply |
| `lib.revisions` | read | 列某路径修订链(annotate/edit/revert/move/retire/restore 全记录) | 纯读 |
| `lib.revert` | write | 回滚到某修订快照(frontmatter+正文整体恢复,version 前向) | plan→apply |
| `lib.move` | write | 移动/重命名:引用完整性扫描 + 旧路径留墓碑重定向 | plan→apply |
| `lib.retire` | write | 软删:移进 `_archive/` + 墓碑 + 索引移除(可 restore) | plan→apply |
| `lib.restore` | write | 恢复:把 `_archive/` 里的退役文件放回原路径 | plan→apply |

全部**超管专用**(owner/开发助手实例):scoped 实例(如学生会)恒被拒——
gate.SCOPE_DENIED_WRITE_METHODS 在 default_write 之外硬拦(fail-closed,即使策略被
误配也拒);lib.revisions 因修订历史=owner 内省,scoped 下也拒(未分类读方法)。
被共享方只能 lib.contribute→_inbox→owner finalize,不在库里直接写。

## 二、写四件套(每个写动词都走)
1. **合同校验**:contract.validate_front,error 即拒(空/过短正文是 warning 不拦,
   plan 的 `warnings` 里 surface)。
2. **修订快照**:改动前全量原文存 `_revisions/<sha(path)>/NNNNNN.md` + chain.jsonl
   追加一行(per-path 统一链,三动词共用)。
3. **原子落盘**:tmp + rename。
4. **索引一致**:apply 成功后 update_meta_columns(单行 UPDATE,零重嵌入)。
   ⚠️ **lib.edit/lib.revert 改了正文 → 搜索嵌入过时**,返回 `index_embedding_stale=true`
   提示需重索引(增量索引写入路径是后续档;元数据列仍即时同步)。

## 三、两段式与乐观并发
plan 返回 diff + `confirm_token`(不写任何东西);apply 必须带 token。
token = sha256(verb|path|目标内容sha|**当前文件内容sha**)——plan 之后文件内容被谁动过,
apply 即 `stale_token`(绑内容不绑 mtime:粗时间戳内核上 mtime 指纹会漏检同一时间粒内
的改动,这是 orangepi CI 真机抓到的缺陷)。

## 四、调用形态(经网关 MCP)
```
# 改正文
lib.edit  {op:plan,  path:"knowledge/x.md", new_body:"# 标题\n新正文…"}   → {diff, confirm_token, warnings, version}
lib.edit  {op:apply, path:"knowledge/x.md", new_body:"…", confirm_token:"…"} → {ok, version, revision, index_embedding_stale}
# 看历史 + 回滚
lib.revisions {path:"knowledge/x.md"}                                      → {current_version, revisions:[{version,verb,ts,actor,snapshot,…}]}
lib.revert {op:plan,  path:"knowledge/x.md", snapshot:"000001.md"}          → {diff, confirm_token, version}
lib.revert {op:apply, path:"knowledge/x.md", snapshot:"000001.md", confirm_token:"…"} → {ok, version, revision, reverted_to}
```
revert 的 snapshot 名来自 lib.revisions 的 `revisions[].snapshot`;回滚是**前向历史**
(不抹掉中间版本,回滚本身也进链,verb=revert + reverted_to)。

## 四·五、M3 维护动词:move / retire / restore
```
# 移动 / 重命名(先看谁引用它,旧路径变墓碑)
lib.move   {op:plan,  from_path:"knowledge/a.md", to_path:"knowledge/b.md"}   → {affected_refs:[{path,kinds}], affected_ref_count, tombstone, confirm_token}
lib.move   {op:apply, from_path:"knowledge/a.md", to_path:"knowledge/b.md", confirm_token:"…"} → {ok, from, to, tombstone, affected_refs, revision, index_synced, index_rebuild_needed}
# 软删到归档(检索不再命中,可恢复)
lib.retire {op:plan,  path:"knowledge/a.md"}                                  → {archived_to:"_archive/knowledge/a.md", affected_refs, tombstone, confirm_token}
lib.retire {op:apply, path:"knowledge/a.md", confirm_token:"…"}               → {ok, path, archived_to, tombstone, revision, index_synced}
# 恢复退役文件
lib.restore{op:plan,  path:"knowledge/a.md"}                                  → {restored_from:"_archive/knowledge/a.md", confirm_token}
lib.restore{op:apply, path:"knowledge/a.md", confirm_token:"…"}               → {ok, path, restored_from, revision, index_rebuild_needed}
```

- **引用完整性(move/retire 的 plan/apply 都报)**:扫 `knowledge/` 下 .md,报告哪些
  文件用 `[[slug]]` wikilink(命中全相对路径 / 去 .md / basename 任一形态,忽略
  `|别名` 与 `#锚点`)或 frontmatter `superseded_by: <该路径>` 指向它。列在
  `affected_refs`,**只报告不自动改**——操作者据此手动修引用(维护动词是低频人审操作,
  不做反向链接索引)。
- **墓碑**:move 后旧路径留 `status: moved` + `moved_to: <to>` 的墓碑,retire 后留
  `status: retired` 墓碑,各带一句人读的重定向说明——旧路径 `lib.read` 即读到重定向。
- **归档=可恢复**:retire 把原文整份移到 `_archive/<原相对路径>`(保留目录结构),
  完整保留;`lib.restore` 把它移回原路径(原路径须是 retired 墓碑或已不存在,否则拒,
  绝不覆盖活文件),恢复后归档副本清除、修订链留 verb=restore 轨迹。
- **索引一致**:move/retire 让原路径不再是知识(墓碑/归档),网关 handler 调
  `indexer.remove_from_index` 删旧 path 行(documents/fts/vec/courses,不重建、不重
  嵌入);move 的新路径与 restore 的原路径由后续增量重建收录,返回
  `index_rebuild_needed=true` 提示。索引器 SKIP_DIRS 已含 `_archive`,归档不会被重建
  收进检索。
- 修订快照:move/retire/restore 都写 per-path 修订链(与 annotate/edit 同一链,
  verb=move/retire/restore),move/retire 的墓碑作为"改动后"内容记进 chain。

## 五、越界与自检
- 路径越界(`../`、绝对路径、跨 root)→ 拒(_resolve)。
- snapshot 名带路径分隔符 / 不存在 → `unknown_snapshot`。
- apply 自检:edit 后 frontmatter 除 version 逐字段不变 + 正文 == new_body;
  违反即报错不落盘(version_bump_failed/frontmatter_changed/body_mismatch)。

## 六、改这些动词时
- 新写动词照 edit.py / maintain.py 纹理:plan/apply + 复用 annotate._write_revision
  (统一修订链)+ _resolve(越界)+ 内容-sha token。
- 网关三处同步登记:gate.METHOD_TIERS + mcp_server._INPROCESS + policy JSON(否则
  test_policy_file_methods_match_tier_table / 可达性门会红);直接写方法还要进
  gate.SCOPE_DENIED_WRITE_METHODS + grants._DENY_METHODS(scoped 恒拒)。move 用
  from_path/to_path 两个路径参数 → 都进 gate.PATH_LIKE_KEYS(路径门校验)。
- 索引移除:retire/move 从索引删单条 path 走 indexer.remove_from_index(与 build_index
  增量删 stale 行同一套 SQL,但按 path 直删,不重建);update_meta_columns 只更新元
  数据列,删行是另一条路径。
- 测试:tests/test_brain_library_edit.py + test_brain_library_maintain.py(单元)+
  test_rtime_library_gateway_mcp.py(e2e 经网关)+ test_rtime_library_gateway_scope.py
  (scoped 恒拒)+ test_rtime_library_gateway_gate.py(_INPROCESS_METHODS 副本同步)。
