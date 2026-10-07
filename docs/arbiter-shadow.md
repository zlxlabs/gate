# Arbiter 影子期（C2b）

`gate-v2.yml` 的 `arbiter` job 在生产里跑异族裁决（cross-family arbitration），
目的只有一个：**攒够真实裁决数据**，供 C2on 决定是否给裁决赋予约束力。它不参与
`gate / gate` 的裁定，也不改变任何 finding 的存续。

- 上位设计：gate-hub `docs/sessions/261006-review-precision/c2-arbiter-design.md`
  （方案 3/5 + 关键不变式）。
- 裁决入口：gate-hub `scripts/review/review-arbiter`（C2a）。
- 契约测试：`tests/test_gate_v2_arbiter_shadow.py`（影子 job 的构造不变式）、
  `tests/test_aggregate_prior_rounds.py`（轮数与阻断 finding 的投影）。

## 触发条件

`arbiter` job 只在同时满足以下条件时执行（job 级 `if:` 与 primary 的路由谓词逐字一致，
另加 primary 实际跑过）：

1. 事件是 PR、同仓 head、`runner: self`、作者是合法人类（非 Dependabot）；
2. `needs.primary.result != 'skipped'`；
3. 步骤级的追加条件（`Decide whether the arbiter runs`）：
   - 本轮主审之前**已有 ≥ 2 个可计轮次**（`prior_rounds ≥ 2`，即第 3 轮起），
     且历史**可列**（`state != history_unavailable`）；
   - 本轮 canonical primary audit 里存在 ≥ 1 条 P1 finding（`blocker` 或 `major`）。

`prior_rounds` 与 `gate` job 的轮数口径同源：`aggregate.py --prior-rounds` 复用
`convergence.pr_round_budget`，并复用带 45s 硬预算的历史加载器（gate#287→#290 的
同一道墙），**排除本 run_id**（rerun 与首次 attempt 共享 run id）。历史列不出来时
输出 `prior_rounds: null` 且 `state: history_unavailable`，此时裁决**不运行**——
列不出来的历史不能当作零轮。

## 不变式（构造上保证，由契约测试锁死）

1. **结论不变**：没有任何 job 的 `needs` 含 `arbiter`；其余 job 的定义与 base 提交
   逐字节一致（`tests/fixtures/gate-v2-base-jobs.json` 存每个 job 的内容摘要）；
   `arbiter` 自身带 job 级 `continue-on-error: true`。
2. **不可得时不解阻断**：裁决腿不可用、schema 失败、超时（`review-arbiter` 内部
   记 `unavailable_reason` 并把 decision 全部置 `undetermined`）或本轮未触发，
   都不会改变 gate 的判定——影子期根本没有消费方。
3. **历史不可得不当零**：`state=history_unavailable` 时裁决步骤的 `if:` 直接为假。

## 日志字段

每次运行（包括未触发、裁决失败）恰好一行，前缀 `ARBITER-SHADOW:`，同时写进
`$GITHUB_STEP_SUMMARY`：

```
ARBITER-SHADOW: prior_rounds=2 limit=5 state=collecting ran=true skip_reason=- \
arbiter=claude-glm-5-3 primary_family=openai arbiter_family=zhipu \
uphold=1 overturn=0 undetermined=2 unavailable_reason=-
```

| 字段 | 含义 |
| --- | --- |
| `prior_rounds` | 本 PR 之前（不含本 run）的可计主审轮数；历史不可得为 `-` |
| `limit` | 该 tier 的轮数上限（personal 5 / internal 8 / saas 12） |
| `state` | `collecting` / `arbitration_required` / `history_unavailable` |
| `ran` | 是否真的调用了 `review-arbiter` |
| `skip_reason` | 未调用的理由：`history_unavailable`、`prior_rounds_below_2`、`no_active_blocker`、`audit_missing` / `audit_invalid`、`source_unavailable`、`prior_rounds_unavailable`（未触发时为 `-`） |
| `arbiter` / `primary_family` / `arbiter_family` | 裁决腿与两边家族（未跑为 `-`） |
| `uphold` / `overturn` / `undetermined` | 各 decision 计数（未跑为 `-`） |
| `unavailable_reason` | `review-arbiter` 记录的类型化理由；产物没写出来时为 `arbiter_audit_missing` |

