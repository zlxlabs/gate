verdict: pass

# gate#106 r1 独立全量评审 verdict

第 1 轮。方向 = 正向全量（契约是否兑现：重试对象集合、HTTPError 不重试、耗尽抛原始异常、只经 URL_OPENER.open、测试不真睡）+ 反向抽查（`_RETRYABLE_CONNECTION_ERRORS` 里 `urllib.error.URLError` 是 `HTTPError` 的父类——except 顺序是否保证 HTTPError 先被捕获；`socket.timeout` 在 py3.10+ 是 `TimeoutError` 别名是否冗余；`IncompleteRead` 是否会从 `response.read()` 抛出而落在 with 块内）。风险档 personal，失败路径按 internal 收敛条件审视。无 P1/P2/P3 finding。

## 本轮新证据

本轮是该 diff 的第一轮独立审查，证据不是「再读一遍同一份 diff」：

- OCR：`ocr-review --from 94ec3e76 --to 06d50c5`，`status=reviewed`，`profile=minimax` / MiniMax-M3，`coverage=complete`，`findings: []`（不是 skipped 空数组）。
- H0 临时 worktree `/tmp/review-106` @ `06d50c5`：`uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_review_ledger.py` → **203 passed in 1.56s**。
- 本机 Python 3.12.3（与 `.github/workflows/ci.yml` 的 `python-version: "3.12"` 同主版本）实测：`issubclass(HTTPError, URLError) is True`；`socket.timeout is TimeoutError is True`。
- except 顺序仿真：先 `except HTTPError` 再 `except RETRYABLE` 时 404 走 HTTPError 立即抛出；若只保留 RETRYABLE 元组则同一 HTTPError **会被当成 URLError 重试**。
- 反向探针：本地 HTTP 服务器故意 `Content-Length: 10` 只写 3 字节，`http.client.HTTPResponse.read()` 抛出 `http.client.IncompleteRead`；用与生产相同的 `try / with / read / except HTTPError / except RETRYABLE` 骨架，该异常从 with 块内冒出并被重试，耗尽后仍是原始 `IncompleteRead`。

审查对象冻结 `94ec3e76cd97a16927435437c76a6e6b0e4244f8..06d50c5d756ca8afc11d9a7333687bb3011d9727`。spec = PR #106 正文 + issue #103 第 2 条评论（id 5489824378，「更正：前面三条出处的根因我判错了」；issues comments API 仅 2 条，卡面「评论 4」对应此条更正文）。已否决方案（requests/tenacity、重试 5xx、抽 `_with_retry`）不作为 finding 重提。

## Findings

无 P1 / P2 / P3 finding。下面按本轮方向列出已查项、对应 spec/不变式，以及为何不立 finding。OCR 无条目，对照表仍保留表头。

### 正向：契约核对

| 查过什么 | spec / 不变式 | 为何没问题 |
|---|---|---|
| 重试对象集合 | PR 正文：URLError、ssl.SSLError、ConnectionResetError、IncompleteRead、TimeoutError/socket.timeout | `_RETRYABLE_CONNECTION_ERRORS` 六元组与清单一一对应；#103 真实栈是 `URLError` 包装 `SSLEOFError`，`SSLEOFError ⊂ SSLError` 且 URLError 在元组内。 |
| HTTPError 不重试 | 服务端已应答 4xx/5xx 原样抛出；不重试 5xx | `except urllib.error.HTTPError: raise` 在 RETRYABLE 之前，无状态码分支。测试 `test_api_request_does_not_retry_http_error` 锁 404、1 次调用、零 sleep。 |
| 耗尽抛原始异常 | 最后一次原始类型、不包装 | 最后一轮 `raise` 裸抛。测试 `assert type(exc_info.value) is urllib.error.URLError`（精确类型，不是 `isinstance`，不会被 HTTPError 子类蒙混）。 |
| 只经 URL_OPENER.open | 签名、headers、timeout=30 不变 | Request 仍在循环外构造一次；循环内只有 `URL_OPENER.open(request, timeout=30)` + `response.read()`。测试 monkeypatch 的是 `module.URL_OPENER.open`。 |
| 测试不真睡 | monkeypatch `time.sleep` 记录序列 | 三条新测试都 `setattr("time.sleep", ...)`；成功/耗尽路径断言 `sleeps == [1, 2]`，HTTPError 路径 `sleeps == []`。模块是 `import time` 后 `time.sleep`，补丁打在 `time` 模块上，调用时生效。 |
| 次数与退避 | 首发 + 2 次；`API_REQUEST_ATTEMPTS=3`、`API_REQUEST_BACKOFF_SECONDS=(1,2)`；无环境变量 | `last_attempt = ATTEMPTS-1`，仅 `attempt < last_attempt` 时 `sleep(BACKOFF[attempt])`，下标不会越界。 |

