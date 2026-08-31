# gate PR #98 disposition 下载仓审查 verdict

审查轮次：第 1 轮独立全量审查
审查对象：`3cd9b70e2baf85c58b0896bda201364d01f229c4..c0ee3a1515f45db489445d46eed48c34e4e85cdd`（H0 冻结）
风险等级：`personal`

## 总评

PASS。当前 diff 只把 `gh run download` 的目标仓明确设为 reusable workflow 的 caller 仓，并以契约测试锁定该行为；未发现 P1、P2 或 P3 finding。

## 逐条不变式核验

1. `Resolve current PR head and canonical primary audit` 的 `env` 含 `GH_REPO: ${{ github.repository }}`，满足不变式 1。
2. 同一步执行 `gh run download -R "$GITHUB_REPOSITORY" "$PRIMARY_RUN_ID" ...`，目标明确为 caller 仓，满足不变式 2；不再依赖 checkout 后 `zlxlabs/gate` 的 git remote。
3. 契约测试精确断言 `GH_REPO` 的 env 值和带 `-R "$GITHUB_REPOSITORY"` 的完整下载命令；删除任一改动行都会使对应断言不成立，满足不变式 3。
4. diff 未改变 `permissions`、receipt 语义或撤销通道，满足不变式 4。

## Findings

无。

线上失败日志提供了改动动机：此前下载请求落到 `zlxlabs/gate` 并返回 HTTP 404，而同一步对 caller 仓的 `gh api repos/$GITHUB_REPOSITORY/pulls/...` 已成功。该失败现已由显式 caller 仓定位修复；它不是本轮对当前 H0 diff 的 finding。

## 外部工具与风险分诊

| 来源 | 工具标注 | 本仓判定 | P1 两问 |
|---|---|---|---|
| OCR（profile=minimax） | `status=reviewed`、`coverage=complete`、`findings=[]`，无 severity | 无 finding，不阻塞 | 无意见可逐条分诊；不适用 |

本仓 P1/P2/P3 分诊：P1=0，P2=0，P3=0。对于旧下载仓错误，真实 caller 使用确会触发下载 404；但后果是命令 fail-loud，未达到本仓 personal 的数据丢失、静默出错或崩溃红线，因此不另列为当前 finding。

## 验证证据

- 在 H0 临时快照运行：`uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_v2_contract.py` → `68 passed`。
- `git diff --check 3cd9b70..c0ee3a1` 通过。
- 本地 `gh run download --help` 确认 `-R, --repo` 支持 `[HOST/]OWNER/REPO`；`gh help environment` 确认 `GH_REPO` 用于本地仓库上下文缺失时指定目标仓。
- Diff 统计：2 个文件，`4 insertions(+), 2 deletions(-)`；未超 `Diff-Lines-Target: 80` 或 `Diff-Lines-Hard: 160`。
