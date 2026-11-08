# gate-394 v2 抬升资格要求 GATE-TESTED-EVIDENCE-V1 进度存档

## 里程碑 1：真实日志切片 + 判定函数 + 红绿用例

- **当前阶段**：implementing
- **本段结论**：把参考脚本的 Emit 区间定位移植进 `scripts/v2_tag_promotion_evidence.py` 的 `_candidate_result`：quality job 恰好一个且 success，日志区间内恰有一行 `mode=pytest` 证据且 head 等于 run 详情 `head_sha`。fixture 来自真实 job 日志边界切片（正样本 job-128648744350 / 负样本 job-128486678343）。
- **关键决策与已否决方案**：不对整份日志宽泛 grep；日志走 `actions/jobs/{id}/logs` 文本读取，302 不转发 Authorization。已否决：在 notify-gate 处阻断、校验 merge_sha 与 checkout 对应。
- **下一步唯一动作**：跑窄测与全量测，写文档，scope check，开 PR。

## 里程碑 2：窄测绿、准备开 PR

- **当前阶段**：implementing
- **本段结论**：`tests/test_v2_tag_promotion_evidence.py` 55 passed；正样本 eligible 且确实打了 jobs/{id}/logs，负样本 reason 可 grep 区分。文档已追加「v2 抬升资格」。
- **关键决策与已否决方案**：fixture 只保留 Emit 边界与相邻 `##[group]Run` 头（省略中间脚本回显），以满足 <30KB 与 diff 预算；派生用例在测试里改切片，不另存文件。
- **下一步唯一动作**：scope check、commit、push、开 PR（Refs zlxlabs/gate#394，不合并）。

## 里程碑 3：返修——gate 只读 tested-evidence job 元数据

- **当前阶段**：implementing
- **本段结论**：按主脑契约变更，删掉跨仓 job 日志读取/解析；`_candidate_result` 在 primary 成功后只要求恰好一个 `tested-evidence` job 且 conclusion=success。`_api_json` 恢复为只读 JSON。
- **关键决策与已否决方案**：日志判定挪到 canary 仓（另卡，用 canary 自己的 token）。已否决：给 v2-tag-sync 加 PAT/跨仓 actions:read。
- **下一步唯一动作**：跑窄测与全量测，更新 PR #397，不合并。
