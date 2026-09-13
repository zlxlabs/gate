# c2b 独立评审 verdict：`_fetch_pr_draft` 失败契约

## 结论

**pass（非阻塞，P1=0）**。固定审查对象
`55f50021a939178f35bdcdf9db4a6798d5e959f4..72d01323dbb321b5fa4b24cd0176b3dcb29e0042`
的两文件改动已覆盖目标缺陷：HTTP 200 非 JSON body 和非法 UTF-8 body 均返回
`None`，主流程继续进入 `_finish`，写终态、Step Summary 和 `::error::`。发现一个
极端的解析器栈深度残余，按 personal 档记为 P2 backlog，不阻塞本轮。

审查对象证据：

```text
$ git log --oneline 55f50021a939178f35bdcdf9db4a6798d5e959f4..card/gate-20260913-09
72d0132 fix(aggregator): fail closed on malformed PR draft payload

$ git diff --stat 55f50021a939178f35bdcdf9db4a6798d5e959f4..card/gate-20260913-09
 .github/actions/gate-aggregator/aggregate.py |  2 ++
 tests/test_gate_aggregator.py                | 48 ++++++++++++++++++++++++++++
 2 files changed, 50 insertions(+)
```

OCR 前置扫描已真实运行，envelope 为
`status=reviewed`、`profile=minimax`、`model=MiniMax-M3`、`coverage=complete`、
`cli_status=complete`、`findings=[]`；不是 `skipped`。

## 1. 契约完整性

结论：文档列出的四类失败在正常生产异常面上均 fail-closed；但 `_github_json` 的
完整异常面仍有一个可复现的 `RecursionError` 会绕过所有捕获，因此不能声称覆盖
任意解析失败。

目标代码行号（固定 H0）：

| 来源 | 失败形态 | 处理结果 |
|---|---|---|
| 输入门 | `not token` 或 `pr_number is None` | `aggregate.py:1345-1346` 直接返回 `None`，不发请求 |
| `_github_request` | `urllib.error.HTTPError` | `aggregate.py:1353-1354` 直接返回 `None`，不重试 |
| `_github_request` | `URLError`、`SSLError`、`ConnectionResetError`、`IncompleteRead`、`TimeoutError`、`socket.timeout` | `aggregate.py:1357-1360` 按既有 3 次尝试和 1/2 秒退避执行，耗尽返回 `None` |
| `json.loads(raw)` | JSON 语法错误 | `aggregate.py:1355-1356` 返回 `None` |
| `json.loads(raw)` | bytes 非 UTF-8 | `aggregate.py:1355-1356` 返回 `None` |
| 空 body / JSON 形状 | `_github_json` 返回 `None`，或解析结果非 dict / `draft` 非严格 bool | `aggregate.py:1361-1363` 返回 `None` |
| `json.loads(raw)` | 10,000 层嵌套数组触发 `RecursionError` | 未捕获，会冒到 `main()`；见第 6 项和 F-1 |

这里没有把测试桩异常表当作完整依据，而是直接检查了实现：`_github_request` 在
`aggregate.py:1252-1280` 只以 bytes 返回响应，`_github_json` 在 `:1330-1332`
执行 `json.loads(raw) if raw else None`。`json.loads` 的坏语法实测抛
`JSONDecodeError`，非法 UTF-8 实测抛 `UnicodeDecodeError`，深层数组实测抛
`RecursionError`。`_github_request` 的请求构造在本调用中没有 payload；主流程调用
`_fetch_pr_draft` 时也尚未启用 publish budget（`aggregate.py:2327-2333`）。

`HTTPError` 是 `URLError` 的子类，但它位于 retryable tuple 之前，所以 HTTP 错误
不会被错误地重试。其他不属于 `_RETRYABLE_CONNECTION_ERRORS` 的底层协议异常（例如
`http.client.BadStatusLine`）也不属于 docstring 明确承诺的“exhausted connection
retries”集合，本轮不将它们扩成新的阻塞 finding。

## 2. 异常边界

结论：新增解析异常边界位置正确，未把校验、形状判断或返回值构造卷进同一 `try`；
PR draft 的外部调用没有掉到所有 `try` 之外。

- `aggregate.py:1350-1352` 的 `try` 只包一次 `_github_json(token=..., url=...)`。
- HTTP、解析和连接异常各自是独立 `except` 分支；新增分支只在 `:1355-1356`
  返回 `None`。
- payload 形状判断在 `:1361-1363`，位于 `try/except` 之后；因此 dict 形状校验、
  `type(payload.get("draft")) is bool` 和最终返回值没有被新增捕获吞掉。
- URL f-string 构造在 `:1347` 不是外部调用；唯一的 GitHub API 调用在 `try` 内。
  `time.sleep` 只在 retry 分支执行，常量退避值为 1、2 秒。

## 3. fail-closed 方向

结论：新增失败形态都返回 `None`，没有把失败转成 `True` 或 `False`。

`evaluate()` 在 `aggregate.py:704-727` 对 `primary_result == "skipped"` 且
`is_draft` 的路径明确区分：`pr_draft_now is False` 是 stale，`is None` 在
`:718-723` 设置 `classification="review_unavailable"`、
`reason_code="pr_state_unverifiable"` 并加入失败问题；只有严格的 `True` 才保留
expected skip（`:724-727`）。因此 `None` 不会被解释为 “still draft”。目标端到端
测试也锁住了 `rc == 1` 和 `reason_code == "pr_state_unverifiable"`。

## 4. 测试约束力与变异验证

