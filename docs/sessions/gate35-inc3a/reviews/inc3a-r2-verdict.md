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

实际命令（YAML 由 H0 解析，`${{ runner.temp }}` 在实验目录中按 Actions 渲染结果替换）：

```sh
uv run --with PyYAML python - <<'PY'
workflow = subprocess.check_output(["git", "show", "a501b7a1d43092efd77c10488efefceaf3630c04:.github/workflows/gate-v2.yml"], text=True)
raw = yaml.safe_load(workflow)
run_template = next(step["run"] for step in raw["jobs"]["gate"]["steps"] if step.get("name") == "Aggregate required verdict")
subprocess.run(["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", run], ...)
PY
```

stub 对 `STUB_WRITE` 穷举 `none/full/half`，对 `STUB_TERM` 穷举 `zero/nonzero/signal`，共 9 组。真实提取的 shell 起始/收尾为：

```text
set +e
python3 _gate-aggregator-src/.github/actions/gate-aggregator/aggregate.py \
  ... --convergence-receipt-path "$CONVERGENCE_RECEIPT_PATH"
rc=$?
set -e
if [ -f "$CONVERGENCE_RECEIPT_PATH" ]; then
  echo "convergence-receipt=present" >> "$GITHUB_OUTPUT"
else
  echo "convergence-receipt=absent" >> "$GITHUB_OUTPUT"
fi
exit "$rc"
```

真实输出：

```text
CASE write=none termination=zero shell_rc=0 github_output='convergence-receipt=absent\n' target=b'<missing>' stderr=''
CASE write=none termination=nonzero shell_rc=7 github_output='convergence-receipt=absent\n' target=b'<missing>' stderr=''
CASE write=none termination=signal shell_rc=143 github_output='convergence-receipt=absent\n' target=b'<missing>' stderr='Terminated'
CASE write=full termination=zero shell_rc=0 github_output='convergence-receipt=present\n' target=b'FULL' stderr=''
CASE write=full termination=nonzero shell_rc=7 github_output='convergence-receipt=present\n' target=b'FULL' stderr=''
CASE write=full termination=signal shell_rc=143 github_output='convergence-receipt=present\n' target=b'FULL' stderr='Terminated'
CASE write=half termination=zero shell_rc=0 github_output='convergence-receipt=present\n' target=b'HALF' stderr=''
CASE write=half termination=nonzero shell_rc=7 github_output='convergence-receipt=present\n' target=b'HALF' stderr=''
CASE write=half termination=signal shell_rc=143 github_output='convergence-receipt=present\n' target=b'HALF' stderr='Terminated'
```

结论：receipt 标志只看本轮目标文件是否存在，与 aggregate 退出码无关；shell 捕获并原样返回 0、7、143。半写 stub 的 `present` 只证明 shell 语义，生产写入的原子性在实验四验证。

## 实验三：CLI 形态穷举

实际命令以 H0 detached worktree 中的真实脚本运行：

```sh
git worktree add --detach "$cli_dir" a501b7a1d43092efd77c10488efefceaf3630c04
uv run --with pytest,PyYAML python - "$cli_dir" <<'PY'
subprocess.run([sys.executable, str(aggregate), ...,
                "--convergence-receipt-path", str(receipt)], ...)
PY
git worktree remove --force "$cli_dir"
```

审查矩阵覆盖合法 verdict、四种已知 severity、未知 severity、质量失败、文件缺失/多个/非 UTF-8/JSON 非对象、source attempt 不匹配、draft/fork/expected 与 unexpected primary skip。真实输出摘录（`receipt` 后的字段来自实际落盘 JSON）：

