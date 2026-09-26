# gate

全部 zlxlabs / 个人仓库共用的**复用 pre-merge 门禁**（lint / tests /
Codex review）。这是私有 `zlxlabs/gate-hub` 的
"纯逻辑公开半"——本仓提供 reusable workflows 和契约测试；仓库清单
（registry）、Codex review 的 prompt/策略（烧在 self-hosted runner 镜像里）、
runner 基建、onboard 工具全部留在私有 gate-hub。

## 为什么单独一个公开 org 仓（2026-07-09）

1. **公开仓不能 `uses:` 私有仓的 reusable workflow**（GitHub 硬限制，0-job 启动
   失败）→ 门禁本体必须公开，公开仓（obsidian-clip-api / youtube_download_api）
   才能接入。
2. **org runner group 的 `restricted_to_workflows` 白名单只接受 org 内仓库的
   workflow**（实测：个人账号下的公开仓也不行）→ 必须是 `zlxlabs/` 下的仓库，
   白名单这道硬闸才配得上。曾短暂落在 `zj1123581321/ci-templates`，同日因此迁出。

## caller（每仓 ~10 行，由 gate-hub 的 onboard-repo.sh 生成）

```yaml
permissions:
  contents: read
  pull-requests: write        # codex review 要发 PR 评论
jobs:
  gate:
    uses: zlxlabs/gate/.github/workflows/gate-v2.yml@v2
    with:
      tier: personal          # personal | internal | saas
      runner: self            # self(自建两台, 有 codex review) | hosted(免费分钟)
      # 可选覆盖: max_diff_lines: 4000, max_review_shards: 8, pr_size_warn_lines: 8000
    secrets:
      SILO_ACCESS_KEY: ${{ secrets.SILO_ACCESS_KEY }}
      SILO_SECRET_KEY: ${{ secrets.SILO_SECRET_KEY }}
      FEISHU_CI_WEBHOOK: ${{ secrets.FEISHU_CI_WEBHOOK }}   # 公开仓必须 secret;私有仓可用同名 variable 兜底
```

### 仓库自有质量入口（推荐）

接入仓库可以在仓库根目录提供固定入口 `scripts/gate-quality`，由业务仓库拥有完整的
质量流水线（依赖安装、lint、unit/integration/e2e 测试及其隔离方式）；gate 只负责
调用、资源、权限、超时、聚合与审计。

```bash
chmod +x scripts/gate-quality
```

gate checkout 后从仓库根目录以独立进程执行 `./scripts/gate-quality` 一次，并传入
临时目录环境变量 `GATE_ARTIFACT_DIR`。入口进程的退出码原样决定 quality 结果：非零
即失败，gate 不重试、不替换命令，也不接收任意测试命令输入。

迁移期间，入口缺失会输出醒目的弃用告警并继续 legacy 自动探测；入口路径存在但不可
执行会立即失败且不会回退。入口可执行后，legacy 的 install/lint/duplicate/test 猜测
步骤全部跳过。未迁移仓库应尽快补上入口，避免依赖兼容路径。

## Required Gate v2 + Shadow Calibration v2

当前 Required Gate caller 使用 `gate-v2.yml@v2`。`shadow-review-independence` 计划
（2026-07-24 定稿，私有 `zlxlabs/gate-hub` 仓
`ceo-plans/2026-07-24-shadow-review-independence.md`）将 Required Gate 与 Shadow
Calibration 拆成两个独立 reusable workflow；旧版 `gate.yml` 已删除。

### 两个 reusable workflow

| workflow(`name:`) | job 拓扑 | 说明 |
|---|---|---|
| `.github/workflows/gate-v2.yml`(`gate`) | `quality` ∥ `primary` → `gate`(`needs: [quality, primary]`,`if: always()`) | Required Gate。`gate` job id 与 `name:` 都字面等于 `gate`，required status check context 为 `gate / gate`。 |
| `.github/workflows/gate-shadow-v2.yml`(`gate-shadow`) | `resolve` → `shadow`(matrix，每 reviewer 一个 job)→ `summary` | Shadow Calibration。**不产生任何 required status check**，只用于校准；失败/取消/超时不影响 Required Gate。 |