被审 worktree 中按卡面命令运行：

```text
$ uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest tests/test_gate_aggregator.py -q
........................................................................ [ 28%]
........................................................................ [ 57%]
........................................................................ [ 86%]
.................................                                        [100%]
249 passed in 5.32s
```

只跑 `_fetch_pr_draft` 相关回归用例也得到：

```text
7 passed, 242 deselected in 0.06s
```

三个新增用例逐项做了删成员变异。变异只在从 H0 复制出的临时 worktree
`/tmp/gate-20260913-09-isolated.HtdZen` 中进行，目标分支 worktree 从未修改；变异
结束后临时 worktree 已移除，目标分支和本 worktree 均为 clean。

| 用例 | 临时变异 | 结果 |
|---|---|---|
| `test_fetch_pr_draft_malformed_json_fails_closed` | 将 `except (json.JSONDecodeError, UnicodeDecodeError)` 改为仅 `except UnicodeDecodeError` | `F`，`JSONDecodeError` 冒出 |
| `test_fetch_pr_draft_invalid_utf8_payload_fails_closed` | 将同一行改为仅 `except json.JSONDecodeError` | `F`，`UnicodeDecodeError` 冒出 |
| `test_main_malformed_pr_draft_response_writes_unverifiable_terminal` | 仅保留 UTF-8 捕获（JSON 捕获成员被删） | `F`，真实 `json.loads(raw)` 的 `JSONDecodeError` 冒出，终态未写 |

对应输出分别为 `2 failed, 247 deselected`、`1 failed, 248 deselected`；注入前用
`sed -n '1353,1357p'` 确认了变异行。端到端用例在
`tests/test_gate_aggregator.py:959` 桩的是 `_github_request`，返回 bytes
`b"<html>not json</html>"`；它没有桩 `_github_json`。所以执行到
`aggregate.py:1331-1332` 的 `json.loads(raw)` 后，失败确实是生产解析器产生的
`JSONDecodeError`。删掉 JSON 捕获成员后的失败堆栈也指向该行，而不是测试自造的
异常。

## 5. 回归面

结论：既有 HTTPError、连接重试、payload 形状判断、重试次数和退避均未改变。

- 固定 diff 只在 `aggregate.py:1355-1356` 增加一个解析异常分支；
  `PR_DRAFT_FETCH_ATTEMPTS = 3`、`PR_DRAFT_FETCH_BACKOFF_SECONDS = (1, 2)` 和
  `_RETRYABLE_CONNECTION_ERRORS` 均未改。
- 新分支位于 `break` 之后的成功路径之外，并位于 retryable connection 分支之前；
  JSON/UTF-8 异常不属于 retry tuple，因此该排序不会改变连接重试。
- 既有用例 `test_fetch_pr_draft_http_error_is_not_retried`、
  `test_fetch_pr_draft_retries_connection_errors_then_returns_current_state`、
  `test_fetch_pr_draft_exhausts_connection_retries_and_fails_closed` 和
  `test_fetch_pr_draft_missing_draft_boolean_fails_closed` 均在上述 7 条相关测试中
  通过；调用次数/退避仍是 HTTPError 1 次、连接失败最多 3 次、退避 `[1, 2]`。
- `git diff --check 55f50021a939178f35bdcdf9db4a6798d5e959f4..card/gate-20260913-09`
  无输出、退出码 0。

## 6. 对抗尝试

构造了 PR draft 复核路径的深层 JSON 输入：把 `_github_request` 替换为返回
`b"[" * 10000 + b"0" + b"]" * 10000`，调用真实目标 worktree 的
`main()`，参数为 `primary_result=skipped`、`is_draft=true`、有效 token、PR 号和
终态路径。结果：

```text
exception= RecursionError
message= maximum recursion depth exceeded while decoding a JSON array from a unicode string
terminal_exists= False
summary_exists= False
```

这证明当前仍存在一个“解析失败绕过所有 except → `_finish` 不执行”的极端输入，登记
如下。

### F-1 — P2：`RecursionError` 仍可令 PR draft 复核路径崩溃

- 违反的契约：`_fetch_pr_draft` docstring 的 malformed payload fail-closed 承诺；
  生产落点为 `aggregate.py:1330-1332` 和捕获区 `:1350-1360`。
- 触发证据：上述 10,000 层嵌套 JSON 的真实 `main()` 探针；异常为
  `RecursionError`，`gate-terminal.json` 和摘要均不存在。
- personal 两问：
  1. **真实使用触发性：不满足 P1。** 本项目实际请求的是 GitHub 的固定
     `/repos/{repository}/pulls/{pr_number}` 端点；本次能触发的输入是专门构造的
     10,000 层嵌套数组，不是 PR API 的正常对象形状，也不是 PR 作者能通过字段值
     注入的结构。实测证明的是代码边界存在，不证明当前 GitHub 生产响应会给出这种
     深度。
  2. **后果可接受性：不满足。** 若上游代理/API 真返回该 body，会和原缺陷一样
     崩溃并漏写终态、摘要和错误标记。

  只有第二问通过，因此按 personal 档记 **P2 backlog**，本轮 pass，不拆阻塞修复卡。
  本轮没有其他 P1/P2/P3 finding。

## 收口

- 只审固定 H0，不纳入 H0 后的新提交；没有修改 `.github/actions/**` 或 `tests/**`。
- 被审 worktree 只读运行；变异只使用临时 worktree，结束后已移除。
- 本 verdict 是当前分支唯一新增文件，已提交到 delegate 分配的分支。

outcome: pass
