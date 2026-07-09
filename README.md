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
    secrets:
      FEISHU_CI_WEBHOOK: ${{ secrets.FEISHU_CI_WEBHOOK }}   # 公开仓必须 secret;私有仓可用同名 variable 兜底
```

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
