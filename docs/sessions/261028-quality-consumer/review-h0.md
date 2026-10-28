# gate v2 quality consumer 来源供给：H0 独立审查

<!-- delegate-outcome: succeeded -->
## 结论

- review-verdict: **PASS**；阻断交付：**否**。本次冻结 diff 未发现需登记的 P1/P2/P3 finding。
- failure-visibility 字段仅反映本轮独立审查；OCR 初筛另为 skipped，不冒充扫过。

failure-visibility: clean

- Dispatch-Id：`dlg-20261028-181954-ffd244`；风险等级：personal。
- 冻结范围：`03430d443bed7cf2218fe236a5af689579a8850a..597f2c81d81f262b98d9963680cc38c92adb49a7`。
- 规格：派发卡 `gate-261028-1917-quality-consumer-source.md`；完整本地路径记录在指定的私有派发报告。
- 审查根目录：本派发独立 checkout 的仓库根目录，开工 HEAD 精确为上述冻结 H0。
- 已先读 review-discipline；未读取实现报告、实现推理或实现会话全文；未委派子代理。
- 这是来源供给审查，不是 gate-hub 消费闭环、生产运行或 v2 发布证明；本卡不合并、不改标签。

## 全量 diff 与关键不变式

完整阅读四个变更文件，统计为 328 行新增、5 行删除；未以测试绿替代源码审查。
下表文件路径均相对上述审查根目录，行号固定于冻结 H0。

| 不变式 | 实现位置 | 锁定测试／独立证据 |
| --- | --- | --- |
| 来源绑定实际 reusable workflow 的 repository/SHA | `.github/workflows/gate-v2.yml:741-756` | `test_quality_provisions_the_consumer_before_the_quality_entry`；独立 main/caller SHA/registry_commit/repository 变异全部红 |
| checkout 在质量入口前，且两者仅 entry 模式运行 | 同文件 `741-769` | 解析实际 steps 顺序；后移 checkout、改 legacy 条件均红 |
| 目的目录不复用 caller 根目录 | `GATE_CHECKOUT_PATH=_gate-ledger-consumer-src` | caller 根路径变异红；真实 producer 清除预放 `caller-planted.txt` |
| sparse 恰为四个真实 consumer 文件 | 同文件 `747-751` | 实际磁盘集合精确相等，逐文件字节等于 H0 blob；缺文件声明、实际缺失分别红 |
| 绝对路径进入同一质量步骤子进程 | 同文件 `769` | 相对路径变异红；实际执行 entry，子进程写出的路径字节等于绝对 checkout 路径 |
| 复用既有 bootstrap/bounded checkout，无新取码机制 | 同文件 `755-756` | `test_every_workflow_bootstrap_shares_the_one_script`、`test_workflow_sha_checkouts_use_centralized_bounded_retry`；计数各由 9 更新为 10 |
| caller 预放／上轮残留先清理，消费前不误删新目录 | 同文件 `596-612`、`717-756` | stale cleanup 新增该目录；pre-caller cleanup 不含新目录；service/origin 重建目的目录实测 |
| 错 SHA、缺文件在入口前 fail-loud | `scripts/gate_source.py:284-377`、`scripts/gate_bounded_retry.py:167-232`（既有实现） | 原始步骤实跑：错误 demand SHA 报 `SOURCE-PIN-MISMATCH`；两模式缺文件均非零，后续 marker 不产生 |
| gate_run_and_record、退出码、JUnit/artifact 与其他 job 不变 | 工作流既有 helper、entry、后续步骤 | 独立解析 base/H0：仅撤去新增 checkout/env 并还原 stale cleanup 后，全 workflow 对象相等；entry 实跑退出 0/42、artifact 字节守恒 |

新增的 `tests/test_gate_v2_ledger_contract_source.py` 已逐行审查；其测试辅助函数未引入生产抽象。
`tests/test_gate_v2_contract.py` 只更新 checkout 计数和清理／入口相邻关系；
`tests/test_gate_source.py` 只更新共享 bootstrap 调用计数。没有新增 job、fallback 或重试实现。
环境变量的第二消费者是后续 gate-hub 质量入口；本轮不要求该消费者已经启用。

## 独立执行与反向验证

以下命令均绑定审查根目录，Python 固定 3.12：

```sh
uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_v2_ledger_contract_source.py
python3 scripts/check_pinned_uses.py
uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_source.py tests/test_gate_bounded_retry.py tests/test_gate_v2_contract.py tests/test_gate_v2_caller_checks_signal.py
```

