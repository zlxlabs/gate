# gate-notify-panel R3 verdict（换家轮：静默吞错反向猎捕 + 用户可感知层 + 四方一致性）

- 审查对象：`2729d2ae4997432be7f226a075ab04572c226435..290bd5eb77af63eb8f2331ab1f99c1ec06bd1d18`（base..H2，冻结）
- 审查者：kimi（R3，与 R1 静态轴表审、R2 子进程 HTTP 实测均不同视角）
- 基线：`python3 -m pytest tests/ -q` → **444 passed in 6.05s**（H2 worktree 实测）

## 本轮新证据声明

1. **fail-open 捕获点穷举 + 反向实测**：枚举 `aggregate.py` 与 `gate-v2.yml`（aggregate/publish/OCR 三段）全部 try/except、fail-open 分支、`continue-on-error`、条件跳过，并**实际触发 6 个 R2 未测分支**（history 重建 HTTP 500、identity /user HTTP 401、Step Summary 不可写、receipt 路径不可写、publish-only terminal 制品损坏、OCR shell gh POST 500 与 identity 403），全部为真实进程执行输出（非静态阅读）。
2. **用户可感知层渲染实测**：用真实 renderer 渲染四个动作桶 + 历史边界形态 + 三种 skipped 形态的 action sentence，按「发生了什么→影响我什么→我现在做什么」逐桶核。
3. **文档侧首读**：PR #65 body、`docs/gate-v2-status-panel.md` 此前两轮未读，本轮做 PR body / 文档 / 实现 / 测试四方逐条对照。

## 专项 1：静默吞错反向矩阵

### aggregate.py

| # | 捕获点 | 吞掉的失败 | 第二出口 | 出口真的会产生吗 | 内容够定位吗 |
|---|---|---|---|---|---|
| 1 | `find_audit_file` (L491) | 审计制品缺失/多文件/坏 JSON | gate fail-closed 红 + Step Summary problems | 是（存量测试矩阵） | 够（含文件名与解析错误） |
| 2 | `_warn` 内层 guard (L926) | stdout 断管时 warning 打印本身失败 | 无（设计如此：stdout 已坏，无处可写） | — | 故意 guard-of-guard，可接受 |
| 3 | history 重建 except (L1006) | artifacts 列表/下载任意失败 | ::warning:: + receipt(history_unavailable) + Step Summary diagnostic；面板保留旧正文 | **实测 A 确认** | 够（HTTP status + category + 异常文本） |
| 4 | 重复评论 DELETE except (L1036) | 自愈删除失败 | receipt self_heal_errors + Summary | 测试指针 test:1225 | 够（comment id + 异常） |
| 5 | POST 后验证 except (L1049) | 复列评论失败 | 同上 | 测试指针 test:1225 | 够 |
| 6 | POST 自愈 except (L1059) | winner PATCH/删重失败 | 同上 | 同上 | 够 |
| 7 | 发布 HTTPError (L1067) | POST/PATCH 4xx/5xx | ::warning:: + receipt not_created + Summary | R2 已实测 403/500 | 够 |
| 8 | 发布 generic except (L1074) | 网络超时等不定态 | 同上，delivery="unknown"（不定态不谎报） | 测试指针 test:1051/1089 | 够 |
| 9 | receipt unlink OSError (L1115) | 旧 receipt 删不掉 | ::warning:: + 后续失效标记路径 | 测试指针 test:1152 | 够 |
| 10 | receipt write OSError (L1120) | receipt 写失败 | ::warning::（"file is missing and upload will red"）+ upload if-no-files-found:error → job 红 | **实测 D 确认** | 够 |
| 11 | publish-only terminal 损坏 (L1160) | terminal JSON 不可读 | ::warning:: + receipt(terminal_unavailable) + Summary；**不发布面板** | **实测 E 确认** | 够（见 P3-a 讨论） |
| 12 | Summary 打印 guard (L1217) / 写 Summary OSError (L1223) | Step Summary 不可写 | ::warning::，exit 0 | **实测 C 确认** | 够（含路径与 errno） |
| 13 | main 各 malformed-input 路径 | 输入畸形 | fail-closed 红 | 存量测试 | 够 |

### gate-v2.yml