PR1 的 `REVIEW_RUN_MODE` 由两个 reusable 的实际 review entry step 显式固定为
`PAYLOAD_ONLY`，不是 `workflow_call` input；这是当前唯一合法模式，待真正启用
`FULL_SOURCE` 时再引入 caller-level 契约。

### `gate-v2.yml` inputs(`workflow_call`)

| input | 默认值 | 说明 |
|---|---|---|
| `tier` | `personal` | `personal` / `internal` / `saas` |
| `runner` | `self` | `self`（自建，跑 `primary` review）/ `hosted`（免费分钟，`primary` 整个 job 跳过） |
| `has_ui` | `false` | 兼容输入，当前 workflow 未消费 |
| `design_doc` | `""` | 设计文档路径 |
| `max_diff_lines` | `4000` | 单轮 review diff 预算 |
| `max_review_shards` | `8` | 大 PR 完整覆盖预算 |
| `pr_size_warn_lines` | `8000` | 强警告线 |
| `timeout_minutes` | `45` | 仅 `quality` job 的硬超时 |
| `primary_timeout_minutes` | `25` | `primary` job 自己的超时预算，与 `timeout_minutes` 解耦——需要给 `review-primary` 留出「GitHub 硬 SIGKILL 前写完并上传 canonical audit」的收尾余量 |
| `control_runner` | `""` | `gate` 聚合器与 `notify` 的 runner 池：留空（默认）跟随 `runner` 走自建（gate#27 起，取整税修复）；`github-hosted` 把控制面钉回 hosted，是单仓回滚逃生舱；遗留值 `self-hosted-control` 等价于留空 |

### `gate-shadow-v2.yml` inputs(`workflow_call`)

| input | 默认值 | 说明 |
|---|---|---|
| `tier` | `personal` | 应与 `gate-v2.yml` caller 保持一致 |
| `runner` | `self` | `hosted` 时整个 workflow 不跑任何 shadow job |
| `design_doc` | `""` | 应与 `gate-v2.yml` caller 保持一致 |
| `max_diff_lines` | `4000` | 应与 `gate-v2.yml` caller 保持一致，让 primary/shadow 评审同一份 diff/覆盖预算 |
| `max_review_shards` | `8` | 同上 |
| `shadow_timeout_minutes` | `15` | shadow matrix job 的硬超时；默认内部预算为 `780s`，caller 可调高以覆盖较慢的 detached shadow |

刻意**不**镜像 `has_ui`/`timeout_minutes`/`pr_size_warn_lines`/`primary_timeout_minutes`/
`control_runner`：这个 workflow 没有 `quality` job，也没有 required 聚合器，这些字段没有
对应语义。`shadow_timeout_minutes` 只作用于 shadow matrix，不影响 Required Gate 的
primary。它必须是无前导零的整数，最终有效范围为 4–60；其中 2 分钟固定留给 checkout、
Jobs API 查 job id 和 artifact 上传，内部预算按 `(shadow_timeout_minutes - 2) × 60` 秒计算。
下游 gate-hub `scripts/review/job_budget.py:61-70` 还会保留 30 秒 kill grace 与 60 秒
finalize reserve；本 workflow 要求扣除这 90 秒后仍至少剩 30 秒 hop budget，所以 4 分钟
是可接受的最小值（120 - 90 = 30）。非法、前导零、科学计数法文本或超过 60 的值会在
`resolve` job fail-fast，即使仓库没有 shadow 腿也不会静默接受。未传入参时仍是 15 分钟
job 上限与 780 秒内部预算。也没有 `secrets:` 声明——`gate-shadow-v2.yml` 从不调用外部
webhook，也从不发 PR 评论（发校准收据是计划 T6 的范围，尚未实现）。

### caller 模板位置

`templates/caller-gate-v2.yml` / `templates/caller-gate-shadow-v2.yml` 是两份独立的
caller 模板，分别调用 Required Gate 与 Shadow Calibration。两个 workflow 分开接入；
只配置 Required Gate 不会自动启用 Shadow Calibration。

