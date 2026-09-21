outcome: success

## 结论

gate 自有 Silo 客户端现在只有一个执行入口 `scripts/silo_exec.sh`：入口要求 `RUNNER_TEMP` 和 `SILO_STORE`，保留中文 uv 缺失错误并 fail loud，切换到 `cd "$RUNNER_TEMP"` 后执行 `uv run --no-project --python 3.12 --with boto3 -- python3 "$SILO_STORE" "$@"`，使用 `exec` 原样透传退出码。

`gate-v2.yml`、`gate-v2-disposition.yml` 的 Silo 子命令均改为 `"$SILO_EXEC"`；5 个 job 的 `SILO_STORE` / `SILO_EXEC` 均为 `${{ github.workspace }}/...` 绝对路径。所有包含 `scripts/silo_store.py` 的 sparse 清单都加入了 `scripts/silo_exec.sh`。diff-cover advisory 保持消费仓 cwd，但其 uv 调用显式加入 `--no-project`。

## 交付提交

- `7f1d14f feat: add isolated silo execution wrapper`
- `4c762db fix: isolate gate-owned silo and diff tools`
- `a7ab2c1 test: lock silo wrapper fail-loud environment contract`
- `e870cf3 test: align v2 contracts with silo wrapper`

最后一个实现验收快照（报告文件写入前）如下：

```text
$ git log --oneline -1
e870cf3 test: align v2 contracts with silo wrapper

$ git show --stat --format= HEAD
commit e870cf3 test: align v2 contracts with silo wrapper

 tests/test_gate_v2_contract.py           | 46 ++++++++++++++++++++------------
 tests/test_gate_v2_diagnostics_upload.py |  2 +-
 2 files changed, 30 insertions(+), 18 deletions(-)

$ git status --short --untracked-files=all
(无输出)
```

## 验证结果

- `uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_tool_isolation.py`：7 passed。
- 相关既有 v2 合约集合（含新测试）：166 passed。
- `uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q`：`1083 passed in 40.95s`。
- `python3 scripts/check_pinned_uses.py`：`OK: checked 9 live workflow/action metadata file(s); all internal uses are workspace-relative`。
- `bash -n scripts/silo_exec.sh`、`git diff --check`：通过。
- `git ls-files -s scripts/silo_exec.sh`：`100755 1927f510686aa11e2629ce7c7303cb15fc304d5a 0 scripts/silo_exec.sh`。

基线与成品计数：

```text
$ git show origin/main:.github/workflows/gate-v2.yml | grep -c 'uv run --python 3.12 --with boto3'
16
$ git show origin/main:.github/workflows/gate-v2-disposition.yml | grep -c 'uv run --python 3.12 --with boto3'
2
$ grep -c 'uv run' .github/workflows/gate-v2.yml
1
$ grep -c 'uv run' .github/workflows/gate-v2-disposition.yml
0
```

基线实测值是 gate-v2 的 16 条 Silo 行（任务卡中的 17 与当前 `origin/main` 实际输出不一致）；成品 gate-v2 文本中仍有 2 次 `uv run`，但它们位于同一条消费仓测试条件行的 frozen / non-frozen 两个分支，因此 `grep -c` 按物理行返回 1。新契约按文本出现次数断言为 2，且不允许任何 `python3 "$SILO_STORE"` 直接调用。

## 真实 uv 双向实测

临时目录 `/tmp/gate206-uv-eSfXmw` 已在验证后销毁。

新 wrapper 的真实命令行，cwd 是含毒 `pyproject.toml` 的目录，Silo 脚本只打印 argv：

```text
SILO_STUB ['probe']
```

手工去掉 `--no-project` 的真实命令输出原文：

