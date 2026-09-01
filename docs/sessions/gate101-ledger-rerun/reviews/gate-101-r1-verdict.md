# gate#101 ledger terminal 回落：r1 全量独立评审 verdict

## 总体 verdict

**pass**。固定审查范围为 origin/main..79b1fc14303283ee662f4fa9a83af6b7c0d1d431，未发现 personal 风险档 P1；P2/P3 项见 backlog，不阻塞本 PR。

本轮按 infra/状态机类改动执行 internal 收敛口径，并完成失败路径、资源账本寻址、跨进程 Jobs API 边界和误拒路径的全量复核。gate 当前 attempt 未运行时回落、当前 attempt 运行但没有 terminal 时硬失败、未来 attempt 不可选、terminal 非 attempt 身份字段仍严格匹配，以及来源 attempt 记录，均与设计文档中的 5 条关键不变式一致。

## 审查范围与验证

- H0：79b1fc14303283ee662f4fa9a83af6b7c0d1d431；基线：origin/main。
- H0 diff 实际为 7 个文件、757 insertions(+), 26 deletions(-)；包含 workflow、ledger consumer、两组契约测试及本会话的任务卡/设计/进度记录。未把审查期间的新提交纳入范围。
- uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q：750 passed in 12.41s。
- uv run --with pytest,PyYAML python -m pytest -q tests/test_gate_v2_contract.py tests/test_review_ledger.py：281 passed in 3.21s。
- python3 scripts/check_pinned_uses.py：OK: checked 8 live workflow/action metadata file(s); all internal uses are workspace-relative。
- git diff --check origin/main..79b1fc14303283ee662f4fa9a83af6b7c0d1d431：无输出、退出 0。
- 真实 API 证据：
  - gh api repos/zlxlabs/agent-config/actions/runs/33462777858/attempts/2 --jq '{run_attempt,run_started_at}' 返回 {"run_attempt":2,"run_started_at":"2026-09-01T02:57:51Z"}，elapsed=0.61s exit=0。
  - gh api repos/zlxlabs/agent-config/actions/runs/33462777858/attempts/2/jobs?per_page=100 --paginate --jq '[.jobs[] | select(.name == "gate / gate") | {status,conclusion,started_at,completed_at,run_attempt}]' 返回 [{"completed_at":"2026-09-01T02:50:11Z","conclusion":"success","run_attempt":2,"started_at":"2026-09-01T02:49:00Z","status":"completed"}]，elapsed=0.78s exit=0。这确认了真实 rerun 中复制的 aggregator job 名称仍为 gate / gate，但 started_at 保留前序时间，当前改动的回落路径能识别该形态。
  - 对 agent-config 最近 60 个 run 的 Jobs API 实测，筛出的 8 个 gate / gate 记录均有非空 started_at；同一批真实排队中的非 aggregator job 可见 started_at:null。因此 started_at:null 是真实 API 形态，但本项目 ledger 的 needs: quality, primary, gate 使当前观察窗口内的 aggregator 已非排队态。

## Findings

### F-1：Jobs API 子进程没有单调用超时

- 级别：P2，接受不修，不阻塞。
- 位置：.github/workflows/gate-v2.yml:1204-1214、:1221-1229。
- 违反的 spec/不变式：设计目标要求 gh run rerun --failed 的 ledger 能成功完成；ledger job 总预算在 :1102 为 5 分钟。设计文档没有单独规定秒数，因此按评审纪律降为 P2，而不是直接判 P1。
- 问题：subprocess.run(["gh", "api", ...], check=False, capture_output=True, text=True) 的 Jobs API 和 attempt metadata 两次调用均没有 timeout=。网络或 GitHub CLI 卡住时，只能等 job 级 5 分钟取消，回落功能无法在预算内完成。
- P1 两问：
  1. 真实使用中会触发吗？本轮未触发。对真实 agent-config attempt 2 的两个端点分别实测，均在 0.61s/0.78s 返回成功；不能从代码形态推断已触发挂起。
  2. 触发后果能否接受？对可用性目标不可接受，但对 personal P1 红线仍可接受为 P2：结果是 ledger job 超时失败、不会静默写入错误账本，也没有数据丢失或进程崩溃。
- 证据指针：上述两条真实 gh api 命令及输出；代码行 .github/workflows/gate-v2.yml:1204-1214、:1221-1229；ledger timeout-minutes: 5 在 :1102。
- 建议方向：给两次外部调用设置小于 job 预算的明确上限，并让超时保持 fail-loud；不要用静默回落掩盖查询超时。

### F-2：匹配到的 aggregator job started_at 为空时，未区分“尚未运行”