| # | 捕获点 | 吞掉的失败 | 第二出口 | 出口真的会产生吗 | 内容够定位吗 |
|---|---|---|---|---|---|
| 14 | resolve-audit-artifact `continue-on-error` (L761) + 空 outputs | artifact 枚举失败/无候选 | 下游 aggregator audit_missing → fail-closed 红 | 契约测试 + R2 | 够 |
| 15 | download canonical audit `continue-on-error` (L826) | 下载失败 | 同上 | 同上 | 够 |
| 16 | upload-gate-terminal 无 continue-on-error + `if-no-files-found: error` (L877) | terminal 上传失败/文件缺失 | **job 直接红** → 必需检查红 + notify 飞书卡 | 静态推演（见下） | 够 |
| 17 | publish 步 `if: always() && upload.outcome=='success'` (L886) 被跳过 | 本轮面板不更新 | 见「重点问题」 | 静态推演 | 够 |
| 18 | delivery diagnostic upload `if-no-files-found: error` (L920) | receipt 文件缺失 | step 红 → job 红 | 实测 D 的 warning 文案与此互证 | 够 |
| 19 | OCR comment 文件缺失 (L582) | shadow 未产出 | ::error:: exit 1，job 红 | 静态 | 够 |
| 20 | OCR comment 空 (L586) | 空评论 | log 一行 + exit 0（刻意无操作，无可发内容） | 静态 | 可接受 |
| 21 | OCR identity 失败 (L600) | /user 失败 | delivery JSON + Summary + ::warning:: + exit 0 | **实测确认（403）** | 够 |
| 22 | OCR identity 形状非法 (L612) | jq 校验失败 | 同上（category=configuration） | 静态 | 够 |
| 23 | OCR lookup 失败 (L628) | 评论列表失败 | 同上（operation=LOOKUP） | 静态 | 够 |
| 24 | OCR POST/PATCH 失败 (L650) | 发布失败 | 同上 | **实测确认（500）** | 够 |
| 25 | OCR 自愈部分失败 (L704) | 删重/复 PATCH 失败 | Summary self-heal 行 + ::warning:: + delivery JSON self_heal_error | 静态 | 够 |
| 26 | OCR event upload `if-no-files-found: error` (L716) | delivery JSON 未写出 | step 红 → job 红 | 静态 | 够 |

存量（不在本 diff，仅登记不动）：notify 飞书 webhook `except Exception: print("...(swallowed)")`（L1117）只打 log 不打 ::warning::；quality legacy 段若干 `continue-on-error`/`|| true`（L196/238/287/291）。均未被本 PR 触碰。

### 实测记录（命令与关键输出）

全部实测经真实 `main()`/`_publish_only` 入口或从 YAML 抽出的真实 step shell（`bash -n` 校验后执行），非复写。探针脚本在系统临时目录（/tmp/r3_probe.py、/tmp/r3_gap.py、/tmp/ocr_step.sh），已清理。

- **A. history 重建 HTTP 500**（存在 own 旧面板）：`exit_code=0`；
  `::warning::gate status panel history reconstruction failed — HTTP status=500; permission category=server_error; reason=http_5xx; gate verdict is unchanged and Step Summary remains authoritative`；
  receipt `{"delivery":"not_created","reason_code":"history_unavailable","http_status":500,"history_error":"HTTPError: HTTP Error 500: status 500","operation":"LOOKUP"}`；
  Step Summary 含完整 diagnostic 五字段。旧面板正文保留不 PATCH。
- **B. identity /user HTTP 401**：`exit_code=0`；warning `comment publish failed — HTTP status=401; permission category=http_error; reason=http_error`；receipt not_created/http_error。→ 见 P3-a。
- **C. Step Summary 不可写**（父目录不存在）：`exit_code=0`；`::warning::could not append the status panel diagnostic to Step Summary (FileNotFoundError: ...)`。
- **D. receipt 路径不可写**（只读目录）：`exit_code=0`；`::warning::gate PR-comment receipt write failed (PermissionError: ...); file is missing and upload will red`——与 upload 步 `if-no-files-found: error` 互证，job 会红。（注：本场景因未打桩意外走了一次真实 api.github.com /user 并得真实 401，顺带验证了真实网络 401 路径同 B。）
- **E. publish-only terminal 制品损坏**（`{not json`）：`exit_code=0`；warning `terminal artifact validation failed ... reason=terminal_unavailable`；receipt `delivery=not_created, operation=PUBLISH_ONLY, history_error=JSONDecodeError:...`；**未做任何发布调用**，面板保持旧态。
- **F. OCR shell**：从 gate-v2.yml L580-708 抽出真实 run 块（`bash -n` 通过），fake `gh` 注入失败：
  - POST HTTP 500 → `exit_code=0`；`::warning::OCR advisory sticky POST failed; HTTP status=500; permission category=server_error`；delivery JSON `{delivery:not_created, operation:POST, http_status:"500"}`；Summary 四行诊断齐。
  - identity HTTP 403 → `exit_code=0`；`::warning::... identity lookup failed; HTTP status=403; permission category=permission_or_rate_limit`；delivery JSON operation=IDENTITY；Summary 含 `Reason: workflow_identity_lookup_failed`。

