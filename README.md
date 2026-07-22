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
      runner: self            # self(自建 VM201, 有 codex review) | hosted(免费分钟)
      # 可选覆盖: max_diff_lines: 4000, max_review_shards: 8, pr_size_warn_lines: 8000
    secrets:
      FEISHU_CI_WEBHOOK: ${{ secrets.FEISHU_CI_WEBHOOK }}   # 公开仓必须 secret;私有仓可用同名 variable 兜底
```

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

## 公开仓安全模型（三层）

1. **fork-PR 防护写死在 gate.yml 本体**：fork PR（head.repo ≠ 本仓）一律强制降级
   GitHub-hosted 一次性沙箱并跳过 codex review；只有本仓分支的 PR 才上 self-hosted。
   pull_request 事件下 caller 文件是 PR 作者的版本（拦不住人），本文件永远取 @main
   （拦得住）。三处防护由 `tests/test_gate_contract.py` 钉死。
2. **org runner group 白名单**：`restricted_to_workflows` 只放行本文件
   `@refs/heads/main` —— 绕过本文件的任意 job（包括 fork PR 里改写 caller 硬点名
   self-hosted）根本派不到自建 runner。
3. **fork PR 审批**：两个公开仓的 Actions 设置为 all external contributors 必须
   人工批准才能跑任何 workflow。

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
