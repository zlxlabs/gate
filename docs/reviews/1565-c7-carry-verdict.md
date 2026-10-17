<!-- delegate-outcome: succeeded -->

## C7 审查结论

failure-visibility: p1-found

**结论：FAIL；1 条 P1，阻断合并。** 审查固定范围 `358a0caf7f5055c942e62f06222cd3d0970773bb..660f9348f1fbc1f3e72fbcaacf92af63c3a7d710`，risk-tier 为 personal。OCR envelope 为 `status=reviewed`，4 条候选逐条核验；其中 1 条确认并按本仓风险档位定为 P1。派发时基线查询不可用，继承红/新红无法判定。

## P1：非空预标注 audit 会丢弃此前未关闭 finding

**违反不变式：** 历史未关闭 finding 必须经真实 aggregator 输出、JSONL 写入和下一轮读取后保留；本轮未再报告不代表关闭。

**证据：** `aggregate.py:3901-3904` 发现当前 finding 已有 `relation_to_previous` 时，把 `previous_round.findings` 强制设为空；而新增的 `build_finding_relation` 在 `aggregate.py:613-642` 只从该 previous 列表和本轮 findings 生成 `open_findings`。真实 workflow 在 `gate-v2.yml:1121-1123` 先运行 `--annotate-primary-audit`，随后 `gate-v2.yml:1243-1254` 上传被标注的同一 audit。标注器 `aggregate.py:872-880` 只把关系字段写回 audit findings，没有把其 `Outcome.finding_relation.open_findings` 写入 audit，因此 aggregator 无法从 audit 取回历史 carry。

我用目标 SHA 的真实 `aggregate.main`、`--annotate-primary-audit`、`build_ledger.build_entry/write_ledger` 和 `_previous_findings_from_silo` 串起临时两轮探针，Silo 读取由临时 JSONL fixture stub 提供、没有生产写入：

- 第一轮 JSONL 的 `open_findings`：`[old-open]`；下一轮 loader 读回：`[old-open]`。
- 第二轮 audit 含另一条 `new-current`；真实标注器将其标为 `new`。聚合终态及第二份 JSONL 的 `open_findings` 都只有 `[new-current]`。
- 第二轮 gate verdict 仍为 `pass`，没有 carry 丢失告警；旧项不会再进入后续 review 的 `previous-findings.json`。

**后果与风险：** 任何有非空且经过标注的当前审查轮都会静默丢掉未被本轮复述的历史项；后续轮次因只读最新 ledger 的 `open_findings`，无法继续把该项作为 previous findings 上下文。这是核心持久化行为的静默错误，符合 personal 档 P1 红线。

**阻断判断：阻断。** 该路径是 workflow 正常顺序，不依赖非法输入；若合并，carry 只在静默轮成立，遇到另一条当前 finding 就失效。

## 边界核验与未采纳候选

- 空的当前 findings 会得到 `items=[]`、全零 `counts` 且继续携带历史项；目标 SHA 新增的沉默轮测试验证了 aggregator→JSONL→loader。旧 ledger 没有 `open_findings` 时仍回退到 `review.result.findings`；`test_previous_findings_get_latest_once_without_list` 使用旧格式 fixture 通过。
- 同 ID 的新位置、issue、acceptance 会覆盖 carry 中对应值；缺少的可选字段会保留旧值。已有契约把相同 ID 定义为 `repeat`，且每轮 ledger 行单独写出，旧原始记录仍保留；没有证据表明该覆盖破坏本卡规定的原始记录追溯。空列表、同 ID 更新和缺字段形态已用目标 SHA 函数探针核对。
- OCR 关于 carry 中旧关系标签和历史 open 参与本轮关系匹配的两项候选未判缺陷：当前 `items/counts` 仍只按当前 findings 生成，历史项作为 previous 上下文；本卡没有要求剥除原始上下文字段或把匹配范围限制为紧邻一轮。未检查或报告四条被卡面明确排除的关闭机制。

## 验证

- 目标 SHA 定向测试：`test_silent_round_carries_open_finding_through_ledger_jsonl`、`test_ledger_copies_finding_relation_counts`、`test_ledger_rejects_non_object_open_finding`：3 passed。
- 旧格式与关系回归：`test_previous_findings_get_latest_once_without_list`、`test_render_previous_context_preserves_full_findings_and_prior_head`、`test_same_finding_id_is_repeat_with_previous_pointer`：3 passed。
- 未跑全量测试、付费模型或生产写入；未修改产品代码。
