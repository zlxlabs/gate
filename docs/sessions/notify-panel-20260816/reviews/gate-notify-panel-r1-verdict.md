<!-- delegate-outcome: succeeded -->

## 结论

最终判定：P1 需修复，不能 CLEAR。

本轮审查对象固定为 `origin/main...origin/card/gate-notify-panel`，H0=`da5bd4c`；没有把后续提交纳入本轮。该 diff 是 internal/infra 状态机，按 review-discipline 采用 internal 红线并执行降层三问。

证据：

- `python3 -m pytest tests/ -q` 在 H0 工作树实跑：`426 passed in 5.38s`。
- OCR 前置包装器三腿均返回 `status=skipped`，`reason_code=status_missing`（minimax/qwen/glm），不是“已扫过且无 finding”；本轮未把空 findings 当作清洁证据。
- 穷举红验未改文件：运行时向 `GATE_RESULT_DOMAIN` 注入 `future_state`，`test_panel_bucket_mapping_exhaustively_covers_current_gate_result_domain` 变红，确认该处断言有约束力。
- 真实 producer 探针：`aggregate.main` 写出的 `gate-terminal.json` 可被 `_terminal_row` 消费；但真实 artifact 列表探针显示，混入另一个 PR 的同前缀 artifact 会在 `_terminal_row` 抛 identity mismatch，过期 artifact 会被静默过滤成空列表。

## P1

### P1-1：公开 marker 没有所有权校验，会覆盖他人评论

位置：`.github/actions/gate-aggregator/aggregate.py:123,797-800,880-898`；OCR 路径为 `.github/workflows/gate-v2.yml:590,598-620`。

触发：任何有 PR 评论写入权的用户或 bot 发送包含公开字符串 `<!-- gate-v2-status-panel:v1 -->` 的评论；下一次 aggregate 只按 body `contains` 找到它并 PATCH 全文。OCR 同样只按 `gate-v2-ocr-advisory:<reviewer>:v1` 找 marker，然后 PATCH。没有作者、bot 身份、评论创建者、评论 ID 所有权或“由本 workflow 创建”的证明。

违反：面板“一个 marker 评论”的身份守卫不唯一；internal P1 的“损坏他人数据”与面板纯投影不变式均被破坏。真实 PR 评论场景可触发，后果不可接受：普通讨论、审查结论或 bot 评论会被状态面板覆盖。`repository_id` 只校验 artifact，不保护评论归属。

### P1-2：历史 artifact 按仓库前缀取全量，其他 PR 的一条 artifact 会使整次重建失败

位置：`.github/actions/gate-aggregator/aggregate.py:761-787`。

触发：同一仓库存在两个 PR 的 `gate-terminal-v1-<repository_id>-...` artifact（这是多 PR 正常使用形态）。代码按 repository_id 前缀下载所有未过期 artifact，尚未按 PR 过滤就调用 `_terminal_row`；第一条其他 PR 记录在 `:730` 被判 identity mismatch，异常冒泡到 `:883-891`。

结果：有旧面板时保留 stale body；没有旧面板时连当前行也不 POST；receipt 只记 `history_unavailable`。因此一个无关 PR 会阻断当前 PR 的 sticky 历史与面板更新。实现文档声称“过滤每个 terminal record by ... PR number”，但当前实现是“遇到第一条不匹配即整批失败”。这是正常部署路径下的状态静默陈旧/不可复盘，后果不可接受，判 P1。

### P1-3：artifact 过期导致历史静默缩水；面板删除后与过期双失效没有诊断

位置：`.github/actions/gate-aggregator/aggregate.py:776-787,883-896,941-943`；`.github/workflows/gate-v2.yml:813-827`。

触发：`_fetch_terminal_history` 对 `expired` artifact 直接跳过，没有计数、告警、receipt reason 或“历史不完整”标记；terminal upload 也没有设置保留期之外的持久介质或 `retention-days`。当旧 artifact 到期而面板仍存在时，下一次运行会把缩水历史 PATCH 回去；当面板也被删时，历史为空，代码以当前行 POST 新面板，并把 delivery 记为正常 `created`。`:941-943` 对成功创建/更新直接不写诊断。

