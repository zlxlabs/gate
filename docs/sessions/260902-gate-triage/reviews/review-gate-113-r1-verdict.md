verdict: pass

# gate#113 r1 独立全量评审 verdict

第 1 轮。方向 = 正向全量（PR 正文每句是否兑现：真值表三分叉、零额外请求、evaluate 纯函数、fail-closed、域扩展）+ 降层三问 + 反向抽查。已覆盖问题清单：evaluate 三分叉与矩阵四格、`_fetch_pr_draft` 重试/HTTPError/缺输入、`main()` 接线与零请求守卫、`TERMINAL_REASON_DOMAIN` 两码、problems 重触发命令、workflow `IS_DRAFT`/`REVIEW_EXPECTED`/`timeout-minutes: 8`/`PR_NUMBER`/`GH_TOKEN`、publish 预算激活点、fork/draft×review_expected 组合、OCR 两条、熵增与「不抽共享重试」。风险档 personal，失败路径按 internal 收敛条件审视，并做降层三问。无 P1/P2/P3 finding。

## 本轮新证据

本轮是该 diff 的第一轮独立审查，证据不是「再读一遍同一份 diff」：

- OCR：`ocr-review --from 5123e3120ca6e9c4d84244528b74fe9346bd730c --to be3e4aa47af7dbe3d0f37e3e9133a126e91396da`，`status=reviewed`，`profile=minimax` / MiniMax-M3，`coverage=complete`，2 条 finding（medium/low，复核器超时未核实）。不是 skipped 空数组。
- H0 临时 worktree `/tmp/review-113` @ `be3e4aa`：`uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_aggregator.py` → **219 passed in 7.81s**。
- 本机 Python 3.12.3 实测：`issubclass(urllib.error.HTTPError, urllib.error.URLError) is True`；`HTTPError.__mro__` 含 `URLError`；`socket.timeout is TimeoutError is True`。
- 直接调用 H0 `_fetch_pr_draft` / `evaluate` 的反向探针（输出见「反向抽查」）。
- 本工作树 `origin/main` 的 `gate-v2.yml:882` `timeout-minutes: 8`、`:985-1001` `IS_DRAFT`/`REVIEW_EXPECTED`/`PR_NUMBER`/`GH_TOKEN`（本 PR 未改，作部署形态证据）。
- `_ACTIVE_PUBLISH_BUDGET.get()` 在 fetch 之前为 `None`；最坏 fetch 墙钟 `3×15 + 1 + 2 = 48s`；`DEFAULT_PUBLISH_BUDGET_SECONDS = 120`。

审查对象冻结 `5123e3120ca6e9c4d84244528b74fe9346bd730c..be3e4aa47af7dbe3d0f37e3e9133a126e91396da`。spec = PR #113 正文 + issue #110 + `docs/sessions/260902-gate-triage/design.md` 关键不变式 1、2 与已否决方案。已否决（改 concurrency group、primary job 自查 API、API 失败 fail-open、从 build_ledger import 重试形态）不作为 finding 重提。不审 `gate-v2.yml`、不审 `origin/main` 存量。

## Findings

无 P1 / P2 / P3 finding。下面按本轮方向列出已查项、对应 spec/不变式，以及为何不立 finding。OCR 两条进对照表后降级，不进 findings。

### 正向：PR 正文 / 不变式核对

