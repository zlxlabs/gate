# AGENTS.md — zlxlabs/gate

risk-tier: personal

本仓是个人自用的多 Agent 开发门禁（CI gate）。write 权限只有 owner 本人与其派出的 agent，
没有其他人类协作者，也不对外提供服务。

## 与 gate-hub 的关系

本仓是 `zlxlabs/gate-hub`（控制仓）的**附属仓库**：同一 owner、同一批 agent 维护。
agent-config `core.md`「跨仓写权边界：他仓问题只去对方仓提 issue 挂证据，不跨仓改代码」
**在 gate-hub / gate / ci-templates 三仓之间不适用**——从 gate-hub 那边发现本仓的缺口，
可以直接来改码开 PR，不必先挂 issue 等人接。完整的豁免范围与仍然成立的约束见
gate-hub 的 `AGENTS.md`「附属仓库」节。

本仓自己的两条不能忘：

- **risk-tier 不继承。** 本仓是 `personal`，不因为是 gate-hub 的附属仓就按 `internal` 审。
- **合并方式是发布动作的一部分。** 调用方钉 `@v2`；本仓 workflow 历史仍不可 rebase。
  squash / rebase 会重写 SHA、当场打断下游 pin。改 workflow 的 PR 必须用
  merge commit 合并。合并后无需任何人工推广动作：调用方跟踪的是移动标签 `@v2`，
  由 `.github/workflows/v2-tag-sync.yml`（push main / 每小时 cron）在 canary 绿后自动
  把 v2 抬到最新的、canary 实测绿过的 main 提交。**没有 caller pin bump 这一步**——
  旧批量换钉工具已退役（gate-hub `scripts/bump_caller_pins.py` 头注释、`registry.yaml`）。
  runner-group 白名单（org runner group 按文件名 admit `gate-v2.yml` / `gate-shadow-v2.yml`）
  只在**新仓接入（onboarding）**时涉及；workflow 版本合并不改变仓的 runner-group 成员关系。

## 发布完成与关单

修本仓 issue，关闭前确认远端 `refs/tags/v2` 已包含修复 SHA（`git ls-remote --tags origin v2`，再 `git merge-base --is-ancestor <fix-sha> <v2-commit>`）。未满足不得 CLOSED，改标 `status:waiting`（或仓库惯用的「待发布」），v2 抬升后再关。禁止用本地 tag 副本判断发布。

## 威胁模型

唯一需要防的是「agent 给自己开绿灯」——执行器不应能绕过门禁让自己的 PR 变绿。
**不需要**防备有预谋的内部攻击者：没有这个角色。安全类意见按 personal 档处理，
P1 红线只有数据丢失、静默出错、崩溃。

## 验证命令

全量测试（与 CI 一致，见 `.github/workflows/ci.yml`）：

    uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q

workflow pin 检查：

    python3 scripts/check_pinned_uses.py

本仓没有 Makefile。不要用裸 `pytest`——缺依赖会假红。