```text
Using CPython 3.12.3 interpreter at: /usr/bin/python3.12
Creating virtual environment at: .venv
warning: No `requires-python` value found in the workspace. Defaulting to `>=3.12`.
   Building aiohttp==3.8.5
  × Failed to build `aiohttp==3.8.5`
  ├─▶ The build backend returned an error
  ╰─▶ Call to `setuptools.build_meta.build_wheel` failed (exit status: 1)

      [stdout]
      running bdist_wheel
      running build
      running build_py
      running egg_info
      writing aiohttp.egg-info/PKG-INFO
      writing dependency_links to aiohttp.egg-info/dependency_links.txt
      writing requirements to aiohttp.egg-info/requires.txt
      writing top-level names to aiohttp.egg-info/top_level.txt
      reading manifest file 'aiohttp.egg-info/SOURCES.txt'
      reading manifest template 'MANIFEST.in'
      adding license file 'LICENSE.txt'
      writing manifest file 'aiohttp.egg-info/SOURCES.txt'
      running build_ext
      building 'aiohttp._websocket' extension
      x86_64-linux-gnu-gcc -fno-strict-overflow -Wsign-compare -DNDEBUG -g
      -O2 -Wall -fPIC -I/home/zlx/.cache/uv/builds-v0/.tmpDwI5Jb/include
      -I/usr/include/python3.12 -c aiohttp/_websocket.c -o
      build/temp.linux-x86_64-cpython-312/aiohttp/_websocket.o

      [stderr]
      *********************
      * Accelerated build *
      *********************
      /home/zlx/.cache/uv/builds-v0/.tmpDwI5Jb/lib/python3.12/site-packages/setuptools/dist.py:765:
      SetuptoolsDeprecationWarning: License classifiers are deprecated.
      !!

      
      ********************************************************************************
              Please consider removing the following classifiers in favor of a
              SPDX license expression:

              License :: OSI Approved :: Apache Software License

              See
              https://packaging.python.org/en/latest/guides/writing-pyproject-toml/#license
              for details.

      ********************************************************************************

      !!
        self._finalize_license_expression()
      warning: no files found matching 'aiohttp' anywhere in distribution
      warning: no previously-included files matching '*.pyc' found anywhere
      warning: no previously-included files matching '*.pyd' found anywhere
      warning: no previously-included files matching '*.so' found anywhere
      warning: no previously-included files matching '*.lib' found anywhere
      warning: no previously-included files matching '*.dll' found anywhere
      warning: no previously-included files matching '*.a' found anywhere
      warning: no previously-included files matching '*.obj' found anywhere
      warning: no previously-included files found matching 'aiohttp/*.html'
      no previously-included directories found matching 'docs/_build'
      aiohttp/_websocket.c: In function
      ‘__pyx_pf_7aiohttp_10_websocket__websocket_mask_cython’:
      aiohttp/_websocket.c:1475:3: warning: ‘Py_OptimizeFlag’ is deprecated
      [-Wdeprecated-declarations]
       1475 |   if (unlikely(!Py_OptimizeFlag)) {
            |   ^~
      In file included from /usr/include/python3.12/Python.h:48,
                       from aiohttp/_websocket.c:6:
      /usr/include/python3.12/cpython/pydebug.h:13:37: note: declared here
      Py_DEPRECATED(3.12) PyAPI_DATA(int) ma_version_tag;
      aiohttp/_websocket.c: In function ‘__Pyx_get_tp_dict_version’:
      aiohttp/_websocket.c:2680:5: warning: ‘ma_version_tag’ is deprecated
      [-Wdeprecated-declarations]
       2680 |     return likely(dict) ? __PYX_GET_DICT_VERSION(dict) : 0;
            |     ^~
      /usr/include/python3.12/cpython/dictobject.h:90:
      /usr/include/python3.12/cpython/dictobject.h:95:3: note: declared here
      Py_DEPRECATED(3.12) uint64_t ma_version_tag;
      aiohttp/_websocket.c: In function ‘__Pyx_get_object_dict_version’:
      aiohttp/_websocket.c:2692:5: warning: ‘ma_version_tag’ is deprecated
      [-Wdeprecated-declarations]
       2692 |     return (dictptr && *dictptr) ?
      __PYX_GET_DICT_VERSION(*dictptr) : 0;
      |                                             ^~
      /usr/include/python3.12/cpython/dictobject.h:90:
      /usr/include/python3.12/cpython/dictobject.h:95:3: note: declared here
      Py_DEPRECATED(3.12) uint64_t ma_version_tag;
      aiohttp/_websocket.c: In function ‘__Pyx_object_dict_version_matches’:
      aiohttp/_websocket.c:2696:5: warning: ‘ma_version_tag’ is deprecated
      [-Wdeprecated-declarations]
       2696 |     return (dictptr && *dictptr) ?
      __PYX_GET_DICT_VERSION(*dictptr) : 0;
      |                                             ^~
      /usr/include/python3.12/cpython/dictobject.h:90:
      /usr/include/python3.12/cpython/dictobject.h:95:3: note: declared here
      Py_DEPRECATED(3.12) uint64_t ma_version_tag;
      aiohttp/_websocket.c: In function ‘__Pyx_PyInt_As_long’:
      aiohttp/_websocket.c:3042:53: error: ‘PyLongObject’ {aka ‘struct
      _longobject’} has no member named ‘ob_digit’
       3042 |             const digit* digits = ((PyLongObject*)x)->ob_digit;
            |                                                     ^~
      aiohttp/_websocket.c:3097:53: error: ‘PyLongObject’ {aka ‘struct
      _longobject’} has no member named ‘ob_digit’
       3097 |             const digit* digits = ((PyLongObject*)x)->ob_digit;
            |                                                     ^~
      aiohttp/_websocket.c: In function ‘__Pyx_PyInt_As_int’:
      aiohttp/_websocket.c:3238:53: error: ‘PyLongObject’ {aka ‘struct
      _longobject’} has no member named ‘ob_digit’
       3238 |             const digit* digits = ((PyLongObject*)x)->ob_digit;
            |                                                     ^~
      aiohttp/_websocket.c:3293:53: error: ‘PyLongObject’ {aka ‘struct
      _longobject’} has no member named ‘ob_digit’
       3293 |             const digit* digits = ((PyLongObject*)x)->ob_digit;
            |                                                     ^~
      aiohttp/_websocket.c: In function ‘__Pyx_PyIndex_AsSsize_t’:
      aiohttp/_websocket.c:3744:45: error: ‘PyLongObject’ {aka ‘struct
      _longobject’} has no member named ‘ob_digit’
       3744 |     const digit* digits = ((PyLongObject*)b)->ob_digit;
            |                                             ^~
      error: Command '['x86_64-linux-gnu-gcc', '-fno-strict-overflow',
      '-Wsign-compare', '-DNDEBUG', '-g', '-O2', '-Wall', '-fPIC',
      '-I/home/zlx/.cache/uv/builds-v0/.tmpDwI5Jb/include', '-I/usr/include/python3.12', '-c', 'aiohttp/_websocket.c', '-o',
      'build/temp.linux-x86_64-cpython-312/aiohttp/_websocket.o']' returned
      non-zero exit status 1.


hint: `aiohttp` (v3.8.5) was included because `poisoned` (v0.1.0) depends on `aiohttp`
hint: Build failures usually indicate a problem with the package or the build environment
manual_uv_rc=1
```

