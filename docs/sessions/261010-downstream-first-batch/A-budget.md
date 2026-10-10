# A-budget：gate #289 汇总 job 墙钟预算

取证时间：2026-10-10。只读下游；未重跑、未改消费仓。
API 只读请求：14 / 15。本文件只缓存时间 / conclusion / annotation 白名单。

## 锁定决策

来源：gate-hub lead 会话 `docs/sessions/261010-downstream-first-batch/design.md`（只读）。

- 候选预算 **15 分钟**，只提高 `gate` job 已有 `timeout-minutes` 声明。
- 不拆面板 job、不加 retry/fallback/continue-on-error、不改聚合语义。
- 与 PR #292 无产物依赖：本卡独立分支先交付。
- 已否决：以扩大超时宣称根治所有 API 慢；未确认取消即是超时就改预算。

## 主干确认

- `origin/main` / 本卡 base：`6b70e369955891b6f58428e377cd30f139aacde2`
- `.github/workflows/gate-v2.yml` `jobs.gate.timeout-minutes` **仍为 8**
- `tests/test_gate_v2_contract.py::test_gate_job_timeout_matches_aggregate_publish_budget` 在改断言前锁定 8

缺陷仍在主干，不是已修。

## 历史超时证据（确定性超时信号）

三份 `gate / gate` job 的 check-run annotation 均为：

> The job has exceeded the maximum execution time of 8m0s

不是新 push / concurrency / 人工取消。`gate` job 声明 `cancel-in-progress: false`。

### run 37500314726 attempt 2（PR #4179）

| 字段 | 值 |
|---|---|
| job id | 112408977035 |
| name | gate / gate |
| started_at | 2026-10-06T17:32:29Z |
| completed_at | 2026-10-06T17:41:24Z |
| wallclock | 8m55s |
| conclusion | cancelled |
| Aggregate required verdict | 17:32:35Z–17:36:09Z = 3m34s，success |
| Publish gate status panel | 17:36:33Z–17:41:12Z = 4m39s，success |
| Complete job | success |
| 裁决 annotation | `gate_result=skipped` / `reason_code=review_not_expected`（裁决已完成） |

查询：`gh api repos/zlxlabs/agent-config/actions/runs/37500314726/attempts/2/jobs` 与 `.../check-runs/112408977035/annotations`。

### run 37494214791 attempt 2（PR #4162）——工单表中的 11m04

默认 `.../runs/37494214791/jobs` 返回 **attempt 3**（success，6m15s），不是工单那次取消。attempt 3 成功 **不等于** 已验证修复：当时仍是 `timeout-minutes: 8`，只是那次 API 没慢到超限。

| 字段 | 值 |
|---|---|
| job id | 112400743934 |
| run_attempt | 2 |
| started_at | 2026-10-06T17:13:47Z |
| completed_at | 2026-10-06T17:24:51Z |
| wallclock | 11m04s |
| conclusion | cancelled |
| Aggregate required verdict | 17:13:54Z–17:18:43Z = 4m49s，success |
| Publish gate status panel | 17:19:20Z–17:24:32Z = 5m12s，success |
| Complete job | success |
| 裁决 annotation | `gate_result=pass` / `reason_code=primary_pass`（裁决已完成） |

查询：`gh api repos/zlxlabs/agent-config/actions/jobs/112400743934` 与 `.../check-runs/112400743934/annotations`。

### run 37494214791 attempt 1（同 run 更早一次）

| 字段 | 值 |
|---|---|
| job id | 112378221992 |
| run_attempt | 1 |
| started_at | 2026-10-06T16:23:32Z |
| completed_at | 2026-10-06T16:32:42Z |
| wallclock | 9m10s |
| conclusion | cancelled |
| Aggregate required verdict | 16:23:48Z–16:27:50Z = 4m02s，success |
| Publish gate status panel | 16:27:50Z–16:32:25Z = 4m35s，success |
| annotation | 同样 `exceeded the maximum execution time of 8m0s`；`gate_result=pass` |

### 证据状态

- 查询失败：无（14 次只读均返回）。
- 证据过期：无。旧 run 仍存在。
- 工单命令对 37494214791 未指定 attempt，默认落到 attempt 3 success——已另查 attempt 1/2，不把「最新 attempt 绿」写成已修。

## 新旧预算

| | 旧 | 新 |
|---|---|---|
| `jobs.gate.timeout-minutes` | 8 | 15 |
| 依据 | 过时注释「aggregation is seconds, publish <=2 minutes」 | 已观察最大墙钟 11m04，15 留约 4 分钟余量 |

