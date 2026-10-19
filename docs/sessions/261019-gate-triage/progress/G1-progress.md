# 261019-gate-triage G1 进度存档（判定记录走 job outputs；Silo 全部归记账区）

## 里程碑 1：第 0 步前提核实（先做、先报）

- **当前阶段**：evidence-committed
- **本段结论**：前提 1 成立。Silo d14 层全部 2439 个 primary-audit-v2 实测：max 27 384 B（26.7 KiB）、P99 18 250 B、P50 4 682 B，为 256 KB 停车线的 10.7%、1 MB 硬限制的 2.7%；2439/2439 JSON 可解析、TOKEN 扫描 0 命中。1 MB 限制的官方依据定位到 actions/runner 表达式求值内存预算（`EvaluationOptions.cs` 默认 1048576 B，超限抛异常 fail-closed，非静默截断）。secret 屏蔽行为官方确认存在：actions/runner#1061 维护者原文「We currently don't support having secrets in job outputs」——output 含 secret 字面值时下游读到 `***`。**实现约束由此产生：output 必须 base64 单行编码**（打破子串匹配，数据面保真；日志面 masking 不受影响）。产物：`evidence/G1-audit-size.md` + 真实样本 fixture（单独 commit b4db3fd）。
- **关键决策与已否决方案**：Silo 取证在宿主 `zlx-vm-work-i5-infra` 上跑只读 S3 探针（复用 gate 仓 silo_store.py 客户端），凭据仅存在于宿主远端进程环境，未打印未落盘未传出；探针与临时文件用后即删。已否决：明文 JSON 直写 output（#1061 确认的静默损毁路径）；以「shield 风险无法排除」停车（风险有确定性规避，编码即解）。
- **下一步唯一动作**：按 TDD 落地 A+E 段实现。

## 里程碑 2：A+E 段实现（workflow 改造 + 守卫 + 断言反转）

- **当前阶段**：implementing-done
- **本段结论**：primary 新增 4 个 job outputs（audit_json_b64/audit_bytes/audit_attempt/audit_key）与 fail-closed publish 步骤（缺失/空/超 512 KB base64 预算线即红）；gate 删除 Silo resolve+download 两跳，新「Read canonical primary audit from primary job output」步骤（缺失/坏 base64/长度标记不符三种损坏全部收敛到聚合器 audit_missing，JSON 不合法走 audit_invalid；`job_audit_mismatch` 防线原样保留）；ledger 同步改读 output，reviewed run 缺 output 保持 fail-loud（row 不写、job 级 continue-on-error 兜结论）；audit 归档 gate 条件改 `needs.primary.outputs.audit_key != ''`，archive key 与改造前同模板。全部 Silo 上传/归档步骤（primary 4、gate 6、ledger 8、ocr 2）+ 各 job MagicDNS/注入准备步骤归记账区：continue-on-error + 失败输出 `GATE-ARCHIVE-DEFERRED kind=<kind> key=<silo key> reason=<类别>`（::warning:: 同发，底层 SILO-REQUEST-FAILED 照打 stderr 不吞）。raw evidence 的 TOKEN 扫描逐字未动（G2 范围）。守卫测试 `test_gate_v2_silo_region_guard.py`：扫描全部 Silo 触点 + 判定通道生产/消费端，分区注释 `gate-silo-region: verdict|bookkeeping` 强制声明，反例红验内置。全量 1865 passed、`check_pinned_uses` 绿。
- **关键决策与已否决方案**：分区声明选注释（步骤名是大量既有契约测试的定位锚，加前缀会放大无关改动；守卫对原文按 6 空格列表项切块并向北吞紧邻注释行，避免 YAML 解析丢注释漏检）；「记账区不改结论」的守卫判据 = step 级或 job 级 continue-on-error（ledger job 自带 job 级，其内部上传步骤不强加 step 级——与改造前失败路径逐点等价）。实现中修掉一个真 bug：`reason=$(… | grep …)` 在 pipefail 下无匹配会让赋值非零、set -e 在输出 deferred 行之前静默终止步骤——统一 `|| true` 收尾（13 处），由行为测试（断言 stderr 含 SILO-REQUEST-FAILED）与红验锁死。已否决：verdict 步骤输出 deferred（判定失败必须真红，守卫禁止）；gate read 步骤 exit 非 0（保持 terminal envelope 照常生成记录原因，与改造前 audit_missing 行为一致）。
- **下一步唯一动作**：行为测试（跨边界 + 故障注入）补齐并红验。

## 里程碑 3：跨边界行为测试与红验收口

