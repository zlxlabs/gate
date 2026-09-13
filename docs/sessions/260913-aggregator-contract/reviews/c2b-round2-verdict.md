# c2b 独立对抗评审第二轮 verdict：_fetch_pr_draft 语义边界

## 结论

**pass（非阻塞，P1=0，P2=0，P3=0）**。固定审查对象为
55f50021a939178f35bdcdf9db4a6798d5e959f4..card/gate-20260913-11，包含
72d0132 与 2722804，仅改动 .github/actions/gate-aggregator/aggregate.py
和 tests/test_gate_aggregator.py。第二轮重点验证了异常可达性、3.12 栈残余、
真实 bytes 生产边界、旧测试变异约束力及对抗输入；未发现新的 finding。

## OCR 前置扫描

已运行 ocr-review，结果为：

    {"status":"reviewed","profile":"minimax","model":"MiniMax-M3",
     "cli_status":"complete","coverage":"complete",
     "verify":{"verify_status":"partial","counts":{"total":2,"verified":1,
     "confirmed":0,"refuted":0,"unverifiable":1,"unverified":1}}}

OCR 给出两条 low 级测试建议，均未转为本仓 finding：其一声称深嵌套用例不存在，
但固定 H0 的 tests/test_gate_aggregator.py:982-1003 确实存在；且删除
except Exception 的变异后该路径会转红。其二声称 json.loads(bytes) 不会抛
UnicodeDecodeError，该事实判断被 Python 3.12 实测反驳（见第 4 节）。工具严重度
只是输入，不改变本仓 personal 风险判定。

## 1. except 顺序与可达性

结论：没有 retryable 类型错误地落入泛化 except Exception，也没有本应直接
fail-closed 的 HTTP 错误反而被重试。

固定目标源码 aggregate.py:1353-1360 顺序是：

    1353 except urllib.error.HTTPError: return None
    1355 except _RETRYABLE_CONNECTION_ERRORS:
    1356     ...
    1359 except Exception: return None

HTTPError 是 URLError 的子类，因此显式 HTTP 分支在 retryable 元组前，
HTTP 404/其他 HTTP 错误不会因继承关系被重试。元组定义在
aggregate.py:156-163，包含 URLError、ssl.SSLError、ConnectionResetError、
http.client.IncompleteRead、TimeoutError、socket.timeout；其中
socket.timeout is TimeoutError == True，只是重复枚举同一类型，不改变可达性。
Python 3.12 实测输出：

    HTTPError_is_URLError= True
    socket.timeout_is_TimeoutError= True

因此每个元组成员均在泛化分支前匹配；未列出的普通 Exception 才进入新分支，
符合本轮按来源划边界的锁定决策。

## 2. 循环变量残留语义

结论：按当前常量 PR_DRAFT_FETCH_ATTEMPTS = 3，不存在循环正常结束、
没有 break/return 却携带上一轮 payload 落入形状校验的路径。

源码 aggregate.py:1348-1363 中：

- payload 每次调用先初始化为 None；
- _github_json 正常返回后立即在 :1352 执行 break；
- HTTP、最终一次 retryable、泛化异常均直接 return；
- retryable 非最终次只 sleep 后进入下一次；
- 只有成功 break 后才会执行 :1361-1363 的形状检查/返回。

额外用 Python 3.12 做了边界探针，将内存中的尝试次数临时设为 0（未改文件）：

    zero_attempt_loop_result= None calls= 0 initial_payload=None

即使人为制造空 range，形状校验看到的也是初始 None，不是上一轮残留。
正常生产常量下该空循环路径不可达。

## 3. 3.12 的 RecursionError 与终态完整性

结论：RecursionError 被新泛化分支捕获后，Python 3.12 栈状态足以继续执行
evaluate()、_finish() 和终态写入；实际文件非空且完整可解析。

探针直接导入目标提交的生产模块，以 _github_request -> bytes 为唯一桩点，
输入 b"[" * 10000 + b"]" * 10000，调用完整 main()，参数为
primary_result=skipped、is_draft=true、有效 token/PR 和临时输出路径。
命令使用 Python 3.12：

    rc= 1
    summary_exists= True summary_bytes= 591 summary_ends_newline= True
    terminal_exists= True terminal_bytes= 732 terminal_ends_newline= True
    terminal_json_parse= True
    reason_code= pr_state_unverifiable gate_result= unavailable
    summary_has_reason= True summary_has_error_text= True

