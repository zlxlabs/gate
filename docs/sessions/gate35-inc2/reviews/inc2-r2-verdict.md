FAIL

# gate#35 增量 2 第 2 轮独立评审

审查对象：

- base：`1d6a2f05b756052e77c155cf1f9db8fb156cefde`
- H0：`e9fed77adf1e0f9cb61221adef8f05544b8b4c02`
- H1：`ff796e400fae82acb5c5db1967e110012bbb1f28`

审查时间：2026-08-20（Asia/Shanghai）  
执行器与模型：Codex / GPT-5（`delegate --class big`）  
风险等级：仓库未声明 `risk-tier`，按 internal；本 diff 属 infra/状态机类，按 saas 档收敛。  
本轮新证据：GitHub API 实测 `gh api repos/zlxlabs/gate/environments` 返回环境总数 0、名称为空；H1 producer/consumer 跨进程探针实测普通 `ordinary-write` actor 可写出 receipt，consumer 返回 `active_false_positive`；同 receipt 重放、换 primary run、撤销及 2099 远期过期时间均按代码结果验证。OCR 前置扫描运行约 190 秒后无 envelope，手动中止并按 timeout/skipped 记录，未把它当作已审过。

## A. 修复增量审四问（H0..H1）

### 1. 是否只修登记在案的 5 条

否。五条主修复均能在当前 diff 中对应：

- P1-1：`.github/workflows/gate-v2-disposition.yml:71-77` 按 run/attempt 构造完整 primary audit 名称。
- P1-2：`.github/workflows/gate-v2-disposition.yml:97-145` 从完整 Scope 重算 epoch；`.github/actions/gate-disposition/issue_receipt.py:224-258` 不再从 audit 盲读 `.epoch`。
- P1-3：`.github/workflows/gate-v2-disposition.yml:147-196` 只允许读取 Git blob 并交叉校验 SHA-256/Git blob SHA；`.github/actions/gate-disposition/issue_receipt.py:155-181` 只消费已验证 manifest。
- P2-1：`.github/actions/gate-aggregator/convergence.py:163-220`、`:581-596` 将完整 Scope 纳入 receipt 并消费时完全比较。
- P2-2：旧的 `maintainer:` 前缀路径已删除；当前 actor/PR author 约束在 `.github/workflows/gate-v2-disposition.yml:198-208`，producer 的字符串约束在 `.github/actions/gate-disposition/issue_receipt.py:203-212`。

但 `bf82672` 夹带了未登记的 public 行为改动：`.github/actions/gate-aggregator/convergence.py:269-277` 新增 `DispositionConsumption.malformed_inputs`，并在 `:694-703,740-746` 改为把原始畸形输入移到新字段、把 `rejected_receipts` 改存空的 typed receipt。它不改变正常 gate 结果，但不是上述 5 条中的修复，因此本问判否。`ff796e4` 将 repository id 改为 runtime identity，属于 P2-1/P1-2 的 Scope 绑定修复，不另算夹带行为。

### 2. 是否新增未经批准的抽象

有一项未经批准的输出状态/接口形态：`DispositionConsumption.malformed_inputs`（`.github/actions/gate-aggregator/convergence.py:269-277`）。它没有第二个消费者，且本卡没有批准新增该字段。新增 `scope`、evidence manifest 和 canonical Scope 计算则分别直接对应 P2-1、P1-3、P1-2，不计为未经批准抽象。

### 3. 是否无依据增加状态、事实源或 fallback

未发现新增「先试 A、失败退 B」的事实源双路径。新 scope 由 `.github/workflows/gate-v2-disposition.yml:97-145` 唯一构造并 fail-fast；缺 scope 字段或 epoch 不匹配在 `.github/workflows/gate-v2-disposition.yml:107-118`、`.github/actions/gate-disposition/issue_receipt.py:149-151` 直接失败；证据缺失、非 allowlist ref 或校验不一致在 `.github/workflows/gate-v2-disposition.yml:159-196` 直接失败。`malformed_inputs` 是额外诊断输出，但不是新的状态事实源或 fallback。H1 未新增 retry、fallback 或防御式吞错。

### 4. 是否留下双路径

未发现五条修复的旧逻辑与新逻辑并存：artifact 名已统一为 `.github/workflows/gate-v2-disposition.yml:71-72` 的完整 run/attempt 名；epoch 由 `.github/workflows/gate-v2-disposition.yml:139-145` 与 `.github/actions/gate-disposition/issue_receipt.py:243` 从 Scope 派生；evidence 只走 `.github/workflows/gate-v2-disposition.yml:161-180` 的 blob allowlist；旧 `maintainer:` 判断不再存在；receipt digest 的 scope 比较统一走 `.github/actions/gate-aggregator/convergence.py:581-586`。workflow 的 `epoch` input（`:13`）只服务 revoke 分支（`:253-257`），不是 issue 分支的旧 epoch fallback；`approval_ref` 仍是字段，但已被强制为 synthetic `issuer-not-pr-author:<author>`，不是旧的 `maintainer:` 路径。