真实 wrapper 命令 rc=0；手工去掉隔离标记的命令 rc=1，失败发生在消费仓依赖构建而不是 stub 脚本。

## 反向红验（每项均已还原）

所有变异均在已有真实修复提交后进行；每次测试只运行到明确断言，红类型均为 `AssertionError`，随后用反向 patch 还原并继续。以下是转红输出原文。

### gate-v2 Silo 调用重新出现 `uv run`

```text
F                                                                        [100%]
_____________ test_v2_workflows_have_no_package_manager_silo_calls _____________
E       AssertionError: gate-v2 uv run lines must be caller install/test only
E       assert 3 == 2
E        +  where 3 = <built-in method count of str object at 0x3340ae60>('uv run')
1 failed in 0.05s
```

### disposition Silo 调用重新出现 `uv run`

```text
F                                                                        [100%]
_____________ test_v2_workflows_have_no_package_manager_silo_calls _____________
E       AssertionError: disposition must not run uv
E       assert 1 == 0
1 failed in 0.04s
```

### sparse 清单漏掉 wrapper

```text
F                                                                        [100%]
_______________ test_every_silo_sparse_checkout_includes_wrapper _______________
E       AssertionError: gate-v2.yml: sparse checkout with silo_store.py omits silo_exec.sh
E       assert 'scripts/silo_exec.sh' in 'scripts/silo_store.py\n'
1 failed in 0.08s
```

