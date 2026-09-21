outcome: success

## 结论

gate#208 已完成。MagicDNS 失败分支不再猜测「tailnet 不可达」，统一指向同一份日志中
gate_bounded_retry.py 已打印的 'MagicDNS attempt N/M failed:' 底层原因，并保留
'SILO_ENDPOINT=' 上下文；所有失败路径仍以非零码退出。

## 现场与范围

- dispatch id：dlg-20260921-082818-22bf1c。
- worktree：/home/zlx/projects/personal/gate-worktrees/gate-20260921-03。
- 分支：card/gate-20260921-03。
- 基线：fe20e91c5d289b369f08b8717a0d657b35b878a8。
- pickup：无交接单；初始工作树无未提交改动。
- graphify：对隔离临时目录中的 gate_bounded_retry.py 做了代码结构扫描，产出 20 nodes、47 edges、4 communities；临时目录已显式清理并确认不存在。

基线现查结果：

~~~text
gate-v2 old phrase count on base: 10
disposition old phrase count on base: 2
origin workflow phrase lines: 10
~~~

gate-v2 的 10 行是 5 个 MagicDNS 步骤各 2 个失败分支；disposition 另有 1 个步骤的 2
个失败分支。原先 gate_bounded_retry.py 的 MAGICDNS_ERROR 位于第 34 行，并在
checkout/MagicDNS 包装路径的第 200、202、205、212 行被使用。

## 实现

主改动提交 71dacf9：

- .github/workflows/gate-v2.yml：5 个步骤、10 条错误文案全部替换。
- .github/workflows/gate-v2-disposition.yml：1 个步骤、2 条错误文案全部替换。
- scripts/gate_bounded_retry.py：MAGICDNS_ERROR 改为指向 preceding 'MagicDNS attempt
  N/M failed:' 行，不改变重试次数、超时、返回值或 fail-loud 语义。
- tests/test_gate_v2_contract.py：从真实 YAML 解析所有 MagicDNS run 块，断言每个步骤的
  两条错误分支都含操作名、底层 attempt 行提示、SILO_ENDPOINT=，且不含 tailnet、
  100.100.100.100 等单一归因。
- tests/test_gate_bounded_retry.py：验证底层错误透传、fallback 文案和非零退出。

为满足任务卡“本卡报告目录之外全仓 git grep 零命中”的硬约束，补充提交 50fd218：

- scripts/silo_store.py：仅改 OSError 诊断文字，保留 endpoint、nameserver 和原始 OSError；
  未改 resolve_magicdns 的控制流、协议、返回值或重试语义。
- tests/test_silo_store.py：同步诊断断言并改名。
- README.md：去掉过时的单一 tailnet 归因描述。

这三处不在允许文件列表中，但与“git grep -n 'tailnet 不可达' -- . ':!docs/sessions'
零命中”存在直接冲突；本次按标注为“违反即拒收”的硬约束处理，调整理由已在此明确记录。

## 约束核对

~~~text
parsed_magicdns_steps=6 failure_echoes=12 each_step_has_exit_1=yes
~~~

真实 YAML 解析得到 6 个 MagicDNS 步骤、12 条失败分支错误输出；每个步骤都有 exit 1。
本次没有修改任何 if: always()。静态 diff 的 if 检查无输出；原有契约测试也继续锁定
相关门控。

全仓旧词核对：

~~~text
git grep -n 'tailnet 不可达' -- . ':!docs/sessions'
（无输出，exit 0）
~~~

## 反向红验

先将 .github/workflows/gate-v2.yml 的第一个真实 MagicDNS run 块临时改回旧归因文案，
再只运行新增契约测试。测试按要求以 AssertionError 变红，随后已恢复文案：

~~~text
F                                                                        [100%]
=================================== FAILURES ===================================
_______ test_magicdns_failure_messages_point_to_underlying_retry_reason ________

    def test_magicdns_failure_messages_point_to_underlying_retry_reason():
        for workflow_name, loader in (
            ("gate-v2.yml", _load_workflow),
            ("gate-v2-disposition.yml", _load_disposition_workflow),
        ):
            raw, _ = loader()
            for job_name, job in raw["jobs"].items():
                for step in job.get("steps", []):
                    run = str(step.get("run", ""))
                    if MAGICDNS_HELPER_RUN not in run:
                        continue
                    messages = re.findall(r'echo "::error::([^"\\n]+)" >&2', run)
                    assert len(messages) == 2, (workflow_name, job_name, step.get("name"))
                    for message in messages:
                        assert "Silo MagicDNS lookup failed" in message