违反：任务卡要求每轮记录在 artifact 保留期外仍可读，且禁止历史静默缩水。面板是设计规定的兜底之一；面板删除 + artifact 过期时，旧行不可恢复、没有告警，用户看到的是看似成功但不完整的面板。两问均过：正常 retention 生命周期可触发，数据丢失且不可接受，判 P1。

### P1-4：评论发布发生在 terminal artifact 上传之前，产生不可逆的非权威状态

位置：`.github/actions/gate-aggregator/aggregate.py:992-1009`；`.github/workflows/gate-v2.yml:813-827`。

触发：aggregate 先把 Step Summary 写入，再把 `gate-terminal.json` 写到 runner 本地临时目录，然后立即 POST/PATCH 面板；真正的 `actions/upload-artifact` 在 aggregate 步骤之后才执行。如果 upload 403/5xx/超时、runner 被终止或 job 在 upload 前退出，评论已经对外可见，但权威 terminal artifact 没有发布。

违反：面板声明自己由持久化 terminal artifact 投影，然而当前行在 artifact upload 成功前就进入外部不可逆评论状态。之后没有 run 再触发时，用户会永久看到无法由权威源重建的行；下一次成功运行又可能把它删掉。该顺序违反“终态持久化成功后才做外部发布”的降层不变式，属于数据/状态静默不一致，判 P1。

### P1-5：find-marker → POST 没有并发闸门，同一 PR 的快速 push 会产生多个 sticky 面板

位置：`.github/workflows/gate-v2.yml:655-663`；`.github/actions/gate-aggregator/aggregate.py:880-898`。

触发：两个 run 并发进入 aggregate；二者均在 comments GET 时看不到 marker，均成功重建 history，随后均执行 POST。workflow 的 `gate` 没有 concurrency，ledger 的仓库级锁不覆盖 gate publisher。若一个 run 看到另一个刚创建的评论但读取到旧历史，PATCH 还会互相覆盖。

结果：违反“一个 aggregate status panel per PR”和创建计数≤1；POST 是会发邮件的不可逆动作，第二个评论会留下 stale panel，后续只 PATCH 第一个 marker。快速连续 push 是正常触发形态，静默重复通知和分裂状态不可接受，判 P1。

## P2

- `.github/actions/gate-aggregator/aggregate.py:880-881` 的评论 GET 只请求 `per_page=100`，没有分页；marker 位于第 101 条以后会被当作不存在并 POST 第二个面板。`_find_panel_comment` 也不处理多个 marker。与 P1-5 同属幂等边界，但需要超出 100 条评论的真实条件，单列 P2。
- `.github/workflows/gate-v2.yml:624-634` 将 OCR 的 PATCH 失败、POST 失败、超时/连接断开全部写成 `delivery=not_created`；PATCH 已可能在响应丢失前成功，不能证明“未创建/未更新”。这不改变 gate verdict，但会污染 fail-loud 第二出口的事实。
- `.github/actions/gate-aggregator/aggregate.py:728-740` 对一个损坏、旧 schema 或不匹配的历史 artifact 采用整批 abort；`schema_version` 还用 `!= 1` 而非严格类型检查。单条坏记录足以让历史不可重建，虽然当前 receipt 会报 `history_unavailable`，应隔离坏记录并保留可读历史。
- `.github/actions/gate-aggregator/aggregate.py:221-235,725-750` 只分别校验 classification/reason_code/gate_result 是否在域内，没有校验三者组合关系；可构造 `gate_result=pass` 但 `classification=code_fail` 的可解析 artifact，面板会照样展示。当前 producer 不会正常产生它，但跨 artifact 边界缺少一致性约束。
- 当前穷举测试只对 `PANEL_BUCKET_BY_GATE_RESULT` 与 `GATE_RESULT_DOMAIN` 做集合相等断言（`tests/test_gate_aggregator.py:1093-1103`）；classification/reason_code 的矩阵是手写 case（`tests/test_gate_aggregator.py:527-539`），新增 domain 值本身不会使测试必红。红验已证明 gate-result 映射断言有效，但未证明第二层 17 个值具备同等 future-value 约束。
- `tests/test_gate_aggregator.py:826-1112` 的新面板测试大量 mock `_fetch_terminal_history`、评论 GET 和 POST/PATCH；没有真实 artifact 列表分页/过期/跨 PR 夹杂、marker spoof、并发或 upload 顺序测试。producer fixture（`:998-1011`）确实来自 `aggregate.main`，但没有断言 workflow 实际发出的 artifact 名称、压缩包字节、subprocess env/argv。`tests/test_gate_v2_contract.py:103-126,352-409` 主要是字符串契约，不能锁住运行时 payload。
- diff 删除了旧 Stage 4 receipt 的失败保护，未等价迁移到 sticky delivery：缺 token、429、网络超时、warning 输出失败、receipt 清理/写入失败、stale receipt 毁损等场景原先有测试，现在均无对应断言。新路径仍保留这些分支（`.github/actions/gate-aggregator/aggregate.py:914-1042`），删除断言降低了回归保护。

