# PR313 固定提交独立审查结论

审查对象：`f4213194f162062bc22d57cd6ac5589e97d920af..87de16570bf22cbce9055b3bb48f552b2b13b6b8`；risk-tier：personal。

**结论：有 1 项 P2，非阻断交付；P1/P3 为零。** 仅审固定 H0。未改生产代码/测试/其他 PR，未改 PR 状态、默认分支或 issue；未运行生产 PUT、CI、部署。

failure-visibility: p2-only

OCR 前置：`skipped`，完整 JSON envelope `status=skipped`，`reason=primary=leg_timeout; backup:deepseek=leg_timeout`。此状态不代表 clean；独立审查已继续。

## Findings

### R313-P2-01 — 新响应元数据字段可把 secret 写入 stderr

- 严重度：P2。阻断交付：否。personal 单 owner 部署中，没有证据表明当前 Silo 会正常把凭据放入响应头，也没有非 owner 读日志路径；但远端响应值未经过凭据过滤，仍违反任务卡不变式 3 的 stderr 不泄 secret 约束。
- 位置：`scripts/silo_store.py:118–138`（`_diag_ascii` 与响应头发布）。
- 触发证据：在 base scratch 中用 `ThreadingHTTPServer` 对真实 CLI 子进程返回 `HTTP 400`、`Code=IncompleteBody`、`Server=<AWS secret 哨兵>`。base 的 stderr 不含该哨兵；H0 输出一条事件，`resp_server` 等于该哨兵。`_diag_ascii` 只剔除非可打印 ASCII 并截到 128 字符，无法区分普通元数据与凭据。相同路径也用于 request-id、id2 和 `s3_error_code`。XML Code 哨兵也进入 H0 新 JSON 字段；但 base 的旧 `S3Error` 人类文案本已输出 Code，因此不另列为新增泄漏 finding。
- 实际使用场景：失败事件写入 stderr，任务卡指定的消费者是 Actions 失败日志；上游或中间层返回含凭据的允许名单头时，凭据会随诊断事件进入日志。建议后续限制这些远端值的可接受格式，或在无法确认安全时省略字段。

## 覆盖与证据

- 全量差异：base..H0 共 4 个文件、400 行新增、3 行删除；读设计契约，逐项检查 `scripts/silo_store.py` 与新增 `tests/test_silo_store.py`。执行进度文档未作为行为证据。`git diff --check base H0` 无输出。
- 消费者：`cx references --name S3Error` 命中 4 个同文件引用；`_raise_for_status` 的异常分别由 put、get、list 下载分支消费。get/list 的结构化 Code 映射仍读 `response['Error']['Code']`；错误文案由状态码/Code 构成，仍清楚表达失败。`S3Error.response` 的 Code/Message 形状未改。
- base 红验：使用 `scratch-worktree.sh <repo> <base-sha> -- bash -c <copy H0 test file; uv run ... pytest -k ...>`，只把 H0 的 `tests/test_silo_store.py` 复制到 base scratch；实际 cwd 为隔离 scratch worktree，SHA `f4213194f162062bc22d57cd6ac5589e97d920af`。结果 `2 failed, 32 deselected`，两处均为预期 `AssertionError`（旧错误文案泄 Message、无诊断事件），不是环境/导入错误。完整命令与实际 cwd 记在派发完整报告。
- H0 仓内测试：`uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_silo_store.py`，cwd 为本审 worktree，SHA H0：`34 passed`。`uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_no_runtime_install.py::test_stdlib_client_end_to_end_without_package_cache`，同 cwd/SHA：`1 passed`。
- 真实 producer 对照：ThreadingHTTPServer 接收 base 与 H0 的真实 CLI 子进程请求；scratch SHA 为 base，同时 H0 CLI 使用固定 H0 脚本。相同固定文件名/内容下，base 与 H0 成功 stdout bytes hex 均为 `64312f312f646961672f7061796c6f61642e62696e0a`；请求行、wire body SHA-256 (`93d10cbfadd3d2411cb6a17adf50edf1698af1a6cb845b783f9c356bb2c670c1`)、非时间相关签名头相同；样本恰处于同一秒，Authorization 与 `x-amz-date` 也逐字相同。成功 stderr 均为空，退出码为 0；client timeout 为 60 秒。完整命令和实际 cwd 记在派发完整报告。
- H0 本地黑盒：ThreadingHTTPServer + 子进程覆盖 send 拒绝连接、recv_headers 断连、recv_body 短读、HTTP 400 XML、502 HTML、长 Server 头和空 PUT body；每次失败均 exit 1 且恰一诊断 JSON 事件，exception 阶段分别为 `send`、`recv_headers`、`recv_body`，HTTP 错误为 `status`。普通 400 字段含 `IncompleteBody` 和白名单响应头；HTML/body/message 哨兵未进入 stderr；空 body 事件 `body_len=0` 且 `declared_content_length=null`，wire Content-Length 为 `0`。多对象 `put-dir` 在第一项成功、第二项 400 时实际发出两次请求，第二次事件 `request_seq=2`；请求 body 长度和 key 与实际发送项相符。scratch SHA H0；完整命令和实际 cwd 记在派发完整报告。
- 未做线上故障归因；回环只证明本地请求/事件字段。卡面给出的主干 CI 基线不可用（`gh api request failed`），故继承红未能判定；未运行 CI。

## 未覆盖风险

没有验证真实 Silo 服务/反代是否会返回含凭据的响应元数据，也没有验证 Actions 日志访问策略；本地 loopback 不能代表线上部署状态。除此之外，本次固定 diff 未发现 P1/P3。