### 重点问题：upload 失败 → publish 跳过那一轮，用户在 PR 上看到什么

不是静默。链路：upload-gate-terminal 失败（无 continue-on-error）→ gate job 红 → 必需检查红；aggregate 步此前已写 Step Summary（真实裁决可见）；notify job `if: failure()` 触发飞书 P2 卡；publish 步跳过不写 receipt → delivery diagnostic upload `if-no-files-found: error` 再红一层。面板保持上一轮状态（可能 stale），但检查是红的，用户被自然引向 run 页面。唯一瑕疵：若本轮裁决本是 pass 而仅 upload 挂，用户看到的是红检查 + Summary 里写 pass——方向安全（假红不假绿），≤P2 不列 finding。

## 专项 2：用户可感知层（文案三问）

真实 renderer 输出逐桶核过（/tmp/r3_render.py，已清理）：

| 桶 | 发生了什么 | 影响我什么 | 我现在做什么 | 结论 |
|---|---|---|---|---|
| pass→可合并 | `**pass** · **可合并**` | 可合并 | 桶名即动作 | 通过 |
| fail→要修代码 | `**fail** · **要修代码**` + 裁决码 | 门禁红 | 「要修代码」+ 历史表 run 深链 | 通过 |
| skipped→无需动作 | `无需动作（主审未跑，绿≠过审）` + blockquote「draft / fork / hosted 的跳过不代表真实通过」 | 明确绿≠过审 | 无需动作 | 通过（三种形态共用同一表述，均出现；hosted 形态「想主审请切 runner=self」只在 Step Summary 有，面板未区分——P3-b） |
| unavailable→修基础设施 | `**unavailable** · **修基础设施**` | 需查设施 | 桶名 + run 深链 | 通过 |

- 深链：历史表每行 run_id 直达 actions/runs/<id>；当前行即表末行，可点。面板在 PR 上，无需 PR 链接。通过。
- 历史表边界：0 行不可达（renderer 对空 rows raise，调用方恒有 current 行，fail-loud）；1 行正常；「历史可能不完整：<原因>」blockquote 渲染可读。通过。
- 机器码上浮：桶与警告是人话；但「当前裁决：`code_fail` / `primary_findings`」机器码原样怼出，面板内无人话解释（REASON_CODE_EXPLANATIONS 只在 Step Summary）。中文收件人读到 `primary_findings` 需自行解码。P3-c。

## 专项 3：四方一致性（PR body / docs / 实现 / 测试）

| 条目 | PR body | docs | 实现 | 测试 | 判定 |
|---|---|---|---|---|---|
| 面板 marker `gate-v2-status-panel:v1` | ✓ | ✓ | PANEL_MARKER L124 | ✓ | 一致 |
| OCR marker `gate-v2-ocr-advisory:<reviewer>:v1` | ✓ | ✓ | L590 | test:103 | 一致 |
| 历史行 schema v1 八字段 | ✓ | ✓ | `_terminal_row`/`_panel_current_row` | ✓ | 一致 |
| 发布顺序：upload 成功才 publish-only | ✓ | ✓ | L886 if 条件 | test:181 钉死顺序+条件 | 一致 |
| concurrency 只含 group + cancel-in-progress:false，per-PR 键 | ✓ | ✓ | L729-731 | test:136/161 钉死 | 一致（`queue: max` 不支持键已在 H 序列移除） |
| 所有权：/user 身份、全量分页、取最早 own、非 own 不动 | ✓ | ✓ | L1000-1003/L863-877 | test:1196/1208 | 一致 |
| POST 后复列 + PATCH 最早 + 删重 | ✓ | ✓ | L1044-1060 | test:1225 | 一致 |
| 历史合并 artifact ∪ cache、按 run_id+attempt 去重只增不删 | ✓ | ✓ | `_merge_panel_rows` | test:1308 | 一致 |
| **「artifact/缓存任一缺失导致覆盖不全时正文显式 历史可能不完整」** | ✓ 明文 | ✓ 明文 | **缺口**，见 P2-a | 现有测试未覆盖该形态 | **不一致** |
| 无 --pr-comment per-run receipt 开关 | ✓ | — | 已移除（diff 确认） | — | 一致 |
| fail-open + receipt/Summary 记录 status/category/跳过/自愈 | ✓ | ✓ | 实测 A-F | ✓ | 一致 |