### job env 漏掉 `SILO_EXEC`

```text
F                                                                        [100%]
__________ test_silo_store_jobs_have_absolute_store_and_wrapper_paths __________
E       AssertionError: gate-v2.yml:quality must define SILO_EXEC
E       assert None
1 failed in 0.04s
```

### `SILO_STORE` 恢复相对路径

```text
F                                                                        [100%]
__________ test_silo_store_jobs_have_absolute_store_and_wrapper_paths __________
E       AssertionError: gate-v2.yml:quality SILO_STORE must be workspace-absolute
E       assert False
E        +  where False = <built-in method startswith of str object at 0x71719aa96010>('${{ github.workspace }}/')
1 failed in 0.04s
```

### `SILO_EXEC` 恢复相对路径

```text
F                                                                        [100%]
__________ test_silo_store_jobs_have_absolute_store_and_wrapper_paths __________
E       AssertionError: gate-v2.yml:quality SILO_EXEC must be workspace-absolute
E       assert False
E        +  where False = <built-in method startswith of str object at 0x7cbfb80d5fc0>('${{ github.workspace }}/')
1 failed in 0.04s
```

### diff-cover 去掉 `--no-project`

```text
F                                                                        [100%]
___________________ test_diff_coverage_uses_no_project_mode ____________________
E       assert 'uv run --no-project --with diff-cover python3' in 'set -euo pipefail\nif command -v uv >/dev/null 2>&1; then\n  runner=(uv run --with diff-cover python3)\n...'
1 failed in 0.04s
```

### wrapper 不切中立 cwd

```text
F                                                                        [100%]
________________ test_silo_wrapper_emits_isolated_cwd_and_argv ________________
E       AssertionError: assert PosixPath('/tmp/pytest-of-zlx/pytest-171/test_silo_wrapper_emits_isolat0/poisoned') == PosixPath('/tmp/pytest-of-zlx/pytest-171/test_silo_wrapper_emits_isolat0/runner-temp')
E        +  where PosixPath('/tmp/pytest-of-zlx/pytest-171/test_silo_wrapper_emits_isolat0/poisoned') = resolve()
1 failed in 0.04s
```

### wrapper 去掉 `--no-project`

```text
F                                                                        [100%]
________________ test_silo_wrapper_emits_isolated_cwd_and_argv ________________
E       AssertionError: assert ['run', '--py...3', '--', ...] == ['run', '--no... 'boto3', ...]
E         At index 1 diff: '--python' != '--no-project'
E         Right contains one more item: 'example'
1 failed in 0.04s
```

### wrapper 吞掉退出码

```text
F                                                                        [100%]
___________________ test_silo_wrapper_transparent_exit_code ____________________
E       AssertionError: assert 0 == 2
E        +  where 0 = CompletedProcess(..., returncode=0, ...)
1 failed in 0.03s
```

### wrapper 缺失 `SILO_STORE` 不再输出 fail-loud 文案

