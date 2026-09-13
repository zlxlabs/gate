# gate PR #164 独立对抗评审

评审对象固定为 base 785666e98819eacad6d65e04cfc7797cc4859fd1 到 head 2956615af25814c9f849950460a68559664bd315。仓库风险档为 personal。

## Verdict

review_verdict: changes-requested

发现一条 P1 和两条 P2：

- P1：迁移过渡期存在真实的 workflow 启动失败组合。新 caller 不传 gate_ref，但当前 v2 仍解析到要求 gate_ref 的旧 workflow。
- P2：新增的“兼容两种传参方式”测试是由同一份声明集合构造输入，关键子断言恒真，没有测试 caller 与 callee 的真实边界。
- P2：正文把 e74743bb 描述成 38 个仓共同 pin 的 commit，与 gate-hub#806 的最新实测更正不符，并因此错误地宣称迁移到 @v2 行为中性。

## 1. job.workflow_sha 是否等价于原 gate_ref

结论：核心自解析在当前 GitHub.com 部署上等价，并且 workflow_call 与 workflow_dispatch 都指向“定义当前 job 的 workflow 文件”的具体 commit；workflow_dispatch 不会把输入 gate_ref 当作 checkout ref。

可复核依据：

- GitHub 官方 Contexts reference：
  https://docs.github.com/en/actions/reference/workflows-and-actions/contexts
  的 job context 明确写明：job.workflow_ref 是定义当前 job 的 workflow 文件 ref；对 reusable workflow 的 job，它指向 reusable workflow 文件；job.workflow_sha 是该 workflow 文件的 commit SHA；job.workflow_repository 是其所在仓库（官方文档表格及 reusable workflow 示例）。
- 同一官方页面的 reusable workflow 示例使用 job.workflow_repository 加 job.workflow_sha 作为 actions/checkout 的 repository/ref。这与本 PR 的 .github/workflows/gate-v2-disposition.yml:41-42 完全一致。
- workflow_call 路径：被调用文件就是定义 control job 的文件，官方定义因此把 job.workflow_sha 绑定到 caller 实际解析的 reusable workflow 文件 commit，而不是 caller 的 github.sha 或手传输入。
- workflow_dispatch 路径：没有 caller，当前 gate-v2-disposition.yml 直接定义 control job；job.workflow_ref/job.workflow_sha 仍按上述 job context 定义取当前手工运行的 workflow 文件及其 commit。GitHub 的 workflow_dispatch 事件文档另说明事件的 GITHUB_SHA 是所选 branch/tag 的最后 commit；本 PR没有使用该值，而是使用 job.workflow_sha，所以不会把 branch/tag 直接交给 checkout。
- 仓内既有实证：.github/workflows/gate-v2.yml:140-141、246-247、387-388、867-868、1100-1101、1328-1329，以及 gate.yml 和 gate-shadow-v2.yml 的对应位置，都已使用同一 job.workflow_repository/job.workflow_sha 组合。

job context 属性在 GitHub.com 的普通 job/step 中可用，类型是 string；当前远程检查 URL 也是 github.com。官方文档注明这些 workflow identity 属性在 GitHub Enterprise Server 不可用：若部署形态改为 GHES，表达式可能为空，这是本仓当前部署之外的兼容性前提，不构成本 PR 在真实 GitHub.com 使用方式下的 P1。

## 2. sparse-checkout 路径

结论：当前 base、head、v2 标签和 origin/main 生产历史中，两个路径都存在；没有找到一个现存生产 ref 能证明“旧 gate_ref 能 checkout、新 job.workflow_sha 不能”。但 job.workflow_sha 的 GitHub 语义本身只保证 workflow 文件所在 commit，不自动保证两个 action 文件存在，所以这是依赖仓库共定位的不变式，而非 GitHub API 单独提供的保证。

证据：

- HEAD 的 .github/workflows/gate-v2-disposition.yml:43-46 精确列出：
  .github/actions/gate-disposition/issue_receipt.py
  .github/actions/gate-aggregator/convergence.py
  并关闭 sparse-checkout cone mode。
- 命令：
  git ls-tree -r --name-only HEAD -- .github/workflows/gate-v2-disposition.yml .github/actions/gate-disposition/issue_receipt.py .github/actions/gate-aggregator/convergence.py
  输出三行，分别为上述 workflow、issue_receipt.py、convergence.py。
