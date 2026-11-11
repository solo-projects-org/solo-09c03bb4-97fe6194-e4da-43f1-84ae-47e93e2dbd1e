# 公交清分数仓 · 换乘标志口径调整影响分析

公交清分数仓要调整「换乘标志」字段的口径，工程师需要先找出**需要复核的明细列**。
本工具用 [SQLGlot](https://github.com/tobymao/sqlglot) 解析 DuckDB SQL，
从选中的源表字段出发，沿模型血缘传播，给出：

- 受影响的**模型**与每个模型的**受影响输出列**；
- 每列**一条可回溯到源表字段的依赖路径**，逐跳标注是「值传递」还是「条件污染」。

纯本地运行，**不需要连接数仓**；网页可一键载入清分样例与长链清分样例。
长依赖链（如 **1200 个模型逐层引用**）可完整分析：拓扑排序为迭代实现，
多条路径共用的前缀在分析与传输中只保留一份（详见下方「路径压缩传输」）。

## 分析口径

### 支持的 SQL 范围

- 普通 `SELECT`、**非递归** CTE（`WITH`）、`INNER JOIN`、`WHERE`；
- 禁止星号（`SELECT *` / `t.*`），所有列引用必须带表名或别名；
- 支持 `WHERE ... IN (SELECT ...)` / `WHERE EXISTS (SELECT ...)` 形式的非关联谓词子查询、
  FROM 派生表（需别名）；投影中允许任意函数、运算、`CASE`、类型转换等表达式；
- 不支持递归 CTE、关联子查询、SELECT 投影中的标量子查询、`UNION` 等集合运算、
  `GROUP BY`、窗口函数、`LEFT/RIGHT/FULL/CROSS JOIN`、逗号隐式连接、DML 等，
  遇到即报错指出所在模型并停止分析。

### 传播规则

1. **取值依赖按列引用传递**：输出表达式引用了某列，该列的污染传到这个输出列。
2. **条件污染整表扩散**：字段一旦进入某查询的 `JOIN ON`、`WHERE` 或
   `IN (SELECT ...)` 谓词（含谓词子查询的污染结果集），该查询的**全部输出列**都标记受影响。
3. **跨模型只走真实读取**：下游模型实际 `FROM/JOIN` 读取了上游模型的哪些输出列，
   才接收对应污染；模型可读取上游模型，模型依赖必须是有向无环图。
4. 任一列引用无法解析（未知表/别名、源表或上游模型上不存在的列、未限定列名等）时，
   指出所在模型与具体位置并**停止分析**。

## 目录结构

```
.
├── docker-compose.yml          # 一键启动
└── app/
    ├── Dockerfile
    ├── requirements.txt
    ├── app.py                  # Flask 路由（/、/api/sample、/api/sample/longchain、/api/validate、/api/analyze）
    ├── lineage.py              # SQLGlot 解析、血缘构建、污染传播与压缩路径构造
    ├── data/
    │   └── sample_clearing.json  # 清分样例（3 源表 + 3 模型，含 CTE 与多跳链路）
    ├── templates/
    │   └── index.html          # 单页前端（无外部 CDN 依赖，点开列展开完整路径）
    └── tests/
        ├── test_lineage.py     # 传播规则、长链（1200 层）与错误处理测试
        └── test_api.py         # HTTP API 与长链样例端到端测试
```

## 用 Docker Compose 启动

```bash
docker compose up --build
```

浏览器打开 <http://localhost:8000>：

1. 点击「载入清分样例」（3 模型小例子）或「载入长链样例」（1200 个模型逐层引用 + 结算层），或上传自己的 JSON；
2. 在左侧选择源表与待调整字段（如 `ods_card_transaction.transfer_flag`）；
3. 点击「分析受影响模型列」，右侧先列出受影响模型与输出列；
4. 点击某列右侧的「依赖路径」，展开从源字段到该列的一条完整路径（含值传递/条件标记）。

停止：`docker compose down`。

## 输入 JSON 格式

```json
{
  "source_tables": [
    { "table": "ods_card_transaction",
      "columns": ["transaction_id", "card_id", "transfer_flag"] }
  ],
  "models": [
    { "name": "dwd_card_transaction_detail",
      "description": "可选说明",
      "sql": "SELECT t.transaction_id AS transaction_id, t.transfer_flag AS transfer_flag FROM ods_card_transaction t WHERE t.transfer_flag = 0" }
  ]
}
```

- `source_tables[].table` 为源表名，`columns` 为该表全部字段；
- `models[].name` 为模型名，模型 SQL 中 `FROM/JOIN` 同名关系即视为读取该上游模型；
- 其余名字按源表处理，必须在 `source_tables` 中声明。

## HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/sample` | 返回内置清分样例 JSON |
| GET | `/api/sample/longchain` | 返回长链清分样例（1200 个模型逐层引用 + 结算层） |
| POST | `/api/validate` | 校验 JSON 与全部 SQL，返回源表字段清单和模型读取关系 |
| POST | `/api/analyze?table=<源表>&column=<字段>` | 返回受影响模型、输出列与压缩路径 |

错误统一返回 HTTP 400 与 `{"ok": false, "error": "中文原因（含模型名）"}`。

### 路径压缩传输

`/api/analyze` 的响应中，依赖路径以**步骤登记表**形式返回，多条路径共用的前缀只传输一份：

```json
{
  "ok": true,
  "result": {
    "affected_models": [
      { "model": "...", "condition_tainted": false, "condition_queries": [],
        "columns": [ { "column": "transfer_flag", "via": "value", "path_id": 7 } ] }
    ],
    "path_steps": [
      { "id": 0, "type": "source", "relation": "ods_card_transaction", "column": "transfer_flag", "prev": null },
      { "id": 7, "type": "node", "model": "...", "query": "...", "output": "transfer_flag",
        "via": "value", "input_relation": "...", "input_column": "transfer_flag", "prev": 0 }
    ]
  }
}
```

- 每列的 `path_id` 指向 `path_steps` 中该列路径的**最后一步**；
- 沿每步的 `prev` 回溯到 `prev: null` 的源表步骤，即还原「源字段 → 该列」的完整路径；
- 长链场景下传输体积为 O(链长)，而非逐列重复携带上游路径的 O(链长²)。

## 本地开发（不用 Docker）

```bash
python -m venv .venv && source .venv/bin
pip install -r app/requirements.txt
python app/app.py            # 监听 8000
python app/tests/test_lineage.py   # 血缘与长链测试
python app/tests/test_api.py       # API 与长链样例端到端测试
```
