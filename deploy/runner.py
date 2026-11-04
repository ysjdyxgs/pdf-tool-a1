#!/usr/bin/env python3
"""GitHub Actions 版：轮询飞书群 → 识别「N层」→ 读在线表格 → 出 PDF → 发回群。

只用飞书开放平台 HTTP API，不依赖 lark-cli。
环境变量：FEISHU_APP_ID / FEISHU_APP_SECRET

工作流：
  1. 每轮先比对在线表格结构与 templates/<层>.json，有变化就更新模板并通知，本轮不出图
  2. 结构一致后，才把消息里的「N层」当成出图指令
"""
import json, os, re, subprocess, sys
import requests
from openpyxl import Workbook, load_workbook

BASE = "https://open.feishu.cn/open-apis"
CONFIG = "deploy/config.json"
STATE = "deploy/state.json"
TPL_DIR = "deploy/templates"
PIPELINE = ".trae/skills/dwg-sheet-text-pdf/scripts/pipeline.py"

STRUCT_KEYS = ("header", "rows", "info")


# ---------------------------------------------------------------- 飞书 API
def token():
    r = requests.post(f"{BASE}/auth/v3/tenant_access_token/internal",
                      json={"app_id": os.environ["FEISHU_APP_ID"],
                            "app_secret": os.environ["FEISHU_APP_SECRET"]}, timeout=20)
    r.raise_for_status()
    return r.json()["tenant_access_token"]


def api(tk, method, path, **kw):
    r = requests.request(method, f"{BASE}{path}", headers={"Authorization": f"Bearer {tk}"}, timeout=30, **kw)
    data = r.json()
    if data.get("code") not in (0, None):
        raise RuntimeError(f"{path}: {data.get('code')} {data.get('msg')}")
    return data.get("data", {})


def sheet_ids(tk, sht):
    return {s["title"]: s["sheet_id"] for s in api(tk, "GET", f"/sheets/v3/spreadsheets/{sht}/sheets/query")["sheets"]}


def values(tk, sht, sheet_id, rng="A1:F200"):
    d = api(tk, "GET", f"/sheets/v2/spreadsheets/{sht}/values/{sheet_id}!{rng}")
    return d.get("valueRange", {}).get("values", [])


def structure(tk, sht):
    """在线表格的结构：表头、数据行数、项目信息标签。"""
    ids = sheet_ids(tk, sht)
    grid = [r for r in values(tk, sht, ids["材料清单"]) if any(str(c).strip() for c in r if c is not None)]
    header = [str(c).strip() for c in grid[0]] if grid else []
    info = []
    for r in values(tk, sht, ids["项目信息"], "A1:B20"):
        if not r or not str(r[0] or "").strip():
            continue
        label = str(r[0]).strip()
        second = str(r[1]).strip() if len(r) > 1 and r[1] is not None else ""
        if second in ("值", "内容") or label in ("项目", "项目信息", "名称"):
            continue
        info.append(label)
    return {"header": header, "rows": max(0, len(grid) - 1), "info": info}


def build_xlsx(tk, sht, path):
    ids = sheet_ids(tk, sht)
    wb = Workbook()
    ws = wb.active
    ws.title = "材料清单"
    for row in values(tk, sht, ids["材料清单"]):
        ws.append(row)
    ws2 = wb.create_sheet("项目信息")
    for row in values(tk, sht, ids["项目信息"], "A1:B20"):
        ws2.append(row)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    wb.save(path)


def messages(tk, chat_id, size=10):
    d = api(tk, "GET", "/im/v1/messages",
            params={"container_id_type": "chat", "container_id": chat_id,
                    "sort_type": "ByCreateTimeDesc", "page_size": size})
    return d.get("items", [])


def text_of(m):
    if m.get("msg_type") != "text":
        return ""
    try:
        return json.loads(m["body"]["content"]).get("text", "")
    except Exception:
        return ""


def send_text(tk, chat_id, text):
    api(tk, "POST", "/im/v1/messages", params={"receive_id_type": "chat_id"},
        json={"receive_id": chat_id, "msg_type": "text",
              "content": json.dumps({"text": text}, ensure_ascii=False)})


def send_file(tk, chat_id, path):
    with open(path, "rb") as f:
        r = requests.post(f"{BASE}/im/v1/files",
                          headers={"Authorization": f"Bearer {tk}"},
                          data={"file_type": "stream", "file_name": os.path.basename(path)},
                          files={"file": f}, timeout=60)
    key = r.json()["data"]["file_key"]
    api(tk, "POST", "/im/v1/messages", params={"receive_id_type": "chat_id"},
        json={"receive_id": chat_id, "msg_type": "file",
              "content": json.dumps({"file_key": key})})


# ---------------------------------------------------------------- 模板
def tpl_path(layer):
    return os.path.join(TPL_DIR, f"{layer}.json")


def load_tpl(layer):
    p = tpl_path(layer)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def diff_struct(old, new):
    """只比较结构字段；模板里的手工配置（如 dwg 路径）不受影响。"""
    changes = []
    if old.get("header") != new.get("header"):
        changes.append(f"表头 {old.get('header')} → {new.get('header')}")
    if old.get("rows") != new.get("rows"):
        changes.append(f"行数 {old.get('rows')} → {new.get('rows')}")
    if old.get("info") != new.get("info"):
        changes.append(f"项目信息 {old.get('info')} → {new.get('info')}")
    return changes