| 查过什么 | spec / 不变式 | 为何没问题 |
|---|---|---|
| 真值表三分叉 | PR 正文：`pr_draft_now` True/False/None；不变式 1 | `evaluate` `:678-694`：`False` → `review_unavailable/review_expected_stale` + problems 逐字含 `gh pr ready --undo && gh pr ready`；`None` → `pr_state_unverifiable`；else（True）→ `expected_skip/review_not_expected` + notes `pr draft state re-verified: still draft`。矩阵 `:781-784` 四格（三分叉 + fork 非 draft skip）+ 三条直调测试锁死。 |
| `gate_result` 不得为 pass/skipped | 不变式 1 | `gate_result` 映射 `:773`：`review_unavailable` → `unavailable`；`ok = gate_result in ("pass", "skipped")`（`:775`）。CLI `test_cli_stale_draft_payload_fails_closed_with_retrigger_command`：exit 1、summary 含 reason 与重触发命令、terminal JSON `gate_result=unavailable`。 |
| 零额外请求 | 不变式 2；PR：正常 run 零额外 API | `main()` `:2232-2234` 仅 `primary_result == "skipped" and is_draft` 才调用 fetch。CLI `test_cli_non_draft_skip_never_calls_pr_draft_fetch`：`is_draft=false` 时 fetch 调用即 raise，rc=0。fork 路径走 `:695-697`，不读 `pr_draft_now`。 |
| evaluate 纯函数 | PR：I/O 只在 `main()` | `evaluate` 只消费预取的 `pr_draft_now`；`_fetch_pr_draft` 仅 `main` 一处调用。测试可在不打网的情况下覆盖三分叉。 |
| fail-closed | PR / 已否决 fail-open；不变式 1 的红路径 | token 空 / `pr_number` 缺 → 零请求返回 None（`:1259-1260`）；HTTPError / 耗尽 / `draft` 非 bool → None；`None` 在 evaluate 里给红。draft 期红不阻塞合并，与设计文「代价只是一次 rerun」一致。 |
| 域扩展 | PR：`TERMINAL_REASON_DOMAIN` 加两码；classification 域不变 | `:144` 追加 `review_expected_stale`、`pr_state_unverifiable`。`test_terminal_reason_domain_lock` 锁全元组。`TERMINAL_CLASSIFICATION_DOMAIN` 未改。 |
| `_fetch_pr_draft` 重试形态 | PR：连接级最多 3 次、退避 1s/2s；HTTPError 不重试；不 import ledger | `PR_DRAFT_FETCH_ATTEMPTS=3`、`BACKOFF=(1,2)`；`except HTTPError: return None` 在 RETRYABLE 之前。单测 5 条覆盖成功重试、HTTP 不重试、耗尽、畸形 draft、缺 token/pr_number。 |
| 不改 concurrency / 不改 gate-v2.yml | PR 非目标；已否决改锁 | diff 四文件无 workflow。本审查按卡面不审 `gate-v2.yml`。 |
| 重触发命令落在哪 | PR：problems 逐字；不变式 1 锁 **summary**；设计验收路径写「面板含命令」 | `render_summary` `:1020-1023` 把 problems 写进 Step Summary；`_finish` `:2084-2085` 再打 `::error::`。sticky `render_status_panel` 只投影 `classification/reason_code`（`:1121`），没有 problems 字段——这是面板既有形状，本 PR 未改 renderer。按不变式 1 的锁死面（CLI 已断言 summary）兑现；验收路径「面板」与 summary 不完全同词，不另立 finding（见 backlog）。 |
| `REASON_CODE_EXPLANATIONS` 未加两码 | 非 PR 条款 | `.get()` 缺失即跳过。`review_not_expected` 等既有码同样没有专段。action sentence 对 `unavailable` 走通用句（`:929-932`），具体命令在 problems。与既有 `primary_cancelled` 同形。 |

### 降层三问（infra 失败路径；各问带行号）

1. **终态写入成功之前已发生哪些不可逆动作？fetch 会不会挤掉 publish 预算或撞上 job 8 分钟超时？**

   `_fetch_pr_draft` 在 `evaluate` 之前、`_finish` 之前发起 GET（`main` `:2228-2236`）。注释写明此时 publish 预算尚未激活。实测：`_ACTIVE_PUBLISH_BUDGET.get()` 默认 `None`；唯一 `.set` 在 `_post_status_panel_fail_open` `:1879`，发生在 evaluate 之后的面板发布。因此 fetch 走 `_github_request` `:1180-1181` 的 `GITHUB_API_TIMEOUT_SECONDS=15` 路径，**不扣** `DEFAULT_PUBLISH_BUDGET_SECONDS=120`。

   最坏墙钟：3 次 × 15s 超时 + backoff 1s+2s = **48s**。`gate` job `timeout-minutes: 8`（`gate-v2.yml:882`，480s）。draft-skip 路径上 `needs.primary.result == skipped`，resolve-audit / download-artifact 两步的 `if:` 为假（`:903`、`:968`），python 启动前几乎没有制品 API。48s + 随后 120s publish 预算 + 45s history 上限仍远小于 480s。GET 是只读，不是删文件/发通知；终态写入是 `_finish` 的 summary/terminal/面板。fetch 失败只返回 None → fail-closed 红，不会在写入成功前留下半份绿结论。

2. **守卫用的值在真实部署形态下是否唯一？PR 已 ready 但本 run 的 head 已陈旧时，给红是否仍正确、会不会与新 head 的 run 打架？**

   守卫读的是 `GET /repos/{repo}/pulls/{n}` 的 `draft`（`_fetch_pr_draft` `:1261-1275`），这是 **PR 级全局**布尔，不是 head SHA。本 run 的 check 绑在 **本 run 的 head SHA**（`identity.head_sha`，面板行 `:1908`）。

   - 同 SHA 上 synchronize 幸存、ready run 被 primary/quality 的 job 级锁取消：正是 #110 形态。fetch 见 `draft=false` → 本 SHA 的 `gate / gate` 为 `unavailable`，与「主审没跑不能绿」一致。rollup 若取同名 check 的较新结论，新旧都是红，不会再被一条 payload-draft 的绿盖掉。
   - 新 push 换了 head：旧 SHA 的红不影响 PR 可合并性（required check 看最新 head）；新 SHA 自有 run。两套 check run 不共享 SHA，不打架。
   - `draft` 不是 run id，也不需要在多副本下唯一——aggregator 是单 job。用 PR 级 `draft` 回答「主审现在该不该已跑」是对的；用它当 SHA 身份会错，但代码没有把它当 SHA 身份。

