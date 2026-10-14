# gate315 mise 目标 job 真实探针证据（gate-20261014-45）

一次性 fixture：分支 `probe/gate315-mise-env-45`，PR #316（draft）已关闭未合并。
探针 caller = `.github/workflows/gate315-mise-probe.yml`（相对 `uses: ./.github/workflows/gate-v2.yml`，
`tier: personal` / `runner: self` / `quality_prepare_mise: true`，透传既有 SILO 两 secret，无新增）。
main（05389f4）是 H1 祖先 → PR merge ref 树 = 本分支树，reusable 实际消费本分支 gate-v2.yml。

## 消费版本冻结
- H1 基线：`7c2ef54f9e505bc84222bcc7142fa96741c97b36`
- `git diff --exit-code 7c2ef54… -- .github/workflows/gate-v2.yml` ⇒ exit 0
- blob 一致：H1 `ecc1fcfb89d59d45ebdf77d950110a51e75306cc` == HEAD `ecc1fcfb89d59d45ebdf77d950110a51e75306cc`
- `python3 scripts/check_pinned_uses.py` ⇒ `OK: checked 9 …`；`git diff --check` ⇒ exit 0

## 正向轮（分支 c3c26ce）
- 来源：`gh api repos/zlxlabs/gate/actions/runs/38537161199/jobs`
- run `38537161199` started 2026-10-14T08:43:36Z，run 级 conclusion=`cancelled`（归因见下节）
- job `probe / quality` id=`115762732694`：conclusion=**success**；labels=`[self-hosted,linux,ci]`；
  runner `gatehub-395e6c3df75c-slot-8`；08:43:41Z–08:44:44Z
- step：`Prepare mise tool manager (quality entry opt-in)`=success（jdx/mise-action@v3，
  install:false / env:false / add_shims_to_path:false / working_directory=runner.temp）；
  `PR size preflight`=success；`Run scripts/gate-quality`=success；legacy lint/test/install 全 skipped（entry 模式预期）
- 布尔判据（step 日志 sha256 `58907c6e…`）：
  - assert：project_env_not_exported=true；fixture_pythonpath_not_inherited=true；
    no_platform_leak_marker=true；mise_shims_not_on_path=true；mise_version_runs=true
  - fact：mise_bin_dir_on_path=true（上游预期：action 无条件把 `${data}/bin` 加 PATH）；
    mise_trusted_config_paths_present=true、mise_yes_present=true（上游已披露行为，记录不判错）
  - `GATE315-PROBE-OK round=positive` 命中 1 次
- 原日志缓存：`/tmp/gate315-target-probe-261014-45/quality-positive.log`（0600，sha256 `58907c6ee1cc…`）

## 负向轮
- 第 1 次负向（分支 ec09377）：run `38541005441` job `115775458185` quality=success —— **未达目标信号**：
  卡面示例命令 `pnpm install --offline --frozen-lockfile`（空目录）在 runner pnpm v12.11.2 下是
  no-op success（日志 "Already up to date / Done in 28ms"，exit 0；本地 v11.7.0 报
  ERR_PNPM_NO_PKG_MANIFEST）。已改为 `file:` 依赖指向不存在目录（版本稳定、--offline、隔离 mktemp 目录）。
- 第 2 次负向（分支 0dec071）：run `38542773439` job `probe / quality` id=`115781360620`：
  conclusion=**failure**；labels=`[self-hosted,linux,ci]`；runner `gatehub-395e6c3df75c-slot-51`
- step：`Run scripts/gate-quality`=**failure**（`Process completed with exit code 1`）；
  同 job 的 `Prepare mise tool manager`=success、`PR size preflight`=success（平台步骤不因 entry 失败而败，
  不冒充全 job 绿——本 job 即以 failure 为预期）
- 布尔判据（step 日志 sha256 `3bbc46f1…`）：5 项 assert 仍全 true；`pnpm_present=true`、
  `pnpm_install_start=true`（证明到达真实安装命令而非 not found）；
  `GATE315-PROBE-UNREACHABLE` 命中 **0** 次；失败点 = pnpm 解析
  `gate315-probe-missing-local@file:./missing-pkg-dir`（目录不存在）
- 原日志缓存：`/tmp/gate315-target-probe-261014-45/quality-negative2.log`（0600，sha256 `3bbc46f14ee0…`）

## Run 级 cancelled 归因（两次 run 同因，非 quality 缺陷）
- org runner group 实况（`gh api orgs/zlxlabs/actions/runner-groups`）：Default（id=1）
  `restricted_to_workflows=true`，白名单按文件名+ref 枚举 gate-v2.yml/gate-shadow-v2.yml/gate.yml/
  gate-v2-disposition.yml 等，probe caller 文件名不在内 → `classify_pr_paths`（codex 池=Default 组）
  永不 admit，被取消；ci 组（id=4）`restricted_to_workflows=false` → quality 正常被 ci 池认领。
- 两次 run 的 Jobs API total_count=2：`gate` 聚合 / `ledger` / `notify` job 未创建。
  本探针 target=quality job，不依赖聚合绿；生产 caller 文件名已在白名单者不受影响。
- 观察到 run 级 status 长时间 `queued` 而 quality job 早已完成——run 级状态跟随未 admit 的 job；
  判完成必须看 job 级。

## 判定
- 正向成立：真实 GitHub Actions caller → 与 H1 字节一致的 reusable → 官方 mise bootstrap success →
  平台 python 步骤（gate-actions checkout、PR size preflight）未继承项目 PYTHONPATH/env（泄漏 marker 零命中）
  → mise shims 未进 PATH（entry 级精确目录比对）→ `mise --version` 可执行；项目 env 激活权在 entry。
- 负向成立：显式确定性依赖准备失败以非零 exit 真实传播、后续标记缺席、`Record caller checks outcome` 照常记录。
- 限制：fixture `[tools]` 为空，未验证真实工具链安装；真实 caller ≠ herdweb 业务全量；owner 实际接入仍需业务仓操作。