def sync_tpl(layer, struct):
    tpl = load_tpl(layer)
    old = {k: tpl.get(k) for k in STRUCT_KEYS}
    changes = diff_struct(old, struct)
    if changes:
        tpl.update(struct)
        os.makedirs(TPL_DIR, exist_ok=True)
        json.dump(tpl, open(tpl_path(layer), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return changes


# ---------------------------------------------------------------- 出图
def generate(cfg, layer):
    item = cfg["layers"][layer]
    tmp = f"out/{layer}.xlsx"
    build_xlsx(token(), item["spreadsheet"], tmp)
    inv = os.path.join("work", os.path.basename(item["inventory"]))
    os.makedirs("work", exist_ok=True)
    if os.path.exists(item["inventory"]):
        subprocess.run(["cp", item["inventory"], inv], check=True)
        os.utime(inv)
    os.makedirs(os.path.dirname(item["out"]) or "out", exist_ok=True)
    r = subprocess.run([sys.executable, PIPELINE, item["dwg"], "--from-xlsx", tmp,
                        "--out", item["out"], "--workdir", "work"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None, (r.stdout + r.stderr).strip()[-200:]
    info = next((l.replace("替换：", "").strip() for l in r.stdout.splitlines()
                 if l.startswith("替换：")), "")
    return item["out"], info


# ---------------------------------------------------------------- 本地自检
def selftest(path):
    """用本地 xlsx 模拟在线表格，验证结构比对逻辑。"""
    wb = load_workbook(path, data_only=True)
    ws = wb["材料清单"] if "材料清单" in wb.sheetnames else wb.worksheets[0]
    grid = [list(r) for r in ws.iter_rows(values_only=True)
            if any(str(c).strip() for c in r if c is not None)]
    header = [str(c).strip() for c in grid[0]] if grid else []
    rows = max(0, len(grid) - 1)
    info = []
    if "项目信息" in wb.sheetnames:
        for r in wb["项目信息"].iter_rows(values_only=True):
            if not r or not str(r[0] or "").strip():
                continue
            label = str(r[0]).strip()
            second = str(r[1]).strip() if len(r) > 1 and r[1] is not None else ""
            if second in ("值", "内容") or label in ("项目", "项目信息", "名称"):
                continue
            info.append(label)
    struct = {"header": header, "rows": rows, "info": info}
    layer = os.path.splitext(os.path.basename(path))[0].replace("装箱清单", "")
    tpl = load_tpl(layer)
    changes = diff_struct({k: tpl.get(k) for k in STRUCT_KEYS}, struct)
    print(f"表格结构：表头 {header}，数据 {rows} 行，信息 {info}")
    print("模板差异：" + ("；".join(changes) if changes else "无"))
    if changes and os.environ.get("SELFTEST_WRITE"):
        sync_tpl(layer, struct)
        print("已写入模板")


# ---------------------------------------------------------------- 主流程
def main():
    if "--self-test" in sys.argv:
        return selftest(sys.argv[sys.argv.index("--self-test") + 1])

    cfg = json.load(open(CONFIG, encoding="utf-8"))
    chat_id = cfg["chat_id"]
    tk = token()

    # 1) 结构比对：有变化的层数本轮不出图，只通知
    changed_layers = set()
    for layer, item in cfg["layers"].items():
        try:
            ch = sync_tpl(layer, structure(tk, item["spreadsheet"]))
        except Exception as e:
            send_text(tk, chat_id, f"读取「{layer}」表格失败：{e}")
            changed_layers.add(layer)
            continue
        if ch:
            changed_layers.add(layer)
            send_text(tk, chat_id,
                      f"「{layer}」在线表格结构已变更：{'；'.join(ch)}\n"
                      f"已同步模板，请复核后再发指令出图。")

    # 2) 处理指令
    seen = json.load(open(STATE)) if os.path.exists(STATE) else {"seen": []}
    ids = seen.get("seen", [])
    pat = re.compile(r"(\d+)\s*层[。.!！]?")
    for m in reversed(messages(tk, chat_id)):
        mid = m.get("message_id")
        if not mid or mid in ids:
            continue
        ids.append(mid)
        json.dump({"seen": ids[-100:]}, open(STATE, "w"))
        if m.get("sender", {}).get("sender_type") == "app":
            continue
        txt = re.sub(r"@\S+", "", text_of(m)).strip()
        hit = pat.fullmatch(txt)
        if not hit:
            continue
        key = f"{hit.group(1)}层"
        if key not in cfg["layers"]:
            send_text(tk, chat_id, f"没有「{key}」的数据表")
            continue
        if key in changed_layers:
            send_text(tk, chat_id, f"「{key}」结构刚变更，本轮未出图，请复核模板后再发指令。")
            continue
        send_text(tk, chat_id, "收到指令，正在按在线表格出图…")
        pdf, info = generate(cfg, key)
        if not pdf:
            send_text(tk, chat_id, f"生成失败：{info}")
            continue
        send_file(tk, chat_id, pdf)
        send_text(tk, chat_id, f"PDF 已生成（{info}）")


if __name__ == "__main__":
    main()