- 命令：
  git ls-tree -r --name-only e74743bb621c5a373956545c730dbe81b5304eaa -- .github/workflows/gate-v2-disposition.yml .github/actions/gate-disposition/issue_receipt.py .github/actions/gate-aggregator/convergence.py
  同样输出三行。
- 对 base、head、v2 的逐路径 git cat-file -e 检查全部为 present；对 origin/main 中 workflow 文件的历史逐 commit 检查没有缺失输出。

因此本项结论是“当前生产 ref 通过，长期保证依赖共定位”，不是把路径存在性夸大为 job.workflow_sha 的内建保证。由于当前支持的生产历史均满足共定位，未单列 P1。

## 3. 被删除的 40-hex 校验守护了什么

结论：旧守卫守的是手工输入 gate_ref 的非空、40 位、小写十六进制格式，以及不接受 branch/tag；它没有验证对象实际存在，也没有在 workflow 内比较 gate_ref 与 uses pin。GitHub 产生的 job.workflow_sha 在当前部署上覆盖了“不是 branch/tag、是具体 commit SHA”的全部输入范围，因此删除守卫在这一本变式上成立。

证据：

- base 的 .github/workflows/gate-v2-disposition.yml:38-46 是旧 step。它把 inputs.gate_ref 放入 GATE_REF，以正则 ^[0-9a-f]{40}$ 检查，失败时输出 error 并 exit 1；base:51-52 随后把相同输入直接交给 checkout。
- head 的 .github/workflows/gate-v2-disposition.yml:38-47 删除了该 step，checkout 改为 :41-42 的 job workflow identity。
- 官方 Contexts reference 对 job.workflow_sha 的定义是“commit SHA of the workflow file that defines the current job”，不是 ref 字符串；因此在 GitHub.com 上不会是 branch/tag，也不是空的手工输入。
- 两条触发路径均在同一个 control job 的 step 中消费该属性。workflow_dispatch 的 event ref 可以是 branch/tag，但那是事件选择；checkout 消费的是 job.workflow_sha。

剩余前提只有两项：GitHub.com 而非 GHES，以及 workflow 文件所在 commit 中确实保留两个 sparse 文件。前者是部署平台前提，后者在第 2 项已用当前生产历史实测。

## 4. 向后兼容与 v2 过渡期

结论：新 callee 的两种 caller 方向本身兼容，但当前 v2 标签与新模板之间有失败组合。

| caller 形态 | callee 版本 | 结果 |
| --- | --- | --- |
| 旧 caller：传 gate_ref | 新 PR callee：gate_ref optional/ignored | workflow_call 可接受；gate_ref 不再参与 checkout，改用 job.workflow_sha |
| 新 caller：不传 gate_ref | 新 PR callee | 可接受；五个业务输入仍为 required，checkout 用 job.workflow_sha |
| 旧 caller：传合法 40-hex gate_ref | 旧 v2 callee e74743bb | 按旧逻辑可工作，继续由 gate_ref checkout |
| 新 caller：不传 gate_ref | 旧 v2 callee e74743bb | 失败：旧 callee 的 workflow_call 两处 gate_ref 声明仍 required:true，调用在进入 control job 前缺 required input |
| 新 caller：传 gate_ref=v2 | 旧 v2 callee e74743bb | 失败：即使声明存在，旧 40-hex step 也拒绝 v2 |

真实环境证据：

- 命令 gh api repos/zlxlabs/gate/git/ref/tags/v2 --jq '{ref:.ref,sha:.object.sha,type:.object.type}' 输出：
  {"ref":"refs/tags/v2","sha":"e74743bb621c5a373956545c730dbe81b5304eaa","type":"commit"}
- 命令 git show e74743bb621c5a373956545c730dbe81b5304eaa:.github/workflows/gate-v2-disposition.yml | nl -ba | grep gate_ref 输出第 11、19 行，均为 required: true。
- head 的 templates/caller-gate-disposition.yml:27 使用 reusable workflow，:29-33 只传五个业务输入，没有 gate_ref。
- GitHub 官方 reusable workflow 文档说明 required input 由 workflow_call 声明，caller 通过 jobs.<job_id>.with 传入；因此上述缺失输入不是业务脚本里的可恢复错误，而是 reusable workflow 调用边界失败。