- **当前阶段**：validating-done
- **本段结论**：新增 `test_gate_v2_audit_output_channel.py` 16 条：真实样本（Silo 实取、扫描干净）经 publish→read→aggregate.py 完整路径 verdict=pass、terminal 的 audit.source_attempt/artifact_name 与 output 声明逐一吻合；缺失/坏 base64/截断三种注入全走 audit_missing → gate unavailable；`job_audit_mismatch` 在通道切换后保持（audit pass + primary failure → unavailable）；Silo 全挂注入（fake silo_exec 报 SlowDown）下 5 个记账步骤各自输出 deferred 行、底层错误不被吞；ledger 对 skipped primary 降级 null 字段。红验两处（先 commit 真修复 b31face/d38700b，只还原改坏处，无 stash）：① publish 缺 audit 改为 exit 0 → `test_publish_run_block_fails_closed_on_missing_and_oversize` 红（`assert result.returncode != 0`）；② upload gate terminal 丢 deferred 行 → `test_silo_down_produces_deferred_rows_and_fails_the_step_only[gate-Upload gate terminal envelope-gate-terminal]` 红（stdout 无 `GATE-ARCHIVE-DEFERRED kind=gate-terminal`）。反转的旧断言：`test_gate_v2_diagnostics_upload.py`（诊断上传必须 fail-closed → 必须记账区化）、`test_gate_v2_contract.py::test_ledger_persistence_*`（同理）、`test_gate_job_downloads_the_same_artifact_name_primary_uploads` 重写为 output 通道 + key 模板一致契约。
- **关键决策与已否决方案**：diff 规模 1668 行（target 1500、hard 3500 之间，显式声明：新增代码集中在 18 个记账步骤的 deferred 样板与守卫测试，无生成式膨胀）。已否决：为「记账步骤失败」新增汇总步骤（deferred 行就地输出即可满足 G3 输入契约，不引入第二消费者不明的新抽象）。
- **下一步唯一动作**：push 分支、写报告，门禁真实规模验证命令交主脑在 canary/消费仓执行。

## 里程碑 4：交付与待主脑动作

- **当前阶段**：delivered（本仓验证全绿）
- **本段结论**：分支 `card/261019-g1` 推送（commit b4db3fd → b31face → d38700b → progress）。全量 `uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q` = 1865 passed；`python3 scripts/check_pinned_uses.py` 绿；Accept-Check `grep -c GATE-ARCHIVE-DEFERRED … => 1` 通过。**继承红/新红**：主干基线派发时刻不可用（gh api request failed），无法判定继承红；全量当前全绿，无新红。**门禁真实规模验证待主脑执行**（执行器无 fleet 触发权）：在任一真实消费仓（或 canary）以分支 ref 调用跑一次 PR，命令：在目标仓 PR 上以 `uses: zlxlabs/gate/.github/workflows/gate-v2.yml@card/261019-g1` 触发（或临时 workflow_dispatch 包装），验收点：① primary job 的 output 往返（含 findings quote 的真实 audit）在 gate/ledger job 内可解码且 gate 结论与审阅一致；② 用 `iptables -A OUTPUT -d 100.126.6.73 -j REJECT`（或等价方式断 Silo）重跑，gate=pass/正常结论、日志含 `GATE-ARCHIVE-DEFERRED`、ledger job conclusion=success；③ 观察步骤日志无 secret 被 mask 成 `***` 混入 audit 解码结果。
- **关键决策与已否决方案**：无
- **下一步唯一动作**：等主脑触发真实规模验证后合入（merge commit 方式，勿 squash——v2 标签依赖 SHA）。

## 里程碑 5：门禁审查 P2 修复（守卫补识别直接调用 silo_store.py）

- **当前阶段**：review-fix-done
- **本段结论**：codex 审查（branch `card/261019-g1r`，`reviews/G1-verdict.md`）verdict pass、仅一条 P2：守卫 `_silo_touchpoint` 漏扫不经 `$SILO_EXEC` 的直接调用（`python3 "$SILO_STORE" …` / `python3 scripts/silo_store.py …`）。TDD 收口：先加两条变异测试取红（`AssertionError: undeclared direct silo_store.py invocation must turn the guard red … assert []`），再新增 `SILO_STORE_INVOKE_RE`（锚 python 调用位：`python3?\s+"?(?:\$SILO_STORE\b|[^"\s]*silo_store\.py)"?`）转绿；同时加负例测试锁死「env 赋值/sparse-checkout 列表项/注释提及不算触点」防反向过宽。真实 workflow 触点集合不变（30 个）、新规则零命中、零违规，无需补分区声明。全量 1868 passed（原 1865 + 新增 3）、`check_pinned_uses` 绿。commit dc0f846，分支 `card/261019-g1` 已推送。
- **关键决策与已否决方案**：识别规则选「python 调用位锚定」而非裸匹配 `silo_store.py` 字面量——workflow 里 env 定义（`SILO_STORE: …/silo_store.py`）、sparse-checkout 列表项、注释提及都含该字面量，裸匹配会把 4 个 job 的 job 级 env 误卷进触点（其中 job 级 env 行文本落在前一个 job 末步的切块内，会造成跨 job 误报）。已否决：把 `"$SILO_STORE"` 任意出现都算触点（同上误伤路径，且无增益）。
- **下一步唯一动作**：无新增待办——门禁真实规模验证仍按里程碑 4 的三点验收等主脑执行，随后 merge commit 合入。
