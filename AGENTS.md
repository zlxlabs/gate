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
- **合并方式是发布动作的一部分。** 本仓的 workflow 被全舰队下游仓以 `@<40hex>` immutable
  SHA 精确 pin，squash / rebase 会重写 SHA、当场打断下游 pin。改 workflow 的 PR 必须用
  merge commit 合并；合并后的「runner-group 白名单 + caller pin bump」是独立的推广步骤，
  不能停在中间态。

## 威胁模型

唯一需要防的是「agent 给自己开绿灯」——执行器不应能绕过门禁让自己的 PR 变绿。
**不需要**防备有预谋的内部攻击者：没有这个角色。安全类意见按 personal 档处理，
P1 红线只有数据丢失、静默出错、崩溃。

## 验证命令

全量测试（与 CI 一致，见 `.github/workflows/ci.yml`）：

    uv run --with pytest,PyYAML python -m pytest -q

workflow pin 检查：

    python3 scripts/check_pinned_uses.py

本仓没有 Makefile。不要用裸 `pytest`——缺依赖会假红。
