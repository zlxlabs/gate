# gate-261030-shadow-partial-progress

## 2026-10-30 — 实现与规模证据

- **当前阶段**：实现中；集成测试与真实大仓规模探针已完成，尚待全量测试、红验、提交与 PR。
- **本段结论**：新增真实 Git 部分克隆测试覆盖 ordinary edit、`-M` 命中的改名、大文件、删除、force-push 后旧 SHA 不可达以及 diff 懒取失败。对 zlxlabs/agent-config PR #5887 的两个真实 commit SHA 实测，`git diff -U0 -M` 的新增行集合一致，`.git/objects` 存储字节下降 88.33%；新路径 diff 耗时 7.818 秒，未触及 60 秒停止线。
- **关键决策与已否决方案**：单次 `fetch --depth 1 --filter=blob:none origin <prev> <head>`，不加 fallback/retry；保留既有 timeout/degrade 映射。字节口径采用 `.git/objects` 文件总大小（不是网卡级传输计数），并明确标注该口径，不将其称为网络字节。
- **下一步唯一动作**：跑指定 narrow 验证（含 wiring），再跑全量测试。

### 大仓证据原始命令与输出

SHA 从 GitHub CLI 的 PR commit 列表读取，未手拼：

```sh
gh pr view 5887 -R zlxlabs/agent-config --json commits --jq '.commits[0].oid'
gh pr view 5887 -R zlxlabs/agent-config --json commits --jq '.commits[-1].oid'
```

PR 元数据：`gh pr view 5887 -R zlxlabs/agent-config --json number,changedFiles,commits,baseRefOid,headRefOid,url` 返回 `changedFiles=83`，URL `https://github.com/zlxlabs/agent-config/pull/5887`；commit 列表首尾分别为 `2d2ed99840d0b8396cc823f66b6ff1432ab87781` 与 `77815f7a4da09c2cb4fe4e54c5505e67c90197f6`。

测量命令的安全入口（token 只经进程环境，不打印或落盘）：

```sh
export GH_TOKEN="$(gh auth token)"
export PR_BASE_COMMIT="$(gh pr view 5887 -R zlxlabs/agent-config --json commits --jq '.commits[0].oid')"
export PR_HEAD_COMMIT="$(gh pr view 5887 -R zlxlabs/agent-config --json commits --jq '.commits[-1].oid')"
python3 - <<'PY'
# 临时目录中分别 git init + remote add https://github.com/zlxlabs/agent-config.git。
# http.extraheader 经 GIT_CONFIG_COUNT/KEY/VALUE 环境变量传给 git，不写 git config 文件。
# 旧组：对 base、head 各运行 git fetch --depth 1 origin <sha>；新组：
# git fetch --depth 1 --filter=blob:none origin <base> <head>。
# 两组都运行 git diff -U0 -M <base>..<head>；调用
# incremental_shadow_io.parse_diff_added_lines 对 diff 文本解析并比较 dict/set。
# fetch 与 diff 分别用 time.monotonic()；下载量代理口径为 fetch 后、diff 后
# .git/objects 下所有普通文件的 st_size 总和。
PY
```

测量输出原文（对象存储字节口径；耗时秒；集合经生产 parser 比较）：

```text
ghx_ledger_frame_parse_failed: no-request-frames
pr=5887 base_commit=2d2ed99840d0b8396cc823f66b6ff1432ab87781 head_commit=77815f7a4da09c2cb4fe4e54c5505e67c90197f6
old_two_unfiltered_fetches: fetch_seconds=[10.909411360044032, 11.489767259918153] fetch_total_seconds=22.399 diff_seconds=0.241 object_bytes_after_fetch=79895785 object_bytes_after_diff=79895785 diff_bytes=6449020 added_files=1181 added_lines=65535
new_one_blob_none_fetch: fetch_seconds=[4.005666222888976] fetch_total_seconds=4.006 diff_seconds=7.818 object_bytes_after_fetch=570996 object_bytes_after_diff=9326798 diff_bytes=6446630 added_files=1181 added_lines=65535
added_line_sets_identical=True
object_bytes_reduction=88.33%
```

说明：`ghx_ledger_frame_parse_failed: no-request-frames` 是探针启动时 shell/CLI 环境输出的诊断行；两次 gh SHA 查询和随后 git 测量均成功。新路径 diff 比 fetch 后多取约 8.76 MB 的 blob，仍远少于旧方式约 79.90 MB；diff 7.818 秒，按任务卡 60 秒止损线继续。

## 2026-10-30 — 验证结果

