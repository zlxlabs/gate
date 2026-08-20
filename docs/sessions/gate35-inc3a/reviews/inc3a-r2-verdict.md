VERDICT: pass

# gate#35 增量 3a 独立评审 R2

审查范围固定为 `1ea66b7..a501b7a1d43092efd77c10488efefceaf3630c04`；只审本次 diff。项目风险等级为 personal。本轮 P1=0（截至实验一）。

## 新证据与实验一：base 红验抽查

新证据是把 H0 修改过的四个测试文件拷入基线 detached worktree，仅使用基线生产代码运行；这不是再次阅读同一份 diff。

实际命令：

```sh
red_dir=$(mktemp -d /tmp/gate35-red.XXXXXX)
git worktree add --detach "$red_dir" 1ea66b7
for test_path in tests/test_gate_aggregator.py tests/test_gate_convergence.py tests/test_gate_convergence_artifact.py tests/test_gate_v2_contract.py; do
  mkdir -p "$red_dir/$(dirname "$test_path")"
  git show a501b7a1d43092efd77c10488efefceaf3630c04:"$test_path" > "$red_dir/$test_path"
done
uv run --with pytest,PyYAML python -m pytest -q "$red_dir/tests/test_gate_aggregator.py" "$red_dir/tests/test_gate_convergence.py" "$red_dir/tests/test_gate_convergence_artifact.py" "$red_dir/tests/test_gate_v2_contract.py"
git worktree remove --force "$red_dir"
```

真实输出摘录：

```text
--- red worktree status ---
 M tests/test_gate_aggregator.py
 M tests/test_gate_convergence.py
 M tests/test_gate_convergence_artifact.py
 M tests/test_gate_v2_contract.py
........................................................................ [ 22%]
.FFFFFFF......................................................... [ 44%]
..........................................F............................. [ 67%]
................................F.............................FF........ [ 89%]
.................................                                        [100%]
17 failed, 304 passed in 3.00s
RED_TEST_RC=1
```

失败均命中本次新增行为的缺失：基线没有 `_read_audit_file`、`--convergence-receipt-path`、`receipt_for_round` 以及 workflow 的 receipt upload/output wiring。因新增测试在 base 上确实变红，本组没有恒真测试 finding。

## 实验二：workflow shell 真实语义

待执行；将从 H0 提取 `Aggregate required verdict` 的 `run`，在默认 bash `-e -o pipefail` 下用 stub 穷举 receipt/退出码/信号/半写组合。

## 实验三：CLI 形态穷举

待执行；将通过真实 subprocess 调用 H0 `aggregate.py`，记录退出码、receipt 存在性与内容。

## 实验四：并发与重入

待执行；将比较同一 run identity 的重复输出字节，并验证同一路径并发写入时的可见文件。

## 对抗清单

待四组运行时证据完成后逐条填写。

## Findings

当前无已验证 finding。

## Backlog 与越界项

- OCR 前置扫描：第一次背景文件为空，工具返回 `status=skipped` 与 `caller_error:background_empty`；第二次使用 6000 字节冻结 diff 背景运行约两分钟后中止，未取得 reviewed envelope，不能表述为“扫过且干净”。
- 不审刻意未通电的 3b 路径：历史 receipt 读回、artifact 分页下载/消歧、disposition receipt 消费、convergence envelope 落盘、真实 canary、外部 state 可信根。
