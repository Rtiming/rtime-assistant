# 库内容合同与夜巡(brain-library contract)使用参考

设计: [../design/library-lifecycle-maintenance-2026-07.zh-CN.md](../design/library-lifecycle-maintenance-2026-07.zh-CN.md) §四/§五(H 泳道 M0)。
代码: `packages/brain-library/src/brain_library/contract.py`。只读,不写库、不写索引。

## 合同字段(frontmatter)

| 字段 | 取值 | 缺失 | 非法 |
|---|---|---|---|
| status | draft / active / needs-review / superseded / archived | warning | warning(nonstandard_status:status是自由元数据破坏不了下游;真库已有 experimental-draft/filed/verified 等自然词汇,渐进迁移不阻塞) |
| review_after | YYYY-MM-DD | (可缺) | error |
| superseded_by | brain 相对路径 | status=superseded 时缺=error | 指向不存在/越界=error(dangling) |
| version | 正整数 | (可缺) | error |
| source | 来源 URL(资料带源硬规则) | warning | — |

另:去 frontmatter 后正文 <40 字符 → `stub_body`(warning);文件读不了 → `unreadable`(warning)。
**存量宽容**:缺字段只 warning,不拒读不翻 ok;只有"字段存在但非法"才是 error。

## CLI

```bash
PYTHONPATH=packages/brain-library/src python3 -m brain_library contract <brain-root> \
  [--index <sqlite索引路径>] [--path-prefix knowledge/institutions/ustc] \
  [--max-files N] [--sample-limit N]
```

- 输出 JSON 报告:`files_scanned`/`files_with_frontmatter`/`issues{error,warning}`/
  `by_code{code:{count,severity,sample[]}}`;带 `--index` 时附 `index_drift`
  (磁盘↔索引双向差,两侧同一 walker 口径)。
- 退出码:有 error(或 drift 不净)=1,否则 0——夜巡可直接当门用。
- **报告无正文**:只有路径、字段名、code、截断的元数据值(run-log 脱敏纪律)。

## Python API(入库/编辑共用)

```python
from brain_library.contract import validate_front, scan_contract, check_index_drift, contract_report
validate_front(front_dict, body_chars=n)   # 纯函数 -> [(severity, code, field, detail)]
contract_report(root, index=Path|None, path_prefix="", ...)  # 夜巡入口
```

M1+ 的写动词(lib.annotate/lib.edit)在落盘前必须调 `validate_front`,error 即拒绝写入。

## 与索引器的口径一致性

frontmatter 解析(`_parse_frontmatter`)与文件遍历(`_walk_index_files`,含 docpack/
SKIP_DIRS 规则)直接复用 indexer——"合同看到的库"="索引看到的库",drift 差集即真漂移。

## 测试

`tests/test_brain_library_contract.py`(宽容性/非法值/悬挂/前缀过滤/drift 双向/无正文/CLI 退出码)。

## lib.annotate:第一个库写动词(H M1)

只改 frontmatter 合同字段的两段式写动词,是 edit/move/retire 全部写动词的地基。
代码: `packages/brain-library/src/brain_library/annotate.py` + 网关 `lib.annotate`。

**两段式**(仿 lib.contribute):
- `op=plan`:合同校验 + 逐字段 diff + confirm_token(无状态乐观并发=
  sha256(path|changes|文件 mtime_ns);plan 后文件被动过则 apply 报 stale_token)。写零东西。
- `op=apply`:凭 token 落盘。四件套原子完成——①合同校验(error 拒)②修订快照
  (`_revisions/<h>/…/NNNNNN.md` 存改动前全量原文 + `chain.jsonl` 台账,可回放任意版本)
  ③行级改写(只动目标字段那一行,**正文逐字节不变**,自检 body sha 相等)④索引元数据列
  单行 UPDATE(annotate 只动 frontmatter,而 FTS/向量输入是去 frontmatter 正文→零重嵌入、
  检索不降速)。

**可注记字段**:status / review_after / superseded_by / source / tags(空串=删除该字段)。
**系统管理**:version 每次 apply 自动 +1,显式修改被拒。**复杂字段拒**:多行 YAML 列表
(如缩进 tags)在 plan 就拒,绝不产生孤儿续行。

**权限(超管专用)**:`lib.annotate` 在 scoped(非空 allowed_path_prefixes)实例一律
DENY(gate.SCOPE_DENIED_WRITE_METHODS,在 client allow-list 之前硬拒);被共享方(学生会
等)的写走 lib.contribute→owner 审核,不获得直接编辑权。对应权限模型:owner 与开发助手
=超级管理员(design/admin-console-ops §一)。

**测试**:`tests/test_brain_library_annotate.py`(10:plan diff/token 乐观并发/正文字节不变/
修订链回放/version 自增/非法字段与合同 error 拒/空串删/多行拒/越界拒);
网关端到端与 scoped-deny 在 test_rtime_library_gateway_mcp.py / _scope.py。
