# progress — public ui-evidence 评论 action（gate-20261014-48，refs gate#314 / gate-hub#801）

## M1 设计 + 测试红（阶段：implementing）
- 本段结论：设计 ≤35 行落 `docs/sessions/261014-herdweb-ui/design.md`；12 用例在实现落盘前全红，
  红均为显式 `AssertionError: public action 文件缺失`（非 FileNotFoundError/KeyError）。
- 关键决策：采纳 Codex（consult/20261014-183723-codex-09e2bb）——无需真实 GitHub 加载 probe，
  A 入远端 v2 后才做 B；已否决：复制 private 版默认 URL/内网措辞。
- 下一步唯一动作：落 action.yml + sticky-comment.sh 实现转绿。

## M2 实现绿 + 红验（阶段：implementing）
- 本段结论：18 passed（本卡 12 + 闭集 6）；全量 1563 passed exit=0（165s，
  /tmp/herdweb-public-ui-48/full-verify.log）；两条红验均 AssertionError 后仅恢复变异行：
  ①移除 `:?` URL 校验 → stderr 缺 `UI-EVIDENCE-URL-MISSING` 红；②去 `jq -s` → returncode 5 红。
- 关键决策：闭集测试精确扩 `.github/actions/*/*.sh` 发现并登记两入口；durations 仅加本卡键 0.30。
- 下一步唯一动作：push + draft PR + 等 CI 终态。

## M3 发布边界（阶段：review-waiting）
- 本段结论：draft PR zlxlabs/gate#317（head 4c0809da889b446cc567915b1e622e159a54be19）；
  PR CI run 38553441773：test conclusion=SUCCESS（2m41s，实际执行非 skip）、actionlint
  conclusion=SUCCESS；分支远端实际输出见报告 ls-remote 段。远端 v2 抬升含本 action 前，
  B 卡不得开工、README uses@v2 未生效。
- 外部时变证据快照（原输出 + sha256(evidence-snapshot.txt)=74c02755909773444864bd014230042a10e045b4c1ed77c5deefe87ebe2fb75e，副本在 /tmp/herdweb-public-ui-48/）：
  - `gh repo view zlxlabs/gate --json visibility` → `{"visibility":"PUBLIC"}`
  - `gh api repos/zlxlabs/herdweb/actions/jobs/115400951529`（白名单字段）→
    `{"conclusion":"failure","name":"ui-visual","steps":[{"name":"Set up job","conclusion":"failure"}]}`
    —— 即 #314 现场：private action 跨 public 仓不可加载，Set up job 直接红。
  - `git ls-remote https://github.com/zlxlabs/gate.git refs/tags/v2` → `008170e11a949c77029d1dbb658e36554b2c8b7b`（=本卡 base）
- 未宣称：herdweb 的 Set up job 故障已消失——真实 owner 消费验证属 B 卡/owner 后续。