15 不与现有 input 上限冲突：`timeout_minutes`（quality，默认 45）和 `primary_timeout_minutes`（默认 25）是别的 job；`gate` job 的超时是字面量，不是从 input 推导。未新增旋钮。

## TDD

1. 只改断言 `== 8` → `== 15`，base YAML 仍为 8。
2. 红：`assert 8 == 15`（AssertionError），见下方原文。
3. 再改 YAML 注释与 `timeout-minutes: 15`。
4. 同测试绿。

红验原文（改 YAML 前）：

```
tests/test_gate_v2_contract.py:2959: in test_gate_job_timeout_matches_aggregate_publish_budget
    assert raw["jobs"]["gate"]["timeout-minutes"] == 15
E   assert 8 == 15
```

## 非预算字段结构差集

命令（不入库新运行时框架，一次性本地 python）：

```sh
python3 - <<'PY'
from pathlib import Path
import subprocess, yaml, copy, difflib
base_sha = "6b70e369955891b6f58428e377cd30f139aacde2"
path = ".github/workflows/gate-v2.yml"
base_text = subprocess.check_output(["git", "show", f"{base_sha}:{path}"], text=True)
head_text = Path(path).read_text(encoding="utf-8")
base = yaml.safe_load(base_text)
head = yaml.safe_load(head_text)
base_top = {k: v for k, v in base.items() if k != "jobs"}
head_top = {k: v for k, v in head.items() if k != "jobs"}
print("top_level_non_jobs_equal", base_top == head_top)
print("job_ids_equal", list(base["jobs"]) == list(head["jobs"]))
print("other_jobs_struct_diff", [n for n in base["jobs"] if n != "gate" and base["jobs"][n] != head["jobs"].get(n)])
bg, hg = copy.deepcopy(base["jobs"]["gate"]), copy.deepcopy(head["jobs"]["gate"])
bg.pop("timeout-minutes", None); hg.pop("timeout-minutes", None)
print("gate_other_fields_equal", bg == hg)
print("base_timeout", base["jobs"]["gate"]["timeout-minutes"], "head_timeout", head["jobs"]["gate"]["timeout-minutes"])
udiff = list(difflib.unified_diff(base_text.splitlines(keepends=True), head_text.splitlines(keepends=True), fromfile=f"a/{path}", tofile=f"b/{path}"))
print("".join(udiff))
changed = []
for line in udiff:
    if line[:1] in "+-" and not line.startswith(("+++", "---")):
        body = line[1:].strip()
        if not (body.startswith("#") or body.startswith("timeout-minutes:")):
            changed.append(line.rstrip("\n"))
print("changed_non_comment_non_timeout_lines", changed or "EMPTY")
PY
```

结果：

```
top_level_non_jobs_equal True
job_ids_equal True
other_jobs_struct_diff []
gate_other_fields_equal True
base_timeout 8 head_timeout 15
changed_non_comment_non_timeout_lines EMPTY
```

YAML `safe_load` 解析通过。unified diff 只有 gate job 注释与 `timeout-minutes` 一处 hunk。

## 真实规模 / canary 入口

先查本仓现有入口授权与输入：

- `.github/workflows/gate-v2.yml`：仅 `on.workflow_call`，**无** `workflow_dispatch`。inputs 无候选 SHA；调用方 pin `@v2` 或 SHA。
- `.github/workflows/ci.yml`：PR/push main 跑 pytest + pinned uses，不调用 gate 汇总 job，测不到 8m55/11m04 墙钟。
- `.github/workflows/v2-tag-sync.yml`：只在 `refs/heads/main` 上把 v2 抬到 canary 已绿的 main SHA。本卡禁止手移 v2、禁止生产部署。
- 本卡不改消费仓、不重跑下游。没有受控慢 API 服务可在不越界的前提下注入 11m04 量级。

因此：**本卡不能在候选 ref 上复现慢 API 墙钟并看到真实 job 终态。** 静态契约把 15 锁死，历史 annotation 证明 8 会在裁决已完成后把 job 染成 cancelled。端到端「15 分钟下慢 API 得到 success/failure 而非 timeout-cancelled」留给合并后自动 canary 与自然下游观察。不得把改 8→15 的断言绿写成已根治墙钟。

本地 pytest durations 求和约 6.55s 只是既有基线，不外推真实 wallclock。

## 失败可见性

未新增失败分支、未加 continue-on-error。聚合失败语义保持：真实失败仍应得到非成功结论；本次只放宽墙钟，让已完成的裁决能落到 job 终态。
