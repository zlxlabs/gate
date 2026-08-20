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

## Findings

### F-1 — P1：实际不存在的 environment 让任意非 author 的 write 用户充当 issuer

- 严重级别：P1。
- 文件：`.github/workflows/gate-v2-disposition.yml:34-37,198-208`；`.github/actions/gate-disposition/issue_receipt.py:203-212`；消费端 `.github/actions/gate-aggregator/convergence.py:603-604`。
- 违反 spec：`docs/design/clean-streak-convergence.md` §2.3 轴 C「签发申请」：只有 owner/maintainer 可通过受保护 workflow dispatch，普通评论者/PR author/committer/reviewer 不能自批；不变式 9（issuer provenance 必须受保护）。
- 具体失败场景：用户 `bob` 具有 write 权限但不是 maintainer，且不是目标 PR author。`bob` dispatch `operation=issue`，提交真实存在的当前 audit、一个可读取的 blob evidence、任意 reason 和未来 expiry。代码唯一身份阻断是 `CURRENT_PR_AUTHOR == GITHUB_ACTOR`；`issuer_login/user_id/approval_ref` 只需非空，且 workflow 自己拼接 `issuer-not-pr-author:<author>`。GitHub API 实测 `gh api repos/zlxlabs/gate/environments` 返回 `total_count=0,names=[]`，因此 `environment: gate-disposition` 当前没有 required reviewer，`:34-37` 实际不拦任何人。H1 跨进程探针以 `issuer_login=ordinary-write` 返回 producer `returncode=0, written=true`，随后 consumer 返回 `active_false_positive`、`fail_closed=false`；这不是旧的 `maintainer:` 前缀问题，而是部署事实使“非 author 即可”成为可消费授权。
- 建议修法：先在 GitHub 配置并验证 `gate-disposition` environment 的 required reviewers；control job 必须读取真实 deployment/environment approval 的 approver login/user id 与时间，核对其 maintainer/owner 资格及与 PR author、committer、reviewer 的区分，并把该已验证 provenance 写入 receipt。environment 不存在或没有有效审批时直接拒绝，不以 synthetic `approval_ref` 代替。

### F-2 — P1：control job 可被调用方指定的任意 Actions run/artifact 伪造 canonical audit

- 严重级别：P1。
- 文件：`.github/workflows/gate-v2-disposition.yml:54-77`；真实 canonical producer 的 artifact 约定为 `.github/workflows/gate-v2.yml:441-457`；receipt producer 消费 audit 的入口为 `.github/actions/gate-disposition/issue_receipt.py:184-233`。
- 违反 spec：`docs/design/clean-streak-convergence.md` §2.3 轴 C「签发申请/绑定」第 2 条：control job 必须重新下载当前 canonical primary audit 并确认 exact finding、scope 与原始 bytes；不变式 3（canonical audit digest）和不变式 4（目标 primary run/attempt 必须是可信来源）。
- 具体失败场景：write 用户在自己的 PR 分支添加或 dispatch 一个任意 Actions workflow，让它上传名为 `primary-audit-v2-<repository_id>-<current_head>-<attacker_run_id>-1` 的 JSON artifact；JSON 自报当前 PR 的 `head_sha/pr/run_id/run_attempt`、完整 scope 字段，并在 `result.findings` 放入任意 P1。随后 control dispatch 把 `PRIMARY_RUN_ID/ATTEMPT` 指向该 run。`:72` 只按输入 run id 和名称下载，`:76-77` 只检查 artifact 自己声称的四个身份字段；没有查询 run 的 workflow id/path、ref、event、conclusion、primary job、artifact id 或上传来源。`:224-233` 随即按这份 attacker-controlled bytes 找到 P1 并签发 receipt。因 receipt digest 绑定的是伪造 bytes，后续纯 consumer 仍会把它视为当前 exact finding 的 active false-positive；这是来源认证缺失，不是 R1 已修的 artifact 名称错误。
- 建议修法：control job 只接受 GitHub API 明确识别为 canonical `gate-v2.yml` primary job 的 run；校验 run 的 repository、workflow identity/ref、head/base、event/conclusion、primary job 及唯一 artifact id/name，再读取该 artifact 的原始 bytes。拒绝由调用方仅凭 run id/name 指定的任意 artifact，不引入第二套 fallback 来源。

## Backlog（不计入本轮 P1）

### 存量 / 规格澄清

- 仓库缺 `risk-tier` 声明，继续跟踪 open issue #75；本轮按卡面规定的 internal + saas 收敛处理。
- artifact retention/浅 checkout 与 diagnostics 上传问题（open issue #38、#63）不在本轮新增 diff 内。
- evidence 目前验证的是“本仓可读取的 blob 内容不可变”，不验证 blob 是否从 base/PR commit 可达、是否具备语义相关性；由于 §2.3 C3 只明文要求 immutable ref + 对应摘要，本轮不把它判为违反已写 spec 的 P1。若产品要求来源/相关性证明，应先补 spec，再选择 commit reachability 或受信 artifact provenance。
- `expires_at` 没有最大 TTL，且 revoke producer 的 `evidence_ref` 仍是字符串形状；当前 spec 没有最大 TTL/撤销证据读取的已通电消费契约，先作为设计澄清与 canary 项，不新增 P1。

