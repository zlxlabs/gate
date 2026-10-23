# A-progress：disposition 反证改从宿主镜像取码（gate#373）

## 里程碑 1：设计文档入库

- 当前阶段：implementing
- 本段结论：把主脑设计原文复制进 `docs/sessions/261023-disposition-mirror/design.md`，cmp 与咨询仓一致。
- 关键决策与已否决方案：文档一字不改；异议只进 report。
- 下一步唯一动作：改 `gate-v2-disposition.yml` 按 SOURCE-MODE 分流，并加夹具 E2E。

## 里程碑 2：service 取码 + 同 step 持锁复跑

- 当前阶段：implementing
- 本段结论：取码 step 在 service 下只调 `git-source-prepare`（absent 再 prepare base），origin 仍 fetch 但 124 报 `COUNTEREVIDENCE-SOURCE-TIMEOUT`。签发 step 在 service 下 `flock -s` 包住 demand 核对与 `issue_receipt.py`，argv 不改，镜像通过 `--repo-dir` 与 `GIT_CONFIG_*`/`GIT_NO_REPLACE_OBJECTS` 传入。
- 关键决策与已否决方案：prepare 放取码 step、锁与复跑放签发 step（锁定决策「锁与复跑同 step」+ E2E 抽出两个 step）；不把 prepare 并进签发、不跨 step 持锁、不加重试、不改作者 argv。
- 下一步唯一动作：跑 Narrow-Verify 与红验。

## 里程碑 3：红验 + 收紧 prepare 失败断言

- 当前阶段：implementing
- 本段结论：三条红验均以 AssertionError 转红后只还原注入行。prepare 失败原断言是恒真（协议行自带 SOURCE-SERVICE-UNAVAILABLE），已改成要求连续字面量 COUNTEREVIDENCE-SOURCE-SERVICE-UNAVAILABLE。
- 关键决策与已否决方案：红验注入只改判据行。TIMEOUT 改成 TIMED-OUT 后 `test_origin_fetch_timeout_is_counterevidence_source_timeout` 红：`assert 'COUNTEREVIDENCE-SOURCE-TIMEOUT' in '::error::COUNTEREVIDENCE-SOURCE-TIMED-OUT: git fetch --depth 1 exceeded 300s\n'`。映射 3→SOURCE-PREPARE-FAILED 后 `test_service_prepare_unavailable_does_not_touch_origin` 红：`assert 'COUNTEREVIDENCE-SOURCE-SERVICE-UNAVAILABLE' in '::error::COUNTEREVIDENCE-SOURCE-PREPARE-FAILED: exit=3 line=GIT-SOURCE-PREPARE-V1 {"code": "SOURCE-SERVICE-UNAVAILABL...`。GIT_NO_REPLACE_OBJECTS=0 后 present 红：`assert False`（grep 事件的 GIT_NO_REPLACE_OBJECTS 不再是 1）。
- 下一步唯一动作：全量 pytest、scope check、push、开 PR。
