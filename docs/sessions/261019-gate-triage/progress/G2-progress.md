# G2 进展（raw evidence 脱敏后上传）

## 段 1：红测试先行

- 当前阶段：implementing（TDD 红测试已落盘并确认变红）。
- 本段结论：新增 `tests/test_gate_v2_raw_evidence_scrub.py` 4 用例（TOKEN 脱敏上传、干净透传、非 UTF-8、脱敏器不可加载），在旧实现下 4/4 变红，且均为断言失败（非环境错误）；旧实现代表性红输出：`Primary raw evidence TOKEN scan failed ... reason=token_scan_blocked`（exit 1）。
- 关键决策与已否决方案：fixture 采用 `@@ ... token: synthetic-owner-token-abcdefghijklmnop123456` 形状——裸 `synthetic-owner-token` 不触发 TOKEN 正则（已实测），必须带 `token:` 前缀 + 20+ 位值才能既含 hunk 头特征又触发 TOKEN；否决「fixture 只放 ghp_ token」的方案（缺 #348 hunk 头形状，约束力不足）。
- 下一步唯一动作：改 workflow 步骤实现脱敏后上传。

## 段 2：实现与绿测试

- 当前阶段：implementing（实现完成，新测试转绿）。
- 本段结论：`Archive raw primary review evidence permanently` 改为：RUNNER_TEMP 下新建 mode 700 暂存目录（保持相对路径），逐文件 `scrub_outbound_text(text, runtime_values=runtime_values_from_environment())` 写入后 `put-dir` 只传暂存目录；输出 `raw-evidence-scrubbed categories=<排序|none> files=<改写>/<总数>`；删 `token_scan_blocked`（`grep -c` = 0）；非 UTF-8（严格 `decode("utf-8")`）与脱敏器加载/`ScrubError` 走 `GATE-ARCHIVE-DEFERRED reason=non_utf8_file/scrub_failed` 且不上传原文件；`tests/test_gate_v2_diagnostics_upload.py` 的旧 token-阻断断言同步反转为脱敏上传成功（含 manifest 按脱敏字节计），`put-failure`/`occupied` 改用合法 UTF-8 载荷以走到上传阶段。
- 关键决策与已否决方案：暂存目录创建失败也归 `scrub_failed`（fail-loud 且不上传，无静默）；内联脚本放 YAML（单消费者，无新文件，符合反熵条款）；否决「给 synthetic-* 开白名单」（设计已否决）与「猜测编码跳过非 UTF-8」（规范要求 fail-loud）。
- 下一步唯一动作：做上传原目录的反例红验。

## 段 3：反例红验与全量验收

- 当前阶段：implementing（红验闭环，全量绿）。
- 本段结论：真修复已提交（f0222d8）后，仅把 `--dir "$scrubbed_dir"` 改回 `--dir "$evidence_dir"`，字节断言变红：`assert b'[REDACTED:TOKEN]' in b'diff --git ... token: synthetic-owner-token-...'`（断言失败原文）；只还原该一处后 4/4 转绿；`test_gate_v2_silo_region_guard` 绿；全量 `1872 passed` 无继承红；`python3 scripts/check_pinned_uses.py` 绿；`grep -c token_scan_blocked` = 0，`grep -c raw-evidence-scrubbed` = 1。
- 关键决策与已否决方案：红验前后未用 `git stash`，改坏前后以提交隔离；否决「跳过全量只跑单文件」（约束要求全量绿或贴继承红证据）。
- 下一步唯一动作：写报告并 push。
