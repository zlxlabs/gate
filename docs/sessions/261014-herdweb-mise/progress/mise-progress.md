# mise-progress（gate-20261014-41）

## 2026-10-14 pickup / 设计定稿

- 阶段：implementing（TDD 先行）。
- 结论：证据亲核成立——herdweb run 38427594289 `gate / quality` 失败
  `./scripts/gate-quality: 9: mise: not found`（exit 127），head `2d37b771`；
  本仓 gate-v2.yml 全文无 mise 接口。方案按卡面锁定决策：default-false opt-in、
  `jdx/mise-action@v3` + `install: false`、仅 gate-v2.yml 一处。
- 关键决策：新步骤插在 Detect repository quality entry 之后、gate action source
  checkout 之前——既满足卡面「detect 后、业务入口前」，又不破坏
  tests/test_gate_v2_contract.py 对「Remove gate action source before caller
  checks」与「Run scripts/gate-quality」相邻关系的既有守卫。
- 已否决方案：插入 cleanup 与 run-quality 之间（会红既有相邻断言，须改存量守卫，
  违反不放宽守卫条款）；install 默认 true（与业务入口重复准备依赖）；自建 curl
  安装器（卡面禁止）。
- 已知伴随改动：tests/test_gate_v2_contract.py
  `test_workflow_call_inputs_include_review_exempt_paths` 断言 workflow_call inputs
  全集，新增 input 必须同步期望集（契约扩展的必要同步，非放宽）。
- 下一步唯一动作：落新测试 tests/test_gate_quality_mise.py，在 base 上验证红。
