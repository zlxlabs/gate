verdict: pass

# review gate PR #115 第 2 轮（换家换证据源）

- 审查对象：`5123e3120ca6e9c4d84244528b74fe9346bd730c..7553379add08795be2be0be9633a4c7790bacc59`（H0 冻结，与第 1 轮同一对象）
- spec：PR #115 正文 + issue #107 + issue #105 第二条 + `docs/sessions/260902-gate-triage/design.md`（`origin/card/gate-20260902-05`）不变式 3、4、5
- 风险等级：personal，失败路径按 internal 收敛
- 执行器：kimi（与第 1 轮 grok 换家）
- 本轮新证据（第 1 轮未做）：① outcome 步 run 块 shell 实跑 4×4 + unset/空串；② upload-artifact v4.6.2 源码与 dist 取证；③ GitHub 官方文档原文取证；④ 契约测试两个新方向变异。

## 证据 1：outcome 步 shell 实跑 4×4

抽取方式（`yaml.safe_load` 读 H0 `.github/workflows/gate-v2.yml` 的 `jobs.quality.steps[id=ledger-input-upload-outcome].run`）：

```
set -euo pipefail
if [ "$LEDGER_INPUT_UPLOAD" = "success" ] || [ "$LEDGER_INPUT_UPLOAD_RETRY" = "success" ]; then
  echo "ledger_input_upload=success" >> "$GITHUB_OUTPUT"
else
  echo "ledger_input_upload=failure" >> "$GITHUB_OUTPUT"
  echo "::error::review ledger input upload failed after one retry (network); gate / ledger will report the input missing"
fi
```

命令：`bash matrix.sh`（对每个组合 `LEDGER_INPUT_UPLOAD=$a LEDGER_INPUT_UPLOAD_RETRY=$b GITHUB_OUTPUT=<tmp> bash outcome-step.sh`，记 output 值 / `::error::` 计数 / 退出码）。输出原文：

```
UPLOAD     RETRY      | OUTPUT   | ERR    | RC
success    success    | success  | 0      | 0
success    failure    | success  | 0      | 0
success    cancelled  | success  | 0      | 0
success    skipped    | success  | 0      | 0
failure    success    | success  | 0      | 0
failure    failure    | failure  | 1      | 0
failure    cancelled  | failure  | 1      | 0
failure    skipped    | failure  | 1      | 0
cancelled  success    | success  | 0      | 0
cancelled  failure    | failure  | 1      | 0
cancelled  cancelled  | failure  | 1      | 0
cancelled  skipped    | failure  | 1      | 0
skipped    success    | success  | 0      | 0
skipped    failure    | failure  | 1      | 0
skipped    cancelled  | failure  | 1      | 0
skipped    skipped    | failure  | 1      | 0
--- unset env ---
unset_retry: rc=1 stderr=outcome-step.sh: line 2: LEDGER_INPUT_UPLOAD_RETRY: unbound variable  output=
unset_upload: rc=1 stderr=outcome-step.sh: line 2: LEDGER_INPUT_UPLOAD: unbound variable  output=
unset_both: rc=1 stderr=outcome-step.sh: line 2: LEDGER_INPUT_UPLOAD: unbound variable  output=
```

补跑空串（`LEDGER_INPUT_UPLOAD='' LEDGER_INPUT_UPLOAD_RETRY=''`）：`rc=0 output=ledger_input_upload=failure`（打 `::error::`）。

结论：

- 16 格全部符合预期：任一 success → `success`；否则 `failure` 且打 `::error::`；**退出码恒 0**（`::error::` 只是 annotation，不红步）。不变式 4 的「两次都失败 → failure」逐格成立。
- 任务卡点名的 `unbound variable` 隐患**本地确实存在**（任一 env 未设置 → rc=1，outcome 步自己红 → quality 红），但按 GitHub 表达式语义**不可达**：contexts 文档明写 "If you attempt to dereference a nonexistent property, it will evaluate to an empty string."（URL 见证据 3），`env:` 值经表达式求值后必然是已设置的字符串（最坏为空串），空串格实测 rc=0、保守落 failure。不给 P 等级；两问：①真实使用方式下会触发吗——不会，GH 不产生 unset env（文档句 + 空串实测）；②触发后果——假设性触发会让 quality 红，违反已否决方案，但前提不成立。

## 证据 2：upload-artifact v4.6.2 源码取证

命令：`curl -sL https://raw.githubusercontent.com/actions/upload-artifact/ea165f8d65b6e75b540449e92b4886f43607fa02/{src/upload/upload-artifact.ts,src/shared/upload-artifact.ts,package.json,dist/upload/index.js,README.md}`；`@actions/artifact` 版本由 package.json 锁定 `^2.3.2`。

