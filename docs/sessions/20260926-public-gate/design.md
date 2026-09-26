# DESIGN-note: quality 无 Silo 凭据，改由可信作业保存产物

Refs #248 #250。基线 `c701e367a1680cde85ffdea1f92142d62e4fb885`（远端 main/@v2）。

## 目标

公开 PR 的 quality 作业运行时不获得私有存储权限，同时保留 ledger-input 的消费与 Silo 持久化。用 GitHub job outputs 把真实 producer 字节交给已有 `ledger` 作业；不新建服务、不烤 key、不扩大 org secret。

## 非目标

不改 primary 工具、caller 政策、runner 配置；不跨仓写 gate-hub；不把 GitHub artifact actions 重新引进 `gate-v2.yml`；不平行造身份机制。runner/缓存隔离交卡 B。

## 分区删除约定

quality 删除 job 级 `AWS_*`/`SILO_*`、Silo checkout、MagicDNS、Silo put/retry、silo 网络诊断。producer 只把 `pr-size-preflight.json` 与 `install-result.json` 打成 job output `ledger_input_bundle`。ledger 用 GitHub 事实合成身份后落盘并 `silo_store put --tier d1`（原 quality 上传+重试原样搬来，仍 advisory）。

## 通道选择

走 **job outputs**，不启用 `actions/upload-artifact`。原禁令（`test_gate_v2_has_no_github_artifact_actions` 与 260916-silo-migration）禁止双通道 fallback。当前真实 producer fixture 约 1KB；`excluded_files` 无条数上限，consumer 单 env `LEDGER_INPUT_BUNDLE` 受 Linux `MAX_ARG_STRLEN`（先于 Actions 1MB UTF-16）约束。只覆盖小规模 advisory persist，空 bundle 消费 fail-loud。不宣称任意 PR 容量，不新增大 artifact 通道。

## 否决

- 只把 secret 从 job env 挪到 step env（同 job 无隔离，咨询 20260926-150616）。
- 新建 broker / 把 key 烤进镜像 / 扩大 org secret 可见范围。
- 把 PR 自报 repo/head 当身份；身份只用 `github.repository_id` / `head.sha` / `run_id` / `run_attempt`。
- 双通道（job outputs + artifact，或 Silo listing fallback）。

## 咨询结论（#248 三份，本机 `retro/consult/`，未混入他人在途文件）

1. `20260926-145341-codex.md`：quality 唯一 Silo 用途是上传两份 ledger 输入；改经 GitHub 原生交给不跑 PR 代码的 gate/ledger。
2. `20260926-150044-claude-fable.md`：fork 缺 key 不能直接当 P1；ledger-input 上传本就是 advisory。
3. `20260926-150616-claude-fable.md`：GitHub 边界在 job 不在 step；预置 `$RUNNER_TEMP/gate_bounded_retry.py` 只能打同 job。改动后桩若仍跑，AWS 计数必须为 0；ledger 按 `job.workflow_sha` 独立取 helper。

## 不变式（代码位置 / 测试）

1. quality 无私有存储 secret、无 Silo 调用：`.github/workflows/gate-v2.yml` quality job；`test_quality_job_has_no_private_storage_secrets_or_silo_calls`。
2. 必需 ledger-input 经 job outputs 到达 ledger：quality `ledger_input_bundle`、ledger Download 步；`test_quality_publish_and_ledger_consume_roundtrip_fixture_bytes` 用真实 `preflight.py` 与 workflow `printf` 产物，字节经发布/消费不变。
3. 身份来自 GitHub 事实，Silo listing / payload 内 spoof 字段不参与命名：resolver `github_fact_input_artifact`、persist `ARTIFACT_NAME`；`test_review_ledger_input_uploads_declare_one_day_retention` 锁 identity，`test_ledger_resolver_ignores_silo_listing_and_spoofed_prefix_for_input` 锁 listing。
4. 存储失败保持 advisory：ledger persist `continue-on-error` + 一次 retry + `::error::`；quality 必需检查不依赖 Silo。
5. 预置 helper 不能指挥 ledger：ledger checkout `job.workflow_sha`（`test_silo_store_env_aligns_with_job_checkout_path`）；publish 步不跑 `gate_bounded_retry.py`（`test_quality_job_has_no_private_storage_secrets_or_silo_calls`）。
6. 空 bundle 失败可见：`test_empty_bundle_download_fails_loud`。

## 待证

- 真实消费仓同仓 PR 的 marker 探针（AWS 计数 0）与一次性 runner/共享缓存：卡 B。
- 无 secret fork 的真实有效测试、draft→ready primary SUCCESS vs SKIPPED：本草稿 PR 不标 ready。
- 私有仓完整门禁回归：主脑合入后观察。

## 验收

定点 `tests/test_gate_v2_contract.py` 等与全量 pytest；`python3 scripts/check_pinned_uses.py`。draft 绿不算 primary。