## Silo 对象名

与 canonical primary audit 同一个桶（Silo 的 `ci-artifacts`）、同一个 d14 tier：

```
arbiter-audit-v1-<repository_id>-<head_sha>-<run_id>-<run_attempt>
```

内容为 `arbiter-audit/v1`（gate-hub `scripts/review/schema/arbiter-audit.json`）。
未裁决或 `review-arbiter` 拒绝写产物（输入损坏 exit 2 / registry 非法 exit 3）时
不上传。

## 影子期数据收集（C2on 的入口）

C2on 需要**至少 30 条** `ran=true` 的裁决记录，另外要看每轮新出现 / 重复出现的
major 各占多少。取数与统计（`<caller>` 为被评审仓）：

```sh
# 1. 逐 run 抓日志行（本仓所有 PR 的 gate 运行）
gh run list --repo <caller> --workflow gate --limit 200 --json databaseId \
  --jq '.[].databaseId' | while read -r run; do
  gh run view "$run" --repo <caller> --log 2>/dev/null | grep -o 'ARBITER-SHADOW:.*' || true
done > arbiter-shadow.log

grep -c 'ran=true' arbiter-shadow.log                  # 影子期样本量（目标 >= 30）
sed -n 's/.*ran=false skip_reason=\([^ ]*\).*/\1/p' arbiter-shadow.log | sort | uniq -c   # 跳过原因分布
awk '{for (i = 1; i <= NF; i++) if ($i ~ /^(uphold|overturn|undetermined)=/) print $i}' arbiter-shadow.log |
  sort | uniq -c                                       # 裁决构成（C2on 达标判据的输入）
```

产物本体（逐条复盘证据门槛）按对象名前缀 `arbiter-audit-v1-<repo_id>-` 从 Silo d14
列出，或 `gh api repos/<caller>/actions/runs/<run_id>/jobs --jq '.jobs[] | select(.name | endswith("/ arbiter")) | .id'`。
统计口径与设计稿一致：`overturn` 只在裁决器引用了**本轮真实执行过、status=ok 且输出
非空**的 premise check 时才会出现，否则被机械改判为 `undetermined`。

## 已知边界（C2on 之前需要收敛的点）

- **工作树来源**：`review-arbiter` 的 cwd 必须是被裁决 head 的检出，并且要能算出
  `base..head` 的 diff scope（非 shallow 的祖先判定）。线上 primary job 的检出在合成
  merge commit 处是 shallow 的，`review-primary` 是在自己的 run 里从 host source
  service 现场补齐 base/head 的；`arbiter` job 里没有这个入口，因此 job 内用同一套
  原语做了同样的事（申请 `refs/demand/<sha>` + 在共享 consume.lock 下从只读镜像
  deepen）。宿主没有声明 `service` 时**不**回落到 origin 拉全量历史，而以
  `GATE-ARBITER-SOURCE-UNAVAILABLE reason=source_service_required` 跳过。
  这份逻辑更适合随 C2a 的入口一起提供（`review-arbiter` 自己补齐，或提到
  `scripts/gate_source.py`），届时 gate 侧这十来行可以删掉。
- **预算**：job 上限 15 分钟，裁决预算按「上限 − 5 分钟收尾预留」推导
  （`REVIEW_GATE_TIMEOUT_S=600`，单轮 `REVIEW_TIMEOUT_S=300`）。超时只会让该次
  裁决记为 `undetermined`/`unavailable`，不影响 gate。
- **Silo 凭据**：与 primary 一样取自 `SILO_ACCESS_KEY`/`SILO_SECRET_KEY`；未传入时
  影子 job 会失败（`continue-on-error` 保证结论不变），日志行给出
  `unavailable_reason=` 或 `GATE-ARBITER-*` 行。