对应源码链为 aggregate.py:1330-1332 的真实 json.loads(raw)，
:2331 调用复核，随后 :2353-2369 进入 _finish，:2196-2200
以临时文件写入并 replace。没有发现二次栈耗尽、零字节或截断 JSON。

目标分支指定测试也在 Python 3.12 下通过：

    PYTHONDONTWRITEBYTECODE=1 uv run --python 3.12 --with \
    pytest,PyYAML,diff-cover,coverage python -m pytest \
    tests/test_gate_aggregator.py -q
    250 passed in 2.64s

## 4. 跨边界真实性与用例隔离

结论：深嵌套用例确实走生产同款 bytes -> json.loads 路径；它与第一轮端到端
用例使用同一个边界桩点，但不是同一个测试，pytest 的 monkeypatch 按测试恢复，
两者无互相干扰。

- 第一轮端到端用例在 tests/test_gate_aggregator.py:957-979，
  monkeypatch.setattr(AGG, "_github_request", lambda ...: b"<html>...")。
- 本轮深嵌套用例在 :982-1003，同样只桩 _github_request，返回真实 bytes，
  没有桩 _github_json。
- 生产链在 aggregate.py:1330-1332 明确由 _github_request 返回 bytes 后调用
  json.loads(raw)。

Python 3.12 的直接生产解析探针输出：

    json.loads bytes -> UnicodeDecodeError 'utf-8' codec can't decode byte 0xff in position 0
    json.loads bytes -> JSONDecodeError Expecting value
    json.loads bytes -> RecursionError maximum recursion depth exceeded while decoding a JSON array from a unicode string
    fetch_case= invalid_utf8_bytes result= None
    fetch_case= deep_nested_json result= None

两个端到端用例一起运行的实际输出为：

    .. [100%]
    2 passed, 248 deselected in 0.27s

## 5. 两个提交合起来的净效果与三条旧用例变异

结论：72d0132 添加的三个用例在 2722804 删除显式类型元组、改为
except Exception 后仍然有约束力；逐个删掉该泛化分支后，三条均转红，故不存在
“全部恒真”的 P1。

变异只在独立临时 worktree 中进行，注入前以
sed -n '1348,1364p' 确认目标函数；注入后同一段源码中不再有
except Exception。目标分支 worktree 与本审 worktree 均保持 clean，临时 worktree
已删除，未污染对方现场。三条测试均使用 Python 3.12：

    test_fetch_pr_draft_malformed_json_fails_closed:
    FAILED ... JSONDecodeError ... aggregate.py:1351
    1 failed, 249 deselected in 0.33s

    test_fetch_pr_draft_invalid_utf8_payload_fails_closed:
    FAILED ... UnicodeDecodeError ... aggregate.py:1351
    1 failed, 249 deselected in 0.33s

    test_main_malformed_pr_draft_response_writes_unverifiable_terminal:
    FAILED ... JSONDecodeError ... aggregate.py:1332
    1 failed, 249 deselected in 0.37s

第三条的栈明确落在真实 json.loads(raw)，不是测试桩提前短路；三条都红，说明
旧契约测试没有被本轮改动架空。

## 6. 对抗尝试

构造并运行了以下 PR draft 复核输入，均未让 _fetch_pr_draft 崩溃或返回错误
布尔值：

    fetch_case= empty_body result= None
    fetch_case= top_level_array result= None
    fetch_case= draft_string result= None
    fetch_case= invalid_utf8_bytes result= None
    fetch_case= malformed_json_bytes result= None
    fetch_case= deep_nested_json result= None
    fetch_case= unexpected_runtime_exception result= None
    connection_exhaustion_result= None calls= 3

其中 unexpected_runtime_exception 是具体的普通 ValueError，验证新来源边界；
连接异常验证仍是 3 次调用后 fail-closed。第 3 节的完整 main() 探针进一步确认
终态文件完整。没有把 KeyboardInterrupt/SystemExit 等 BaseException
列为 finding；它们不属于该捕获契约，且没有生产路径会在此处主动产生它们。

## Findings 与收口

本轮无 P1/P2/P3 finding。个人档两问判定因此没有待分诊项；第一轮记录的深嵌套
RecursionError 缺口已由 2722804 的泛化捕获和 3.12 端到端实测闭合。

git diff --check 55f50021a939178f35bdcdf9db4a6798d5e959f4..card/gate-20260913-11
无输出且退出码 0。目标分支 worktree 与本审 worktree 最终均 clean。

outcome: pass
