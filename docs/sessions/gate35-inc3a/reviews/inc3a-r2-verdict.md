VERDICT: pass

# gate#35 增量 3a 独立评审 R2

审查范围固定为 `1ea66b7..a501b7a1d43092efd77c10488efefceaf3630c04`；只审本次 diff。项目风险等级为 personal。本轮 P1=0。

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

实际命令以 H0 detached worktree 的真实 `aggregate.py` 运行：

```sh
git worktree add --detach "$conc_dir" a501b7a1d43092efd77c10488efefceaf3630c04
uv run --with pytest,PyYAML python - "$conc_dir" <<'PY'
first = subprocess.run(command(..., receipt=first_receipt), ...)
second = subprocess.run(command(..., receipt=second_receipt), ...)
p1 = subprocess.Popen(command(..., receipt=shared_receipt), ...)
p2 = subprocess.Popen(command(..., receipt=shared_receipt), ...)
PY
git worktree remove --force "$conc_dir"
```

真实输出：

```text
REENTRY first_rc=0 second_rc=0 bytes_equal=True len=1284 sha256_first=01521972630971429706735504e3b2cab9e3efcfbbb3202796987155b17ce6fa sha256_second=01521972630971429706735504e3b2cab9e3efcfbbb3202796987155b17ce6fa
REENTRY_JSON first_valid=True second_valid=True
CONCURRENT attempt=1 p1_rc=0 p2_rc=0 target_exists=True target_len=1284 json_valid=True siblings=['convergence-receipt.json'] stderr1='' stderr2=''
CONCURRENT attempt=2 p1_rc=0 p2_rc=0 target_exists=True target_len=1284 json_valid=True siblings=['convergence-receipt.json'] stderr1='' stderr2=''
CONCURRENT attempt=3 p1_rc=0 p2_rc=0 target_exists=True target_len=1284 json_valid=True siblings=['convergence-receipt.json'] stderr1='' stderr2=''
CONCURRENT attempt=4 p1_rc=0 p2_rc=0 target_exists=True target_len=1284 json_valid=True siblings=['convergence-receipt.json'] stderr1='' stderr2=''
CONCURRENT attempt=5 p1_rc=0 p2_rc=1 target_exists=True target_len=1284 json_valid=True siblings=['convergence-receipt.json'] stderr1='' stderr2='... FileNotFoundError ... .convergence-receipt.json.tmp -> ... convergence-receipt.json'
```

结论：同一 run identity 的顺序重入字节完全一致；原子 replace 未暴露半截 target。非真实单进程入口的并发竞争可让一个 writer 在共享固定 `.tmp` 名称上失败，但已成功 writer 留下完整 JSON，按 personal 档记 P2/backlog。

## 运行时不变式锁定

实际命令：

```sh
uv run --with pytest,PyYAML python -m pytest -q \
  "$test_dir/tests/test_gate_convergence.py::test_receipt_for_round_copies_decision_identity_and_validates" \
  "$test_dir/tests/test_gate_convergence_artifact.py::test_aggregate_cli_receipt_bytes_validate_and_replay" \
  "$test_dir/tests/test_gate_v2_contract.py::test_gate_aggregate_writes_receipt_output_and_transparently_exits_with_aggregate_rc"
```

真实输出：

```text
...                                                                      [100%]
3 passed in 0.41s
TARGETED_RC=0
```

这三条测试分别锁定 `RoundDecision` 三元身份复制、subprocess producer/consumer receipt replay，以及 workflow 输出/原始退出码契约。

## 对抗清单

- 有没有办法让 `gate/gate` 变绿，而本轮的 receipt 没有成功上传？不能在本轮真实单进程路径中证明存在。实验二的完整 receipt/零退出码组合为 `shell_rc=0` 且 `github_output='convergence-receipt=present\n'`；H0 workflow 结构探针输出 `ORDER receipt=4 terminal=5 panel=6`、`continue_on_error=None`、`if_no_files_found='error'`，所以 receipt upload 失败会使 job 失败，且上传排在 terminal/status panel 前。

  证据命令/输出：

  ```text
  CASE write=full termination=zero shell_rc=0 github_output='convergence-receipt=present\n' target=b'FULL' stderr=''
  ORDER receipt=4 terminal=5 panel=6
  RECEIPT_UPLOAD continue_on_error=None if="always() && steps.aggregate-required-verdict.outputs.convergence-receipt == 'present'" if_no_files_found='error'
  ```

