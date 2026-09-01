# gate-A disposition workflow_call 改造：R1 verdict

## 审查边界与本轮新证据

- 审查对象固定为 `e0bd30f88e99decb1e35a6601768d41615defbf8..1708fef6d584306444fb5b7ff501199bcd9b717f`；PR #93 的 base/head 与该范围一致，审查期间不纳入其它提交。
- 本仓 `risk-tier: personal`。该 diff 涉及 disposition 失败路径/发布边界，按 `core-lead.md` 的例外规则采用 personal 项目的“两轮无新增 P1”收敛门槛。
- 本轮新证据（均在初次 diff 阅读后取得）：OCR 返回 `status=reviewed`、`coverage=complete`；`uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_v2_contract.py` 为 `65 passed in 1.70s`；`actionlint` 检查两份工作流无输出且退出码 0；`python3 scripts/check_pinned_uses.py` 为 8 个 live metadata 文件全通过；H0 临时 worktree 红验结果见下文。

## Finding F1：gate_ref 没有强制 40-hex SHA

- **Severity：P1。**
- **违反条款：** spec 1 的“`gate_ref` 40-hex 必填”；spec 1 的“签发代码 = caller pin 的那一份”；不变式/威胁模型中的“agent 不得给自己开绿灯”。
- **证据：** `.github/workflows/gate-v2-disposition.yml:11,19` 只声明 `required: true`、`type: string` 和描述文字；`:41-42` 将输入直接传给 `actions/checkout` 的 `ref`；`tests/test_gate_v2_contract.py:150-153` 只锁 `required/type`，没有形状断言或运行时拒绝路径。官方 `actions/checkout` 的 `ref` 输入明确接受 branch、tag 或 SHA（[action.yml](https://github.com/actions/checkout/blob/main/action.yml)）。
- **真实触发方式：** `workflow_dispatch` 是该 workflow 的实际入口，操作者或 agent 可以提交 `main`、tag 或其它 gate 分支作为 `gate_ref`，GitHub 不会因当前 schema 的 `type: string` 拒绝它；因此 checkout 的 producer 代码不再由 immutable SHA 约束。正常的业务 caller 模板路径会替换占位符为 SHA，但不能覆盖直接 dispatch 路径。
- **P1 两问：** ①会被真实使用触发吗？**会**——直接 dispatch 接受任意字符串，而 checkout 的既定语义就是 branch/tag/SHA。②后果能否接受？**不能**——未审/可变 producer 可生成看似合法的 `gate-disposition-receipt-v1`，下游会把匹配 P1 finding 解析为已 disposition，形成绕过门禁的自我放行；这是本仓明确的 P1 威胁模型。
- **建议方向（仅记录，不在本轮修复）：** checkout 前 fail-fast 校验输入为精确 40 位十六进制，并新增一个把非法 branch/tag 改坏即转为 `AssertionError` 的契约测试；不增加 fallback 或兼容分支。

## 已核对且不成立的意见

- callee 保留 `actions: write`、`contents: read`、`pull-requests: read`，job 保留 `environment: gate-disposition`；caller 三权限与 callee 交集符合锁定设计。
- 动态导入的 `spec/loader` 判空、`sys.modules[spec.name] = module` 先于 `exec_module` 均存在；文件级 sparse-checkout 配置 `sparse-checkout-cone-mode: false` 存在；两处修复都有真实红验。
- workflow 的 `workflow_dispatch` / `workflow_call` 都包含五业务输入和 `gate_ref`；caller 模板透传五业务输入，并让 `uses` 与 `gate_ref` 使用同一占位符。README 已明确 caller 模板的占位符是接入前替换的既有约定，不是 live workflow。
- 没有改动 `.github/actions/**`、`gate-v2.yml` 或 `scripts/`；既有安全负断言保持。

## 工具标注 / 本仓判定 / 两问答案

| OCR 工具标注 | 本仓判定 | 两问答案与依据 |
|---|---|---|
| high：`gate_ref` 未校验 40-hex（1 条 confirmed，另 1 条重复且未复核） | **P1，形成 F1** | 会：dispatch 接受 branch/tag；不能：可执行可变 producer 并绕过门禁。 |
| high：`workflow_call` 未写 `secrets`，会默认转发全部 secrets | 不成立，不记 finding | 不会按该理由触发：GitHub 文档规定 named secrets 需在 caller 显式传递；全量传递是 caller 写 `secrets: inherit` 的显式行为，而非 callee 缺省行为。见 [workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax) 与 [reuse workflows](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows)。当前 caller 没有 `secrets`。 |
| low：动态导入修复正确 | 无 finding | 代码与新增测试均覆盖；不是缺陷。 |
| medium：heredoc 用 `.index`、缺少显式断言错误 | 无 finding | `tests/test_gate_v2_contract.py:188-191` 先用断言确认两个目标字符串存在，再比较顺序；目标回归会是 `AssertionError`。 |
| medium：subprocess 没有 timeout | 无 finding | 当前 producer probe 不读 stdin，且只审本次 diff；无证据表明当前使用会挂死，也无法溯源到本卡 spec。 |
| low：sparse 列表过滤空行后用 set | 无 finding | spec 锁文件集合与 cone-mode，不要求空白行成为语义；当前断言能锁定新增文件路径与 false。 |
| low：caller pin 是占位符 | 无 finding | 这是仓库 README:112-118 明确的 onboarding 模板契约，且本模板注释要求替换两处为同一 40-hex；不能反着文档契约判 fail。 |

## 红验抽查证据

在临时 detached worktree `e0bd30f...` 上，仅拷入当前提交的 `tests/test_gate_v2_contract.py`，未拷入实现文件；两个抽查均按要求转为 `AssertionError`，没有意外绿，也没有 ImportError/AttributeError/SyntaxError。

```text
--- red verification: sys.modules registration ---
F                                                                        [100%]
=================================== FAILURES ===================================
__ test_disposition_inline_python_registers_sys_modules_before_dataclass_exec __
>       assert register in source
E       assert 'sys.modules[spec.name] = module' in 'import importlib.util\\nimport json\\nimport sys\\nfrom pathlib import Path\\n\\naudit = json.loads(Path(sys.argv[1]).read...'
tests/test_gate_v2_contract.py:188: AssertionError
=========================== short test summary info ============================
FAILED tests/test_gate_v2_contract.py::test_disposition_inline_python_registers_sys_modules_before_dataclass_exec
1 failed in 0.13s

--- red verification: sparse checkout cone mode ---
F                                                                        [100%]
=================================== FAILURES ===================================
_____ test_disposition_sparse_checkout_lists_files_and_disables_cone_mode ______
>       assert checkout["with"].get("sparse-checkout-cone-mode") is False
E       AssertionError: assert None is False
tests/test_gate_v2_contract.py:240: AssertionError
=========================== short test summary info ============================
FAILED tests/test_gate_v2_contract.py::test_disposition_sparse_checkout_lists_files_and_disables_cone_mode
1 failed in 0.04s
```

## 熵增审查

- `.github/workflows/gate-v2-disposition.yml` 新增 `workflow_call` 与 `gate_ref`：不是无消费者抽象；`gate_ref` 同时由 caller pin 产生、由 callee checkout 消费，正是跨 caller/callee 发布边界所需的输入。其 40-hex 守卫缺失已单列 F1。
- `templates/caller-gate-disposition.yml`：是用户要求的业务仓 caller 入口，第二消费者是每个接入业务仓的复制/替换流程；不是转发层之外的新运行时抽象。三项权限是锁定设计要求。
- `tests/test_gate_v2_contract.py` 的 `_disposition_scope_python`、`_load_disposition_caller` 与 pin 常量：均为测试边界 fixture；前者必须提取并执行 workflow 实际 heredoc，后者必须独立加载新模板，避免复制生产片段后测错对象；没有新增生产状态、fallback、重试或通用配置层。
- `docs/sessions/260830-tri-repo-triage/progress/gate-a-progress.md`：仅是已有会话进度记录，无运行时消费者、状态迁移或包装层，不计熵增 finding。

## 结论

存在 1 条 P1，必须修复后重新全量审查；本轮 review verdict 为 fail。审查执行本身已完成，未发现其它 P1/P2/P3 阻塞意见。

verdict: fail