- **当前阶段**：实现完成；本地测试已通过，准备提交与开 PR。
- **本段结论**：指定 narrow 命令 98 passed；全量在 CI-like hook 环境变量下 2002 passed。原样全量命令在当前开发机受全局 `core.hooksPath` 影响，12 个 `tests/test_neighbor_evidence.py` 用例在 fixture checkout 阶段被本机 post-checkout detached-HEAD 防护挡住；指定的测试目录本身原样运行通过。规模探针与真实 Git 集成行为均未出现新红。
- **关键决策与已否决方案**：不改无关的 neighbor-evidence 测试；用 `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null` 重跑全量，以模拟 GitHub runner 无个人全局 hook 的消费环境。该次全量结果为 2002 passed。
- **下一步唯一动作**：提交实现与测试，并执行任务卡指定的 scope 检查。

验证命令与输出：

```text
uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_incremental_shadow_partial_clone.py tests/test_incremental_shadow.py tests/test_incremental_shadow_wiring.py
98 passed in 38.38s

GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null uv run --python 3.12 --with pytest,PyYAML,diff-cover,coverage python -m pytest -q
2002 passed in 293.40s (0:04:53)
```

### 大仓规模探针完整复现命令

```sh
export GH_TOKEN="$(gh auth token)"
export PR_BASE_COMMIT="$(gh pr view 5887 -R zlxlabs/agent-config --json commits --jq '.commits[0].oid')"
export PR_HEAD_COMMIT="$(gh pr view 5887 -R zlxlabs/agent-config --json commits --jq '.commits[-1].oid')"
python3 - <<'PY'
import base64, importlib.util, os, subprocess, tempfile, time
from pathlib import Path
base, head = os.environ['PR_BASE_COMMIT'], os.environ['PR_HEAD_COMMIT']
url = 'https://github.com/zlxlabs/agent-config.git'
header = 'AUTHORIZATION: basic ' + base64.b64encode(('x-access-token:' + os.environ['GH_TOKEN']).encode()).decode()
env = dict(os.environ, GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='http.extraheader', GIT_CONFIG_VALUE_0=header, GIT_TERMINAL_PROMPT='0')
spec = importlib.util.spec_from_file_location('measure_shadow_io', '.github/actions/gate-aggregator/incremental_shadow_io.py')
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
def run(args):
    result = subprocess.run(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise RuntimeError(f'{args[:5]} rc={result.returncode}: {result.stderr[-1200:]}')
    return result.stdout
def object_bytes(repo):
    return sum(path.stat().st_size for path in (repo / '.git' / 'objects').rglob('*') if path.is_file())
def measure(filtered):
    with tempfile.TemporaryDirectory(prefix='shadow-scale-') as temp:
        repo = Path(temp) / 'repo'; repo.mkdir()
        run(['git', 'init', '--quiet', str(repo)])
        run(['git', '-C', str(repo), 'remote', 'add', 'origin', url])
        fetch_times = []
        fetches = ([['git','-C',str(repo),'fetch','--depth','1','origin',sha] for sha in (base,head)]
                   if not filtered else
                   [['git','-C',str(repo),'fetch','--depth','1','--filter=blob:none','origin',base,head]])
        for argv in fetches:
            started=time.monotonic(); run(argv); fetch_times.append(time.monotonic()-started)
        bytes_after_fetch=object_bytes(repo)
        started=time.monotonic(); diff=run(['git','-C',str(repo),'diff','-U0','-M',f'{base}..{head}']); diff_time=time.monotonic()-started
        bytes_after_diff=object_bytes(repo)
        parsed=mod.parse_diff_added_lines(diff.decode('utf-8','replace'))
        return fetch_times,diff_time,bytes_after_fetch,bytes_after_diff,len(diff),parsed
old=measure(False); new=measure(True)
print(f'pr=5887 base_commit={base} head_commit={head}')
for name,result in [('old_two_unfiltered_fetches',old),('new_one_blob_none_fetch',new)]:
    fetches,diff_time,before,after,diff_size,lines=result
    print(f'{name}: fetch_seconds={fetches} fetch_total_seconds={sum(fetches):.3f} diff_seconds={diff_time:.3f} object_bytes_after_fetch={before} object_bytes_after_diff={after} diff_bytes={diff_size} added_files={len(lines)} added_lines={sum(map(len,lines.values()))}')
print(f'added_line_sets_identical={old[-1] == new[-1]}')
print(f'object_bytes_reduction={(1-new[3]/old[2])*100:.2f}%')
PY
```