3. **保护覆盖的是「写入」还是「行为」？`is_draft=True` 且 `review_expected=True` 走哪支？fork 是否零请求？**

   保护的是 **行为（required check 结论）**：`gate_result=unavailable` 且 process exit 1，不是只改一条评论。面板发布仍是既有 fail-open；即使评论没写上，check 已经红。

   `is_draft=True, review_expected=True`：evaluate 先看 `if is_draft`（`:672`），**根本不读** `review_expected`。探针：该组合 + `pr_draft_now=True` → `expected_skip`；+ `False` → `review_expected_stale`。真实 workflow 里两者来自同一载荷字段的相反极性（`gate-v2.yml:985` `IS_DRAFT: github.event.pull_request.draft`，`:990` `REVIEW_EXPECTED: draft != true && same-repo && runner==self`），**不可能同时为真**。旧代码同样 `if is_draft` 优先，不是回归。

   fork：`is_draft=False, review_expected=False` → `:695-697` expected_skip，`main` 条件不成立，零 fetch。CLI 已锁。

### 反向抽查（直接调用，输出原文）

本机 3.12.3；`sys.path` 指向 `/tmp/review-113/.github/actions/gate-aggregator`。

**R1. `_RETRYABLE_CONNECTION_ERRORS` 含 `URLError`，`HTTPError` 是其子类——except 顺序是否保证 HTTPError 先被捕获？**

会。`HTTPError.__mro__` 含 `URLError`；`issubclass(HTTPError, URLError) is True`。生产顺序 `_fetch_pr_draft` `:1267-1269`：先 `except urllib.error.HTTPError: return None`，再 `except _RETRYABLE_CONNECTION_ERRORS`。

探针：`_github_json` 抛 404 `HTTPError` → `result None, calls 1, sleeps []`。反证：同一实例能被裸 `except _RETRYABLE_CONNECTION_ERRORS` 抓住（打印 `HTTPError matches RETRYABLE tuple? HTTPError`）。若顺序写反，404 会被当连接抖动重试 3 次，违反「HTTPError 不重试」。当前顺序正确，`test_fetch_pr_draft_http_error_is_not_retried` 锁死。

- 工具标注：OCR 未报此条；本审查自查。
- 本仓判定：不立 finding。
- 两问：①真实使用会触发 HTTPError 吗？会，PR 号错/权限/404 是常规形态；本轮直调已走通。②若顺序写反会静默绿吗？不会绿，但会把确定性 4xx 拖满 48s；当前顺序写对，后果不发生。

**R2. `payload["draft"]` 为 `None` 时返回 None 而非 False 是否合理？**

合理。守卫是 `type(payload.get("draft")) is not bool`（`:1273-1274`），`None` / 缺键 / `"false"` 字符串一律 None。探针：

```
draft=None -> None  is False? False  is None? True
draft key missing -> None
draft=False -> False
```

GitHub REST `pulls/{n}` 文档里 `draft` 是 boolean。若出现 null，当成「已非 draft」（False）会误判 stale、把仍可能是 draft 的 PR 打成 `review_expected_stale`。返回 None → `pr_state_unverifiable` 红，fail-closed，与「None 从未表示 still draft」的注释（`:1256-1257`、`:676-677`）一致。

- 工具标注：OCR 未报。
- 本仓判定：不立 finding。
- 两问：①真实 API 会给 null 吗？本机无法对 GitHub 生产 schema 造 null；守卫按「非严格 bool」处理。②当成 False 的后果能否接受？不能（假 stale）；当前实现避免了这条。

**R3. `PR_DRAFT_FETCH_ATTEMPTS` 改 1 时 `time.sleep` 边界。**

`for attempt in range(ATTEMPTS)`，连接失败且 `attempt >= ATTEMPTS - 1` 立即 `return None`，不 sleep。ATTEMPTS=1 时 `range(1)=[0]`，`0 >= 0`，零 sleep。探针：

```
ATTEMPTS=1 URLError result None calls 1 sleeps []
ATTEMPTS=1 success True sleeps []
```

不会下标访问 `BACKOFF[0]`。生产值 3 与 backoff 长度 2 匹配（最后一轮不睡）。改 ATTEMPTS 而不改 backoff 会在中间轮 IndexError——常量耦合，不是本轮生产路径；记 backlog。

