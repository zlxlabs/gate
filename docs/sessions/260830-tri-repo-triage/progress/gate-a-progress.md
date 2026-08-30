# gate-A：disposition workflow_call + #88 通电修复

## 2026-08-30 里程碑 ① #88 sys.modules 注册

- 当前阶段：implementing / milestone ① #88 修复①
- 本段结论：内联 python 动态导入 `convergence.py` 已按 `issue_receipt.py:28-36` 补上 `spec/loader` 判空与 `sys.modules[spec.name] = module` 注册，路径改为 `Path(...).resolve()`。契约测试提取 workflow 内该段 python，先断言注册发生在 `exec_module` 之前，再以 issue #88 最小复现方式对真实 `convergence.py` 子进程执行。
- 关键决策与已否决方案：执行验证用 subprocess 跑提取出的源码（子进程 AttributeError 会变成父进程 `returncode != 0` 的 AssertionError），不在测试进程里直接 `exec` 以免红验撞成 ERROR。不改 `convergence.py` / `issue_receipt.py`。
- 下一步唯一动作：checkout 加 `sparse-checkout-cone-mode: false` 并补「改坏即红」断言。

## 2026-08-30 里程碑 ② #88 cone-mode 关闭

- 当前阶段：implementing / milestone ② #88 修复②
- 本段结论：checkout 已加 `sparse-checkout-cone-mode: false`，与两个文件级 sparse-checkout 路径同在。契约测试断言清单恰好是 `issue_receipt.py` 与 `convergence.py`，且 cone-mode 为 false。
- 关键决策与已否决方案：不把清单改成目录（会扩大检出面）；不改 `actions/checkout` pin。
- 下一步唯一动作：给 workflow 加 `workflow_call` 与必填 `gate_ref`，checkout 改为 `repository: zlxlabs/gate` + `ref: ${{ inputs.gate_ref }}`。

## 2026-08-30 里程碑 ③ workflow_call + gate_ref

- 当前阶段：implementing / milestone ③ workflow_call
- 本段结论：`on` 同时含 `workflow_dispatch` 与 `workflow_call`，两边 inputs 集合同为五业务字段 + 必填 `gate_ref`。checkout 改为 `repository: zlxlabs/gate` + `ref: ${{ inputs.gate_ref }}`，dispatch 路径也显式传 pin，不再靠「跑在自己仓」的巧合。
- 关键决策与已否决方案：两条 trigger 的 inputs 各写一遍（不用 YAML anchor，避免 `on`/`True` 解析歧义）。`actions: write` 本段不收窄，核对结果放末段报告。
- 下一步唯一动作：新增 `templates/caller-gate-disposition.yml` 并补 caller 契约测试。

## 2026-08-30 里程碑 ④ caller 模板

- 当前阶段：implementing / milestone ④ caller 模板
- 本段结论：新增 `templates/caller-gate-disposition.yml`：`workflow_dispatch` 收五个业务输入，`uses` 与 `gate_ref` 同为 `__PINNED_GATE_SHA__` 占位（onboarding 换成同一 40-hex），permissions 仅 `actions: read` + `contents: read`，concurrency 组名沿用 `gate-disposition-<repository_id>-<pr_number>`。
- 关键决策与已否决方案：`gate_ref` 不做成 dispatch 输入（避免与 uses pin 手填不一致）；占位符形态与 `caller-gate-v2.yml` 相同。caller 不声明 `environment`（保护留在被调 workflow 的 `jobs.control`）。
- 下一步唯一动作：红验两处 #88 测试锁，再跑全量 pytest 与 `check_pinned_uses.py`。

## 2026-08-30 红验判据收口

- 当前阶段：implementing / 红验判据
- 本段结论：cone-mode 断言改为 `.get(...) is False`。缺键时原先是 KeyError（pytest ERROR 形态），现在是 AssertionError（None is False），满足红验「断言失败」要求。
- 关键决策与已否决方案：不把缺键写成 `assert "sparse-checkout-cone-mode" in checkout["with"]` 再另断言取值——一条 `is False` 同时锁「键存在且为 false」。
- 下一步唯一动作：去掉 cone-mode 行红验（断言失败）后还原，再跑全量测试。

## 2026-08-30 收口

- 当前阶段：implementing / 验证收口
- 本段结论：两处 #88 红验均为 AssertionError 后已还原。全量 `uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q` 659 passed 退出码 0；`python3 scripts/check_pinned_uses.py` 退出码 0。`actions: write` 因 `actions/upload-artifact` 不能收窄，核对写进报告。
- 关键决策与已否决方案：无。
- 下一步唯一动作：主脑走本地 review 循环；合并须用 merge commit（执行器不合并不删分支）。

## 2026-08-30 主脑修订 caller 权限交集

- 当前阶段：implementing / 验收修订
- 本段结论：caller 模板 permissions 改为精确三项 `actions: write` / `contents: read` / `pull-requests: read`，使 reusable-workflow token（caller ∩ callee）覆盖 upload-artifact 与 `gh api pulls`。契约测试按精确集合锁定，并注释交集机理。
- 关键决策与已否决方案：主脑修订卡面锁定决策 2；不扩其它 scope。去掉原文案负断言 `actions: write not in text`（与定稿矛盾），`pull-requests: write` 负断言保留。
- 下一步唯一动作：主脑走本地 review 循环；合并须用 merge commit（执行器不合并不删分支）。

## 2026-08-30 R1 F1 gate_ref 40-hex 闸

- 当前阶段：repairing / R1 F1
- 本段结论：checkout 前新增独立 step `Require 40-hex gate_ref`，bash 正则 `^[0-9a-f]{40}$` 不匹配则 `::error` 后 `exit 1`。step 读 `inputs.gate_ref`，dispatch 与 workflow_call 同一道闸。不解析 branch/tag、不做 SHA 规范化。
- 关键决策与已否决方案：拒绝把 `git rev-parse` / API 解析成 SHA 再继续（评审明确禁止 fallback）。正则不含 `A-F`（只收小写）。
- 下一步唯一动作：红验改坏正则确认 AssertionError 后还原，再跑全量测试与 `check_pinned_uses.py`。

## 2026-08-30 R1 F1 验证收口

- 当前阶段：repairing / R1 F1 验证收口
- 本段结论：红验把 `{40}` 改成 `{39}` 后 `test_disposition_requires_lowercase_40_hex_gate_ref_before_checkout` 以 AssertionError 转红，已还原。全量 pytest 660 passed 退出码 0；`check_pinned_uses.py` 退出码 0。
- 关键决策与已否决方案：无。
- 下一步唯一动作：主脑走本地 review 循环；不推不合并（推送由主脑做）。
