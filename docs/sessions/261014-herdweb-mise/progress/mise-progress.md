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

## 2026-10-14 TDD 红绿闭环 + 变异验证

- 阶段：implementing → 待 PR CI。
- 红（base 514fcac 前跑）：10 failed / 1 passed，全部是带消息的
  AssertionError（"must declare exactly one ... found 0"、input missing），
  无 KeyError/ValueError 假红；legacy 守卫测试 base 绿（防回归向，预期内）。
- 绿（a5216c8）：窄套件 229 passed；全量 1551 passed（exit 0）；
  check_pinned_uses.py OK（8 files）；git diff --check 干净。
- 变异三连（各自注入→红→仅还原该处，工作树回到干净）：
  default true → "must default to false" 红；去 entry guard → 求值器
  shape 断言 6/6 组合全红（含 legacy-True——正是 guard 缺失会让 legacy
  路径误触发的组合）；install true → "must be exactly install: false" 红。
- YAML 教训：首版编辑把注释续行顶格写出 scanner error，连带
  test_gate_v2_contract.py 189 个解析失败——已修复并重跑；
  workflow 是所有契约测试的共享 fixture，它的语法错误会淹没失败面，
  收敛时先查解析器再查断言。
- durations 基线：仅追加 tests/test_gate_quality_mise.py: 0.05，存量键未动。
- 下一步唯一动作：push + 开 draft PR，等本轮 CI 终态（不连推，避免同 PR 互相取消）。