- 工具标注：OCR 未报。
- 本仓判定：不立 finding。
- 两问：①生产会把 ATTEMPTS 改成 1 吗？不会，是模块常量。②当前 3 次路径会误睡或崩溃吗？不会，单测断言 `sleeps == [1, 2]`。

### 熵增审查

对照 REFACTOR-guide 坏味道词表，对每个新增项问是否熵 +1：

| 新增项 | 是否熵 +1 | 判断 |
|---|---|---|
| `PR_DRAFT_FETCH_ATTEMPTS = 3` | 否 | spec 点名次数；for 上界与 last-attempt 两处消费，不是无主开关。 |
| `PR_DRAFT_FETCH_BACKOFF_SECONDS = (1, 2)` | 否 | spec 点名 1s/2s；与 ATTEMPTS 耦合但是明文常量。 |
| `_RETRYABLE_CONNECTION_ERRORS` 六元组 | 否 | 单点 except 的类型清单，不是通用 retry 框架。与 ledger 同形是已否决「import 共享」之后的所有权隔离，不是投机通用性。 |
| `_fetch_pr_draft` | 否 | 单调用者（`main`），但承载 I/O 边界（重试 + 严格 bool），不是转发-only 包装。抽出它是为了让 `evaluate` 保持纯函数。 |
| `pr_draft_now` 参数 | 否 | 纯函数的第三输入，矩阵与 CLI 的第二消费者都在。不是镜像状态。 |
| `review_expected_stale` / `pr_state_unverifiable` | 否 | 域扩展，有测试锁全元组；不是第二套 classification。 |
| 两条 problems 文案 | 否 | spec 点名的人读命令与 fail-closed 说明，summary/`::error::` 消费。 |

**与 `build_ledger.py` 的同形重试是否值得抽共享？判定：不值得。** 设计已否决「从 build_ledger import 重试形态」；两套 action 目录必须独立（`aggregate.py:151-152` 注释）。共享层会变成无第二真实消费者的基础设施（ledger 与 aggregator 的 timeout、URL opener、失败返回值都不同：ledger 耗尽抛原异常，这里耗尽返回 None）。重复约 15 行换隔离，默认保持复制。

### OCR 对照表

| 工具标注 | 本仓判定 | 两问答案 |
|---|---|---|
| medium / maintainability：`repository=""` / `None` 仍会发请求，no-request 测试只锁 token 与 pr_number | 不立 finding（≤P3 测试对称性）。spec 正文只要求「token 空 / pr_number 缺 → None 且零请求」，未要求 repository 空也短路。生产 `REPOSITORY: ${{ github.repository }}` 恒有 `owner/repo`。空串会打 `repos//pulls/n` → HTTP 404 → 已有 HTTPError 路径 fail-closed，不是静默绿。 | ①真实 GHA 会把 repository 置空吗？不会，本机对照 `gate-v2.yml:992`。②即便发出畸形 URL，后果是红不是绿，可接受。 |
| low / style：CLI 测试用 summary 子串锁重触发命令，建议改锁 terminal.problems | 不立 finding。不变式 1 的锁死面就是 **summary 含命令**；子串断言正对那条契约。terminal JSON 未要求带 problems 数组。 | ①测试风格意见在真实使用下不是缺陷。②不适用。 |

OCR `verify_status=failed`（复核器超时），两条均 `unverified`。按纪律：工具 severity 是输入；本仓两问后都不进 P1/P2。

## Backlog

- sticky 状态面板（`render_status_panel`）对 `unavailable` 只显示桶文案「修基础设施」+ `reason_code`，不展示 problems 里的重触发命令。设计验收路径写「面板含命令」，与不变式 1「summary 含命令」不完全同词。若下一轮要从人读入口收敛，改面板投影才是对口，不在本 PR 的 evaluate 路径上补。
- `PR_DRAFT_FETCH_ATTEMPTS` 与 backoff 长度的耦合没有测试锁；改其中一个会 IndexError。生产常量匹配。
- `REASON_CODE_EXPLANATIONS` 未收录两码——与既有多数 reason 同形，人读命令已在 problems。
- `_fetch_pr_draft` 不接 `JSONDecodeError`：畸形 JSON 会冒到 `main` 成崩溃。真实 GitHub pulls 端点给 JSON 或 HTTP 错；第一问在真实环境量不到这条。属防御面，不是本轮 P1。
- 设计不变式 3–6（#107 上传重试、#105 二 ocr COE）属同会话其他 PR，本 diff 未实现、本轮不审。
- `origin/main` / `gate-v2.yml` 存量不在范围。
