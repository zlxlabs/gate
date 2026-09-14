# v2 晋升证据修复进度（h-fix-r1）

- dispatch：`dlg-20260914-041639-6456a1`
- 分支：`card/gate-20260914-03`
- 基线：`63fb00a226177f812693f75b82468883493026a9`
- 根因：证据判据把“存在一条对得上”误写成成员判断；同一运行的 gate 引用和 primary job 都需要全量一致/成功。
- 引入提交：`63fb00a226177f812693f75b82468883493026a9`
- 变更范围：仅 `scripts/v2_tag_promotion_evidence.py`、`tests/test_v2_tag_promotion_evidence.py`、`.github/workflows/v2-tag-sync.yml`、`tests/test_v2_tag_sync.py` 及本进度文件。

## 实现

1. `CANARY_GATE_WORKFLOW_ID = "gate.yml"`，查询 `actions/workflows/gate.yml/runs`；端点 404 直接作为查询失败，不回退到未过滤列表。
2. gate-v2 同路径引用要求至少一条且全部等于候选；`[候选, 候选]` 合格，`[候选, 其他]` 不合格。
3. 所有匹配的 primary job 都必须是 `success`。
4. 当前 v2 提交超过 `NO_ELIGIBLE_CANDIDATE_ALERT_AFTER_HOURS = 4 * 1` 小时仍无候选时输出 GitHub error 注解并退出 1；未超过时保留退出 0、不移动标签。
5. 增加仅 `workflow_dispatch` 触发的 workflow-token 权限探针，三个端点逐次请求且响应体均重定向到 `/dev/null`；token 只来自环境变量，不写入命令输出。
6. 测试断言写标签步骤是最后一步。

## 实测证据

- gate.yml 最近 100 次运行（本机 `gh` 只读查询；匿名请求因 403 限流）

  ```text
  {"count":100,"earliest":"2026-09-13T10:30:46Z","latest":"2026-09-14T04:24:35Z","runs_per_hour":5.587545980847135,"window_hours":17.896944444444443}
  ```

  定时触发为每小时一次，实测窗口约 17.90 小时，即约 17.9 倍定时间隔，超过 4 倍要求。

- 不一致引用实测：

  ```text
  duplicate_inconsistent_refs selected=None checked=[('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', False, 'run 1: gate-v2 references inconsistent sha(s)=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa,bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb, expected every reference to be a aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')]
  ```

- 长期无候选实测：5 小时返回 1 并产生 `::error::`；3 小时返回 0 且只保留“不移动标签”提示。

  ```text
  no_candidate_age=5h exit=1 ... ::error::no eligible canary-verified main commit after 5.0h; current v2 commit is older than the 4h threshold
  no_candidate_age=3h exit=0 ... no eligible canary-verified main commit; v2 tag will not move
  ```

- 把全量 gate 引用临时改回成员判断后，判据 1 用例实测：`1 failed, 2 passed, 19 deselected`；随后已恢复实现。
- 把写标签步骤后临时追加步骤后，末尾断言实测失败：`move index=8`, `steps length=10`；随后已恢复工作流。

## 验证结果

```text
uv run --with pytest,PyYAML,diff-cover,coverage python -m pytest -q
991 passed in 39.25s (0:00:39)

python3 scripts/check_pinned_uses.py
OK: checked 9 live workflow/action metadata file(s); all internal uses are workspace-relative
```

定向新增测试最终为 `38 passed`。查询错误与不合格仍分路：`EvidenceQueryError` 继续上抛并由 CLI 返回 1；无候选只有在查询成功且未超过 4 小时时返回 0。开关关闭时 evidence 步骤仍由 guard 条件跳过，权限探针按锁定决策独立保留为手动触发。未触发真实 GitHub workflow dispatch，避免执行未经请求的远端工作流副作用；权限探针已通过 YAML/`bash -n` 静态验证，真实答案由 owner 手动带 `canary_run_id` 触发取得。

当前工作区已恢复，无临时 sentinel 或成员判断变异，待显式提交。
