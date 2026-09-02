verdict: pass

# gate#106 r2 独立评审 verdict（对抗视角：非幂等写在重试下的重复副作用）

第 2 轮。方向 = 不重审 r1 已查的三条（HTTPError 父类顺序、timeout 别名、IncompleteRead 落点），改为对抗：**重试让哪些原本只发生一次的副作用变成可能发生两次？后果谁承受？** 风险档 personal（`AGENTS.md:3`），失败路径按 internal 收敛条件审视。无新增 P1/P2/P3 finding。

审查对象冻结 `94ec3e76cd97a16927435437c76a6e6b0e4244f8..06d50c5d756ca8afc11d9a7333687bb3011d9727`。spec = PR #106 正文 + issue #103 更正评论（id 5489824378）。已否决方案（requests/tenacity、重试 5xx、抽 `_with_retry`）不作为 finding 重提。

## 本轮新证据

相对 r1 的新证据源（不是再读同一份 diff）：

1. 调用方枚举：H0 `/tmp/review-106-r2` 上 `grep -n '_api_request\|_api_json' .github/actions/review-ledger/build_ledger.py` 命中 9 行（2 定义 + 7 调用，H0 相对 origin/main 未增减），逐行分类 GET / 非 GET，并对每个非 GET 问「服务端已应用、客户端收到连接错误时重试」的后果。
2. 真实写路径 stub：本地 `ThreadingHTTPServer` 对 `module._api_request(..., method="POST")` 回 201 后截断 body / 只发头就关连接，计数 stub 实收 POST 次数。探针 `/tmp/review-106-r2-stub.py`（不进仓）。`time.sleep` 只记账不真睡（与单元测试同手法）。本机 Python 3.12.3。
3. 读取侧探针：构造两条均含 `STATE_MARKER` 的 bot 评论，跑 `post_state_comment` 的 `next(...)` 与 `parse_state_entries`，确认取的是列表第一条还是最新一条。
4. H0 回归：`uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_review_ledger.py` → **203 passed in 1.52s**。

OCR 主脑已分诊的 1 条 medium（timeout 未断言 `== 30`）判 P3 待修，本轮不重报。

## 调用方分类表

`grep` 9 行全部入表（行数 = 命中数）。后果列回答的是「服务端已应用该次请求后，客户端因连接级错误重试」。

| 行 | 形态 | method | 目标端点 | 重试后果 | 谁读到重复产物 |
|---|---|---|---|---|---|
| 713 | 定义 `_api_request`（默认 GET） | — | — | — | — |
| 740 | 定义 `_api_json` | — | — | — | — |
| 741 | `_api_json` → `_api_request` | GET | 由调用方传入 | 幂等 | 无重复产物（再读同一 JSON） |
| 751 | `fetch_prior_entries` | GET | `/repos/{repo}/actions/artifacts?name=…&per_page=…` | 幂等 | 无；多一次列表读取 |
| 757 | `fetch_prior_entries` | GET | `artifact["archive_download_url"]`（常 302 到签名存储） | 幂等 | 无；多下一次 zip。`Request` 在循环外构造、原 URL 再 open，会再走一遍重定向；签名 URL 非一次性 |
| 772 | `fetch_comments` | GET | `/repos/{repo}/issues/{pr}/comments?per_page=100` | 幂等 | 无。未传 `sort`/`direction`；GitHub 文档：issue comments **按 id 升序**（最老在前） |
| 789 | `post_state_comment` | GET | `/repos/{repo}/pulls/{pr}` | 幂等 | 无；用来比 live head SHA |
| 798 | `post_state_comment` | PATCH | `/repos/{repo}/issues/comments/{id}` | **覆盖** | 同一 comment id 被同一 body 再写一次。人 + `parse_state_entries` + `next(STATE_MARKER)` 仍读这一条 |
| 800 | `post_state_comment` | POST | `/repos/{repo}/issues/{pr}/comments` | **重复创建** | 见下节两问。人看见多条 sticky comment；机器读侧取**列表第一条**（最老），不是最新 |

非 GET 只有 798 / 800。PATCH 是覆盖，不是重复创建。

## 实测输出

stub 对每次入站请求先读完 body 再应答。`_api_request` 的 `Request` 在重试循环外构造一次，POST body 三次字面相同。

| 场景 | method | stub 实收次数 | 客户端异常 | sleeps |
|---|---|---|---|---|
| 201 + `Content-Length` 比实写多 40 字节 | POST | **3** | `IncompleteRead(23 bytes read, 40 more expected)` | `[1, 2]` |
| 201 只发头随即 `Connection: close` | POST | **3** | `IncompleteRead(0 bytes read, 20 more expected)` | `[1, 2]` |
| 201 + 完整 JSON（对照） | POST | **1** | 无，body `{"id":4242,"body":"ok"}` | `[]` |
| 201 + 截断（对照） | PATCH | 3 | 同 IncompleteRead | `[1, 2]` |
| 201 + 截断（对照） | GET | 3 | 同 IncompleteRead | `[1, 2]` |