- 级别：P2，接受不修，不阻塞。
- 位置：.github/workflows/gate-v2.yml:1263-1270。
- 违反的 spec/不变式：设计文档关键不变式 1 明确“gate 本 attempt 未运行时回落到不大于 current 的最大 terminal”；Jobs API 对排队/尚未启动 job 的 started_at 可以是 null。当前代码对匹配的 aggregator 直接 SystemExit，因此没有执行证据时也不走“未运行”分支。
- 问题：当 Jobs API 返回匹配的 gate job 但 started_at:null 时，代码报 aggregator job started_at is missing or unparseable，ledger 失败，而不是按未运行回落。它是 fail-closed 的误拒，不会消费错误 terminal。
- P1 两问：
  1. 真实使用中会触发吗？本轮没有在 ledger 消费窗口触发。真实扫描最近 60 个 run 的 8 个 gate / gate 记录全部已填 started_at；真实 API 确实在同批非 aggregator 排队 job 上返回过 null，所以形态存在，但 needs: gate 使该窗口的 aggregator 不是排队态。
  2. 触发后果能否接受？对 rerun 可用性不理想，但按 personal P1 红线可接受为 P2：它会拒绝运行并报警，不会静默回落到错误终态、丢数据或绕过 required check。
- 证据指针：.github/workflows/gate-v2.yml:1263-1270；真实 Jobs API 输出中的 gate / gate 记录；同一真实 Jobs API 扫描中排队非 aggregator job 的 started_at:null；tests/test_gate_v2_contract.py:1003-1018 已锁死 run 元数据缺失时的 fail-loud，但没有锁死匹配 aggregator 的 started_at:null。
- 建议方向：用 job 的状态/结论及执行证据区分“未开始/跳过”和“已运行无 terminal”；保留对已运行但无 terminal 的硬失败，不以空 started_at 直接把未运行判成异常。

### F-3：新增 Jobs API 调用未显式固定 --hostname

- 级别：P3，接受不修，不阻塞。
- 位置：.github/workflows/gate-v2.yml:1207-1209、:1223-1224。
- 违反的 spec/不变式：本 PR 的设计文档没有 enterprise host 兼容性条款；当前项目的真实部署形态是 GitHub.com。因此这是部署可移植性建议，不是本仓当前资源账本身份不变式的阻塞违反。
- 问题：gh api 依赖 CLI 默认 host。若将同一 workflow 部署到 GitHub Enterprise 或 runner 全局 host 配置异常，repo/run 路径可能被解析到非预期 host。
- P1 两问：
  1. 真实使用中会触发吗？当前不会。真实调用已在默认 GitHub.com host 成功返回目标 run 的 attempt 与 jobs 数据，项目仓库和 workflow 均在 GitHub.com。
  2. 触发后果能否接受？在当前部署不适用；假设切换 host 后属于配置/移植失败，应 fail-loud 而不是把错误 host 的数据当真。该假设不满足本仓 personal 实际使用方式，故降为 P3。
- 证据指针：真实 gh api 命令及输出；workflow 顶层权限 .github/workflows/gate-v2.yml:98-101；代码行 :1207-1209、:1223-1224。
- 建议方向：若未来支持 GitHub Enterprise，用 workflow 的 server URL 显式传给 CLI；当前不建议为本 PR 扩大配置面。

## OCR high/medium 对照表

| OCR 工具标注 | 本仓判定 | P1 两问答案 |
|---|---|---|
| High：:1204 的 subprocess.run 没有 timeout=，CLI 卡住会耗尽 5 分钟 job | F-1，P2。问题真实存在，但本轮真实端点调用未挂起；触发时是可见失败，不是静默错误/数据丢失。 | ①真实本轮未触发；②对可用性不可接受，但 personal P1 红线可接受为 P2。 |
| High：:1262 用 job_started >= attempt_started，可能因时间偏差误判；Jobs API 排队 job 可能 started_at:null | 拆分处理：前半原指控不成立，后半为 F-2，P2。代码在 >= 时设置 aggregator_ran=True 并硬失败；它不会把“started_at 晚于 attempt 开始”静默判为搬运。只有 API/时钟把真实当前 job 的 started_at 错置到更早时才存在残余时间启发式风险，本轮未观测，不另立 P1。null 则确实直接硬失败。 | ①前半真实 target rerun 显示 copied job 的旧时间早于新 attempt，正常回落；匹配 aggregator 的 null 本轮未出现但 API 形态存在；②发生 null 时是 fail-closed 误拒，可接受为 P2。 |
| Medium：:1207 的 gh api 未固定 --hostname | F-3，P3。当前真实部署为 GitHub.com，默认 host 与实际一致，无当前触发证据。 | ①当前不会触发；②当前不适用，未来 enterprise 场景应显式失败/配置化，非本仓 P1。 |
| High：:947 的 test_ledger_resolver_skips_jobs_listing_when_current_terminal_exists 只断言一条，传入 poison 文件可能恒真 | 不成立。测试先用缺失 jobs 文件、再用非法 JSON jobs/attempt 文件执行；若把 if terminal_artifact[0] != current 改成 if True，会在读 poison 文件处失败。它锁死了当前 terminal 存在时不消费 Jobs API 的行为。 | ①真实测试运行中该路径已执行，且固定改坏实测会转红；②若无该守卫会无谓 API/解析失败，但当前断言有约束力，不产生 finding。 |