P1-1（真实触发且不可接受）：gate-hub#806 已把“caller 从裸 SHA 改为 @v2”列为真实舰队迁移动作；当前远端 v2 又确实停在 e74743bb 旧 schema。只要任一仓用本 PR 的新 caller 形态在 tag 尚未指向兼容 callee 时迁移，就会在 workflow 启动前失败，disposition 申诉/豁免通道不可用。第一问：会不会在真实使用方式触发？会，触发条件正是已计划的 38 仓迁移顺序，且当前 tag/schema 状态已实测。第二问：后果能否接受？不能，这是高风险 disposition workflow 的崩溃/启动失败，命中 personal 档 P1 红线。

正文第 22-24 行把“tag 继续停在 e747”和“新 caller 不传 gate_ref 后迁移行为中性”同时作为锁定结论，这个组合不成立。修复不需要重开 @v2 决策，但必须让 tag、caller 形态和 callee schema 的发布顺序互锁。

## 5. 测试约束力

结论：新 checkout 的正向契约有约束力；兼容性测试的关键部分没有约束力，属于 P2。

逐项判断：

1. tests/test_gate_v2_contract.py:161-164 把 gate_ref required 从 true 改为 false。这是对新 schema 的有意放宽，旧约束被产品决策取代；作为测试改动是等价于新 spec，不是误删断言。
2. 原 :275-283 的 gate_ref checkout 测试被替换为 :275-285 的 reusable workflow identity 测试。新测试精确锁 repository/ref，并额外锁定旧守卫不存在及生产 workflow 不读取 inputs.gate_ref；对新实现的静态约束不弱于旧实现对应的 checkout 断言。
3. 原 :286-302 的 40-hex 守卫测试被删除。这与生产删除守卫同步，是行为契约变更；它不再保护 gate_ref 格式，但第 3 项说明该格式不变式已由 GitHub workflow identity 取代。测试没有把 GitHub 运行时语义模拟成同进程假测试。
4. 新增 :288-296 的 test_disposition_workflow_call_accepts_legacy_gate_ref_both_ways 是弱测试。required 与 business_inputs 都从同一个 declared 映射推导，supplied 又是由 business_inputs 或 declared 直接构造；因此 required <= supplied 对这两个 supplied 值是构造上恒真的。它没有执行 caller YAML 的 with 边界，也没有覆盖旧 v2 required schema 与新 caller 的组合。P2-1：当前实现虽正确，但该测试不能锁住正文声称的真实兼容性。
5. caller 测试由旧的 :2087-2092（with.gate_ref 必须等于 uses pin）改为 :2076-2084（with 恰好五个业务输入）。这是新 caller 契约的精确正向测试；同时有意放弃了旧 caller 双份 pin 一致性断言，符合本 PR 消除副本的目标。

实际测试：

- 命令 uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q
  输出：945 passed in 34.65s
- 命令 uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_v2_contract.py -k disposition
  输出：8 passed, 125 deselected in 0.23s
- 红验在临时 base worktree 中只拷入 head 的 tests/test_gate_v2_contract.py，确认 workflow 仍为 base 的 repository zlxlabs/gate、ref inputs.gate_ref 后，运行：
  uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_v2_contract.py::test_disposition_checkout_uses_reusable_workflow_identity
  输出：1 failed；AssertionError 显示期望 job.workflow_repository、实际 zlxlabs/gate；退出码 1。注入位置由 grep 确认为 base workflow:52 的 ref inputs.gate_ref。说明新增正向测试确实会拦住本 PR 的目标回退，不是恒真。

## 6. 生产路径的 checkout commit 判断

结论：head 的 gate 仓生产 workflow 中共有 10 个使用 job.workflow_repository + job.workflow_sha 的 checkout step，全部同一种机制；没有新增第二种“版本事实源”。

可复核命令：

rg -n 'ref: \${{ job.workflow_sha }}' .github/workflows

命中行：

- .github/workflows/gate-v2-disposition.yml:42
- .github/workflows/gate-shadow-v2.yml:147
- .github/workflows/gate.yml:103、381
- .github/workflows/gate-v2.yml:141、247、388、868、1101、1329

