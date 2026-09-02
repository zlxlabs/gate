# DESIGN-note：gate-v2 三处「结论可信度」修补——draft 竞态假绿、ledger 输入上传被吞、advisory 污染 run 级结论

会话：三仓 issue 周清 2026-09-02（第二轮，fable5 主脑）。落盘路径 `docs/sessions/260902-gate-triage/design.md`，随首张实现卡（gate#110）的 PR 进 git。

## 目标

合并并 pin bump 后，接入仓的 PR 上：① 标 ready 时若 ready_for_review run 被同 SHA 的 synchronize run 取消，幸存 run 不再给 `gate / gate` 绿，而是红并在面板写明「重新触发」命令；② quality 上传 review-ledger-input 遇网络瞬断自动重试一次，仍失败时 `gate / ledger` 的缺失文案直接指回「quality 上传失败」，不再出现「quality 绿 + ledger 说没有 artifact」这对矛盾；③ advisory 的 `gate / ocr` 失败不再把 workflow run 级 conclusion 拖成 failure，按 run 终态判读的下游不再假红。

## 非目标

- 不改 concurrency group 表达式（`tests/test_gate_v2_contract.py:419-494` 字节级锁死的两把 job 级锁形态不动）。
- 不让 quality 因上传失败变红（上传是 ledger 的前置，不是门禁的前置）；不把 ledger input 在 draft 下改成 optional。
- gate#105 第一条（汇总 job 被最慢 quality 绑架）本批不做：每个候选方案都改变绿路径时序或引入第二个 `gate / gate` 写入者，需 owner 拍板（见文末「待裁决」）。
- 不改 ocr/advisory 的判定语义，不动 registry。

## 方案要点与已否决方案

- **要点**：
  1. gate#110：aggregator `evaluate()` 在 `primary_result == skipped` 且事件载荷 `draft=true` 的分支上，新增一个输入 `pr_draft_now`（由 `main()` 在该分支下用 `GET /repos/{repo}/pulls/{n}` 读当前 `draft`）。载荷 draft 而当前非 draft → 终态 `review_unavailable / review_expected_stale`（红，面板给重触发命令）；API 读不到 → `review_unavailable / pr_state_unverifiable`（红，fail-closed）；仍是 draft → 维持 `expected_skip`。只在这一分支多一次 API 调用，正常 run 零额外请求。
  2. gate#107：上传步加 `id`，紧随一步 `if: steps.<id>.outcome == 'failure'` 的同名重试（`overwrite: true`，artifact 名不变，保住 ledger 解析器的 attempt 后缀契约）；再一步把最终结果写成 quality 的 job output `ledger_input_upload`；ledger 解析器读 `needs.quality.outputs.ledger_input_upload`，缺失文案带上它。
  3. gate#105 二：`ocr` job 加 job 级 `continue-on-error: true`（全仓首个 job 级 COE；`needs.ocr.*` 无任何消费者，聚合与 notify 不受影响），文件头注释补一句判读契约。
- **已否决**：
  - #110 改 concurrency group 加 `event.action` 让 ready run 不被取消——两条 run 并行都产出 `gate / gate`，rollup 取哪条不可控，且动到字节级锁死的锁形态。
  - #110 让 primary job 自己在 `if:` 里查 API——`if:` 是静态表达式，做不到；改成「总是起 job 再第一步判断」会让每个 draft push 都占一个 self-hosted 槽。
  - #110 API 失败时 fail-open（维持 expected_skip）——竞态 + 瞬断同时发生虽罕见，但那正是「静默出错」红线；draft 期红不阻塞任何合并，代价只是一次 rerun。
  - #107 让 quality 直接红——把可观测性丢失升级成门禁失败，方向反了。
  - #107 重试时换 artifact 名后缀——`INPUT_PREFIX` 以 `-<attempt>` 结尾，解析器按 attempt 选候选，后缀会让它选不到。
  - #107 draft 下 input 改 optional——把丢失藏起来，与 #103 的教训相反。