## 降层三问

1. 终态写入成功之前的不可逆动作：本次新增的 resolver 在终态落盘前只做本地临时文件创建/删除和 GitHub API GET（artifact listing、Jobs API、attempt metadata），没有删除业务数据、发送通知或调用外部写接口。后续 artifact download 也是读操作；build_ledger.py 先写本地 ledger.jsonl，PR 状态评论等外部写操作位于本地账本写入之后。已存在的 gate terminal artifact 在本 resolver 选取前已经由 gate job 产出，本改动没有增加新的不可逆动作。
2. 守卫值在实际部署形态下是否唯一可信：run_id 在一次 workflow run 内唯一，artifact 名称还绑定 repository id、head SHA 和 run id；run_attempt 是本 run 的单调 attempt 编号。run_started_at 不是唯一身份值，只是 Jobs API 的时间证据，当前真实 rerun 证实 copied gate / gate 的旧 started_at 早于 attempt 2 的 run_started_at。因此身份值可信，时间值仅适合作为当前方案规定的归因信号，存在秒级/时钟边界但本轮未观测。
3. 保护覆盖“写入”还是“行为”：覆盖的是行为。resolver 决定选哪个 terminal artifact、何时允许回落、何时硬失败，并输出 terminal_source_attempt；consumer 又对 envelope 身份做校验。它不只是保护最终 ledger.jsonl 的写入，且没有把缺失 terminal 降级为空数据。

## 熵增审查

逐项检查 H0 新增的文件、状态、抽象和包装层：

| 新增项 | 是否熵 +1 | 判断依据 |
|---|---|---|
| REPOSITORY、RUN_ID workflow env | 否 | 是两次真实 API 查询所需的运行身份；attribution_repo 被两个 loader 复用，不是无消费者配置。 |
| jobs_listing_path、attempt_listing_path 可选 argv | 否 | 是跨进程边界的可注入 fixture 入口，生产路径与离线契约测试各有消费者；缺省才进入真实 gh api。 |
| subprocess.run Jobs API 外部调用 | 否 | 是设计要求的当前 attempt 归因事实源，不是包装层或重试机制；失败上抛。 |
| is_gate_aggregator_job、jobs_pages、attribution_repo、两个 loader、时间解析函数 | 否 | 分别隔离 job 名匹配、--slurp shape 校验、身份读取和时区解析；attribution_repo/时间解析有多个调用方，其余单消费者也有明确的跨边界校验理由。 |
| attempt_started、aggregator_ran | 否 | 两个短生命周期局部状态直接表达归因闸，不镜像到持久账本，也未新增配置项。 |
| 顶层可选 terminal_source_attempt | 否 | 设计不变式 5 明确要求复用旧终态时可分辨；同 attempt 不写冗余字段，且不改变消费投影 shape。 |
| 三个 session 文档：任务卡、design、progress | 否（非运行时） | design 是失败路径/资源账本 diff 必需的 spec；任务卡与进度是本批次的过程证据，没有运行时消费者或状态分叉。 |
| 新增测试 helper、fixture 和参数化用例 | 否 | 直接锁死 H0 的跨进程、真实 producer envelope、未来 attempt、identity 字段和归因矩阵，不增加产品运行面。 |

本轮未发现新增 fallback、重试、防御式吞错、重复身份来源或无第二消费者的配置层。

## Backlog（不阻塞本轮）

- F-1 / P2：为两个 gh api 子进程增加单调用时间预算。当前真实调用快且 job 级 5 分钟仍会最终失败，因而不是静默错误；留作可靠性改进。
- F-2 / P2：增加匹配 aggregator started_at:null 的真实状态判定测试，并区分未开始/跳过与已运行无 terminal。当前路径 fail-closed，避免了错误回落，暂不阻塞。
- F-3 / P3：只有在支持 GitHub Enterprise 或多 host runner 时才需要显式 --hostname；本仓真实部署固定 GitHub.com，暂不扩大配置面。
- OCR low 线索（:1137 argv 解包、:1193 环境变量读取、:1243 错误常量及一条无行号线索）：逐项复核后均未发现额外 spec 违反。生产 workflow 总是传入 7 个基础 argv，缺少 REPOSITORY/RUN_ID 会 fail-loud，错误常量用于明确的缺失/不可解析分支；不升格为 finding。
- actionlint 实测报告 .github/workflows/gate-v2.yml:243 与 :911 的 SC2129 风格提示，但它们不是本次 H0 新增的行为缺陷；按“只审本次 diff”记为存量 backlog，不阻塞本 PR。

## 结论

H0 已满足设计文档的回落目标和终态身份不变式，新增测试在 base 上的红验事实与本轮 750 passed 全量结果相互一致。没有需要阻塞合并的 P1；按本仓 personal 档及本次 infra 提档口径，verdict 为 **pass**。
