# C2 实施记录：公开 Required Gate caller

Refs #264 / #249；PR #265 为基于 C1 分支的 draft，不关闭 issue。

- 原始起点 `0b5da65fa37ec00864d64a698844dc2b69f7642f`，原实现 `639843354752b6953ee15cd97f011f38baf481de`；已重放到最终 C1 base `e7a57ece42d4b6d87d7eb27668d167d376c388e0`，对应 C2 实现提交 `15c42fcd0a8db720075933e39d79ce165411d641`、记录提交 `6aad868b05c13bb5269da7fa09c1565d16f8d6ba`。
- Required Gate 公共 caller 模板停止映射 Silo keys，保留 FEISHU；仅 Gate ID `1295374164` 使用 managed source。既有 private callers 继续 optional legacy env；其他 public caller 没有 legacy keys 时 Silo 操作仍 unavailable。disposition 模板继续映射 legacy keys，本卡不为其他仓新增 managed keys、namespace 或 ACL。
- README 说明 Gate 专属 managed source、private legacy compatibility、无旧 keys 的其他 public caller 不可用、quality 隔离、optional callee 声明和 disposition 旧契约，并区分源码、生产部署与真实矩阵状态。
- 契约测试解析实际 YAML，锁住 Required Gate/disposition 两类 caller 与 gate-v2 job 的 key 边界。

## 验证

- 原始 C2 base 上的 caller/secret/disposition 45 项与全量 1270 项仅作历史记录；重基到最终 C1 后重新验证，不将旧全量结果当成本 head 证据。
- 新 base 初次点测命中旧断言“gate-v2 jobs 全无 Silo secret 名称”；按 Gate ID / private legacy 合同修正后，caller 与 gate-v2 contract 点测 210 passed in 78.68s，exit 0。红、绿原始日志均保留。
- 冻结代码与测试于 `11a943cf306e521bf18958a8b2375914fc1903b8`、C1 base `e7a57ece42d4b6d87d7eb27668d167d376c388e0`；全量命令 `uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q`：1273 passed in 119.02s，exit 0。全量原始日志保留。
- `SHELLCHECK_OPTS=--severity=warning actionlint -color .github/workflows/*.yml templates/*.yml` 与 `python3 scripts/check_pinned_uses.py` 均 exit 0。
- 本仓无 Makefile，`make lint` 不可用；未作生产 profile 部署或真实事件矩阵验收。