- 有没有办法让某一轮真实发生过的判定不产生 receipt，从而在账本里凭空消失？本轮矩阵不能证明存在。合法 canonical `pass/fail/unavailable` 都实际产生 receipt；quality 失败时仍产生；只有 audit 缺失/损坏、来源不匹配和 draft/fork/异常 skip 等非 eligible 或 fail-closed 轮不产出，并在 Summary 写明原因。

  证据命令/输出：

  ```text
  CASE verdict-pass exit=0 receipt=present ... summary='Convergence receipt: produced (`convergence-receipt.json`).'
  CASE verdict-fail exit=1 receipt=present ... summary='Convergence receipt: produced (`convergence-receipt.json`).'
  CASE verdict-unavailable exit=1 receipt=present ... summary='Convergence receipt: produced (`convergence-receipt.json`).'
  CASE quality-failure-valid-pass exit=1 receipt=present ... summary='Convergence receipt: produced (`convergence-receipt.json`).'
  CASE audit-missing-primary-failure exit=1 receipt=<absent> summary='Convergence receipt: not produced (reason: `audit_missing`).'
  CASE source-attempt-mismatch exit=1 receipt=<absent> summary='Convergence receipt: not produced (reason: `audit_source_mismatch`).'
  CASE draft-primary-skipped exit=0 receipt=<absent> summary='Convergence receipt: not produced (reason: `review_not_expected`).'
  ```

- 有没有办法让 aggregate 的真实退出码被吞掉或改写，使红轮被当成绿轮？不能。默认 shell 实验中 stub 的 7 和 SIGTERM 都原样成为 `shell_rc=7` 与 `shell_rc=143`，同时 `set +e` 仍继续写 output；`exit "$rc"` 没有吞掉原码。

  证据命令/输出：

  ```text
  CASE write=full termination=nonzero shell_rc=7 github_output='convergence-receipt=present\n' target=b'FULL' stderr=''
  CASE write=full termination=signal shell_rc=143 github_output='convergence-receipt=present\n' target=b'FULL' stderr='Terminated'
  ```

- receipt 文件写到一半进程被杀，留下的残缺文件会被 upload 传出去吗？真实 producer 路径不会把半截写到目标路径。模拟 `_write_convergence_receipt` 在 temp 写半后 SIGKILL 的 subprocess 输出是 `target_exists=False`、仅 temp 存在；workflow 检查的是目标 `convergence-receipt.json`，因此该状态不会被标记为 present。对照 shell stub 的 direct-half 结果确实是 `present`，说明若未来破坏原子写入才会触发上传候选，但当前生产实现未走该路径。

  证据命令/输出：

  ```text
  KILLED_WRITE rc=-9 target_exists=False temp_exists=True temp_len=57 stderr=''
  CASE write=half termination=zero shell_rc=0 github_output='convergence-receipt=present\n' target=b'HALF' stderr=''
  ```

- `GITHUB_OUTPUT` 的写入本身失败（磁盘满、变量未设）时，行为是 fail-loud 还是静默？fail-loud。未定义变量或目标是目录都会让 echo 重定向返回 1；即使 receipt 已存在，step 也不会以 0 结束。

  证据命令/输出：

  ```text
  GITHUB_OUTPUT=unset shell_rc=1 receipt_exists=True stderr='bash: line 23: : No such file or directory'
  GITHUB_OUTPUT=directory shell_rc=1 receipt_exists=True stderr='bash: line 23: /tmp/gate35-output-79pu6ou1/directory/github-output: Is a directory'
  ```

## Findings

| 级别 | 文件:行 | 违反 spec | 可复现触发命令 | 建议修法 |
|---|---|---|---|---|
| P2 | `.github/actions/gate-aggregator/aggregate.py:741-751` | spec 6（原子落盘）在两个进程违反单写者前提、同时复用固定 `.tmp` 名称时，一个 writer 会在 `replace()` 抛 `FileNotFoundError`；真实 workflow 只有一个 aggregate 进程，故不升 P1 | 实验四命令中的两个 `subprocess.Popen(... --convergence-receipt-path "$shared_receipt")`；真实输出为 `p1_rc=0 p2_rc=1`、target `len=1284`、JSON 有效 | 若未来允许同一路径并发写，使用每 writer 唯一临时文件名或显式单写者约束；当前单进程入口可接受不修 |

## Backlog 与越界项

- OCR 前置扫描：第一次背景文件为空，工具返回 `status=skipped` 与 `caller_error:background_empty`；第二次使用 6000 字节冻结 diff 背景运行约两分钟后中止，未取得 reviewed envelope，不能表述为“扫过且干净”。
- 不审刻意未通电的 3b 路径：历史 receipt 读回、artifact 分页下载/消歧、disposition receipt 消费、convergence envelope 落盘、真实 canary、外部 state 可信根。
- 并发竞争是本轮额外探针发现的 P2：实际 workflow 的 aggregate step 是单进程，未将该非真实入口升级为 P1。
