# D2 — v2 失败路径探针在自托管 runner 上不得依赖 PyYAML（gate-hub#1426）

## 里程碑 1

- **当前阶段**：implementing（单元①+②完成：探针改为读传入片段、stdlib only，守卫测试就位）
- **本段结论**：探针脚本删掉 PyYAML 提取块，改为从 `GATE_SOURCE_PREPARE_SCRIPT_FRAGMENT` 环境变量读片段（缺失时报 `SOURCE-PROBE-SCRIPT-EXTRACT-FAILED`）；verdict 判定本就只用标准库，未动。新增守卫测试：`python3 -I -S` shim 前置 PATH 跑探针必须通过，且先锚定 shim 本身确实藏掉 PyYAML。片段传递方式选 job output（片段实测 2830 字符，远低于 1MB 上限）。
- **关键决策与已否决方案**：传递用 job output + 随机 delimiter（不用 artifact——为 2.8KB 片段引 upload/download 两个 pinned action 不值得）；错误码沿用 `SOURCE-PROBE-SCRIPT-EXTRACT-FAILED`（片段未传入与上游提取失败是同一故障链，卡面失败可见性条款钉的就是这个字面量）。
- **下一步唯一动作**：单元③——sync job 新增提取步骤 + 结构测试（先红后绿）。
