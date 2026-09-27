# PR #257 独立审查 verdict

**审查对象：** `c58ec08c0dbf612b315cc4e5bb1187563d947fcc..24e2b7da7aa929385c587feef28de6a57b8d594e`（固定 SHA，不随分支推进）

**风险档：** personal

**裁决：** fail；发现 1 条 P1 和 1 条 P2。这里的 fail 是 review verdict，不代表本次审查任务失败。

## I1–I7 结论

| 不变量 | 结论 | 证据 |
|---|---|---|
| I1 | 通过 | `scripts/silo_exec.sh` 直接 `exec python3 "$SILO_STORE" "$@"`；新客户端没有 `uv`、PyPI 请求或运行期依赖解析路径。 |
| I2 | 违反 | F1：列表对有效对象键无条件 URL 解码，`list` 成功输出了不同的对象键。 |
| I3 | 通过 | 新增导入和函数内导入均为 Python 标准库。 |
| I4 | 违反 | F1：错误键以退出码 0 返回，属于静默错误结果。网络与服务错误仍走非零退出。 |
| I5 | 通过 | 请求路径把 bucket 放在 URL path 中；region 固定为 `us-east-1`；`https` 端点使用 `HTTPSConnection` 并保留配置端口。卡面给定的 MinIO/HTTPS/非默认端口运行环境下未见寻址退化。 |
| I6 | 通过 | 固定范围只改 `scripts/silo_exec.sh`、`scripts/silo_store.py` 和两份测试；没有改 workflow 调用点。 |
| I7 | 违反 | F2：新增的 endpoint scheme 错误消息会原样输出 URL；基线已有 MagicDNS 同类路径记入 backlog。 |

## Findings

### F1 — P1：列表把对象键中的字面百分号序列当作 URL 编码

- **位置：** `scripts/silo_store.py:320`（解析列表键的上下文为 `:315–326`）。
- **违反：** I2（CLI stdout 中的对象键不再与改前逐字一致）、I4（以成功退出码返回错误键）。
- **触发路径：** `put-dir` 保留相对文件名并上传对象；`list_objects_v2` 未在请求中发送 `encoding-type=url`，却对响应里的每个 `<Key>` 无条件调用 `unquote`。对象名中合法的字面 `%2F` 因而变成 `/`。
- **实测：** 从固定 H0 的 Git 对象导出客户端，在本地 S3 协议服务上执行真实 CLI `put-dir` 后执行 `list`。输入文件名为 `100%2Fdone.json`。producer 实际发出的 PUT path 是 `/ci-artifacts/d14/42/name/100%252Fdone.json`，服务存下的对象键是 `d14/42/name/100%2Fdone.json`；后续 LIST 请求不含 `encoding-type`，客户端却以退出码 `0` 输出 `d14/42/name/100/done.json`。这是 S3 XML 响应与 CLI 调用路径的端到端本地探针，不是 `--dry-run`。真实线上 Silo 当前是否已有 `%xx` 对象键，本次未测量；仓库 workflow 确实调用 `put-dir`，该子命令也未限制此类文件名。
- **P1 两问：** ①真实调用路径会触发吗？支持的 `put-dir → list` 路径已实测触发；线上对象中出现该类名字的频率未实测。②触发后果能否接受？不能；调用方收到成功状态和错误对象标识，破坏列表契约，属于 P1 红线“静默出错”。
- **协议依据：** [Amazon S3 ListObjectsV2 文档](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html)说明只有请求 `encoding-type` 才会编码响应键；[MinIO 响应生成代码](https://github.com/minio/minio/blob/master/cmd/api-response.go#L3308-L3444)按请求的 `encodingType` 编码对象名；[botocore handler](https://github.com/boto/botocore/blob/develop/botocore/handlers.py#L3486-L3608)会为 ListObjectsV2 自动请求 URL 编码，并在该自动编码响应中解码键。H0 没有保留这组请求/响应条件。

### F2 — P2：非法 endpoint 的错误消息会打印 URL userinfo

- **位置：** `scripts/silo_store.py:210`。
- **违反：** I7。
- **触发路径：** `connect()` 从环境读取 `SILO_ENDPOINT`；当 scheme 不合法且值含 `user:password@` 时，`fail()` 把完整 endpoint 插入 stderr。
- **实测：** 用固定 H0 子进程执行 `get`，设置 endpoint 为 `ftp://URL_USER_MARKER:URL_PASS_MARKER@minio.invalid:9000`，另设不同的 AWS access/secret 环境值。进程以退出码 `1` 失败，stderr 含两个 URL userinfo 标记，不含 AWS 环境值标记。
- **P1 两问：** ①真实调用路径会触发吗？带 userinfo 且 scheme 错误的配置已在本地子进程实测触发；本 worktree 没有线上 endpoint/凭据，生产配置是否存在此形态未实测，符合任务卡要求的 HTTPS endpoint 时此分支不触发。②触发后果能否接受？不能；错误日志暴露 URL userinfo。按 personal 风险档和 owner 管理的畸形配置路径定为 P2，不属于本仓 P1 三条红线。

## Backlog

- **P2 / I7，非本轮改动：** `scripts/silo_store.py:421–424` 的 MagicDNS `OSError` 分支也会打印完整 `SILO_ENDPOINT`。用固定 H0 代码并将 socket 错误替换为已知 `OSError` 探针，确认 `https://URL_USER_MARKER:URL_PASS_MARKER@minio.invalid:9000` 两个标记都会进入 stderr。实际生产 DNS 故障与 endpoint userinfo 组合未实测。若该畸形配置在实际使用中出现，凭据日志曝光不可接受；依 owner 管理配置和 personal 风险档记为 P2，且该代码在 base 已存在，不计入本轮 verdict findings。

## 初筛与验证记录

- OCR 前置扫描：`reviewed`，profile `minimax` / model `MiniMax-M3`，覆盖固定 SHA 全 diff。OCR 的异常栈、连接复用、测试断言和解释器版本建议没有映射到 I1–I7；URL userinfo 项按上面的独立探针重新核实，F1 由完整 CLI 路径探针发现。
- 未运行本仓全量测试套件；本卡是只读审查。只运行了针对 H0 固定 Git 源码的本地协议与日志行为探针，没有改生产代码或测试。
- 输入隔离偏差：一次过宽的 `git grep` 输出了历史 `docs/sessions` 报告片段。我立即停止读取这类材料；本 verdict 的 finding 证据来自固定 SHA 的代码/测试、任务卡、官方/上游文档和上述探针，不引用历史报告中的说法。完整执行报告会再次披露此偏差。

failure-visibility: p1-found