## P3

- `.github/workflows/gate-v2.yml:581-584` 在 advisory 文件缺失时把 runner 临时绝对路径打印到日志；OCR body 又在 `:593-594` 原样转发 review-shadow 产物。当前未证实 token 或内部 URL进入 PR 评论，但新渲染层没有显式 redaction，至少应有信息卫生契约测试，按本项目现有证据列 P3。
- `.github/actions/gate-aggregator/aggregate.py:803-813` 的“guard-of-the-guard”宽泛吞错与 `:1021-1028` 多层 stale receipt 清理分支延续了旧实现；它们增加维护成本但不是本轮新增 P1。可在收口时用更短的单一 fail-open 出口替代，而不要继续扩展 fallback。

## 降层三问

### ① 终态写入成功之前的不可逆动作与中间态

顺序是：Step Summary append（`:972-976`）→ 本地临时 terminal JSON replace（`:992-997`）→ comments GET、artifact history GET/download、POST/PATCH（`:880-898`）→ delivery diagnostic 写入 Summary 与 receipt JSON（`:1009-1042`）→ workflow 才 upload terminal 与 delivery artifact（`gate-v2.yml:813-827`）。

因此中途失败的中间态是：Summary 可能已有裁决但 terminal 未生成；评论可能已有当前行但 terminal 未上传；评论成功但 delivery artifact 缺失；历史 GET 失败时旧评论保持 stale，首次运行则没有评论。P1-4 是顺序错误：不可逆评论在权威 artifact upload 之前。

### ② 守卫值在实际部署形态下是否唯一

不唯一。`run_id + run_attempt` 对单个 GitHub run 的 dedup key 合理；artifact 名称含 repository/head/run/attempt，但历史扫描实际只用 repository_id 前缀，且不先隔离 PR。`repository_id` 能防跨仓记录混入，却不能防同仓其他 PR artifact 导致整批失败，也不能认证 artifact producer。面板 marker 是公开 body 字符串，不是评论所有权凭证；OCR marker 只多了 reviewer 名称，同样可伪造。fork 的 read-only token 通过 GitHub 403 使写入 fail-open，但没有改变 marker 唯一性。

### ③ 保护的是“写入”还是“行为”

大部分保护只在函数或 mock 层：`test_status_panel_publisher_creates_once_then_patches`（`:846-881`）验证了顺序，但其 `_github_json`/`_fetch_terminal_history` 均是 stub，不能证明真实 HTTP 行为在并发下只 POST 一次；`test_status_panel_mail_invariant_for_five_runs`（`:957-995`）是同进程串行 fake，也没有 producer/upload 边界。`test_terminal_artifact_written_by_real_aggregate...`（`:998-1011`）只证明本地 producer JSON 可消费。真正的创建计数、评论归属、artifact upload 先后和 fork token 行为没有跨进程/HTTP 契约锁死。