```text
F                                                                        [100%]
____________ test_silo_wrapper_fails_loud_when_store_env_is_missing ____________
E       AssertionError: assert 'SILO_STORE 未传入' in '/home/zlx/projects/personal/gate-worktrees/gate-20260921-01/scripts/silo_exec.sh: line 11: SILO_STORE: unbound variable\n'
1 failed in 0.03s
```

## 既有契约同步说明

首次全量运行在生产改动正确的情况下得到 `17 failed, 1066 passed`；失败全部是旧契约仍要求直接 `$SILO_STORE`、相对路径或旧 sparse 集合。由于这些断言与本卡锁定的唯一 wrapper / 绝对路径 / sparse 必须包含 wrapper 互相矛盾，已在 `tests/test_gate_v2_contract.py` 和 `tests/test_gate_v2_diagnostics_upload.py` 更新为新接口语义；更新后全量 `1083 passed`。这两份既有测试不在任务卡最初列出的新增测试文件名中，属于为保持仓库全量门禁绿色而做的必要契约同步，未扩大生产代码范围。

## 契约测试轴的已知绕过形态

有。当前测试轴可以拦住普通的 `uv run ... silo_store.py` 和字面量 `python3 "$SILO_STORE"`，但仍可能被间接 shell 调用绕过，例如：

```bash
runner=python3
store="$SILO_STORE"
"$runner" "$store" put --file "$PATH_TO_ARTIFACT"
```

这个 run 块既不含包管理器命令，也不含当前测试寻找的字面量 `python3 "$SILO_STORE"`，但仍在消费仓 cwd 中直接执行 gate 自有 Silo 脚本，可能继承 `.python-version`、`uv.toml`、`.env` 等上下文；同类形态包括 `python3 -c 'runpy.run_path(...)'`、`bash "$SILO_STORE"`，或通过另一个 gate-owned helper 间接调用 Silo。当前测试为什么拦不住：它断言的是可见的 `uv run` 计数、直接调用字面串、wrapper 实际 argv/cwd，而不是对每个 Silo 子命令做 shell 语义级数据流追踪。后续若要根治这类间接调用，应把静态契约提升为“所有 Silo 子命令只能由 `$SILO_EXEC` 发出”的可解析调用清单或将调用面收敛到不可绕过的 action；本卡不扩大实现范围。

## 现场与额外探针

- pickup：工作树起点为 `card/gate-20260921-01`、基线 `b0bc6f4`、无上一家交接单；dispatch id `dlg-20260921-071817-3eca1e` 是本会话自己的现场。
- `archive_orphan_debts.py --oneline`：5 秒有界探针超时，`archive_rc=124`；不能据此推断无欠账。
- `memory_report.py --oneline`：`memory 巡检报告不可用：memory_dir_mismatch（/home/zlx/.local/state/memory-doctor/latest.json）`，rc=2。
- graphify 全语义路径因 122 个文档且无语言模型 key 失败；随后按指引运行 `graphify . --code-only --no-viz` 成功生成 1382 nodes / 3257 edges / 62 communities，并已删除此次生成的 `graphify-out/` 临时产物。
- `repo-settings-doctor.sh --hookspath` rc=0，无输出；`worktree_reconcile.py --dry-run --repo <当前 worktree>` rc=2，原文为 `worktree-reconcile: --repo 不是主仓（.git 非目录或非主 checkout）`，因此无法从 worktree 侧给出主仓回收清单。
- 临时验证目录已销毁；最终 checkout 保持在 `card/gate-20260921-01`。

## 推送状态

本地已按授权执行 `git push -u origin card/gate-20260921-01`，但远端 SSH 连接超时，原始错误为：

```text
ssh: connect to host ssh.github.com port 443: Connection timed out
fatal: Could not read from remote repository.
```

随后只读执行 `timeout 15s git ls-remote origin refs/heads/card/gate-20260921-01`：rc=0，但 stdout 为空；按照副作用纪律不做无条件重试，当前不能声称远端已收到提交。远端网络恢复后应先再次读取目标状态，再单次推送并用 `git ls-remote` 核对 SHA。