### 增量 3 前置项

- aggregator 必须从 GitHub artifact 分页检索并下载 disposition receipt/revocation，保留 artifact id、source attempt、原始 bytes 和完整 scope；当前 `replay_receipts` 仍未接入 disposition（`.github/actions/gate-aggregator/convergence.py:1609-1627` 传 `waiver_receipts=()`）。
- 增量 3 需要真实 workflow canary：验证 canonical audit 来源校验、control dispatch、环境审批/角色、evidence blob reachability、同 nonce 重放/冲突、撤销、expiry、新 epoch 和 artifact retention。
- 必须实证 PR 修改 `.github/workflows/gate-v2-disposition.yml` 时的 branch protection/Required Check 行为；当前控制 workflow 没有自 diff guard，且本轮没有真实 `gate/gate` 通电证据。
- 需要验证 `gate/gate` 只消费通过 provenance 校验的 disposition，receipt upload/下载失败不会退化为 first-run clean；这些属于尚未通电的 aggregator/Required Check 边界。
- 旧 epoch disposition 在 consumer 被传入时应只产生 stale 诊断、不阻塞新 epoch；artifact 过滤与传入方式尚未接线，保留为增量 3 的既有前置项，不在本轮重报为 finding。

## 越界意见

本轮没有把以下事项列为 finding：aggregator 从 GitHub artifact 下载 receipt 并交给 reducer；真实 `gate/gate` Required Check 因 disposition 变绿；从 artifact 读回上一轮 `ConvergenceState` 并重放；control workflow 的真实 dispatch/canary。它们按 spec §3 的增量 3 归属进入 backlog。F-1 是当前已知 GitHub environment 状态与当前 diff 的 issuer 检查组合即可确定；F-2 是当前 control job 的 run/artifact provenance 检查缺失即可确定，未把真实 workflow run 当作已完成实证。

## 与第 1 轮的关系

| R1 登记项 | 本轮裁决 | 依据 |
|---|---|---|
| P1-1：canonical audit artifact 名错误 | 修好 | `.github/workflows/gate-v2-disposition.yml:71-77` 已包含 run/attempt；同时 H1 contract 断言该完整名（`tests/test_gate_v2_contract.py:119-121`）。这不排除 F-2 的“来源可伪造”，后者是新失败场景。 |
| P1-2：盲读不存在的 audit `.epoch` | 修好 | `.github/workflows/gate-v2-disposition.yml:97-145` 从当前 PR + audit 的完整 scope 调用 `derive_epoch`，`.github/actions/gate-disposition/issue_receipt.py:133-152,224-258` 再校验 scope/epoch；H1 producer/consumer 探针使用派生 epoch，未读取 audit `.epoch`。 |
| P1-3：evidence ref 不读取验证 | 修好 | `.github/workflows/gate-v2-disposition.yml:161-175` 调本仓 Git blobs API、SHA-256 与 `git hash-object` 交叉校验；`.github/actions/gate-disposition/issue_receipt.py:155-181` 拒绝未验证 manifest。路径 4 的“可达性/语义”是 spec 未写的澄清项，不重报为旧 P1。 |
| P2-1：receipt digest 不承载完整 scope | 修好 | `.github/actions/gate-aggregator/convergence.py:195-220,581-586` 把 scope 放入 digest payload 并完全比较；`.github/actions/gate-disposition/issue_receipt.py:133-152,238-258` 由同一 Scope 派生并写入 receipt。 |
| P2-2：approval ref/issuer provenance 可伪造 | 未完全修好；旧路径已删，但新路径仍不证明真实 approver | H1 删除了 `maintainer:` 前缀并拒绝 PR author（`.github/workflows/gate-v2-disposition.yml:198-208`），但当前 environment API 实测不存在，consumer 只检查非空字符串（`.github/actions/gate-aggregator/convergence.py:603-604`）。F-1 使用了新的部署状态与新的“普通非 author write 用户可消费”失败场景，未原样重报 R1 的 prefix finding。

## Review 运行记录

- 未运行测试套件，符合本卡要求。
- 运行 `git diff --check H0..H1`，无输出即通过。
- H1 临时跨进程探针输出摘要：`producer_returncode=0`、`producer_issuer=ordinary-write`、`consumer_status=[true,true,true,"active_false_positive"]`、`duplicate_nonce_noop`、`other_run_status=primary_run_id_mismatch`、`revoked_status=[false,false,"revoked"]`、远期 expiry 仍 `active_false_positive`。临时目录由 Python `TemporaryDirectory` 自动清理。
- OCR 前置扫描：background 文件 6000 bytes；主腿运行约 190 秒无 review envelope，按 timeout/skipped 记录并中止。stderr 显示本地 verify funnel 在等待子进程时被 KeyboardInterrupt；没有把空 findings 当作已审。