- 窄测：**13 passed in 1.87s**；pin 检查：**OK，10 个实际 workflow/action metadata 文件**。
- 相关扩展测试：**351 passed in 127.34s**，包含来源服务、凭据、pin、质量终止信号和结果契约。
- 裸环境：`env -i HOME="$HOME" PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"` 后重跑同一窄测，**13 passed in 1.39s**。
- 本会话自身 systemd 单元 `delegate-dlg-20261028-181954-ffd244.service` 已核为 active/running；上述独立验证在该实际派发环境内执行。
- 独立探针命令：`uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python "$(dirname "$DELEGATE_REPORT_PATH")/review-probe.py"`；完整路径与产物在私有派发报告中保留。
- 探针未使用实现测试的 `MUTATIONS` 映射；自行改写序列化 workflow 输入，再调用真实顶层契约测试。
- 九项注入先确认已生效，随后全部红：main、caller SHA、registry_commit、caller repository、caller 根路径、漏 scrub 文件、相对 env、checkout 后移、legacy 条件。
- service/origin 都执行实际 workflow `run:`，以真实 H0 而非 snapshot SHA 取码；origin 网络边界改接本地 Git upstream，bootstrap 下载由 H0 原始 helper 字节响应。
- 两模式均核验实际 checkout HEAD、磁盘四文件集合、每个文件字节、预放文件清除、子进程 env、退出 0/42、artifact 字节；实际 Git argv 留存。
- 错误 demand SHA 另核实镜像 ref 确已指向注入值，避免误把 fetch 拒绝当成 pin 守卫生效。
- 最终探针：**REVIEW_PROBE: PASS**。未改仓内 workflow、测试、实现或 fixture。

## 浅检 fixture、凭据与输入边界

- `_service_host` 从 `git show <head>:<file>` 构造无父 snapshot，仅用于测试 Git 浅克隆边界。
- 保留的 snapshot 包括四个 consumer 文件及两个 bootstrap 工具；构建后逐字节对照 H0，checkout 后又逐字节对照实际磁盘文件。
- snapshot SHA 不冒充 H0 的提交身份；生产 YAML 的 ref 始终是 `${{ job.workflow_sha }}`，无 snapshot/main/v2/registry_commit 替代路径。
- 独立探针再以完整 H0 身份取码通过；远端默认 fetch-depth=1 的全量 CI 也实际通过，覆盖浅检环境。
- service 不写 Git 凭据；origin 临时 `gate-git-http.conf` 在 finally 删除，两模式 `.git/config` 无 extraheader，stdout/stderr 不含 fixture token。
- **不作全量凭据清空承诺**：origin bootstrap 的 `RUNNER_TEMP/gate-api.hdr` 实测仍存在；该段在 base/H0 逐字一致，原 quality action bootstrap 已走同一路径，非本 diff 新增 finding。
- 四文件 checkout 留在平台专用子目录，不复制进 caller 的 scripts/tests；既有其他 source 目录仍先于 caller checks 清除。

## 远端与验证限度

- 查询冻结 SHA 的真实 run：`gh api 'repos/zlxlabs/gate/actions/runs?head_sha=597f2c81d81f262b98d9963680cc38c92adb49a7&per_page=20'`。
- run `40742086237`：`ci`、`conclusion=success`，开始 `2026-10-28T18:06:59Z`，完成 `18:13:01Z`。
- job `123006068974`（test）与 `123006069200`（actionlint）均为 **success，非 skipped**。
- 测试 job 的白名单摘要：**1997 passed in 333.85s**；实际配置为 Python 3.12、默认浅 checkout，全量 pytest。
- 本地没有再重复全仓测试；本地独立窄测／相关测试与同 H0 远端全量测试分开记账。
- OCR 命令绑定同一冻结 SHA 范围，背景为中性规格摘要；两腿各约 100 秒后超时。
- OCR envelope：`status=skipped`，`reason=primary=leg_timeout; backup:deepseek=leg_timeout`；不计为 clean，不替代人工全量审查。
- 生产 required quality 的真实发布调用尚未由本卡验证；CI 自测通过不证明 v2 已抬升，更不证明 gate-hub 消费已闭环。
- 继承红：**未能判定**，派发时主干基线不可用；新红：本轮指定检查与相关扩展检查均无。

## Findings 与交付裁决

- 本 diff 无 finding，故无需新增分支／机制的修复建议，也无阻断项。
- personal P1 两问：本轮没有“真实使用可触发且后果不可接受”的新增故障候选；不把存量凭据头文件行为或未发布状态冒充本轮 P1。
- 可交付本次来源供给实现；消费卡仍须等待主脑确认实际 v2 发布，不由本审查授权发布。

## 踩到的坑

- 第一次自建错 SHA 探针先撞到 fixture demand ref 的非快进拒绝，未抵达 pin 判断；未把该红计为实现证据。
- 检查失败后保留首轮现场，只删除新探针中那一个 fixture demand ref，核实错 SHA 注入落地，再证实 pin 守卫红；无仓内改动。

## 闸与绕过

- pickup 的源码版 pending 工具报 stale_tool_checkout，改用其明确提示的 runtime 只读入口；未认领、评论或更改任何 PR/issue。
- OCR skipped 如实记录，无绕过或清绿；本轮只推 verdict 分支，不开 PR、不改 v2 标签。
- 首次 push 被 public-scan 拦下，因为文档包含本机私人路径；已查远端确认分支不存在，再将本地路径移至私有报告、修订未发布的文档提交。保留扫描闸，不添加豁免。

## 与卡面的偏差

- 无修改边界偏差；额外完成相关 351 项、裸环境窄测、双来源模式实际 producer 探针和远端 CI 只读取证。
- 无匹配交接单；记忆无命中。检索仅作线索，未打开任何实现会话全文，保持输入隔离。

## 最贵的一步

OCR 两条超时腿约 200 秒，未取得扫描结论；有效独立扩展测试约 127 秒。

### 新增测试判据

无。本卡只新增 verdict；反向探针及其真实 argv/env/文件产物保留于本派发报告目录，不写入仓内测试或 fixture。