## 新增 finding

### P2-a（判「接受不修」建议改由主脑定）：cache-only 历史在特定形态下丢失「历史可能不完整」标记

- 触发路径：本 PR 的 `gate-terminal-v1-*` 制品全部消失（retention 到期后被 GitHub 从列表移除），但**其他 PR** 的制品使 artifacts 列表非空 → `_fetch_terminal_history` 返回 `rows=[]` 且 `incomplete_reasons=[]`（identity mismatch 按设计不计）；现存 own 面板有可解析缓存行 → `aggregate.py:1021` 的守卫 `if cache_only and history.rows:` 因 `history.rows` 为空**不触发**，面板用纯缓存历史渲染却**不带**「历史可能不完整」标记，receipt `reason_code=patched, history_incomplete=false`。
- 实测证据：/tmp/r3_gap.py 探针输出 `history_incomplete: False`、面板无标记、缓存行在。
- 违反 spec：PR body 与 docs 均明文「artifact/缓存任一缺失导致覆盖不全时正文显式 历史可能不完整（原因）」。
- 分级理由：面板是纯投影，裁决不受影响；缓存行通常恰好覆盖缺口，实际历史多半完整；标记缺失只影响读者对历史完整性的判断。属信息准确性，不属「通知未发且无诊断」（receipt 有 skipped_records 可查）。**P2，建议接受不修或下一 PR 顺手**（修法方向：守卫改为 `cache_only and not incomplete_reasons`，避免与空列表时的 `no terminal artifact matched` 双重标记）。

### P3-a：aggregate 与 OCR shell 的 401 分类不一致 + phase 标签错位

`_panel_failure`（aggregate.py:965）把 401 归为 `http_error/http_error`，OCR shell（L632/654）把 401 归为 `permission_or_rate_limit`；且 /user 身份阶段失败在 aggregate 侧统一报 phase="comment publish"（OCR 侧有独立 operation=IDENTITY）。诊断分类口径不一致，不影响 fail-open 行为。判接受不修，记 backlog。

### P3-b：面板 skipped 桶不区分 draft/fork/hosted

三形态共用「无需动作（主审未跑，绿≠过审）」；Step Summary 的 action sentence 有形态区分（含 hosted「switch runner to self」动作），面板没有。表述本身不误导（对 gate 确实无需动作）。判接受不修。

### P3-c：面板「当前裁决」行机器码无人话

`classification/reason_code` 原样上浮，人话解释只在 Step Summary。判接受不修（面板是投影，详情入口是 run 链接）。

### P3-d（OCR shell 健壮性）：identity 解析只查最后一个 jq 的 $?

L610-612：第一条 `jq`（workflow_id）失败而第二条成功时 `$?` 为 0 漏检，空 workflow_id 会在后续 `--argjson` 处炸成 lookup 失败分支——仍落入有诊断的 fail-open 出口，无静默。判接受不修。

## 最终判定

**本轮无新增 P1（收敛达成候选）**。「该响的响了吗」反向猎捕：穷举 26 个捕获点，实测 6 个此前未测分支的全部第二出口真实产生且内容可定位；「upload 失败 → publish 跳过」整轮对外可见性为红检查 + 飞书通知，非静默。新增 P2×1（建议接受/顺手修）+ P3×4（判接受不修）。R2 已一轮无新增 P1，本轮再干净，满足「连续 2 轮无新增 P1」收敛条件。
