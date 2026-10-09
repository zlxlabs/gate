# Counterevidence 门禁重跑契约

父单：zlxlabs/gate-hub#1416（#1241）。威胁模型见本仓 `AGENTS.md`：唯一要防的是「agent 给自己开绿灯」。

## 1. 结论

false-positive 反证不再采信提交方贴的 `command`/`output`。门禁在回执 `head_sha` 上**自己执行**唯一白名单命令；convergence 只认带这次执行结果的反证。

```text
git grep -n -F -e <literal> <HEAD_SHA> -- <pathspec>
```

≥1 命中才成立。零命中不是「没有调用方」的证据，只是反证不成立。

## 2. 白名单 argv

`shlex.split(command)` 必须**精确**等于：

```text
["git", "grep", "-n", "-F", "-e", <literal>, <sha>, "--", <pathspec>]
```

| 字段 | 约束 |
|---|---|
| `<sha>` | 等于回执 `head_sha`（全 40 位，逐字相等） |
| `<literal>` | 非空，不以 `-` 开头 |
| `<pathspec>` | 非空；不以 `-` / `:` 开头；不含 `..` 段；非绝对路径 |

任何偏离 → `counterevidence_command_not_allowlisted`。

校验函数：`convergence.counterevidence_argv_reason(argv, head_sha) -> str | None`。issue_receipt 与 convergence 共用。

## 3. 门禁执行

`issue_receipt.rerun_counterevidence(command, head_sha, repo_dir)` 以 argv 列表执行（不经 shell），`timeout=COUNTEREVIDENCE_RERUN_TIMEOUT_S`（30s），cwd 为调用方仓库目录。

| 结果 | 原因 | 回执 |
|---|---|---|
| 退出码 0 且 ≥1 行 | 成立 | 写入 `counterevidence.gate_rerun` |
| 退出码 1（零命中） | `counterevidence_rerun_no_match` | 不写，步骤非零 |
| 其它退出码 / 超时 / OSError | `counterevidence_rerun_failed` | 不写，步骤非零 |
| 命令不在白名单 | `counterevidence_command_not_allowlisted` | 不写，步骤非零 |

取仓失败同样 fail-closed：不得降级为采信提交方文本。

## 4. `gate_rerun` 字段

```python
{
    "argv": list[str],
    "exit_code": int,
    "match_count": int,
    "stdout_sha256": str,
    "excerpt": str,  # ≤2000 字符
}
```

提交方 `output` 保留，**不参与判定**。审计行优先展示 `gate_rerun.excerpt`。

## 5. convergence 消费

`_counterevidence_reason` 在原有形状校验（command/output/result/pointer、`result=refuted`）之外要求：

- `gate_rerun` 存在且为 object，否则 `counterevidence_not_rerun`
- `argv` 通过同一白名单且 sha 等于 `receipt.head_sha`，否则 `counterevidence_command_not_allowlisted`
- `exit_code == 0` 且 `match_count >= 1`，否则 `counterevidence_not_rerun`

不升 `SCHEMA_VERSION`（仍为 3）。

## 6. 威胁模型

| 防 | 不防 |
|---|---|
| 伪造 `output` / 未执行的 command 文本让 inferred P1 撤出阻断 | 贴了与 finding **语义无关**但仓库里真实存在的字面量 |

后者靠审计行把门禁摘录交给 arbiter / 人看，不做语义判定。

## 7. 兼容性

旧回执（无 `gate_rerun`）在 convergence 侧一律 `counterevidence_not_rerun`。已经靠旧反证撤下的 inferred P1 会在下一次聚合重新阻断。这是预期行为。

tracking-issue / deferred 路径不变。
