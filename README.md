# gate

全部 zlxlabs / 个人仓库共用的**复用 pre-merge 门禁**（lint / jscpd 查重 /
dependency-cruiser / tests / Codex review）。这是私有 `zlxlabs/gate-hub` 的
"纯逻辑公开半"——本仓只有这一份 reusable workflow 和它的契约测试；仓库清单
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
    uses: zlxlabs/gate/.github/workflows/gate.yml@main   # @main 故意不钉:改一处全仓库生效
    with:
      tier: personal          # personal | internal | saas
      runner: self            # self(自建两台, 有 codex review) | hosted(免费分钟)
      # 可选覆盖: max_diff_lines: 4000, max_review_shards: 8, pr_size_warn_lines: 8000
    secrets:
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

## Required Gate v2 + Shadow Calibration v2（canary，2026-07-26 起）

上面的 `gate.yml`（legacy）仍是未迁移仓库的默认路径，原样继续服务。`shadow-review-
independence` 计划（2026-07-24 定稿，私有 `zlxlabs/gate-hub` 仓
`ceo-plans/2026-07-24-shadow-review-independence.md`）把执行面拆成两个独立 reusable
workflow，**目前只有 `zlxlabs/gate-hub` 自己（personal canary）接入**，其余已注册仓库
不受影响，继续走 legacy caller。

### 两个 reusable workflow

| workflow(`name:`) | job 拓扑 | 说明 |
|---|---|---|
| `.github/workflows/gate-v2.yml`(`gate`) | `quality` ∥ `primary` → `gate`(`needs: [quality, primary]`,`if: always()`) | Required Gate。`gate` job id 与 `name:` 都字面等于 `gate`，与 legacy 一致——required status check context 保持 `gate / gate` 不变，branch protection 零迁移。 |
| `.github/workflows/gate-shadow-v2.yml`(`gate-shadow`) | `resolve` → `shadow`(matrix，每 reviewer 一个 job)→ `summary` | Shadow Calibration。**不产生任何 required status check**，只用于校准；失败/取消/超时不影响 Required Gate。 |

PR1 的 `REVIEW_RUN_MODE` 由两个 reusable 的实际 review entry step 显式固定为
`PAYLOAD_ONLY`，不是 `workflow_call` input；这是当前唯一合法模式，待真正启用
`FULL_SOURCE` 时再引入 caller-level 契约。

### `gate-v2.yml` inputs(`workflow_call`)

| input | 默认值 | 说明 |
|---|---|---|
| `tier` | `personal` | `personal` / `internal` / `saas` |
| `runner` | `self` | `self`（自建，跑 `primary` review）/ `hosted`（免费分钟，`primary` 整个 job 跳过） |
| `has_ui` | `false` | 同 legacy |
| `design_doc` | `""` | 同 legacy |
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

刻意**不**镜像 `has_ui`/`timeout_minutes`/`pr_size_warn_lines`/`primary_timeout_minutes`/
`control_runner`：这个 workflow 没有 `quality` job，也没有 required 聚合器，这些字段没有
对应语义。也没有 `secrets:` 声明——`gate-shadow-v2.yml` 从不调用外部 webhook，也从不发
PR 评论（发校准收据是计划 T6 的范围，尚未实现）。

### caller 模板位置

`templates/caller-gate-v2.yml` / `templates/caller-gate-shadow-v2.yml` 是两份独立的
canary caller 模板，与上面 legacy 的 `templates/caller-ci.yml` **并列**，不是替换。两份
模板里 `uses:` 的 SHA 都是占位符 `__PINNED_GATE_SHA__`——接入一个仓库前必须替换成本仓
当时 `zlxlabs/gate` `main` 的真实 commit SHA，不能照抄占位符，也不能用 `@main` 移动引用
（见下面「钉 SHA 纪律」）。两份文件对应两个独立 workflow(`.github/workflows/gate.yml` +
`.github/workflows/gate-shadow.yml`)，只装一份就只有 Required Gate 或只有 Shadow
Calibration，不是「装一份就两者都有」。

### 钉 SHA 纪律

v2 阶段（canary 期间及以后，直到 fleet migration 完成）两个 caller 都**必须**钉死具体
commit SHA，不用 `@main`——这样 `zlxlabs/gate` 主分支继续推进，不会让已经通过 canary
验证的仓库行为跟着漂移，升级是一次显式、可审查的「改 SHA」提交，不是自动生效。**两个
caller 的 SHA 通常保持一致**（同一次评审、同一批推广），但设计上允许独立演进（例如只
bump 了 `gate-shadow-v2.yml` 的一个修复，`gate-v2.yml` 暂不动）——这不是强制要求，只是
治理上更简单的默认做法。`gate-hub` 自己当前两个 caller 都钉在同一 commit
(`9b673035aad284eb4dedaf2fd7554a9581c7decd`，即本次 Stage 2 canary 切换时的
`zlxlabs/gate` `main`)。

### org runner group 白名单运维要点（bump SHA 时最容易漏的一步）

上面「公开仓安全模型」小节讲的 `restricted_to_workflows` 白名单，在 v2 caller 存在后
**必须同时放行 legacy 与 v2 两条 workflow 的 SHA**——只在 caller 里改 `uses: …@<new-
sha>` 是不够的，org runner group 的白名单是另一道独立的闸，不会自动跟着 caller 走。
2026-07-26 canary 切换时这一步漏做过一次，导致 self-hosted job **无限排队且没有任何
告警**（现有容量告警都不覆盖「job 排队卡在白名单外」这种失效模式），排了约 4 小时才被
人工发现。