## B. 对抗视角全量审（base..H1）

| 身份/路径 | 能否成功 | 卡在哪一行 | 实测证据/边界 |
|---|---|---|---|
| 1. PR author 本人 dispatch issue | 直接自批不能成功。 | `.github/workflows/gate-v2-disposition.yml:205-207` 比较 `CURRENT_PR_AUTHOR` 与 `GITHUB_ACTOR`；`.github/actions/gate-disposition/issue_receipt.py:205-207` 也 fail-fast。 | H1 跨进程 producer/consumer 探针覆盖了同一约束的 producer 层；但 author 可转而请求另一名 write 用户走路径 2。 |
| 2. 另一个 write 用户但非 maintainer | 可以成功，且是本轮 P1。 | `.github/workflows/gate-v2-disposition.yml:34-37` 只声明名为 `gate-disposition` 的 environment；没有角色/API 校验，唯一 issuer guard 仍只是 `:205-207` 的“不是 PR author”。`.github/actions/gate-aggregator/convergence.py:603-604` 也只检查字符串非空。 | 已实测 `gh api repos/zlxlabs/gate/environments`：`total_count=0,names=[]`，所以此刻 environment 实际不拦任何人。H1 跨进程探针以 `issuer_login=ordinary-write` 返回 producer `0`、receipt `written=true`，consumer 状态为 `active_false_positive`。 |
| 3. 能改 `.github/` workflow 的人修改 disposition workflow | 当前代码没有自保护；但 PR 分支版本要等进入默认分支后才由这个 `workflow_dispatch` 控制面执行，当前 PR 是否会被真实 `gate/gate` 拦住属于增量 3 的 Required Check/branch-protection 实证。 | `.github/workflows/gate-v2-disposition.yml:3-4` 只有 `workflow_dispatch`，没有针对自身 diff 的审查或 trusted-ref guard；`.github/workflows/gate-v2-disposition.yml:23-27` 也没有把环境/审批身份写入 receipt。 | 代码层可确定“该文件没有自保护”；真实合并闸与该 workflow 的联动尚未通电，故不把它单独计 P1，列增量 3 backlog。 |
| 4. evidence 伪造 | blob 必须先能被本仓 Git blobs API 读取；攻击者可以先把自己写的 blob 推到自己的分支，再以其 SHA 使用。 | `.github/workflows/gate-v2-disposition.yml:161-175` 只检查本仓 `git/blobs/<sha>`、内容 SHA-256 与 Git blob SHA；没有检查 blob 是否从 PR/base 可达，也没有 commit/branch provenance。 | 这确实是内容不可变的 blob 指针，但不是来源/相关性证明。按 spec §2.3 C3 的字面最低要求（immutable ref + 对应摘要）它可通过；spec 未要求 reachability，故本项不另列 P1。当前真实 environment 缺失时，它与路径 2 组合即可被普通 write 用户用于签发。 |
| 5. nonce / receipt 重放 | 同一合法 receipt 在同一 round 重放为 no-op；换 primary run、epoch、scope 不能消费；有 revocation index 时撤销后不能消费。 | `.github/actions/gate-aggregator/convergence.py:581-623` 做 scope/epoch/run/audit/expiry/revocation 校验；`:691-739` 做 nonce 冲突与重复处理。 | H1 探针输出：重复状态 `duplicate_nonce_noop`、换 run `primary_run_id_mismatch`、撤销 `revoked`。但真实 artifact 下载并传入 aggregator 尚未通电，见增量 3 backlog。 |
| 6. audit 来源 | 可以影响，且是本轮 P1：调用方可指定任意 run id，只要该 run 有同名 JSON artifact 且 payload 自报字段通过检查。 | `.github/workflows/gate-v2-disposition.yml:54-77` 直接使用输入的 `PRIMARY_RUN_ID/ATTEMPT` 和名称下载，只用 `.head_sha/.pr/.run_id/.run_attempt` 自报字段 jq 校验；没有查询该 run 的 workflow id、ref、job、conclusion、artifact id 或 canonical primary provenance。producer 随后在 `.github/actions/gate-disposition/issue_receipt.py:224-233` 只相信这份 audit。 | 这是当前 diff 的代码可确定路径：write 用户可用其分支上的 workflow run 上传相同命名 artifact，再 dispatch 指向它；不依赖增量 3 的 aggregator。未跑真实 workflow，真实 canary 仍列 backlog。 |
| 7. 时间 | 可以给很远未来；当前实现仍接受，只要 `expires_at > issued_at`。 | 签发时间来自 runner `date`：`.github/workflows/gate-v2-disposition.yml:248`；过期时间直接来自 dispatch input：`:21,224`；消费时以调用方传入的 `now` 比较：`.github/actions/gate-aggregator/convergence.py:605-613`。 | H1 探针以 `expires_at=2099-08-20T08:00:00Z` 返回 `active_false_positive`。spec §2.3 只要求 expiry/失效，没有最大 TTL，因此本轮不把“远期时间”升级为 spec finding；签发与消费 runner 的时钟一致性留真实 canary 观察。 |