**① `overwrite: true` 在同名 artifact 不存在时不失败——源码证实。** `src/upload/upload-artifact.ts`：

```ts
async function deleteArtifactIfExists(artifactName: string): Promise<void> {
  try {
    await artifact.deleteArtifact(artifactName)
  } catch (error) {
    if (error instanceof ArtifactNotFoundError) {
      core.debug(`Skipping deletion of '${artifactName}', it does not exist`)
      return
    }
    // Best effort, we don't want to fail the action if this fails
    core.debug(`Unable to delete artifact: ${(error as Error).message}`)
  }
}
```

`deleteArtifactInternal`（dist）在 `ListArtifacts` 返回空时抛 `ArtifactNotFoundError`，被上面捕获并直接 return。README 同句："Does not fail if the artifact does not exist." 且**任何其他删除错误也只 debug 不 fail**（best effort）。第 1 轮只引 action.yml 描述，本轮源码级坐实。

**② Finalize 失败（ECONNRESET）后服务端残留——客户端代码可证 Create 先于 Finalize，残留是否可被 list 到属服务端未定义行为。** dist `uploadArtifact` 顺序：`CreateArtifact` → `uploadZipToBlobStorage` → `FinalizeArtifact`。ECONNRESET 在 Finalize 时，服务端已存在 Create 记录 + 已上传 blob。Twirp 客户端重试策略（dist `ArtifactHttpClient.request`）：HTTP 状态码 5xx/429 重试至多 5 次，但**网络级错误码直接抛 NetworkError 不重试**：

```js
NetworkError.isNetworkErrorCode = (code) => {
    if (!code) return false;
    return ['ECONNRESET','ENOTFOUND','ETIMEDOUT','ECONNREFUSED','EHOSTUNREACH'].includes(code);
};
```

即 issue #105 的 `Failed to FinalizeArtifact (ECONNRESET)` 恰是客户端内部不重试的那类——workflow 级重试步的设定与客户端行为互补，设计成立。残留 artifact 是否会被 retry 步 `overwrite` 的 `ListArtifacts(nameFilter)` 看到，客户端源码无法回答；上游有 overwrite 偶发不生效的实报（[issue #571](https://github.com/actions/upload-artifact/issues/571)，删除后重新上传成功但内容未换）。**第 1 轮 P2-1 触发概率不能从客户端代码压到零，维持 P2-1 原判（接受不修、记 backlog、等自然样本）**，无升级依据：personal 档、失败后果是 ledger 回落上一 attempt 并带明确缺失文案（不变式 4），可发现、可重跑。

**③ `if-no-files-found: error` 与 `overwrite` 的先后——先查文件，后删。** `run()` 开头即 `findFilesToUpload`，`filesToUpload.length === 0` 走 `NoFileOptions.error → core.setFailed(...)`，**不进入** else 分支里的 `deleteArtifactIfExists`；只有文件存在才执行 `if (inputs.overwrite) await deleteArtifactIfExists(...)` 再 `uploadArtifact(...)`。含义：重试步若因文件缺失失败，不会先删掉首步可能留下的残留——P2-1 的一个子路径实际被该顺序收窄（删残留只发生在文件齐备、进入上传流程之后）。

## 证据 3：GitHub 官方文档取证

**a. `steps.<step_id>.outcome` 在 step 被 `if` 跳过时的值。** [Contexts reference](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts) 原文：

> "The `steps` context contains information about the steps in the current job that have an `id` specified and **have already run**."
> "`steps.<step_id>.outcome` | `string` | The result of a completed step before `continue-on-error` is applied. Possible values are `success`, `failure`, `cancelled`, or `skipped`."
> "If you attempt to dereference a nonexistent property, it will evaluate to an empty string."

被 `if` 跳过的步未 run → 不在 steps context → 表达式求值为**空串**（不是 unset）。两种形态（空串、`skipped`）都在证据 1 实跑覆盖内，均 rc=0 且保守落 failure。另注意本步 `if: always()` + 首步 `if: always()`，首步只能被 job 取消跳过，而 job 取消时本步通常也活不到执行。

**b. job `failure`/`cancelled` 时 `needs.<job_id>.outputs` 可读性。** 同一文档 `needs` context 节的官方示例即含失败 job：

> `"deploy": { "result": "failure", "outputs": {} }`

[Workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax) `jobs.<job_id>.outputs` 节："Job outputs containing expressions are evaluated on the runner **at the end of each job**." failure 时：文档示例与求值时机共同支持「失败前已写出的 step output 仍进 job outputs」——不变式 4 的 quality→ledger 链路（outcome 步 `if: always()`）有文档依据。**cancelled 时：文档未定义**——求值发生在 runner 上 job 末尾，cancel 杀掉 runner 进程时 outputs 可能不送出，下游读到空串；文档无明文句，待首个自然样本（此形态下 gate 聚合器按 `needs.quality.result == cancelled` 分支处理，不依赖该 output，风险已由第 1 轮覆盖）。

**c. `continue-on-error` 是否影响该 job 的 check run conclusion。** Workflow syntax 文档仅有矩阵语义句（"other jobs in the matrix will continue running even if the job ... fails"），**对 check run conclusion 无明文定义；checks API 文档同样无对应句**。**文档未定义，待首个自然样本**（下一次 ocr job failure 时用 jobs/check-runs API 核 run 级 conclusion，同 design.md 验收路径 4），不猜。

## 证据 4：契约测试有效性变异（新方向）

基线：`uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q tests/test_gate_v2_contract.py` → `85 passed`。

**变异 A**：ocr job 级 `continue-on-error: true` → `continue-on-error: "true"`（字符串）。注入留痕：`grep` 确认第 605 行变为 `continue-on-error: "true"`。结果：

```
>       assert {job for job, spec in jobs.items() if spec.get("continue-on-error") is True} == {"ocr"}
E       AssertionError: assert set() == {'ocr'}
tests/test_gate_v2_contract.py:774: AssertionError
1 failed, 84 passed
```

转红且为 **AssertionError**，锁的就是不变式 5 的 `is True` 身份比较。有效。

**变异 B**：删重试步 `overwrite: true`（H0 全文唯一一行，注入留痕：删除第 361 行后 `grep -c 'overwrite: true'` = 0）。结果：

```
>       assert retry_upload["with"]["overwrite"] is True
E       KeyError: 'overwrite'
tests/test_gate_v2_contract.py:726: KeyError
1 failed, 84 passed
```

转红但失败形态是 **KeyError 而非 AssertionError**。断言有效（删了就红，锁不变式 3 的 overwrite 存在性）；测试写法上 `[...]` 直取与「is True 断言缺失键给 AssertionError」的预期有出入，属措辞级弱点：若未来有人把 `overwrite: false` 写上，走的才是 AssertionError 路径。记 P3（测试写法，不阻塞）：建议改用 `.get("overwrite") is True` 让两种坏形态同为 AssertionError——不修，记 backlog。

两变异均已 `git checkout --` 还原，临时 worktree `/tmp/review-115-r2` 用后删除。

## Findings 汇总（每条跑 P1 两问）

| # | 级别 | 内容 | 工具标注 / 本仓判定 / 两问 |
|---|------|------|---------------------------|
| R2-1 | 非 finding | outcome 步 `set -u` 在 env unset 时会 rc=1 让 quality 红 | 工具标注：任务卡假设；本仓判定：不可达（GH 表达式对不存在属性求值为空串，env 必被设置；空串格实测 rc=0）。两问：①真实触发吗——不触发（文档句 + 实测）；②后果——假设成立才违已否决方案，前提不成立。 |
| R2-2 | 维持 P2-1 | Finalize ECONNRESET 残留是否可 list，客户端不可证 | 工具标注：-；本仓判定：维持第 1 轮 P2-1 接受不修。两问（复跑）：①触发吗——低概率真实存在（上游 issue #571 类实报 + 客户端不重试网络错误）；②后果——ledger 回落上一 attempt + 缺失文案带 quality 结论，可发现可重跑，personal 档可接受。 |
| R2-3 | P3（新增，backlog） | `test_gate_v2_contract.py:726` 缺键时报 KeyError 而非 AssertionError | 工具标注：-；本仓判定：P3 测试写法，不修。两问：①触发吗——只在「键被删」这一已被转红覆盖的形态下表现为报错文案差异；②后果——测试照样红，无语义影响。 |

本轮无新增 P1、无新增 P2。熵增：本 diff 无新增抽象/状态/配置项超出第 1 轮已审范围，无新熵。

## 收敛判定

第 1 轮（grok）：pass，P2-1 接受不修 + P3×2。本轮（kimi，换家 + 换证据源：shell 实跑 / 源码取证 / 官方文档取证 / 新方向变异）：**无新增 P1**。连续 2 轮无新增 P1，按 internal 失败路径收敛判据**已收敛**。遗留跟踪项不变：#107/#105 二的入口层证据等首个自然样本（design.md 验收路径 4）；P2-1 与 P3 各条记 backlog。