## 关键不变式

1. **载荷 draft=true 而 PR 当前非 draft 的 run，`gate / gate` 不得为 pass/skipped。** 代码：`aggregate.py::evaluate` 新分支；锁死：`tests/test_gate_aggregator.py` 判定矩阵新增三格 + CLI 级测试（monkeypatch fetch 返回 False → exit 1 且 summary 含重触发命令）。
2. **正常 run（primary 未 skipped，或 skipped 但非 draft 原因）不多发任何 GitHub API 请求。** 代码：`main()` 只在 `primary_result == skipped and is_draft` 时调用 fetch；锁死：CLI 测试对非 draft 场景 monkeypatch fetch 为「调用即 raise」并断言不被调用。
3. **上传重试不改变 artifact 名，ledger 解析器仍按 `<prefix>-<attempt>` 选到它。** 代码：gate-v2.yml 重试步 `with.name` 与首步逐字相同 + `overwrite: true`；锁死：`test_gate_v2_contract.py` 断言两步 `with.name`/`with.path` 相等。
4. **上传两次都失败时，ledger 的缺失文案含 quality 的上传结论字面量。** 代码：quality `outputs.ledger_input_upload` → ledger 解析器 env → `select_artifact` 的 missing_message；锁死：`_run_ledger_resolver` 场景「无 input artifact + env=failure」断言 SystemExit 文案含 `quality upload outcome: failure`。
5. **quality / gate / ledger 三个 job 仍无 job 级 continue-on-error；只有 ocr 有。** 锁死：契约测试遍历 jobs 断言集合恒等于 `{"ocr"}`。
6. **两把 cancel 锁与两把 writer 锁形态字节不变。** 既有测试 `test_gate_v2_contract.py:435/440/461` 继续绿即为证。

## 验收路径

1. 入口：接入仓（推荐 zlxlabs/agent-config，样本最多）的一个 draft PR，pin bump 到本批 SHA 之后。
2. 步骤：同一 head 上先 `git push` 再几秒内 `gh pr ready`；观察两条 run 中哪条被取消。若幸存的是 synchronize run（复现 #110 三例形态），读其 `gate / gate` job 结论与面板评论。
3. 预期：`gate / gate` 为 failure，面板含 `review_expected_stale` 与 `gh pr ready --undo && gh pr ready`；重触发后新 run 的 primary 真跑。若竞态没复现（幸存的是 ready run），记录一次并改天再试，不以「没复现」当验过。
4. #107 / #105 二的入口层证据只能等自然发生：下一次 `Failed to FinalizeArtifact` 出现时看重试步是否接住、或 ledger 文案是否带 quality 结论；下一次 ocr job failure 时用 jobs API 核 run 级 conclusion ≠ failure。合并时在 issue 上明写「等首个自然样本」，不把「合并了」写成「验过了」。

## 待裁决（gate#105 第一条，owner）

primary 早失败时让 `gate / gate` 早出结论，候选三条，各有代价：
- A. `quality` 改为 `needs: [primary]` 且 `if: needs.primary.result != 'failure'`：红路径省约 13 min，绿路径每次多等 primary 的 1.5–3 min（agent-config 平均墙钟 19 min → 约 21 min）。
- B. `gate` 只 `needs: [primary]`，primary 通过时在 job 内轮询 jobs API 等 quality 完成：不改绿路径时序，但占一个 control-plane 槽最长 15 min，且 gate job 的 8 min timeout 要放宽。
- C. 不动 `gate / gate`，加一个 `needs: [primary] / if: failure()` 的早报 job 只发飞书红卡与面板一行「primary 已判红，等 quality 收尾」：人早知道，check 结论时刻不变，但面板多一个写入者。
推荐 A 仅当 primary 失败率 > 15%；否则 C。需 owner 决定或给出失败率数据。