### 调用版本

调用方使用移动标签 `@v2`，例如
`zlxlabs/gate/.github/workflows/gate-v2.yml@v2`。`.github/workflows/v2-tag-sync.yml`
会在 canary 验证通过后自动推进 `v2`；caller 无需随本仓每次合并更新 SHA。

### Silo 产物存储（gate-v2.yml）

`gate-v2.yml` 的八类 run 内产物（review-ledger-input / primary-audit / diagnostics /
advisory-event / convergence-receipt / gate-terminal / status-panel-delivery /
codex-review-ledger）改走 Silo bucket `ci-artifacts`，不再上传 GitHub Actions
artifact。Caller 必须透传两个 org 级 secret（`workflow_call.secrets` 声明为
`required: false`，与 `FEISHU_CI_WEBHOOK` 同模式）：

- `SILO_ACCESS_KEY`
- `SILO_SECRET_KEY`

下游 caller 不传时，S3 步骤明确报错（文案含「SILO_ACCESS_KEY 未传入」），禁止静默跳过。
fleet 正常拓扑是全 self-hosted；`runner: hosted` 或控制面落到 GitHub-hosted 时，
MagicDNS `100.100.100.100` 解析 Silo 主机名失败即红，没有 GitHub artifact 兜底。

Silo 推广已完成，`.github/v2-tag-sync.hold` 熔断文件已移除，`v2` 移动 tag 随主干前移。
下游 caller 仍必须透传 `SILO_ACCESS_KEY` 与 `SILO_SECRET_KEY`（不传则 S3 步骤红）。

### org runner group 白名单运维要点（新仓接入）

org runner group 的 `restricted_to_workflows` 白名单只在新仓接入时检查。升级 `@v2`
不需要改白名单；若列表仍有旧版 `gate.yml@refs/heads/main`，管理员可以移除该条目。

白名单只存在于**评审池** Default（id=1）；CI 池 `ci`（id=4）
`restricted_to_workflows=false`，不需要改动。

新仓接入时，先读完整白名单，再按仓库实际启用的 workflow 放行：

   ```bash
   # 先看当前白名单(group id 因 org 而异,这里以 zlxlabs 的 id=1 为例)
   gh api orgs/zlxlabs/actions/runner-groups/1 --jq .selected_workflows
   ```

### 旧版工作流已删除

旧版 `.github/workflows/gate.yml` 已删除，当前统一使用 `gate-v2.yml@v2`。runner group
白名单若仍保留旧版 `gate.yml@refs/heads/main` 条目，可由管理员移除；本仓库 PR 不修改
org 设置。

### 已知边界（fleet 推广前必须补齐）

- **fork PR / `runner: hosted` 的 `not_expected` 审计尚未接线**：这两种场景下
  `primary` job 目前是**整个 job 跳过**(`if:` 条件判断 draft/fork/runner)，`gate`
  聚合器靠重算同一份表达式来接受这个 `skipped` 结论，而不是去读一份真正写入的
  `not_expected` canonical audit。
- **聚合器现在无条件拒绝 `not_expected`/`waived` 两个 verdict**——`aggregate.py` 的
  `PRIMARY_VERDICT_DOMAIN` 里保留了这两个值的位置，但当前实现把它们当成不合法输入
  直接拒绝（canary 阶段的 `primary` job 从不会合法产出这两个 verdict，出现即视为
  异常，详见该文件模块 docstring）。真正接上 fork/hosted 的 waiver/not_expected 写入
  路径、并让聚合器补上配套字段校验（`not_expected_reason` 枚举域、
  `waiver.approved_at` 的 ISO-8601 时间校验)，是 fleet 推广到有 fork PR 或 hosted
  仓库场景之前的必修前置项，当前尚未开工。

## PR 体积预检和 review 效果账本

checkout 后、lint/test/Codex 前会先按与 Codex 相同的完整 binary diff 口径测量 PR：

