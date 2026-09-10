# DESIGN-note：纯账本拉取请求走「本来就不该审」

## 目标

只改 `retro/acceptance-log.jsonl` 的拉取请求不再调用任何评审模型；主审、影子、视觉核对 job 结论为 SKIPPED，聚合器记预期跳过。模型额度不再被记账请求烧掉。

## 非目标

- 不改规则源仓的记账发布路径（那边另开单：直推主干 + 重试追加）。
- 不把 jsonl 塞进各仓 caller 的 `paths-ignore`，不改 onboard 覆盖表。
- 不跳过质量检查（lint/test）——工单允许「至少跳过模型腿」；质量检查跳过会撞上聚合器把 `quality=skipped` 当失败，本增量不碰那条状态。
- 不把 markdown / `GOALS.md` / `goals/**` 放进全局可跳过集合（规则源仓的规则文件是最高风险改动）。
- 不在本增量做全舰队钉版本；金丝雀在控制仓自己的 caller 上做（下一张卡）。

## 为什么不是分区 / 删除 / 约定

- **分区**：账本必须跟仓库一起版本化，不能拆到另一个 git 历史里让门禁看不见。分区解决不了「已经开出来的拉取请求」。
- **删除**：不能删模型评审；也不能删账本。能删的是「把记账当成可审代码」这一条错误输入。
- **约定**：上一班夜间已写明「独立 ledger PR，不直 main」，约定挡不住。触发器级忽略名单是约定的近亲，且与「结论应为 SKIPPED」不符、与覆盖表替换语义冲突、与控制仓锁死的忽略名单测试冲突。

## 方案要点与已否决方案

- **要点**：在可复用工作流里加一个只调 GitHub API、不 checkout 业务代码的分类 job。列出该拉取请求的全部改动路径；**仅当集合恰好等于 `{retro/acceptance-log.jsonl}`** 时输出「不应评审」。主审 / 视觉核对 / 影子 resolve 的 job `if:` 在既有草稿·fork·托管 runner 三守卫之外，再要求这个输出不是显式 false。列路径失败、分页不完整、空列表、任何其它路径：输出「应评审」（fail-closed）。聚合器的 `REVIEW_EXPECTED` 必须与主审 job `if:` 逐字节相同（已有契约）。分类脚本放门禁仓，两份可复用工作流（主审与影子）都调用；用 `job.workflow_repository` / `job.workflow_sha` 检出脚本，与聚合器同源。
- **已否决**：caller `paths-ignore` 加 jsonl——工作流不启动，没有 SKIPPED 结论，覆盖表是整表替换，控制仓测试把忽略名单锁死为恰好两项。
- **已否决**：本增量同时跳过质量检查——聚合器现把质量跳过当门禁失败；那是另一条状态机，不搭车。
- **已否决**：全局跳过 markdown——规则源仓不能跳过。

## 关键不变式

1. [实测] 主审 job 的 `if:` 与聚合步骤 `REVIEW_EXPECTED` 逐字节相同。锁：`tests/test_gate_v2_contract.py::test_gate_job_review_expected_matches_primary_jobs_own_condition`。改完后该测试仍绿，且新子句（分类输出）出现在同一表达式里。
2. [实测] 分类器对「只有 jsonl」输出不应评审，对其它所有输入（空、多文件、API 失败、截断）输出应评审。锁：分类脚本的表驱动测试；夹具是 GitHub 拉取请求 files API 的真实 JSON 形状（含 `filename`）。
3. [实测] 主审跳过且 `review_expected=false` 时聚合器是预期跳过，不是「主审异常失踪」。锁：`tests/test_gate_aggregator.py` 既有 `review_expected=False` + `primary_result=skipped` 用例；分类器接入后这条路径仍走同一出口。
4. [实测] 影子可复用工作流对同一判定跳过 resolve/shadow，不调模型。锁：`tests/test_gate_shadow_v2_contract.py` 给 resolve 的 `if:` 加上与主审相同的分类子句，或断言两份工作流调用同一脚本。

## 待验证前提

1. [推断] `gh api --paginate` 列拉取请求 files 在本组织 token 权限下对同仓拉取请求可用。验证：金丝雀拉取请求的分类 job 日志里出现完整路径列表，且没有 403。失败则分类器保持应评审（不跳过），金丝雀会暴露为模型仍被调用，本设计不得改成「列不出就跳过」。

## 验收路径

1. 入口：控制仓钉上本变更的 SHA 之后，开一个只改 `retro/acceptance-log.jsonl` 的拉取请求（可草稿转 ready，避免草稿守卫单独跳过主审造成假阳性）。
2. 步骤：看 `gh pr view --json statusCheckRollup`。主审、影子腿、视觉核对为 SKIPPED；质量检查仍跑；聚合器总灯绿；该次 runner 日志没有模型 API 调用。
3. 预期：对照拉取请求（jsonl + 任意业务文件）主审仍跑。列文件失败的注入（金丝雀不必做）由单测锁死为「仍要审」。
