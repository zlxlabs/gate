# C2 实施记录：公开 Required Gate caller

Refs #264 / #249；PR #265 为基于 C1 分支的 draft，不关闭 issue。

- 原始起点 `0b5da65fa37ec00864d64a698844dc2b69f7642f`，原实现 `639843354752b6953ee15cd97f011f38baf481de`；已重放到最终 C1 base `e7a57ece42d4b6d87d7eb27668d167d376c388e0`，对应 C2 实现提交 `15c42fcd0a8db720075933e39d79ce165411d641`、记录提交 `6aad868b05c13bb5269da7fa09c1565d16f8d6ba`。
- Required Gate 公共 caller 模板停止映射 Silo keys，保留 FEISHU；仅 Gate ID `1295374164` 使用 managed source。既有 private callers 继续 optional legacy env；其他 public caller 没有 legacy keys 时 Silo 操作仍 unavailable。disposition 模板继续映射 legacy keys，本卡不为其他仓新增 managed keys、namespace 或 ACL。
- README 说明 Gate 专属 managed source、private legacy compatibility、无旧 keys 的其他 public caller 不可用、quality 隔离、optional callee 声明和 disposition 旧契约，并区分源码、生产部署与真实矩阵状态。
- 契约测试解析实际 YAML，锁住 Required Gate/disposition 两类 caller 与 gate-v2 job 的 key 边界。

## 验证

- TDD：旧 YAML 的两条 Required Gate key 断言先红；更新后 caller/secret/disposition 筛选集 45 passed。
- 全量命令 `uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q`：1270 passed，120.09 秒，exit 0；来自 session 20828 的 stdout transcript，没有独立原始日志文件。
- actionlint v1.7.12 对 workflows 与 caller templates 通过；`scripts/check_pinned_uses.py` 通过。
- 本仓无 Makefile，`make lint` 不可用；未作生产 profile 部署或真实事件矩阵验收。