结论：服务端已回 201 之后，只要 `response.read()` 抛出 `IncompleteRead`（该类型在 `_RETRYABLE_CONNECTION_ERRORS` 内），客户端会把**同一条 POST** 再发到耗尽（首发 + 2 次）。对照完整 201 只发 1 次，排除「stub 误计数」。

读取侧（两条 bot 评论都带 `STATE_MARKER`，id=1 最老 / id=2 最新）：

- `next(c for c in comments if STATE_MARKER in body)`：oldest-first → id **1**；newest-first → id **2**。取的是列表第一命中，不是「最新」。
- `parse_state_entries`：oldest-first 返回第一条能解码的游标，**不会**再看后面那条。
- 生产 `fetch_comments` 不传 sort，GitHub：「Issue comments are ordered by ascending ID。」→ 生产是 oldest-first，重复 POST 之后后续 run 的 PATCH / 游标都钉在**最老那条**。

## 重复创建的 P1 两问（仅行 800）

| 工具标注 | 本仓判定 | 两问答案 |
|---|---|---|
| 本轮自查（r1 backlog 仅理论；OCR 未报此条） | **不立 P1 / 不立 P2 / 不立 P3 finding**。重试 POST 符合 spec「在 `_api_request` 共享出口对连接级异常有限退避」；与已否决方案无关。残余进 backlog。 | ①真实使用下会发生吗？**窄路径会发生，且已被 stub 量到客户端会再 POST。** gate 每个 run 一次 ledger job。`post_state_comment` 只在「`comments` 里没有 `STATE_MARKER` 且 `relevant_pr_entries` > 1」时 POST（首轮 skip；从第二轮起首次落评论）。该次若 GitHub 已 201 建评、响应在连接上丢失，本 job 最多再 POST 2 次 → 最多 3 条 sticky comment。后续 run 走 PATCH 最老那条，不再 POST，**不会**每轮再翻倍。谁消费：人看见多余评论；`STATE_MARKER`/`STATE_RE` 读侧（`next` 与 `parse_state_entries`）都取**第一条**。②后果能否接受？**能。** `write_ledger` 在 POST 之前已落本地 jsonl；机器游标钉在会被后续 PATCH 更新的最老评论上，不读错、不丢账本。多余评论是人可见噪声，且正文自标「机器状态记录，非评审结论」。personal 红线是丢数据 / 静默结果错 / 崩溃，三条都不中。 |

## Findings

无 P1 / P2 / P3 finding。不重审 r1 三条反向问题。OCR medium 不重报。

未把「POST 不要重试」写成 finding：那是改 spec 的共享出口范围，且会撞「抽 `_with_retry` / 按 method 再包一层」的已否决方向。本 PR 契约接受把重试放在 GET/POST/PATCH 共用的 `_api_request` 上。

## 降层三问（infra 失败路径）

1. **终态写入成功之前已发生哪些不可逆动作？** `main()`：GET 历史 artifact → GET 评论 → **本地 `write_ledger`** → `post_state_comment`（GET pull，然后 PATCH 或 POST）。不可逆的是 GitHub POST 建评。stub 证明：201 已发出后客户端仍可再 POST。PATCH 再写同一 id，不可逆但幂等覆盖。GET 无写副作用。
2. **守卫值在实际部署下是否唯一？** 本 diff 没有给 POST 加幂等键。重试键是「同一个 `Request` 再 `open`」。评论 id 要等 201 响应体才知道；丢失响应时客户端手里没有 id，无法改走 PATCH。ledger 身份仍是既有 repository/run/PR/head SHA。单 job、无多副本争用。
3. **保护覆盖的是写入还是行为？** 覆盖**行为**：连接级失败从「一次 EOF → job exit 1」变成「最多 3 次」。不覆盖写入唯一性。这是共享出口重试的固有残余，spec 已接受。

## Backlog（不阻塞）

- POST 在「服务端已 201、客户端 IncompleteRead」时最多建 3 条 sticky comment；后续 PATCH 只收敛最老一条，其余冻结。r1 已理论点名，本轮 stub 把它从「理论上」改成「客户端确实再发」。不修则人偶发看见重复机器评论。
- 承接 r1：新测试未单列 `IncompleteRead` / 裸 `SSLError` / `ConnectionResetError` / `TimeoutError`；`ATTEMPTS` 与 `BACKOFF` 长度靠约定对齐。
- OCR P3（主脑已判）：测试记录了 `timeout` 但未断言 `== 30`。
- 存量：`fetch_comments` 失败时 `existing is None`，可能对已有 sticky comment 再 POST（pre-existing，不在本 diff）。

## 结论

H0 的共享出口重试会把 POST 的「建一条评论」变成「连接丢失后最多建三条」。真实消费方按列表第一条（最老）读和 PATCH，账本不丢、机器游标不错、不崩溃。对照完整 201 只 POST 一次。不违反 PR #106 / #103 spec。verdict：**pass**。
