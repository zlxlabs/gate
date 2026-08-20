# AGENTS.md — zlxlabs/gate

risk-tier: personal

本仓是个人自用的多 Agent 开发门禁（CI gate）。write 权限只有 owner 本人与其派出的 agent，
没有其他人类协作者，也不对外提供服务。

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