每一处的上一段 with 均为同文件的 repository: job.workflow_repository；例如 disposition :41-42、gate-v2 :140-141/:246-247/:387-388。另有若干 checkout 只服务于被审仓工作区、未指定 gate source ref，不属于“选择 gate producer commit”的判断。

补充命令 grep -rn 'inputs\.gate_ref' .github/ scripts/ templates/ 无输出，退出码 0。因此生产路径内没有残留的 gate_ref checkout 选择。

## 7. PR 正文逐句核对

| 正文描述 | 结论 | 证据 |
| --- | --- | --- |
| disposition 改用 job.workflow_repository/job.workflow_sha，并与 gate-v2 既有三处写法对齐 | 成立 | head :41-42；gate-v2 :140-141、246-247、387-388 |
| gate_ref optional、deprecated、步骤不再读取 | 成立 | head :11、:19；grep 0 命中；head 无 inputs.gate_ref |
| 删除 40-hex step，job.workflow_sha 是 GitHub 产生的具体 commit，不是 branch/tag | 成立（GitHub.com 前提） | base :38-46 对比 head :38-47；官方 Contexts reference |
| caller template 不再传 gate_ref | 成立 | templates/caller-gate-disposition.yml:27-33 |
| 净删 13 行（+29/-42） | 成立 | git diff --numstat：workflow 4/14、progress 7/0、template 2/3、tests 16/25 |
| owner 决策是 caller 改 @v2、退役换钉工具 | 成立 | gate-hub#806 的 issue 正文 |
| “v2 当前为 e74743bb，即 38 个仓所 pin 的那个 commit” | 不成立 | 远端 tag 确实为 e74743bb；但 gate-hub#806 最新巡检更正显示 e74743bb 只有 gate-hub 自己 3 个引用，其余 35 个仓是 d51ce786 或更旧 |
| tag 迁移期必须保持 e747，caller 换引用形态行为中性，@v2 对传/不传都同时成立 | 不成立，见 P1-1 | e747 旧 workflow :11/:19 仍 required:true；head template 不传 gate_ref；新 caller -> 旧 v2 会启动失败 |
| 直接删除 gate_ref 会让仍传该 input 的旧 caller 失败 | 成立 | 旧 caller 仍有 with.gate_ref；新 schema若删除声明会违反 workflow_call 输入边界；本 PR保留 optional 是合理的锁定决策 |
| 保留 40-hex 校验并接受 tag 被否决，因为继续维护形态正则会保留重复事实 | 设计理由与本 PR实现一致 | head 已转 job identity；不重开该锁定决策 |
| 迁移完成后用运行时 inputs 事实确认再删除声明 | 成立且属于后续动作 | 与本 PR范围不冲突 |
| 945 passed、check_pinned_uses exit 0、grep 0 命中 | 全部实测成立 | 见下方验证记录 |

正文事实核查的额外 P2-2：#806 的最新巡检评论已经明确 35 个仓仍是 d51ce786 或更旧，因此不能把 e747 当作全舰队共同旧 pin，也不能据此把切换到 @v2 说成全舰队行为中性。该事实问题不会改变本 PR 核心 job identity 机制的正确性，但会误导发布顺序与风险判断。

## 验证记录

- gh pr checks 164：
  actionlint pass 6s
  test pass 45s
- python3 scripts/check_pinned_uses.py：
  OK: checked 8 live workflow/action metadata file(s); all internal uses are workspace-relative
  退出码 0
- grep -rn 'inputs\.gate_ref' .github/ scripts/ templates/：
  无输出，退出码 0
- git status --short --branch：
  ## card/gate-20260913-16
  评审树干净，未改代码。
- OCR 前置扫描：
  status=skipped；minimax 与 deepseek 均 caller_error:startup_stderr；attendance_ledger_write=ok。不能表述为已扫描且干净。

## 结论

P1-1 需要在发布顺序/迁移契约上修正后再合并；P2-1 测试兼容性断言应改为真实 caller/callee 边界或明确降级为静态 schema 检查；P2-2 应修正正文的舰队 pin 事实和“行为中性”措辞。除上述问题外，job.workflow_sha 的语义、当前 sparse 文件共定位以及生产 checkout 机制均有证据支持。

outcome: pass
