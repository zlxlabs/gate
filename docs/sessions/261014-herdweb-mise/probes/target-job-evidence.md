# gate315 mise 目标 job 真实探针证据（gate-20261014-45 / -46）

一次性 fixture：分支 `probe/gate315-mise-env-45`，PR #316（draft）关闭未合并。-46 轮流程：
closed 状态 push 修复 → reopen 触发唯一目标轮 → 收齐终态 → 再 close（仍未合并）。
观察：reopen 一个 head 已前移的关闭 PR 会在旧记录 head 0dec071 与新分支 head 33045c3
各触发一轮（共 4 run：目标 probe 38545277217@33045c3、陈旧 probe 38545275878@0dec071、
ci 两个）。陈旧轮沿用旧 caller（无 control 路由），复现「run 级卡 queued」已知态。
探针 caller = `.github/workflows/gate315-mise-probe.yml`（相对 `uses: ./.github/workflows/gate-v2.yml`，
`tier: personal` / `runner: self` / `quality_prepare_mise: true`；-46 起加临时一行
`control_runner: github-hosted`——gate-v2 既有生产输入（上游注释：回滚逃生舱），仅把控制面
job 移到 hosted，不改被测 quality 自托管），透传既有 SILO 两 secret，无新增。
main（05389f4）是 H1 祖先 → PR merge ref 树 = 本分支树，reusable 实际消费本分支 gate-v2.yml。

## 消费版本冻结
- H1 基线：`7c2ef54f9e505bc84222bcc7142fa96741c97b36`
- `git diff --exit-code 7c2ef54… -- .github/workflows/gate-v2.yml` ⇒ exit 0（blob `ecc1fcfb…` 两端一致）
- `python3 scripts/check_pinned_uses.py` ⇒ OK；`git diff --check` ⇒ exit 0

## 正向轮（分支 c3c26ce；run 38537161199 run 级=cancelled，主脑 force-cancel，见归因节）
- job `probe / quality` id=`115762732694`：conclusion=**success**；labels=`[self-hosted,linux,ci]`；
  runner `gatehub-395e6c3df75c-slot-8`；08:43:41Z–08:44:44Z
- step：`Prepare mise tool manager (quality entry opt-in)`=success（jdx/mise-action@v3，
  install:false / env:false / add_shims_to_path:false / working_directory=runner.temp）；
  `PR size preflight`=success；`Run scripts/gate-quality`=success；legacy lint/test/install 全 skipped（entry 模式预期）
- 布尔判据（step 日志 sha256 `58907c6e…`）：5 项 assert 全 true；fact：mise_bin_dir_on_path=true
  （上游预期：action 无条件把 `${data}/bin` 加 PATH）；trusted_config_paths_present=true、
  mise_yes_present=true（上游已披露行为，记录不判错）；`GATE315-PROBE-OK round=positive` ×1
- 原日志缓存：`/tmp/gate315-target-probe-261014-45/quality-positive.log`（0600）

## 负向轮（真实依赖安装失败）
- 第 1 次（分支 ec09377，job `115775458185` quality=success）：**未达目标信号**——卡面示例
  `pnpm install --offline --frozen-lockfile`（空目录）在 runner pnpm v12.11.2 下是 no-op success。
  改为 `file:` 依赖指向不存在目录（版本稳定、--offline、隔离 mktemp 目录）。
- 第 2 次（分支 0dec071，run 38542773439，job id=`115781360620`）：conclusion=**failure**；
  labels=`[self-hosted,linux,ci]`；step `Run scripts/gate-quality`=**failure**（exit code 1）；
  同 job 平台步骤（mise bootstrap、preflight）全 success；5 项 assert 全 true；
  `pnpm_install_start=true`（到达真实安装而非 not found）；`GATE315-PROBE-UNREACHABLE` ×0；
  失败点 = pnpm 解析 `gate315-probe-missing-local@file:./missing-pkg-dir`（日志 sha256 `3bbc46f1…`）

