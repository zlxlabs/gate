# F311 Silo 请求失败最小脱敏诊断

## 边界

只在 `S3Client._request` 一次请求生命周期观测。触发=调用者既有 `status != 200`
或既有 `except Exception`。不改传输、成功判据（仍仅 200）、超时、重试、exit、
成功 stdout。非目标：IncompleteBody 修复、workflow/aggregator、silo_exec、接收端。
#311/#312 保持 OPEN。

## 字段（stderr：`SILO-REQUEST-FAILED` + 单行 JSON，一失败一事件）

event=silo_request_failed schema=1 method
phase∈{send,recv_headers,recv_body,status}
body_len
declared_content_length=客户端显式 Content-Length，无该头 null（http.client 补头是另一事实）
request_send_completed=仅 `request()` 返回
elapsed_ms=monotonic client_timeout_s=`S3_TIMEOUT_SECONDS`
http_status s3_error_code=XML Code（HTML 则 null） exception_type=类名
resp_server / resp_amz_request_id / resp_amz_id_2 / resp_content_length
request_seq=进程递增（put-dir 多对象）。缺失头/字段为 null。
响应头与 Code：可打印 ASCII 且 ≤128。禁止 XML Message、HTML/raw err、
access/secret/Authorization/query/key/path/body。

## 不变式来源

- 成功判据 `status != 200`：现 put/get/list_objects_v2。
- 空 body 不写 Content-Length：现 `if body:`。
- 异常出口 `fail` + `finally: close`：现 `_request`。
- 消费者=运维 Actions 失败日志（接收端无 accesslog）。send 返回≠对端收到；
  Server 可被反代改写。回环只锁字段契约，不证明线上已定位或已修复。

## 失败文案收紧（非成功字节兼容）

`Silo request failed` 去掉 raw `{err}`；`S3Error` 字符串改为 `status code`，
不再带 XML Message / HTML 片段。