- 不超过 `max_diff_lines`（默认 4,000）：单轮 review。
- 超过单轮预算但不超过 `pr_size_warn_lines`（默认 8,000）：自动完整分片，并在 sticky comment 提醒下次拆小。
- 超过强警告线、但仍在 `max_diff_lines × max_review_shards`（默认 32,000）内：继续完整分片 review，同时给出强警告。
- 超过完整覆盖预算：预检直接失败，要求 small PR / stacked PR；不会消耗 Codex 后再说审不完。

每次 run（包括测试失败、体积拦截、review waiver 和 review unavailable）都会尽力生成
`codex-review-ledger` artifact，保留 90 天。最新 artifact 的 `ledger.jsonl` 会累计近期历史，
并记录每轮耗时、覆盖、finding 数量和 ID，以及同一 PR 相邻两轮的持续/消失/新增项。
当 primary audit 的 `expected_shadows` 为空时，`review.shadows` 保持 `{}` 表示未配置
shadow；当它非空且 `shadow_mode` 为 `detached` 时，账本会保留完整的
`expected_shadows`，并写入 `shadow_mode: detached`、`status: detached_unavailable`、
`outcomes: null`，明确表示结果由独立的 Shadow workflow 另行采集，本 ledger job 不会
伪造或跨 workflow 聚合 shadow 结果。
账本还写入 **adopted `review.reviewer`**、**`review.failover`**，以及精简
**`review.attempts[]`**（`exit_code` / `reason` / `duration_s` / `cost_usd` /
`diag_snippet`），用于跨仓统计 chain failover（例如 claude-glm HTTP 529 过载 vs 429 额度）。
完整 hop 细节以 runner 上传的 `codex-review-result.json` 为准；字段说明见私有
`gate-hub` 的 `docs/review-effectiveness.md`。
同一 SHA 重跑会单独标为稳定性比较，不会把模型本身的波动误算成代码修复。
GitHub 在点击 Re-run 时会删除同一 run 的旧 artifact，因此每个 PR 另有一条由
`github-actions[bot]` 维护的精简 sticky state comment（含 Reviewer / failover 提示），
作为跨 rerun 游标；完整数据仍只在 artifact。

### 确认误报或人工处置

PR 评论中的以下行只是观察记录：评论本身不会触发任何工作流，也不会直接处置 finding。gate-v2
没有标签豁免，`codex-review-waived` 标签不会解除 finding；v2 caller 也不监听 `labeled` 事件。

```text
Codex finding disposition: correctness.example-id = false-positive — 说明证据
```

评论中的处置值（`false-positive`、`accepted`、`fixed`、`wont-fix`）及作者、理由和链接仍进入观察账本，
但它们不是 v2 的放行入口。唯一受控出口是 `.github/workflows/gate-v2-disposition.yml`，通过
`workflow_dispatch` 或 `workflow_call` 提交回执。老调用方仍只需提供五个必填字段
`pr_number`、`primary_run_id`、`primary_run_attempt`、`finding_id`、`reason`；新字段均可选，
但省略处置种类/证据的旧调用会被签发器明确拒绝，不会静默放行。

- `false-positive` 仅针对当前审计中的 inferred P1，必须提供 JSON 反证对象，包含非空 `command`、
  `output`、`pointer`，且 `result` 必须为 `refuted`。
- `deferred` 必须提供同仓跟踪 issue：`#<正整数>` 或本仓 GitHub `/issues/<正整数>` URL；只校验格式与仓库，
  不查询 issue 是否存在或仍为 open。仅 `personal`、`internal` tier 可用，`saas` 拒绝。
- 两种回执都绑定 `head_sha`、`audit_digest`、`epoch` 和唯一 finding。每张回执只覆盖指向的一条 P1；
  全部 P1 都被有效回执覆盖时，本轮按无 P1 进入 clean-streak 计算；部分覆盖时，未覆盖 P1 仍阻断。
- 同一稳定键（同 `file`/`line`/`category`/`severity`，典型是同文件 `line: null`）命中多条当前 P1 时，
  以回执的精确 `finding_id` 消歧：恰好命中其中一条即可签发、可消费，两张回执产物名也不同；
  `finding_id` 命中 0 条仍按 `finding_key_ambiguous` 拒绝。稳定键只命中一条时行为不变——
  rerun 漂移导致回执 `finding_id` 与当前 id 不等，该条 P1 仍可正常处置。

