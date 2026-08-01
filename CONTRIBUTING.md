# Contribution 纪律

- 仓内 `zlxlabs/gate/...` 的 action/workflow 引用必须使用完整 40 位 commit SHA；同仓本地 action/workflow 才允许使用相对 `./` 路径。提交前运行 `python3 scripts/check_pinned_uses.py`。
- 新增必填 `workflow_call` input 必须提供兼容旧 caller 的默认值；不得因为 caller 未传本卡新增输入而 fail-closed。
