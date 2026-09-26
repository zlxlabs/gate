# disposition-secret-contract progress

## 阶段
implementing → draft PR（Refs #248）

## 结论
`gate-v2-disposition.yml` 的 `on.workflow_call` 原只声明 inputs、无 secrets，control 作业却消费
`SILO_ACCESS_KEY`/`SILO_SECRET_KEY`，公共 caller 只能 `secrets: inherit`。现新增两条
optional（`required: false`、中文 description）声明：旧 inherit caller 不受影响，新 caller 可按名显式
mapping。不改鉴权、处置路径与任何 run 字节；缺钥失败沿用既有「SILO_ACCESS_KEY 未传入」显式错误。
契约测试从真实 producer YAML 反解 control env 消费的 secrets 集合，锁「声明集 == 消费集 == 该 pair
且均 optional」；在 base 上先红（declared 为空集）后绿。

## 决策否决
不声明 FEISHU 等其它 secret、不加 fallback、不动 caller 模板与 hub 侧；第二消费者为公共仓显式
mapping caller。

## 下一步
v2 tag 自动验证推广（canary 绿后 v2-tag-sync 抬升）前，新 public caller 不得启用；主脑验收。
