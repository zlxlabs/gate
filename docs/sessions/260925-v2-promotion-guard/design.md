# v2 抬升守护：信号与送达路径

记录日期：2026-09-25

## 信号定义

`v2-tag-sync.yml` 的 `Report v2 tag sync state` 步骤在 sync job 内执行一次，向 Actions 日志打印一行 `V2-TAG-SYNC-STATE: <state>`，并把同一行追加到该 run 的 Step Summary。状态值固定为：

| 状态 | 含义 |
| --- | --- |
| `promoted` | 推送步骤成功，且通过 `verify_remote_tag` 回读确认远端 `v2` 与目标 SHA 一致。 |
| `already_current` | 已选出候选，候选就是当前 `v2`，没有移动标签。 |
| `held` | guard 阻止推进，包括熔断标记或 hold 文件。 |
| `disabled` | `V2_TAG_SYNC_ENABLED` 未启用。 |
| `no_eligible_candidate` | 候选查询成功，但没有符合条件的候选。 |
| `query_failed` | guard、候选、单调性或远端验证阶段失败，无法确认抬升状态。 |

这行描述标签抬升结果，不替代整个 workflow run 的结论；例如后续滞后观测失败时，日志仍可显示已回读确认的 `promoted`。当前 workflow 没有向外部告警服务发送该状态。owner 与主脑通过 GitHub Actions run 的日志和 Step Summary 消费它。

滞后探针 `scripts/v2_release_state.py` 固定比较 `refs/tags/v2` 与 `refs/heads/main` 上 `.github/workflows/`、`.github/actions/`、`scripts/` 三个调用方消费路径的树对象。探针信号为：退出码 `0` 表示未超阈（包含路径内容一致但 SHA 落后的情形），退出码 `1` 表示消费路径内容不一致且超阈，退出码 `2` 表示查询失败、无法定论。内容滞后用 `V2-RELEASE-STATE-CONTENT-LAG`，查询失败继续用 `V2-RELEASE-STATE-QUERY-FAILED`；同 SHA 的正常情形保持原有静默输出。

## 巡检与落点

现有巡检链路（gate-hub 仓只读核验，未在本卡修改）：

1. 用户级 `patrol-v2-release-state.timer` 在 UTC 奇数小时的 `:37` 触发，每两小时一次；对应 service 是 `patrol-v2-release-state.service`。
2. service 调用 gate-hub 的 `patrol/scan_v2_release_state.py`。它在 `/home/zlx/.cache/patrol/gate-probe` 用 `git clone --filter=blob:none` 建立私有缓存（已有缓存则 fetch），同步到 `origin/main` 后运行 gate 仓探针。
3. 探针 stdout/stderr 与退出码由 scan 壳的子进程写入 service journal；探针退出码 `0/1/2` 原样由壳转发。非零退出导致 service 失败，并触发 unit 中的 `OnFailure=patrol-onfailure@%N.service`。
4. `patrol-onfailure@.service` 调用 `patrol/onfailure_emit.py`，它向 finding-v1 sink 追加一条 `patrol_onfailure` 记录。代码默认路径为 `/home/zlx/.local/state/gate-hub/findings.jsonl`，也可由 `GATE_HUB_FINDINGS_PATH` 覆盖；`patrol/findings_digest.py` 会读取该 sink。

因此，能确认的送达终点是本机 findings JSONL sink，且有日报脚本读取该文件；**finding 从 sink 进一步送到 owner/人的实际通知路径和送达结果未证实**。不要把 journal 或 sink 中存在记录写成已通知到人。Gate 仓没有为本 workflow 配置通知 secret，本卡不新增外部通道。

## 已观测缺口

- 2026-09-24 journal 有两次 `V2-RELEASE-STATE-QUERY-FAILED`，service 以退出码 `2` 失败；另一次 gate-hub scan 壳自身 fetch 失败，抛出 `RuntimeError: V2-RELEASE-STATE: fetch step failed` 并以退出码 `1` 失败。由于 systemd 只看到 service 非零，壳层 fetch 失败与探针“内容滞后超阈”的退出码 `1` 同形，需 gate-hub 另开卡修复。
- 本机 `patrol-v2-release-state.timer` 已启用；2026-09-25 最近 30 条 `v2-tag-sync.yml` run 均为 success，执行时 `refs/tags/v2` 为 `de182d73e3942b270e6a4812dfdcea64eed27823`。run 颜色本身仍不能说明是否发生了标签推进，故以新增 Step Summary 状态行为准。
- finding sink 到人的通知是否触发、由谁接收、是否实际送达，需在 gate-hub 侧追踪 findings digest 与通知配置；本卡不跨仓修改。
