# C1 实施记录：受管 Silo profile

Refs #262 / #249；PR #263 保持 draft。

- 审查基线 `85916ed5c4f9d181fec866f15d1bb5f58111c9c8`，冻结实现 `ea2712b469e8302c61d982c2dfd995a8f4abf58d`。
- gate-v2 的可信 Silo 消费显式使用 managed profile；固定读取 `/opt/review-auth/silo.json`，不回退到 caller AWS key。disposition 保留 legacy env 模式。
- 新增 managed 空目录 skip 的 profile 预检；缺文件、无效 JSON、缺字段或空 key 均失败。legacy 空 skip 行为保持不变。
- 全量新增代码与测试 189 行；本次两行修复测试覆盖了 managed 空 skip 的真实入口和有效 profile 不发 HTTP 的路径。

## 验证

- TDD：修复前 `tests/test_silo_store.py` 的相关筛选为 5 failed、1 passed；修复后整文件为 35 passed。
- 冻结代码全量：`pytest` 1270 passed，exit 0；`scripts/check_pinned_uses.py` 与 `git diff --check` 均通过。
- H1 已有固定路径/签名/argv 及五个 store consumer 证据；最终 head 新增两行预检和相应断言，已专项复核。
- 未构建或部署生产 profile，未切换生产 slot，也未测真实生产 quality job 的依赖源计费。
