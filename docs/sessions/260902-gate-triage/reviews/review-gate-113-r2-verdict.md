verdict: pass

# gate#113 r2 独立审查 verdict

第 2 轮。换家（Cursor → Grok）且换证据源：真实 GitHub API、本地 HTTP 桩走通 `_github_json`、消费端喂 `gate-terminal.json`、面板人读路径。不重审第 1 轮的正向契约、降层三问、三条 monkeypatch `_github_json` 探针。审查对象冻结 `5123e3120ca6e9c4d84244528b74fe9346bd730c..be3e4aa47af7dbe3d0f37e3e9133a126e91396da`。spec = PR #113 正文 + issue #110 + `docs/sessions/260902-gate-triage/design.md` 不变式 1、2。风险档 personal，失败路径按 internal 收敛。无新增 P1。

## 本轮新证据

- H0 临时 worktree `/tmp/review-113-r2` @ `be3e4aa47af7dbe3d0f37e3e9133a126e91396da`。被审文件只读。
- `gh pr list -R zlxlabs/gate --state all --limit 20 --json number,isDraft`：#113/#115 `isDraft=true`，#114 `isDraft=false`。
- 用 `gh auth token` 直调 H0 `_fetch_pr_draft(token=…, repository="zlxlabs/gate", pr_number=N)`（GET only）。
- 本地 `ThreadingHTTPServer` 随机高位端口，monkeypatch `_github_json` 把 `https://api.github.com` 改写到桩（不改被审文件）；三种响应后 kill。
- H0 `aggregate.main`（monkeypatch `_fetch_pr_draft→False`）写出 `gate-terminal.json`，再喂 `build_ledger.load_gate_terminal_envelope`、gate-hub `review-ledger-replay.py --ledger`、`review-ledger-report.py` 的 `parse_ledger_jsonl_line`/`summarize`。
- 直调 `render_status_panel` / `render_summary` / `_panel_current_row`。

## 1. 真实 GitHub API

命令（H0 `sys.path` → `/tmp/review-113-r2/.github/actions/gate-aggregator`）：

```
gh pr list -R zlxlabs/gate --state all --limit 20 --json number,isDraft
python3 -c 'import aggregate as AGG; AGG._fetch_pr_draft(token=gh_auth_token(), repository="zlxlabs/gate", pr_number=N)'
```

并行做了不经 helper 的 raw GET（同一 token / Accept），核对 `Content-Type` 与 `draft` 的 Python 类型。token 只报长度，不落盘。

| 对象 | raw HTTP | Content-Type | `draft` 类型 | `_fetch_pr_draft` |
|---|---|---|---|---|
| #113 draft | 200 | `application/json; charset=utf-8` | `bool` True | `True` |
| #114 已合并非 draft | 200 | 同上 | `bool` False | `False` |
| #115 draft（复核） | 200 | 同上 | `bool` True | `True` |
| #999999 不存在 | 404 JSON `{"message":"Not Found",…,"status":"404"}` | 同上 | — | `None`（不抛） |
| token 故意错 | 401 JSON `{"message":"Bad credentials",…}` | 同上 | — | `None`（不抛） |

原文摘录：

```
_fetch_pr_draft -> True   type=bool  expected=True   match=True   #113
_fetch_pr_draft -> False  type=bool  expected=False  match=True   #114
_fetch_pr_draft -> None   type=NoneType              match=True   #999999
bad token _fetch_pr_draft -> None  raised=False
```

与 `gh pr list` 的 `isDraft` 一致。失败是 HTTPError 路径（JSON 4xx），不是崩溃。满足不变式 1 的输入侧：真实 `draft` 是 bool，读不到则 None。

GitHub 文档 Get a pull request 状态码：200 / 304 / 404 / 406 / 500 / 503；「Unless otherwise specified, the response body is in JSON format.」本轮 live 200/401/404 全是 JSON。代码 `Accept: application/vnd.github+json`，不会走到 diff/patch 的非 JSON 媒体类型。

## 2. 本地 HTTP 桩走真实 `_github_json`

桩 `127.0.0.1:42335`。包装 `_github_json` 只改 URL 前缀，解析/重试仍是 H0 原函数。

**① 200 + 非 JSON body `<html>not json</html>`**