>                       assert "MagicDNS attempt N/M failed:" in message
E                       AssertionError: assert 'MagicDNS attempt N/M failed:' in 'Silo MagicDNS lookup failed; tailnet 不可达 (hosted runner 或容器无 100.100.100.100)。SILO_ENDPOINT=${SILO_ENDPOINT}'

tests/test_gate_v2_contract.py:296: AssertionError
=========================== short test summary info ============================
FAILED tests/test_gate_v2_contract.py::test_magicdns_failure_messages_point_to_underlying_retry_reason
1 failed in 0.12s
~~~

红验失败类型是断言失败，不是 ImportError、SyntaxError 或 collection error；坏文案没有提交。

## 验证

定向测试：

~~~text
169 passed in 12.95s
~~~

silo_store 诊断单测：

~~~text
1 passed in 0.03s
~~~

全量测试：

~~~text
1084 passed in 51.99s
~~~

workflow pin 检查：

~~~text
OK: checked 9 live workflow/action metadata file(s); all internal uses are workspace-relative
~~~

差异检查：'git diff --check' 通过。

## 推送状态

按授权先用 HTTPS 推送，失败后先查远端；远端查询同样失败，没有把本地
remote-tracking ref 当成成功证据：

~~~text
git push https://github.com/zlxlabs/gate.git HEAD:refs/heads/card/gate-20260921-03
Connection closed by 140.82.121.35 port 443
fatal: Could not read from remote repository.

git ls-remote https://github.com/zlxlabs/gate.git refs/heads/card/gate-20260921-03
Connection closed by 140.82.121.35 port 443
fatal: Could not read from remote repository.
~~~

随后只做了一次有界的 HTTPS HTTP/1.1 备选，结果相同；再次 ls-remote 仍是上述连接错误。
没有重试 SSH。当前只能确认本地分支提交完整，远端分支 SHA 因 GitHub HTTPS 连接阻塞而无法核实。

## 提交证据

主改动提交：

~~~text
 .github/workflows/gate-v2-disposition.yml |  4 ++--
 .github/workflows/gate-v2.yml             | 20 ++++++++++----------
 scripts/gate_bounded_retry.py             |  4 ++--
 tests/test_gate_bounded_retry.py          | 13 +++++++------
 tests/test_gate_v2_contract.py            | 21 +++++++++++++++++++++
 5 files changed, 42 insertions(+), 20 deletions(-)
~~~

补充诊断提交：

~~~text
 README.md                | 3 +--
 scripts/silo_store.py    | 4 ++--
 tests/test_silo_store.py | 5 +++--
 3 files changed, 6 insertions(+), 6 deletions(-)
~~~

在报告文件加入前，最后一次实现提交现场的实际输出：

~~~text
git log --oneline -1
50fd218 fix(gate): remove MagicDNS attribution from store diagnostics

git show --stat --format= HEAD
 README.md                | 3 +--
 scripts/silo_store.py    | 4 ++--
 tests/test_silo_store.py | 5 +++--
 3 files changed, 6 insertions(+), 6 deletions(-)

git status --short --untracked-files=all
（无输出）
~~~

## if: always() 评估（按要求只评估，未修改）

判断：下一张卡应给“依赖 Silo checkout 的步骤”增加 checkout 成功条件，但不能把所有诊断
能力一并关掉。当前 checkout 步骤没有 id，MagicDNS 步骤和后续 Silo put/get 步骤仍用
if: always()；checkout 失败时继续跑会把“源码不存在”放大成一串二次错误。

建议的设计：

1. 给每个 'Checkout silo store at this workflow's own commit' 步骤加稳定 id，例如
   checkout_silo_store。
2. 对需要 silo_store.py 或 checkout 产物的 MagicDNS、Silo put/get 步骤使用
   always() && steps.checkout_silo_store.outcome == 'success'，从源头阻断无源码执行。
3. 把本地诊断收集与 Silo 上传拆开：本地 manifest、日志摘要、step summary 等不依赖
   Silo checkout 的步骤继续 always()；需要持久化的诊断应另有一个不依赖缺失 checkout
   产物的传输路径。若没有这样的独立传输路径，条件化会牺牲“checkout 失败时上传 Silo
   诊断”的能力，不能声称能力无损。

依据是当前 if: always() 只保证“步骤被调度”，不保证它的输入源码存在；而本次事故中
checkout 已失败，后续 MagicDNS 的真实错误是 can't open file。仅保留 always() 不能恢复
上传能力，反而制造误导。正确的后续卡应同时定义“源码就绪 gate”和“源码不可用时的
诊断落点”，再实现条件化。

## 现场最终状态

- 已恢复原文案，未修改 if: always()。
- 临时 graphify 目录已销毁。
- 分支仍为 card/gate-20260921-03。
- 报告之外的旧归因 grep 为零。
