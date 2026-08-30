# gate-A：disposition workflow_call + #88 通电修复

## 2026-08-30 里程碑 ① #88 sys.modules 注册

- 当前阶段：implementing / milestone ① #88 修复①
- 本段结论：内联 python 动态导入 `convergence.py` 已按 `issue_receipt.py:28-36` 补上 `spec/loader` 判空与 `sys.modules[spec.name] = module` 注册，路径改为 `Path(...).resolve()`。契约测试提取 workflow 内该段 python，先断言注册发生在 `exec_module` 之前，再以 issue #88 最小复现方式对真实 `convergence.py` 子进程执行。
- 关键决策与已否决方案：执行验证用 subprocess 跑提取出的源码（子进程 AttributeError 会变成父进程 `returncode != 0` 的 AssertionError），不在测试进程里直接 `exec` 以免红验撞成 ERROR。不改 `convergence.py` / `issue_receipt.py`。
- 下一步唯一动作：checkout 加 `sparse-checkout-cone-mode: false` 并补「改坏即红」断言。