```
{"label": "200 non-JSON body", "result": null, "raised": true,
 "exc_type": "JSONDecodeError",
 "exc_msg": "Expecting value: line 1 column 1 (char 0)", "hits": 1}
```

堆栈：`_fetch_pr_draft:1265` → `_github_json:1246 json.loads(raw)` → 未被捕获。`hits=1`：不重试。会冒到 `main():2234`（该处无 try），aggregator 进程崩溃，`_finish` 不跑 → 不写 `gate-terminal.json`、不写 Step Summary、不打 `::error::`。这就是 OCR high「非重试类异常让 aggregator 崩而不是落 `pr_state_unverifiable`」。

空 body 200 不会崩：`json.loads(raw) if raw else None` 对假值 raw 返回 None，随后 `:1273` 判非 dict → None。崩的是**非空非 JSON 200**。

**OCR high 的 P 等级与两问**

- 工具标注：OCR high / 非重试类异常冒到 main 崩溃。
- 本仓判定：**P2**，不立 finding（第一问不过；与 r1 backlog 同意见，换证据后定级）。
- 两问：
  1. 真实使用会被触发吗？**否。** 本机对 `api.github.com/repos/zlxlabs/gate/pulls/{n}` 量了 200/401/404，全是 `application/json`。文档失败码是 HTTP 错，走已有 `except HTTPError: return None`。self-hosted runner 直连该端点，`Accept` 钉死 JSON。没有实测到、文档也不承诺「200 但 body 不是 JSON」。
  2. 若触发，后果能否接受？进程崩溃，check 仍红（不是假绿），但没有设计中的 `pr_state_unverifiable` terminal / 面板。崩溃是 personal 红线，**第一问不过，不能升 P1**。draft 期红不挡合并。

**② 200 + `Content-Length` 大于实际写出**

```
{"label": "200 Content-Length > body (incomplete)", "result": null,
 "raised": false, "hits": 3}
```

直调 `_github_json` 的异常：`http.client.IncompleteRead`（`IncompleteRead(16 bytes read, 80 more expected)`），MRO 不含 `HTTPError`，`isinstance(..., _RETRYABLE_CONNECTION_ERRORS) is True`。三次后返回 None，不抛。符合「连接级重试、耗尽 fail-closed」。

**③ 先 RST 两次再 200 `{"draft": false}`**

```
{"label": "RST twice then 200 {draft:false}", "result": false,
 "result_type": "bool", "raised": false, "hits": 3}
```

两次连接失败后第三次成功，返回 `False`。重试形态在真实 `_github_json`/`urlopen` 上成立。

桩已 shutdown。

## 3. 消费端

命令：H0 `AGG.main([...], monkeypatch _fetch_pr_draft=lambda **kw: False)`，再：

```
python3 -c 'import build_ledger as BL; BL.load_gate_terminal_envelope(Path(terminal))'
python3 /home/zlx/projects/personal/gate-hub/scripts/review-ledger-replay.py --ledger <jsonl> --output-dir …
# report.py 无本地文件入口；import parse_ledger_jsonl_line / summarize
```

aggregator `rc=1`，写出的 terminal：

```
"gate_result": "unavailable",
"classification": "review_unavailable",
"reason_code": "review_expected_stale"
```

Step Summary / `::error::` 含 `gh pr ready --undo && gh pr ready`。

`build_ledger.py`：`load_gate_terminal_envelope` 只校验 `schema_version==1` 且 `kind==gate_terminal`，**不读 `reason_code`**。全文件无 `review_expected_stale` / `pr_state_unverifiable`。消费块只抽 `disposition_receipt_consumption`（本例 `consumed_count=0, fail_closed=false`）。

把 consumption + 额外字段 `gate_terminal_reason_code=review_expected_stale` 塞进 v2 ledger 样例一行：

- `review-ledger-replay.py --ledger` **rc=0**；`ledger_bad_lines: 0`；personal 档 `PRs analyzed: 1`，`Skipped rounds: {"not_applicable": 1}`，manual/missed trigger 全 0。
- 同一公式 `count_p1`：`review.status=not_applicable`，`severity_counts={}`，**P1=0**。P1 只来自 `review.severity_counts` / findings，不来自 gate-terminal reason。
- `review-ledger-report.py` `parse_ledger_jsonl_line` OK；`summarize findings.reported=0`，`severity_counts={}`，`conclusions_usable: true`。未知 key 不被拒。
- 把**裸** `gate-terminal.json` 当 `--ledger`：rc=2、`Ledger bad lines: 30`——那是 JSON 多行不是 jsonl，与新 reason code 无关。

