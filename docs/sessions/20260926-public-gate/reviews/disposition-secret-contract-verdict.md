# verdict: disposition 显式密钥契约独立审查（PR252，Refs #248）

- base: `08a3baa16650e314f05d4e3aea9ec3631cad3760`
- head: `d7b6da178ad5bafcde52c07b86d7b1166e3ba797`
- 审查对象: c26e424 + d7b6da1 两提交，共 +47 行（workflow +7 / 测试 +21 / progress +19）
- risk-tier: internal；独立审查，未读实现报告与其他 reviewer 意见

failure-visibility: clean

## 判定：pass

## 覆盖范围
- `.github/workflows/gate-v2-disposition.yml` 全文逐节核对（声明、消费、缺钥路径）
- `tests/test_gate_v2_contract.py` 新用例及其真实 producer 加载路径
- `templates/caller-gate-disposition.yml`（legacy inherit caller 兼容性）
- progress 文档声明核对；diff --check；红验

## 核验结论
1. `workflow_call.secrets` 恰两项 `SILO_ACCESS_KEY`/`SILO_SECRET_KEY`，均 `required: false`（yml:26-32）。
2. control job env 恰消费同两项（yml:62-63）；全文件 grep `secrets\.` 仅此两处命中。
3. 缺钥原失败保持：yml:122/247 `[ -n "${AWS_ACCESS_KEY_ID:-}" ] || { ::error::SILO_ACCESS_KEY 未传入; exit 1; }`；optional 声明下缺钥解析为空串，仍显式失败，非自动成功。
4. `workflow_dispatch` 无 secrets 块（yml:4-14）；inputs/permissions/runner/environment/concurrency/全部 run 字节零改动（diff 单 hunk +7）。
5. legacy caller 兼容：caller 模板 `secrets: inherit`（caller-gate-disposition.yml:29）未动；optional 声明不约束 inherit。
6. 新测试约束真实 producer：`_load_disposition_workflow()` 读仓内真实 yml；先精确钉死两条 env 映射，再反解消费集断言「声明==消费==pair 且均 optional」，并断言 workflow_dispatch 无 secrets。红验：base(08a3baa) 临时 worktree 仅拷入新测试文件运行如期失败（declared=∅≠pair），HEAD 上同用例通过——非恒真、无假绿。
7. progress 文档未声称本 PR 已合并/v2 已推广/公共 caller 已启用（写明推广前不得启用、主脑验收）。
8. PR251 单次例外（超 200 行预算偏差）未沿用：本 PR +47 行在预算内，且未做 PR251 已否决的 step 级迁 secret。
9. 无密钥生成/写入、无手动 tag；无新增抽象/状态/配置项。

## 发现与真实风险
- 无 P1/P2。
- P3 观察（不阻塞，记此即可）：新测试以 fullmatch 简单形态反解消费集，未来若 control env 改用复合表达式（如 `secrets.X || ''`）消费第三密钥将不计入；当前两条 env 已被精确等值断言钉死，本次无假绿。

## unknown
- Caps 公共 caller 仓的显式 mapping 接线不在本仓，未验（本 PR 仅提供 callee 侧契约）。
- 声明的 description 文案与 hub 侧文档一致性未核（仅文档作用，不影响行为）。

## OCR 状态
- `reviewed`（profile=minimax / MiniMax-M3，cli_status=complete，coverage=complete，findings=[]）。
- envelope: `/home/zlx/.local/state/delegate/20260926-133938-big-kimi-public-disposition-secrets-260926/ocr-envelope.json`
- stderr: `/home/zlx/.local/state/delegate/20260926-133938-big-kimi-public-disposition-secrets-260926/ocr-stderr.log`
