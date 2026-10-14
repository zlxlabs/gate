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

## 2026-10-14 PR CI 终态 + 收尾

- 阶段： awaiting review（执行器不合并、不标 ready，merge 由主脑）。
- draft PR：zlxlabs/gate#315，分支 card/gate-20261014-41-quality-mise，
  head 50149d7（base 05389f4，merge commit 方式，不 rebase）。
- CI run 38527922201：conclusion=success；actionlint SUCCESS（真实执行 5s，
  非 SKIPPED）；test SUCCESS。diff 322 行（target 350 内）。
- 未做：herdweb 真实 consumer opt-in 与自托管执行（发布后由其 owner 验收；
  issue #314 不自动关单，旧 TLS 事项不在本卡宣称范围）。
- 下一步唯一动作：主脑 review / 标 ready / merge，v2 远端发布核验后通知 herdweb owner。

## 2026-10-14 R2 修复增量（gate-20261014-43）：锁死零导出，撤回过强 trust 承诺

- 阶段：repairing（接管原树，同 PR#315 继续，不换分支名；H0=9e72cb5）。
- 起因：顾问裁决（codex 20261014-155052）指出 install:false 仍导出 caller 项目
  env/shims（v3 默认 env:true→`mise env` 写 GITHUB_ENV；add_shims_to_path:true→
  shims 入 PATH），本步骤在平台源码 checkout/PR 预检之前，污染面真实存在；
  「trust 全部业务入口拥有」承诺过满——上游把 workspace 写进
  MISE_TRUSTED_CONFIG_PATHS，auto-trust 不因 cwd 取消。原表述撤回。
- 亲核官方 v3 真行为（action.yml + src/index.ts，缓存 /tmp/gate-mise-261014-41/）：
  env:false 跳过 exportMiseEnv；add_shims_to_path:false 跳过 shims 入 PATH，
  mise 本体 bin 恒入 PATH（入口仍可跑 `mise trust && mise install`）；
  working_directory 仅作 mise 子进程 cwd；install:false 不走 cache save 分支。
- 红绿（TDD 先改测试）：with 四参数精确匹配（install/env/add_shims_to_path:
  false + working_directory: runner.temp）→ 红 exit 1（1 failed / 228 passed，
  `got: {'install': False}`，显式 AssertionError 非 KeyError）；改 gate-v2.yml
  后窄套件 229 passed exit 0。
- 变异红验（commit e3c5122 后注入）：去 env:false 一行 → 精确 with 断言
  AssertionError（Right contains 1 more item: {'env': False}）红 exit 1 →
  仅还原该行（工作树回到与 HEAD 零差异）→ 绿恢复。
- README/design：撤回过强 trust 承诺，如实写上游 auto-trust 与默认缓存读取仍会
  发生、gate 不代跑 trust/install；inputs 表同步；不变式新增第 4 条。
- 关键决策与已否决：保留官方 action 与默认 cache，不自建安装器/隔离壳（卡面
  禁止）；working_directory 取 runner.temp（项目外、runner 恒有、无新状态），
  不用 workspace（正是被修的污染源）；三输入均官方 v3 已支持，无需上行。
- 未做：herdweb 真实 consumer opt-in + 自托管执行（主脑另派 probe）；本卡不宣
  完整修复；issue #314 不自动关单。
- 下一步唯一动作：push（一轮一次）→ 等 PR CI 终态 → 主脑 review/ready/merge/
  v2 远端核验后通知 herdweb owner。
