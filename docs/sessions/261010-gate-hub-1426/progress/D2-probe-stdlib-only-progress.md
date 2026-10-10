# D2 — v2 失败路径探针在自托管 runner 上不得依赖 PyYAML（gate-hub#1426）

## 里程碑 1

- **当前阶段**：implementing（单元①+②完成：探针改为读传入片段、stdlib only，守卫测试就位）
- **本段结论**：探针脚本删掉 PyYAML 提取块，改为从 `GATE_SOURCE_PREPARE_SCRIPT_FRAGMENT` 环境变量读片段（缺失时报 `SOURCE-PROBE-SCRIPT-EXTRACT-FAILED`）；verdict 判定本就只用标准库，未动。新增守卫测试：`python3 -I -S` shim 前置 PATH 跑探针必须通过，且先锚定 shim 本身确实藏掉 PyYAML。片段传递方式选 job output（片段实测 2830 字符，远低于 1MB 上限）。
- **关键决策与已否决方案**：传递用 job output + 随机 delimiter（不用 artifact——为 2.8KB 片段引 upload/download 两个 pinned action 不值得）；错误码沿用 `SOURCE-PROBE-SCRIPT-EXTRACT-FAILED`（片段未传入与上游提取失败是同一故障链，卡面失败可见性条款钉的就是这个字面量）。
- **下一步唯一动作**：单元③——sync job 新增提取步骤 + 结构测试（先红后绿）。

## 里程碑 2

- **当前阶段**：implementing（单元③完成：workflow 提取步骤 + 结构测试，全量绿）
- **本段结论**：sync job 在契约测试之后新增 `probe-script` 提取步骤（`move=='true'` 条件下，从 `target_sha` 的 gate-v2.yml 用 PyYAML 提取片段，经 job output 随机 delimiter heredid 传给 failure-probe 的 `GATE_SOURCE_PREPARE_SCRIPT_FRAGMENT` 环境变量）；提取失败统一走 `SOURCE-PROBE-SCRIPT-EXTRACT-FAILED` fail-loud，sync 变红 ⇒ probe skip ⇒ Move 不执行。结构测试锁住：提取在装 PyYAML 之后、只取 `target_sha`（无 GITHUB_SHA/HEAD）、多行传递格式、探针经 env 接片段。全量 1480 绿、`check_pinned_uses` OK。
- **关键决策与已否决方案**：第一版把 `git show | python3 - <<'PY'` 管道直接进 heredoc 被抢 stdin，本地冒烟当场抓到（fragment 读空报 missing），改为先落 `${RUNNER_TEMP:-/tmp}` 临时文件、python 从 argv 读路径（与原探针写法同构）；冒烟双向补验（fragment 字节一致 round-trip、缺 sha 时 fail-loud），是真实 runner 之外能做的最强执行验证。
- **下一步唯一动作**：收尾——push 卡分支、写报告；真实 runner 配对验证由主脑合并后取证（本卡未在真实 runner 上运行）。