（2026-08 起 org 有两个 runner group：白名单只存在于**评审池** Default（id=1）；
CI 池 `ci`（id=4）`restricted_to_workflows=false`，bump 不涉及它——PATCH 别打错组。
首选入口是 gate-hub `scripts/bump_caller_pins.py`，它固定先同步白名单再改 caller。）

正确顺序（三步缺一不可）：

1. 改 caller 的 `uses: zlxlabs/gate/.github/workflows/gate-v2.yml@<new-sha>`（以及
   `gate-shadow-v2.yml` 那一份，如果也要 bump）；
2. 用 `gh api` **PATCH** 该 org 的 runner group，把 `selected_workflows` 数组里对应
   旧 SHA 的条目换成新 SHA——这是整个数组的**全量覆盖**，不是增量 append，务必先读出
   当前完整列表再改：
   ```bash
   # 先看当前白名单(group id 因 org 而异,这里以 zlxlabs 的 id=1 为例)
   gh api orgs/zlxlabs/actions/runner-groups/1 --jq .selected_workflows

   # 确认后整份数组一起回写(legacy 条目原样保留,只替换 v2 的 SHA):
   gh api --method PATCH orgs/zlxlabs/actions/runner-groups/1 \
     -f 'selected_workflows[]=zlxlabs/gate/.github/workflows/gate.yml@refs/heads/main' \
     -f 'selected_workflows[]=zlxlabs/gate/.github/workflows/gate-v2.yml@<new-sha>' \
     -f 'selected_workflows[]=zlxlabs/gate/.github/workflows/gate-shadow-v2.yml@<new-sha>'
   ```
3. 验证：新 SHA 触发的 run 能正常从 `queued` 转 `in_progress`，而不是卡在 `queued`
   不动。

这一步目前**没有自动化**，纯人工操作，漏做的后果（无限排队 + 零告警）比大多数 CI 故障
更隐蔽——bump 任何一个 v2 reusable workflow 的钉 SHA 时，请把同步这份白名单当成清单里
跟改 caller 同等优先级的一步，不是「回头再说」的收尾动作。

### 与 legacy `gate.yml@main` 的共存关系

v2 两个 caller 目前只在 `zlxlabs/gate-hub` 一个仓库（personal canary）生效。其余全部
已接入仓库（私有 `gate-hub` 仓 `registry.yaml` 台账里的仓库）继续用上面「caller」小节
描述的单一 legacy `gate.yml@main` caller，行为不受本节内容影响，直到按分层灰度顺序
（personal canary → internal → saas）显式切换。两条路径长期共存，不是「v2 上线 legacy
立刻退役」——legacy 退役是 fleet migration 完成之后的事，当前尚未开工。

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
账本还写入 **adopted `review.reviewer`**、**`review.failover`**，以及精简
**`review.attempts[]`**（`exit_code` / `reason` / `duration_s` / `cost_usd` /
`diag_snippet`），用于跨仓统计 chain failover（例如 claude-glm HTTP 529 过载 vs 429 额度）。
完整 hop 细节以 runner 上传的 `codex-review-result.json` 为准；字段说明见私有
`gate-hub` 的 `docs/review-effectiveness.md`。
同一 SHA 重跑会单独标为稳定性比较，不会把模型本身的波动误算成代码修复。
GitHub 在点击 Re-run 时会删除同一 run 的旧 artifact，因此每个 PR 另有一条由
`github-actions[bot]` 维护的精简 sticky state comment（含 Reviewer / failover 提示），
作为跨 rerun 游标；完整数据仍只在 artifact。

确认误报或人工处置时，在 PR 评论中使用一行机器可读记录：

```text
Codex finding disposition: correctness.example-id = false-positive — 说明证据
```

处置值支持 `false-positive`、`accepted`、`fixed`、`wont-fix`；作者、理由和评论链接会进入后续账本。

## 公开仓安全模型（四层）

1. **fork-PR 防护写死在 reusable workflow 本体**：fork PR（`head.repo` ≠ 本仓）一律
   强制降级 GitHub-hosted 一次性沙箱并跳过 codex review；只有本仓分支的 PR 才上
   self-hosted。`pull_request` 事件下 caller 文件是 PR 作者的版本（拦不住人），本文件
   永远取 pin 的 SHA（拦得住）。三处防护由 `tests/test_gate_contract.py` /
   `tests/test_gate_v2_contract.py` 钉死。
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

## 改 gate.yml 注意

- 白名单钉在 `@refs/heads/main`：分支上的 gate.yml 无法派 self-hosted 任务。要真机
  验证未合并的改动，临时把分支 ref 加进 runner group 白名单，或先用 `runner: hosted`
  验证四项门禁，codex 步骤合并后再看。
- 本仓 PR 会自动跑契约测试（hosted，免费）。
- **L1 本机缓存卷的 env 切换（`runner == 'self' && tier == 'personal'`）依赖私有
  `zlxlabs/gate-hub` 仓 `run-ephemeral-runner.sh` 挂载的
  `/opt/gate-hub-cache/{uv,npm,pnpm,go}`（`docs/designs/ci-cache-strategy.md` §0
  D2）。两边可以独立合并、独立部署，顺序不影响正确性：这里只是把 env 指过去，
  uv/npm/pnpm 对不存在的目录会自己 `mkdir -p` 后正常工作（已实测），旧版
  runner 镜像上只是没有加速，不会失败。建议顺序仍是先合 gate-hub 的挂载 →
  VM201 逐槽滚动上线新 release → 再合本仓这半，方便对照“挂载生效前/后”的
  命中率差异，细节见两个配套 PR 描述。
