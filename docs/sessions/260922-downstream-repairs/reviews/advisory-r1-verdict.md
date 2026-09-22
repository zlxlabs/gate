<!-- delegate-outcome: succeeded -->
## 结论

- `VERDICT: pass`；无 P1。P2 测试覆盖缺口接受不修，P3 step id 记 backlog。
failure-visibility: p2-only
- 固定审查范围：base `514f6c8cb40400123845a212c2844dc7846a966c` .. H0 `0cf822b8cb334861b7c804ad43abacd4c70c5941`。
- 全 diff：4 个文件、296 行新增；无实现外改动，`git diff --check` 通过。

## 不变式证据

- 摘要只输出固定白名单：review status 19 项与 gate-hub producer `SHADOW_STATUSES` 精确相等；delivery 仅 `created|updated|not_created`；所有非法/原始值被映射为固定枚举，未读取评论正文。
- 当前身份必须匹配文件名和 JSON 的 `repository_id/head_sha/run_id/run_attempt/reviewer`；缺失、坏 status、旧 head 的实跑结果均为 `unavailable/invalid`。
- review 与评论投递独立：scrub checkout 失败的有效 producer 事件仍显示 `available`，评论显示 `unknown`；不会由 review 成功推断投递成功。
- `always() && matrix.reviewer != '__none__'`、步骤顺序（post 后、Silo checkout 前）及失败路径均锁定；8 个 advisory summary 定向测试通过。
- `build_shadow_audit` 生成对象与 fixture 相等；按 producer `_write_event_atomically` 的序列化方式生成的 1037 字节与 fixture 字节一致。

## Findings

- P1：无。真实 scrub checkout failure 路径已执行，结果仍区分 review 与 comment；无数据丢失、静默放行或崩溃。
- P2：`tests/test_gate_v2_contract.py` 未锁定“有效 review、delivery 文件缺失、checkout/comment 均 success”组合及 `Delivery diagnostic: missing`。该组合可由空 advisory comment 真实触发；当前实现输出正确，后果只是未来回归可能丢失诊断区分，不改变门禁或泄露原文，接受不阻塞。
- P3：`advisory-terminal-summary` step id 没有下游 `steps.<id>` 消费者；无运行时后果，仅维护噪音，接受不阻塞。

## OCR 逐项复核

1. 缺 `shell: bash`：驳回；Linux OCR job 的构造均为 POSIX shell 语法，失败前提不成立。
2. producer/consumer 文件名漂移：驳回；真实 producer 函数、文件名函数、fixture 字节和消费者路径已比对，契约测试会变红。
3. delivery 枚举未来漂移：当前不可证伪，不构成 finding；现有 producer/消费者/测试集合一致。
4. Markdown 注入：驳回；摘要插值均先经过固定枚举，secret-token 测试通过。
5. 缺 delivery 成功组合：确认，P2，见 Findings。
6. helper 重复查找：驳回；job/step 改名会显式 `KeyError`/`StopIteration` 或既有 id 断言失败，不会静默误选。
7. 未消费 step id：确认，P3，见 Findings。

## 验证

`uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_v2_contract.py -k 'advisory_terminal_summary'` → `8 passed, 158 deselected`。
