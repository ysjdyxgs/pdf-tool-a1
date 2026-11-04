#!/usr/bin/env python3
"""监听飞书会话中的「N层」指令 → 读在线表格 → 出 PDF → 发回会话。

用法：
    python3 watch.py            # 持续监听（默认每 15 秒轮询一次）
    python3 watch.py --once     # 只处理最新一条未处理指令，处理完退出

配置：feishu_map.json（与本脚本同级目录）
    {
      "chat_id": "oc_xxx",
      "layers": {
        "6层": {"spreadsheet": "shtcnxxx", "dwg": "/abs/path/6层.dwg", "out": "/abs/path/6层.pdf"}
      }
    }
"""
import json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONFIG = os.path.join(ROOT, "feishu_map.json")
STATE = os.path.join(ROOT, ".feishu_state.json")
PIPELINE = os.path.join(HERE, "pipeline.py")


def cli(args):
    r = subprocess.run(["lark-cli"] + args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300])
    return json.loads(r.stdout)


def load(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def message_text(m):
    c = m.get("content", "")
    if isinstance(c, str):
        try:
            return json.loads(c).get("text", "") or c
        except Exception:
            return c
    return ""


def command_text(m):
    """去掉 @提及 后再判断，允许「6层@某人」。"""
    return re.sub(r"@\S+", "", message_text(m)).strip()


def recent(chat_id, limit=20):
    data = cli(["im", "+chat-messages-list", "--chat-id", chat_id,
                "--page-size", str(limit), "--order", "desc"])
    d = data.get("data") or {}
    return d.get("messages") or d.get("items") or []


def gen_pdf(layer, cfg):
    item = cfg["layers"][layer]
    out = item.get("out") or f"/workspace/output/{layer}.pdf"
    cmd = [sys.executable, PIPELINE, item["dwg"], "--from-feishu", item["spreadsheet"],
           "--out", out, "--workdir", "/workspace/work/skill"]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd="/workspace")
    if r.returncode != 0:
        return None, (r.stdout + r.stderr).strip()[-200:]
    info = next((l.replace("替换：", "").strip() for l in r.stdout.splitlines()
                 if l.startswith("替换：")), "")
    return out, info


def send_text(chat_id, text):
    cli(["im", "+messages-send", "--chat-id", chat_id, "--text", text])


def send_file(chat_id, path):
    cli(["im", "+messages-send", "--chat-id", chat_id, "--file", path])


def handle(cfg, chat_id, msg, layer):
    # 回复文案不能出现「数字+层」，否则会被自己当成新指令
    send_text(chat_id, "收到指令，正在按在线表格出图…")
    pdf, info = gen_pdf(layer, cfg)
    if not pdf:
        send_text(chat_id, f"生成失败：{info}")
        return
    send_file(chat_id, pdf)
    send_text(chat_id, f"PDF 已生成（{info}）")


def main():
    once = "--once" in sys.argv
    cfg = load(CONFIG, {})
    if not cfg.get("layers"):
        raise SystemExit(f"缺少配置：{CONFIG}")

    chat_id = cfg["chat_id"]
    state = load(STATE, {"seen": []})
    seen = list(state.get("seen", []))
    interval = int(cfg.get("interval_seconds", 15))
    pat = re.compile(r"(\d+)\s*层[。.!！]?")

    while True:
        for m in reversed(recent(chat_id)):
            mid = m.get("message_id")
            if not mid or mid in seen:
                continue
            seen.append(mid)
            json.dump({"seen": seen[-100:]}, open(STATE, "w"))
            if m.get("msg_type") != "text":
                continue
            if (m.get("sender") or {}).get("sender_type") == "app":
                continue
            hit = pat.fullmatch(command_text(m))
            key = f"{hit.group(1)}层" if hit else ""
            if key and key in cfg["layers"]:
                handle(cfg, chat_id, m, key)
            elif key:
                send_text(chat_id, f"没有「{key}」的数据表，可用：" + "、".join(cfg["layers"]))
        if once:
            print("已处理完当前指令")
            return
        time.sleep(interval)


if __name__ == "__main__":
    main()