## 已知坏态轮（gate-20261014-46，分支 33045c3，run 38545277217）
- 修 root cause：sitecustomize marker 写入的 `except OSError: pass` 移除，写失败不再静默
  （-45 版本下写失败 ⇒ 无 marker ⇒ 缺席断言 vacuous pass，假阴性）。entry 在全部正向 assert
  之后、故障注入之前做已知坏态：**单条命令级** PYTHONPATH（不导出到 job/后续平台 env）用同
  runner python3 起一个子进程 → sitecustomize 必须创建同一路径 marker → 用与 assert#3 同一
  缺席谓词确认其变否（预期拒绝）；无 marker 立即 fail loud（覆盖 Python site 吞 import 异常
  的场景）；随后只删除本次自建 marker 并复验缺席，不 clean 其他现场。
- run 38545277217（event=reopened，head=33045c3）：Jobs total_count=**8**，control 路由后首个
  全 job 终态轮。classify_pr_paths=success（ubuntu-latest，此前从未跑成）；quality=**failure**
  （预期实验信号；labels 仍 `[self-hosted,linux,ci]`，runner slot-90）；primary / ocr /
  resolve_advisory=**skipped**（draft 期 gate primary 预期 skip——job 级结论是 SKIPPED 不是
  SUCCESS）；gate (draft)=failure（如实传导 quality_failure，gate_result=fail）；ledger=failure、
  notify=success（结论按 job 级如实记录）。
- quality step 日志 sha256 `df426b30b66fa238`（缓存 `/tmp/gate315-probe-predicate46/quality-knownbad.log` 0600）：
  5 项 assert 全 true；`GATE315-PROBE-OK round=negative` ×1；
  `known_bad=fixture_pythonpath_subprocess=true`（真实泄漏态被造出）→
  `GATE315-PROBE-KNOWN-BAD-REJECTED` ×1（原缺席谓词对已知坏态给否）→
  `known_bad=marker_cleanup=true`（现场恢复）；`pnpm_present=true`、`pnpm_install_start=true`、
  `UNREACHABLE` ×0；失败步骤 `Run scripts/gate-quality`（`##[error]Process completed with exit code 1`）。
  E2E 链：真实 CI python 读实际 fixture PYTHONPATH → 写 marker → 缺席谓词拒绝 → 删自建
  marker → 真实安装失败中止。
- 本地机制预验（非 CI 证据，仅佐证）：同 PYTHONPATH 子进程造 marker 成功；marker 路径
  不可写时无文件、site 输出 `Error in sitecustomize` ×1 → entry 判据会 fail loud。

## Run 级 cancelled 归因（-46 纠偏：callee 文件+ref，不是 caller 文件名）
- org runner group admit 按 **callee workflow 文件+ref** 枚举：Default（id=1）
  `restricted_to_workflows=true`，selected_workflows 实含 `gate-v2.yml@main`、`@v2` 等条目，
  **不含本探针分支 ref** → gate-v2.yml 在 probe ref 上的 codex 池控制 job（classify 等）不能
  admit、永久 queued。与 probe caller 文件名 `gate315-mise-probe.yml` 无关（-45 文档误写为
  caller 归因，此处纠正）。质量 job 走 ci 组（id=4，`restricted_to_workflows=false`）不受影响。
- run.cancelled 与 job 结论分开写：-45 两轮 run 38537161199 / 38542773439 run 级
  conclusion=`cancelled`（主脑 force-cancel）；两轮的 quality job 分别 success / failure，均真实
  终态。观察：run 级 status 长期 `queued` 而 quality job 早已完成——判完成必须看 job 级。
  -46 加 `control_runner: github-hosted` 后 run 38545277217 全 job 终态；陈旧
  38545275878@0dec071（旧 caller 无该输入）run 级仍卡 queued（status=queued，conclusion=null）。

## 判定
- 正向成立：真实 GitHub Actions caller → 与 H1 字节一致的 reusable → 官方 mise bootstrap success →
  平台 python 步骤（gate-actions checkout、PR size preflight）未继承项目 PYTHONPATH/env（marker
  零命中）→ mise shims 未进 PATH（entry 级精确目录比对）→ `mise --version` 可执行；项目 env
  激活权在 entry。
- 负向成立：显式确定性依赖准备失败以非零 exit 真实传播、UNREACHABLE 标记缺席、outcome 照常记录。
- 已知坏态成立：缺席谓词在真实 CI 的真实泄漏态给否、正常态给真——阴性判据不再可能 vacuous
  pass；marker 写失败不再被静默吞掉。
- 限制：fixture `[tools]` 为空，未验证真实工具链安装；真实 caller ≠ herdweb 业务全量；
  owner 实际接入仍需业务仓操作。