## 专项审查点

### 1. terminal artifact 历史权威源

分页：实现使用 `per_page=100&page=N`，并在 page 长度小于 100 时终止（`:766-783`），这一点实现正确；但没有单页之外的错误/部分响应标记。API 失败、403/429、5xx、超时：异常被 `:883-891` 捕获，保留已有 body 或在无已有 body时不发布，并写 `history_unavailable` + Summary warning，属于有诊断的 fail-open。

过期：`:776` 直接排除，历史会静默缩短；terminal upload `:813-819` 没有 retention-independent 介质。跨 attempt：命名含 head/run/attempt，record 的 `run_id/run_attempt` 进入行并按 `run_id+run_attempt` 去重；但扫描前缀过宽，跨 PR 记录不会被跳过而是让整批抛错。面板被删 + artifact 过期时，`comments=[]`、`history=[]`，会成功 POST 只有当前行的新面板，无 `history_error` 或诊断；这是 P1-3。

### 2. 状态桶与第二层枚举

四个 `GATE_RESULT_DOMAIN` 有独立 bucket，`render_summary` 与 `render_status_panel` 都保留 classification/reason_code，没有把 17 个已知状态压成“未能执行完成”；`TERMINAL_REASON_DOMAIN` 的当前路径也在手写矩阵中出现。问题是新增 classification/reason_code 值不会由 domain 声明自动驱动测试，且组合一致性未校验（P2）。gate bucket 的集合断言红验已通过，说明那一处不是恒真测试。

### 3. gate-v2.yml 接线

aggregate parser 的参数均有对应 env/argv：quality、primary、runner、draft、review_expected、identity、audit source/name、audit dir、terminal、summary、panel delivery；contract tests 对多数 flag 与 panel path 有静态检查。`gate` 只依赖 quality/primary，`if: always()` 保留；OCR 不在 gate needs，OCR 失败不阻塞 required gate。`resolve_advisory`、OCR job 在 fork/hosted/draft 的 guard 下跳过，gate 仍在 hosted/read-only token 下运行，POST/PATCH 403 被 fail-open 记录。

不足是 contract 测试没有执行真实 shell argv/env，也没有验证 upload artifact 的真实 payload；因此“接线存在”已锁，“接线在 runner 上按预期消费”未锁。

### 4. 并发与幂等

无 gate concurrency，find→POST 竞态会双 POST（P1-5）。PATCH 没有 ETag/版本条件；最后写入胜，但每个 body 来源是各自时刻的 artifact 快照，可能暂时删掉另一个尚未可见/尚未上传的行。理论上下一次可由 artifact 重建，然而 retention、上传顺序和 artifact 夹杂 bug 使“最终可恢复”不成立。OCR 同样 lookup→POST/PATCH 无锁，且只取首个 marker。

### 5. 信息卫生（gate#61）

面板新渲染只输出 repository、run URL、run id、短 head SHA、classification/reason_code 和固定动作，没有 token、Authorization header、`GATE_HUB_DIR` 或内部 URL。OCR sticky wrapper 不把 token写入 payload；但它原样转发 advisory 文本，错误日志暴露 runner 临时路径，缺少 redaction/契约测试。未发现足以升级为 P1 的已证实 public secret leak，记 P3。

### 6. 测试真实性与删除保护

全量 426 passed；producer fixture 确实调用真实 `aggregate.main` 并读取其写出的 JSON，不是手造 terminal dict。面板 publisher 的历史与 HTTP 层大多是 mock，不能捕捉上述跨 PR、过期、marker spoof、并发和 upload 竞态。旧 receipt 测试删除中，仍适用于当前代码的 fail-open、receipt 原子写入/清理、网络超时/429、missing token、warning BrokenPipe 等保护未迁移；这是 P2 测试回归，不应把 426 解释成面板全链路绿。

### 7. 简化机会（不降保护）

