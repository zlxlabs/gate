# DESIGN-note：ledger 的 gate-terminal 寻址允许回落到本 run 的前序 attempt

关联 issue：zlxlabs/gate#101。

## 背景与根因

`gh run rerun --failed` 只重跑失败的 job。`ledger` 因瞬时网络失败时，产出
`gate-terminal-v1-…-<attempt>` 的 `gate` 聚合 job 已 pass、不参与重跑，attempt N
的 terminal 工件因此永不存在；而 `ledger` 的 `Resolve v2 ledger artifacts` 步骤对
terminal 要求 `attempt == current`（67ba1f4 引入），于是**任何单独重跑必然 14 秒
结构性失败**，只剩「全量重跑（重烧模型主审额度且结果非确定）」或「带红合并」两条路。

67ba1f4 把 terminal 收紧为严格同 attempt，理由是「回落到旧 attempt 会在
build_ledger 里以 `gate terminal identity mismatch` 崩溃」——那是把下游的严格等值
校验当成了不可动的前提，实际是症状修复：它用一个更清楚的报错换掉了一个更难懂的
崩溃，同时把结构性死锁焊死了。

同一份工作流里已有相反的、正确的先例：`gate` job 解析 `primary-audit-v2-…` 时就是
「取 ≤ current 的最大 attempt + 显式转发 `AUDIT_SOURCE_ATTEMPT`」，
`build_ledger._verify_primary_identity` 也按 `1 <= source_attempt <= current` 放行。
terminal 是全流程里唯一没跟上这个模式的工件。

## 目标

ledger job 因瞬时故障变红后，`gh run rerun --failed` 能把它修绿，不再要求全量重跑
或带红合并；重跑产出的账本条目如实标注 terminal 取自哪一次 attempt。

## 非目标

- 不改 terminal 工件的命名约定（attempt 后缀保留，工件名在同一 run 内必须唯一）。
- 不改 `gate` 聚合器的判决逻辑、不改 required status check 拓扑。
- 不做「ledger 失败时提示需全量重跑」这条替代路（见下）。
- 不动 gate-hub 私有仓的 `docs/review-effectiveness.md`（跨仓边界；字段新增在报告里
  提示，由持有该文档的仓自行跟进）。

## 方案要点与已否决方案

- **要点**：
  1. `Resolve v2 ledger artifacts` 的 terminal 解析改回「≤ current 取最大 attempt」，
     与 input/audit 同一路径；`terminal_source_attempt` 已经在输出里，继续保留。
  2. 加一道归因闸，防止静默取旧：用 Jobs API 查**当前 attempt**
     （`/actions/runs/{run_id}/attempts/{n}/jobs`）里 `gate` job 是否真的跑了。
     - `gate` 本 attempt **没跑** → 上一 attempt 的判决仍是本 run 的现行判决，回落合法。
     - `gate` 本 attempt **跑了但没有对应 terminal 工件** → 真异常（聚合器跑了却没写出
       终态），fail-loud，报错文案点名这一情形，不许回落。
  3. `build_ledger._disposition_receipt_consumption_from_terminal` 把 `run_attempt`
     一项从严格等值放宽为 `1 <= terminal.run_attempt <= current`，其余身份字段
     （repository / pr_number / run_id / head_sha）保持严格等值。
  4. 账本条目记录 terminal 来源 attempt（仅当 != 本次 run_attempt 时出现的可选字段），
     让复用旧终态在数据里可分辨，不与同 attempt 的条目混为一谈。
- **已否决**：
  - **只改报错文案，提示「需全量重跑」**（issue 里的备选）：诚实但不解决问题，代价仍是
    重烧主审额度或带红合并，正是 #101 要消掉的东西。
  - **terminal 工件去掉 attempt 后缀**：同 run 内工件名必须唯一，`--failed` 重跑不会
    删除前序 attempt 的工件，去后缀会冲突。
  - **terminal 缺失时降级为 `disposition_receipt_consumption: null`**：把结构性失败换成
    静默数据空洞，账本从此分不清「本来就没有」和「重跑丢了」。
  - **只放宽工件选择、不动 build_ledger 的等值校验**：会退回 67ba1f4 修掉的
    `identity mismatch` 崩溃；两处必须同批改。

## 关键不变式

1. 本 attempt 没有 terminal 工件、且 `gate` 本 attempt 未运行时，ledger 取 ≤ current
   的最大 attempt 的 terminal 并成功产账本。
   代码：`.github/workflows/gate-v2.yml` `ledger` job 的 `select_artifact`；
   锁死：`tests/test_gate_v2_contract.py`（改写 67ba1f4 加的两个用例）。
2. `gate` 本 attempt 运行了却没有对应 terminal 工件时，ledger 硬失败且报错文案点名该
   情形，绝不回落。
   代码：同上的归因闸；锁死：同一测试文件新增用例（构造 jobs 列表两种取值各跑一次）。
3. 永不选择 attempt > current 的工件。
   代码：同上；锁死：`test_ledger_resolver_selects_current_attempt_terminal_not_an_older_one`
   的对偶用例。
4. terminal 身份校验只放宽 `run_attempt`，repository / pr_number / run_id / head_sha
   任一不符仍抛 `gate terminal identity mismatch`。
   代码：`.github/actions/review-ledger/build_ledger.py:529`；
   锁死：`tests/test_review_ledger.py` 逐字段用例。
5. 账本条目在复用旧 attempt 终态时带出来源 attempt；同 attempt 时不新增字段（向后兼容）。
   代码：`build_ledger.build_entry`；锁死：`tests/test_review_ledger.py`。

## 验收路径

1. 入口：本仓 PR 的 `gate / ledger` job（自建 runner，真实 GitHub Actions），
   不是本地 pytest。
2. 步骤：
   a. 本地 `python3 -m pytest tests/test_gate_v2_contract.py tests/test_review_ledger.py` 全绿；
   b. 开 PR、标 ready、等 gate 全绿；
   c. 在该 PR 上人为让 ledger 失败一次（例如临时步骤 `exit 1`，或直接对已绿 run 用
      `gh run rerun --failed` 复现 #101 的原始形态），确认 attempt 2 的 ledger
      **成功**，日志里 `terminal_source_attempt=1`；
   d. 把 c 的临时改动撤掉再跑一轮，确认常规路径 `terminal_source_attempt` 等于当前 attempt。
3. 预期：c 步 ledger 由红转绿且账本条目标注来源 attempt=1；d 步行为与今天完全一致。
   证据写进验收记分卡：两次 run 的 URL + ledger 步骤日志里的 `terminal_source_attempt` 行。

## 交付形态

单张卡（M），改动三处文件（workflow / build_ledger.py / 两个测试文件），一个 PR。
按 core-lead「infra/状态机类 diff 例外」，收敛条件按 internal 档：连续 2 轮无新增 P1。