### 反向抽查

**R1. URLError 是 HTTPError 的父类，except 顺序是否保证 HTTPError 先被捕获？**

会。本机 3.12.3：`HTTPError.__mro__` 含 `URLError`。生产代码 `build_ledger.py:732-734` 先 `except HTTPError: raise` 再 `except _RETRYABLE_CONNECTION_ERRORS`。仿真：有专段时 404 立即重抛；去掉专段后同一实例命中 URLError、会被重试。`test_api_request_does_not_retry_http_error` 锁死这条顺序。

- 工具标注：OCR 未报；本审查自查。
- 本仓判定：不立 finding。顺序正确且有测试。
- 两问：①真实使用会触发 HTTPError 吗？会。ledger 调 GitHub API，缺 artifact / 404 是常规形态；本轮在 H0 worktree 跑该测试已走通。②若顺序写反，会把 4xx/5xx 当连接抖动重试，违反「不重试 HTTPError / 不重试 5xx」。当前顺序写对，后果不发生。

**R2. `socket.timeout` 在 py3.10+ 是 `TimeoutError` 别名是否冗余？**

在 CI / 本机 3.12 上 `socket.timeout is TimeoutError` 为 True，元组里两个名字是同一类型对象。spec 正文用斜线写成一项 `TimeoutError`/`socket.timeout`，实现按字面双写，except 元组允许重复，不会双处理。action 入口是 runner 上的 `python3`（`.github/actions/review-ledger/action.yml`）；测试门是 3.12。3.10+ 上别名成立，双写无行为差。

- 工具标注：OCR 未报。
- 本仓判定：不立 finding（不是新抽象，也不违反 spec；删一个会「少写 spec 点名的那个名字」）。
- 两问：①当前 3.12 真实环境触发超时会进重试吗？会，走 `TimeoutError`/`URLError` 任一即可。②多余别名会造成静默错/崩溃/丢数据吗？不会。

**R3. IncompleteRead 是否会从 `response.read()` 抛出而落在 with 块内？**

会。`http.client.IncompleteRead` 不继承 URLError（基类是 `HTTPException`），必须单独列入元组——实现已列入。探针：截断 body 的本地 HTTP，`HTTPResponse.read()` 抛 `http.client.IncompleteRead`；与生产相同的 `with URL_OPENER.open(...) as response: return response.read()` 结构里，该异常发生在 with 套件内，`__exit__` 返回假后继续传到 try 的 except，被 RETRYABLE 接住并重试，耗尽仍抛原始类型。

- 工具标注：OCR 未报。
- 本仓判定：不立 finding。控制流覆盖 `open()` 与 `read()` 两处。测试未单列 IncompleteRead（spec 测试节只要求三条，均已落地），记 backlog。
- 两问：①真实 GitHub 响应截断会发生吗？issue #103 旁证同一 runner 时段有 ECONNRESET / HTTP/2 CANCEL，截断 body 与 TLS EOF 同类瞬时网络；本轮用真实 `http.client` 读路径复现了该异常类型。②当前实现会静默吞掉吗？不会，重试或裸抛。

### OCR 对照表

| 工具标注 | 本仓判定 | 两问答案 |
|---|---|---|
| 无（`status=reviewed`，`findings: []`） | 不立 finding。这是扫过且干净，不是 skipped 空数组。 | ①OCR 未给出缺陷，无触发对象。②不适用。 |

## 降层三问（infra 失败路径）