1276 行净 diff 中有大量新增解释性注释、重复的 OCR lookup/publish 错误诊断 shell，以及 receipt 清理的多层分支。可在收口卡中压缩注释、把相同诊断字段的 shell 分支统一为一处最小逻辑，并恢复而非删除可复用的故障注入测试；不建议为 P2 继续增加状态或重试机制。该项 ≤P2，不影响本轮 P1 判定。

## 轴表逐格核验

标记：`✓` 有直接测试；`△` 共享实现路径或仅静态覆盖；`✗` 本 diff 缺少该格的约束力。

### 轴 1：裁决状态 × 面板存在性

| 裁决 \ 面板 | 不存在 | 完好 marker | 被删 | marker 损坏/不存在 |
|---|---|---|---|---|
| pass | ✓ 首次 POST 路径（`:846-873`） | ✓ PATCH 路径（`:874-881`） | ✓ 删除后 GET=[]、由历史重建（`:884-904`） | ✗ 未验证“损坏 marker”与普通无 marker 的区别 |
| fail | △ 同一 publisher 路径，当前行测试不是 fail 终态 | △ 可 PATCH，但无 fail×完好格测试 | ✗ 未验证 fail 历史删除重建 | ✗ 未验证 |
| skipped | △ renderer 有 skipped 测试，delivery 无该格 | ✗ 无 skipped×完好格 | ✗ 无 skipped×被删格 | ✗ 无 skipped×marker 损坏格 |
| unavailable | △ HTTP/历史失败测试只用 pass current | ✗ 无 unavailable×完好格 | ✗ 无 unavailable×被删格 | ✗ 无 unavailable×marker 损坏格 |

另有未覆盖组合：面板存在但历史 API 失败时应保留旧体；面板被删且历史过期时应显式诊断，当前实现反而静默成功。

### 轴 2：触发形态

| 形态 | 实现观察 | 测试结论 |
|---|---|---|
| 首 run | 无 marker→历史→POST | `✓` 但历史被 mock |
| rerun attempt>1 | artifact resolver 选 `attempt<=current`，面板 key 为 run_id+attempt | `△` 只有 primary audit cross-attempt 测试，无 panel/runtime artifact 测试 |
| 新 push | current row 与历史 rows 合并、按 run identity 排序 | `△` 串行 fake 覆盖，未测真实 artifact visibility/上传顺序 |
| draft→ready | caller template 含 `ready_for_review`，primary 从 skipped 转执行 | `△` 有静态 trigger/guard，缺端到端 panel 迁移格 |
| fork 只读 token | advisory jobs 跳过；gate hosted，评论写入 403 fail-open | `△` 有通用 403 单元格，无 fork token/实际 `gh` REST fixture |

### 轴 3：评论 API 失败 × verdict 不变 + 第二出口

| 失败 | aggregate 行为 | 约束力 |
|---|---|---|
| POST 403 | warning、`not_created`、gate outcome 后续仍按原值返回 | `△` 有 POST 403 helper 测试，未断言 `main` exit 与 receipt upload |
| PATCH 403 | 保留旧评论、warning、`not_created` | `△` 有 helper 测试，未覆盖 stale body/第二出口真实字节 |
| 5xx | lookup 5xx 有 warning；publish 5xx走宽泛 fail-open | `✗` 新测试的 500 是 lookup mock，不是 POST/PATCH真实调用 |
| 超时/连接断开 | generic `unknown/network_error`，不重试 | `✗` 旧超时保护被删；无当前跨边界测试 |

共同缺口：没有测试证明“verdict JSON/exit code不变 + warning/receipt/Step Summary 第二出口都按实际 producer 输出”，只有函数级 mock 与静态 YAML 字符串检查。

## 轮次状态

这是 R1；本轮有新增 P1，不能计入“连续两轮无新增 P1”。按 internal/infra 规则，后续收敛仍需另一轮使用不同证据源/视角复验；主脑应优先把 P1-1、P1-3、P1-4、P1-5 纳入系统性收口，而不是只补单点断言。
