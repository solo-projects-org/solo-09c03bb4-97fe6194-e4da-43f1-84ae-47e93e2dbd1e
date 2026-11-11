"""Flask API 层测试：路由、错误码与样例端到端。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" :: {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


client = app.test_client()
SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "data", "sample_clearing.json")
with open(SAMPLE, encoding="utf-8") as fh:
    sample = json.load(fh)

# 首页
r = client.get("/")
check("首页 200", r.status_code == 200 and "换乘标志".encode() in r.data)

# 样例接口
r = client.get("/api/sample")
check("样例接口", r.status_code == 200 and r.get_json()["models"])

# 校验通过
r = client.post("/api/validate", json=sample)
body = r.get_json()
check("validate 通过", r.status_code == 200 and body["ok"] is True)
check("validate 返回源表", len(body["source_tables"]) == 3)
check("validate 返回模型依赖",
      {m["name"]: m["upstream"] for m in body["models"]}["ads_transfer_settlement"]
      == ["dwd_card_transaction_detail", "dws_line_transfer_summary"])

# 分析
r = client.post("/api/analyze?table=ods_card_transaction&column=transfer_flag", json=sample)
body = r.get_json()
check("analyze 通过", r.status_code == 200 and body["ok"] is True)
check("analyze 命中 3 模型", body["result"]["model_count"] == 3)
check("analyze 命中 19 列", body["result"]["column_count"] == 19)

# 缺参数
r = client.post("/api/analyze", json=sample)
check("缺参数 400", r.status_code == 400 and r.get_json()["ok"] is False)

# 坏 JSON
r = client.post("/api/validate", data="{oops", content_type="application/json")
check("坏 JSON 400", r.status_code == 400 and "JSON" in r.get_json()["error"])

# 空 body
r = client.post("/api/validate", data="", content_type="application/json")
check("空 body 400", r.status_code == 400 and "空" in r.get_json()["error"])

# 无法解析的引用 -> 指出模型并停止
bad = {
    "source_tables": [{"table": "t", "columns": ["a"]}],
    "models": [{"name": "boom", "sql": "SELECT z.a AS a FROM t x"}],
}
r = client.post("/api/validate", json=bad)
check("未知别名 400 带模型名",
      r.status_code == 400 and "boom" in r.get_json()["error"] and "z" in r.get_json()["error"])

# 非字典顶层
r = client.post("/api/validate", json=[1, 2, 3])
check("顶层非对象 400", r.status_code == 400)

# 目标字段不存在
r = client.post("/api/analyze?table=ods_card_transaction&column=ghost", json=sample)
check("目标列不存在 400", r.status_code == 400 and "ghost" in r.get_json()["error"])

# 未被任何模型读取的字段 -> 零影响
r = client.post("/api/analyze?table=ods_card_info&column=issue_city", json=sample)
body = r.get_json()
check("未读取字段零影响",
      r.status_code == 200 and body["result"]["model_count"] == 0)

# 长链清分样例：1201 个模型逐层引用，校验与分析完整、报告线性
r = client.get("/api/sample/longchain")
longchain = r.get_json()
check("长链样例接口", r.status_code == 200 and len(longchain["models"]) == 1201)

r = client.post("/api/validate", json=longchain)
check("长链校验通过", r.status_code == 200 and r.get_json()["ok"] is True)

r = client.post("/api/analyze?table=ods_card_transaction&column=transfer_flag", json=longchain)
body = r.get_json()
check("长链分析通过", r.status_code == 200 and body["ok"] is True)
res = body["result"]
check("长链分析-1201个模型全部受影响", res["model_count"] == 1201, str(res["model_count"]))
# 共用前缀只传输一份：步骤数与受影响列数同阶，而非逐列重复携带上游路径
check("长链分析-步骤数线性",
      len(res["path_steps"]) <= res["column_count"] + 1,
      f'{len(res["path_steps"])} vs {res["column_count"]}')
# 末端结算列的完整路径可沿 prev 回溯到源表字段
settle = next(m for m in res["affected_models"] if m["model"] == "ads_transfer_settlement_chain")
settle_col = next(c for c in settle["columns"] if c["column"] == "settlement_amount")
depth, cur = 0, settle_col["path_id"]
while cur is not None:
    cur = res["path_steps"][cur]["prev"]
    depth += 1
head = res["path_steps"][settle_col["path_id"]]
while head["prev"] is not None:
    head = res["path_steps"][head["prev"]]
check("长链分析-结算列路径超1200步", depth > 1200, str(depth))
check("长链分析-结算列路径回到源表",
      head["type"] == "source" and head["relation"] == "ods_card_transaction"
      and head["column"] == "transfer_flag")
check("长链分析-结算列混合标记", settle_col["via"] == "mixed", settle_col["via"])

print()
if FAILURES:
    print(f"{len(FAILURES)} 项失败：{FAILURES}")
    sys.exit(1)
print("API 测试全部通过。")