1. **终态写入成功之前已发生哪些不可逆动作？** `main()` 先 `fetch_prior_entries`（GET 列表 + GET 下载 zip），再 `write_ledger` 写本地 jsonl，然后才 `post_state_comment`（已有评论则 PATCH，否则 POST）。重试发生在 `_api_request` 共享出口：GET 可重入；PATCH 同 body 近似幂等；**仅首次 POST 状态评论**在「服务端已建评论、响应在连接上丢失」时重试可能多出一条 sticky comment。这不改 ledger 正文、不删数据、不绕过 required check。spec 把重试放在共享 `_api_request` 上，此残余被契约接受，不单独立 finding。
2. **守卫值在实际部署下是否唯一？** 本 diff 没有新增身份守卫。重试键是「同一次 Request 对象再 open」，token / URL / timeout=30 不变。ledger 身份仍是既有 repository/run/PR/head SHA 字段，本 PR 未改。
3. **保护覆盖的是写入还是行为？** 覆盖行为：连接级失败从「一次 EOF 直接 job exit 1」变成「最多 3 次、退避 1s/2s，HTTP 已应答仍 fail-loud」。不把 5xx 降级为成功，不包装异常，不改 artifact / attempt 语义。

## 熵增审查

对 diff 中每个新增常量 / 异常元组 / 循环问是否熵 +1（对照 REFACTOR-guide 坏味道词表；已否决 `_with_retry` / tenacity / 重试 5xx）：

| 新增项 | 是否熵 +1 | 判断依据 |
|---|---|---|
| `API_REQUEST_ATTEMPTS = 3` | 否 | spec 点名的次数上限；被 for 上界与 `last_attempt` 两处消费，不是无主配置或环境变量开关。 |
| `API_REQUEST_BACKOFF_SECONDS = (1, 2)` | 否 | spec 点名的 1s/2s；与 ATTEMPTS 耦合（`len(backoff)==ATTEMPTS-1`），但是明文常量而非第二套事实源。 |
| `_RETRYABLE_CONNECTION_ERRORS` 六元组 | 否 | 单点 except 的类型清单，不是无第二消费者的通用 retry 框架。`socket.timeout` 与 `TimeoutError` 在 3.12 是同一对象，属 spec 双写别名，不另增概念。 |
| `for attempt in range(...)` + `time.sleep` | 否 | spec 要求的有限退避；未抽 `_with_retry` 包装层（已否决）。手写 retry 在词表里是「自建基础设施」，但本卡明确禁止 tenacity/requests，这是授权的最小实现，不是额外抽象。 |
| `except HTTPError: raise` 专段 | 否 | 因为 HTTPError⊂URLError，这是为了兑现「不重试 HTTP 已应答」的必要顺序，不是防御式双路径。 |
| `import ssl/time/urllib.error` | 否 | 常量与 sleep 的直接依赖；`http.client`/`socket` 原文件已有。 |
| `_ApiResponse` / `_connection_urlerror` / `_http_404` + 三条测试 | 否 | 只锁可观察行为（调用次数、sleep 序列、精确异常类型），不引入产品运行面。 |

未新增 fallback、未吞掉非连接异常、未引入第二套 opener。

## Backlog（存量 / 不阻塞）

- 三条新测试只覆盖 `URLError(SSLEOFError)` 代表元 + HTTPError 404 + 耗尽；未单测 `IncompleteRead` / 裸 `SSLError` / `ConnectionResetError` / `TimeoutError`。生产 except 元组已包含它们，属覆盖宽度而非行为错误。
- `API_REQUEST_ATTEMPTS` 与 `BACKOFF` 长度靠约定对齐；将来只改其中一处会 `IndexError`。当前 spec 把两者写死为 3 与 (1,2)。
- `_api_request` 的 POST 状态评论在连接丢失后重试，理论上可重复发一条 sticky comment（见降层三问）。这是共享出口重试的固有残余，spec 未要求按 method 分流。
- 存量：`HTTPError` 既是异常也是 addinfourl，裸 `raise` 不显式关 `fp`；本 PR 之前已如此，不占本轮 finding。

## 结论

H0 兑现 PR #106 与 #103 更正根因：连接级失败有限退避，HTTP 已应答立即失败，耗尽抛原始异常，请求仍只走 `URL_OPENER.open`。反向三条（HTTPError 父类顺序、timeout 别名、IncompleteRead 落在 with 内）均已用本机 3.12 与探针量过，没有静默错 / 丢数据 / 崩溃。OCR 扫过且干净。verdict：**pass**。