结论：新 reason code 不被任何消费枚举拒绝，不改变 P1 计数。

## 4. 面板人读路径

`render_status_panel` 对 `unavailable` 的投影（本轮直调，`pr_draft_now=False` 的 outcome）：

```
当前状态：**unavailable** · **修基础设施**
当前裁决：`review_unavailable` / `review_expected_stale`
| … | `unavailable` | 修基础设施 |
```

- 面板含 `review_expected_stale`：是。
- 面板含 `gh pr ready --undo && gh pr ready`：**否**。
- `_panel_current_row` 无 `problems` 字段。
- `REASON_CODE_EXPLANATIONS.get("review_expected_stale")` 为 `None`。
- `_action_sentence` 走通用 unavailable 句（「investigate the run」），命令只在 `outcome.problems` → `render_summary` / `::error::`。

on-call 顺序（PR checks 红 → 点开 `gate / gate` → Step Summary → sticky 面板）：

| 跳 | 能否看到 `gh pr ready --undo && gh pr ready` |
|---|---|
| 1. PR checks 红 | 能。`_finish` 打 `::error::{problem}`，Checks 注释放全文 |
| 2. 点开 gate/gate job | 能。同一注解 + job log |
| 3. Step Summary | 能。`Problems:` 列表（不变式 1 的锁死面） |
| 4. sticky 面板评论 | **不能**。只有桶文案「修基础设施」+ reason_code |

设计验收路径写「面板含命令」，实现锁的是 summary。桶「修基础设施」对「重新标 ready」有误导，但是既有 `unavailable` 桶，本 PR 未改 renderer。

**是否升为 finding：** 不升 P1/P2。给人读缺口 **P3**，留 backlog（与 r1 同条，本轮用 hop 走访定级）。

- 工具标注：无（r1 backlog / 设计验收用词）。
- 本仓判定：P3，接受不修。
- 两问：① 只读 sticky 评论的人会看不到命令、看到「修基础设施」——真实会发生，但规定顺序在 hop 1–3 已经能看到命令。② 后果是多绕路，check 已红、不是静默绿、不崩、不丢数据。personal P1 红线不过。

## Findings

无 P1 / P2 / P3 finding。OCR high 与面板人读均在对照表 / backlog 定级，不阻塞。

## OCR 对照表

| 工具标注 | 本仓判定 | 两问答案 |
|---|---|---|
| high：非重试类异常（`JSONDecodeError`）冒到 main，aggregator 崩而不是 `pr_state_unverifiable` | **P2**，不立 finding | ① 真实 `pulls/{n}` 在本 fleets 用法下未观测到非 JSON 200；文档失败走 HTTP 错。② 若发生则崩且无 terminal，但仍红不绿。第一问不过 → 非 P1。 |
| medium：repository 空不短路 | 本轮不重审（r1 已两问） | — |
| low：reason code 缺注释 | 本轮不重审；消费端实测两码不被拒 | — |

## Backlog

- `_fetch_pr_draft` 不接 `JSONDecodeError`：桩上已证实会崩；真实 GitHub 量不到。P2 防御缺口。空 200 已安全。
- sticky 面板不投影 problems / 重触发命令，`unavailable` 桶为「修基础设施」。P3。人在 hop 1–3 能看到命令。
- `REASON_CODE_EXPLANATIONS` 未收两码（summary `.get()` 跳过）。
- `PR_DRAFT_FETCH_ATTEMPTS` 与 backoff 长度耦合无锁（r1；本轮不重测）。

已否决方案（改 concurrency、primary 自查 API、API 失败 fail-open、从 build_ledger import 重试）不重提。

## 收敛判定

本轮**无新增 P1**。第 1 轮（Cursor，正向+降层+monkeypatch）P1=0；本轮（Grok，真实 API + HTTP 桩 + 消费端 + 面板 hop）P1=0。换家且换证据源，相邻两轮可计入「连续 2 轮无新增 P1」。失败路径按 internal 的收敛条件本轮满足。`verdict: pass`。