`docs/design/clean-streak-convergence.md` 和 `docs/sessions/260925-disposition-exit/design.md` 记录了
当前契约。gate-hub#810 的身份顾虑仍成立：回执不证明人工审批，但 owner 已裁决接受身份不可证，改用证据约束和留痕；
不会恢复标签豁免，也不要求管理员绕过。

## 公开仓安全模型（四层）

1. **fork-PR 防护写死在 reusable workflow 本体**：fork PR（`head.repo` ≠ 本仓）一律
   强制降级 GitHub-hosted 一次性沙箱并跳过 codex review；只有本仓分支的 PR 才上
   self-hosted。`pull_request` 事件下 caller 文件是 PR 作者的版本（拦不住人），可复用
   workflow 本体执行门禁（拦得住）。三处防护由 `tests/test_gate_v2_contract.py` 钉死。
2. **GitHub 外部贡献者人工批准**：5 个公开仓——`llm-compat`、`MediaResolverAPI`、
   `obsidian-clip-api`、`VideoTranscriptAPI`、`youtube_download_api`——全部设为
   `approval_policy: all_external_contributors`（最严一档；org 默认只是
   `first_time_contributors`）。任何外部贡献者的 workflow 运行都需人工点同意。查法：
   `gh api repos/zlxlabs/<repo>/actions/permissions/fork-pr-contributor-approval`
3. **org runner group 分池 + 白名单（白名单仅评审池）**：自建 runner 分两个 group
   （spec 见 gate-hub `docs/designs/runner-ci-pool-split.md`）——**评审池**（Default，
   id=1，挂 LLM 凭据，`restricted_to_workflows=true`，只放行本仓 workflow 的钉定 SHA /
   `@refs/heads/main`）与**无凭据 CI 池**（`ci`，id=4，`restricted_to_workflows=false`，
   `allows_public_repositories=true`；2026-08-05 翻转，分池理由见上述 spec）。因此公开
   仓的自有测试 CI 可以上 self-hosted 的 ci 池；绕过本文件的任意 job（包括 fork PR 里
   改写 caller 硬点名 self-hosted）仍派不进评审池。

   评审池白名单是隔离承重墙，任何时候不放开。ci 池刻意不设 workflow 白名单，不是遗漏：
   它只承载各仓自己的 CI，池内没有凭据；白名单是资源边界而非安全边界，救不了 fork
   guard 失效，而且每次 bump SHA 多维护一处，漏同步就会无限排队且零告警。
4. **ephemeral 容器**：runner 容器跑完即销毁，不在 self-hosted 机器上留下可被下一个
   job 读到的状态。

已知残余风险：L1 依赖缓存卷在两池之间共享（gate-hub spec D3 明写「两池共享是有意的」）。
这是唯一一条从 ci 池通往评审池的路径；触发它需先穿过上面四层，故当前接受该风险，暂不处理。

## 改 Gate v2 注意

- 分支上的改动不会通过 `@v2` 被下游调用；本仓 PR 会自动运行契约测试，`v2` 由 canary
  验证通过后自动推进。
- 本仓 PR 的契约测试运行在 GitHub-hosted runner 上。
- **L1 本机缓存卷的 env 切换（`runner == 'self' && tier == 'personal'`）依赖私有
  `zlxlabs/gate-hub` 仓 `run-ephemeral-runner.sh` 挂载的
  `/opt/gate-hub-cache/{uv,npm,pnpm,go}`（`docs/designs/ci-cache-strategy.md` §0
  D2）。两边可以独立合并、独立部署，顺序不影响正确性：这里只是把 env 指过去，
  uv/npm/pnpm 对不存在的目录会自己 `mkdir -p` 后正常工作（已实测），旧版
  runner 镜像上只是没有加速，不会失败。建议顺序仍是先合 gate-hub 的挂载 →
  VM201 逐槽滚动上线新 release → 再合本仓这半，方便对照“挂载生效前/后”的
  命中率差异，细节见两个配套 PR 描述。