```text
CASE verdict-pass exit=0 receipt=present {"decision":"converged","clean_streak":1,"eligible_rounds":1,"p1_ids":[],"receipt_kind":"canonical_primary","schema_version":1} summary='Convergence receipt: produced (`convergence-receipt.json`).'
CASE verdict-fail exit=1 receipt=present {"decision":"converged","clean_streak":1,"eligible_rounds":1,"p1_ids":[],"verdict":"fail"} summary='Convergence receipt: produced (`convergence-receipt.json`).'
CASE verdict-unavailable exit=1 receipt=present {"decision":"collecting","clean_streak":0,"eligible_rounds":0,"verdict":"unavailable"} summary='Convergence receipt: produced (`convergence-receipt.json`).'
CASE verdict-not_expected exit=1 receipt=<absent> summary='Convergence receipt: not produced (reason: `audit_invalid`).'
CASE verdict-waived exit=1 receipt=<absent> summary='Convergence receipt: not produced (reason: `audit_invalid`).'
CASE finding-major exit=0 receipt=present {"decision":"collecting","clean_streak":0,"eligible_rounds":1,"p1_ids":["f1"]} summary='Convergence receipt: produced (`convergence-receipt.json`).'
CASE finding-blocker exit=0 receipt=present {"decision":"collecting","clean_streak":0,"eligible_rounds":1,"p1_ids":["f1"]} summary='Convergence receipt: produced (`convergence-receipt.json`).'
CASE finding-minor exit=0 receipt=present {"decision":"converged","clean_streak":1,"eligible_rounds":1,"p1_ids":[]} summary='Convergence receipt: produced (`convergence-receipt.json`).'
CASE finding-nit exit=0 receipt=present {"decision":"converged","clean_streak":1,"eligible_rounds":1,"p1_ids":[]} summary='Convergence receipt: produced (`convergence-receipt.json`).'
CASE finding-unknown exit=1 receipt=<absent> summary='Convergence receipt: not produced (reason: `audit_invalid`).'
CASE quality-failure-valid-pass exit=1 receipt=present {"decision":"converged","clean_streak":1,"eligible_rounds":1,"verdict":"pass"} summary='Convergence receipt: produced (`convergence-receipt.json`).'
CASE audit-missing-primary-failure exit=1 receipt=<absent> summary='Convergence receipt: not produced (reason: `audit_missing`).'
CASE audit-missing-primary-success exit=1 receipt=<absent> summary='Convergence receipt: not produced (reason: `audit_missing`).'
CASE audit-multiple-json exit=1 receipt=<absent> summary='Convergence receipt: not produced (reason: `audit_missing`).'
CASE audit-non-utf8 exit=1 receipt=<absent> summary='Convergence receipt: not produced (reason: `audit_missing`).'
CASE audit-json-list exit=1 receipt=<absent> summary='Convergence receipt: not produced (reason: `audit_invalid`).'
CASE source-attempt-mismatch exit=1 receipt=<absent> summary='Convergence receipt: not produced (reason: `audit_source_mismatch`).'
CASE draft-primary-skipped exit=0 receipt=<absent> summary='Convergence receipt: not produced (reason: `review_not_expected`).'
CASE fork-primary-skipped exit=0 receipt=<absent> summary='Convergence receipt: not produced (reason: `review_not_expected`).'
CASE unexpected-primary-skipped exit=1 receipt=<absent> summary='Convergence receipt: not produced (reason: `unexpected_primary_skip`).'
```

结论：可产出 receipt 的 canonical round 即使 aggregate 因 quality/primary 结果返回非 0 也保留 receipt；缺失、损坏、来源不匹配和非 eligible skip 均不产生 receipt，并在 Summary 给出原因。

## 实验四：并发与重入

待执行；将比较同一 run identity 的重复输出字节，并验证同一路径并发写入时的可见文件。

## 对抗清单

待四组运行时证据完成后逐条填写。

## Findings

当前无已验证 finding。

## Backlog 与越界项

- OCR 前置扫描：第一次背景文件为空，工具返回 `status=skipped` 与 `caller_error:background_empty`；第二次使用 6000 字节冻结 diff 背景运行约两分钟后中止，未取得 reviewed envelope，不能表述为“扫过且干净”。
- 不审刻意未通电的 3b 路径：历史 receipt 读回、artifact 分页下载/消歧、disposition receipt 消费、convergence envelope 落盘、真实 canary、外部 state 可信根。
