#!/usr/bin/env python3
"""DWG 图纸：提取「材料清单 + 项目信息」为 JSON 配置，按配置替换文字并输出矢量 PDF。

用法见 ../SKILL.md
"""
import argparse, csv, io, json, math, os, re, subprocess, sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon

def _pick_font_family():
    """优先思源宋体（CI 上由 fonts-noto-cjk 提供）；Windows 上退回 Noto Serif SC / 系统宋体。"""
    from matplotlib import font_manager as fm
    for p in ("C:/Windows/Fonts/simsun.ttc",
              "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
              "/usr/share/fonts/truetype/noto/NotoSerifCJK-Regular.ttc"):
        if os.path.exists(p):
            try:
                fm.fontManager.addfont(p)
            except Exception:
                pass
    names = {f.name for f in fm.fontManager.ttflist}
    return [n for n in ("Noto Serif CJK SC", "Noto Serif SC", "SimSun") if n in names] + ["DejaVu Serif"]


FONT_FAMILY = _pick_font_family()
matplotlib.rcParams["font.family"] = FONT_FAMILY

TABLE_LABELS = ("名称", "型号规格", "长度", "数量", "单位")
INFO_LABELS = ("生产编号", "颜色", "项目名称", "项目名称：", "图号", "图名")


# ---------------------------------------------------------------- 文本清洗
def clean(s):
    if not s:
        return ""
    s = s.replace("\\P", "\n")
    s = re.sub(r"\\[AaLlOoKk][^;\\]*;", "", s)
    s = re.sub(r"\\[fFhHwWqQcCpPtT][^;\\]*;", "", s)
    s = s.replace("{", "").replace("}", "")
    for a, b in (("%%P", "±"), ("%%p", "±"), ("%%D", "°"), ("%%d", "°"), ("%%C", "Ø")):
        s = s.replace(a, b)
    s = s.replace("\u02c2", "<").replace("\u02c3", ">")
    s = s.replace("\u02b9", "\u2032").replace("\u2032", "\u2032")
    s = s.replace("\u2033", "\u2033").replace("\u02ba", "\u2033")
    s = s.replace("\uff1e", ">").replace("\uff1c", "<").replace("\u00a0", " ")
    return s.strip()


def cell_text(o):
    raw = o.get("text") if o.get("entity") == "MTEXT" else o.get("text_value")
    return clean(raw)


def text_key(o):
    return clean(o.get("text") if o.get("entity") == "MTEXT" else o.get("text_value"))


# ---------------------------------------------------------------- 数据加载
def load_inventory(path):
    d = json.load(open(path))
    objs = [o for o in d["OBJECTS"]]
    hs = {o["handle"][-1]: o for o in objs if isinstance(o.get("handle"), list)}
    return objs, hs


def ensure_inventory(dwg, workdir):
    os.makedirs(workdir, exist_ok=True)
    out = os.path.join(workdir, os.path.splitext(os.path.basename(dwg))[0] + ".inventory.json")
    if os.path.exists(out) and os.path.getmtime(out) > os.path.getmtime(dwg):
        return out
    subprocess.run(["dwgread", "-O", "JSON", "-o", out, dwg], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out


# ---------------------------------------------------------------- 表格识别
def cluster(values, tol):
    vals = sorted(values)
    groups, cur = [], [vals[0]]
    for v in vals[1:]:
        if v - cur[-1] <= tol:
            cur.append(v)
        else:
            groups.append(cur)
            cur = [v]
    groups.append(cur)
    return groups


def find_table(objs):
    text_objs = [o for o in objs if o.get("entity") in ("MTEXT", "TEXT") and o.get("ins_pt")]
    header = [o for o in text_objs if text_key(o) in TABLE_LABELS]
    if len(header) < 3:
        return None
    hy = sorted({round(o["ins_pt"][1], 1) for o in header})
    hy = max(hy, key=lambda y: sum(1 for o in header if abs(o["ins_pt"][1] - y) < 1.2))
    header = sorted([o for o in header if abs(o["ins_pt"][1] - hy) < 1.2], key=lambda o: o["ins_pt"][0])
    hx0, hx1 = header[0]["ins_pt"][0], header[-1]["ins_pt"][0]

    cells = [o for o in text_objs
             if hx0 - 12 <= o["ins_pt"][0] <= hx1 + 12
             and hy - 230 <= o["ins_pt"][1] <= hy - 1.0]
    if not cells:
        return None

    # 列中心用表头位置（同系列图纸表头稳定），比聚类更可靠
    col_x = [o["ins_pt"][0] for o in header]
    ygroups = sorted(cluster([o["ins_pt"][1] for o in cells], 1.6), reverse=True)

    # 行距约 3 单位，出现 > 6 单位的空档即表格结束（避免把下方项目信息当成表格行）
    cut = len(ygroups)
    for i in range(len(ygroups) - 1):
        if (sum(ygroups[i]) / len(ygroups[i])) - (sum(ygroups[i + 1]) / len(ygroups[i + 1])) > 6.0:
            cut = i + 1
            break
    ygroups = ygroups[:cut]

    rows = []
    for gy in ygroups:
        lo, hi = min(gy) - 0.9, max(gy) + 0.9
        row = [None] * len(col_x)
        for o in cells:
            if not (lo <= o["ins_pt"][1] <= hi):
                continue
            ci = min(range(len(col_x)), key=lambda i: abs(o["ins_pt"][0] - col_x[i]))
            if row[ci] is None or abs(o["ins_pt"][0] - col_x[ci]) < abs(row[ci]["ins_pt"][0] - col_x[ci]):
                row[ci] = o
        if [c for c in row if c]:
            rows.append(row)
    return {"header": header, "rows": rows, "col_x": col_x, "y": hy}


def find_info(objs):
    out = []
    for o in objs:
        if o.get("entity") not in ("MTEXT", "TEXT") or not o.get("ins_pt"):
            continue
        t = text_key(o)
        m = re.match(r"(生产编号|颜色|项目名称|图号|图名)\s*([：:])\s*(.*)$", t)
        if m:
            out.append((o, m.group(1), m.group(2), m.group(3)))
    return out


# ---------------------------------------------------------------- spec
def make_spec(objs, hs):
    tb = find_table(objs)
    spec = {"title": "", "page_width_mm": 594.0, "table": None, "info": []}
    if tb:
        spec["table"] = {
            "columns": [text_key(o) for o in tb["header"]],
            "rows": [[text_key(c) if c else "" for c in row] for row in tb["rows"]],
        }
    for _, label, sep, val in find_info(objs):
        spec["info"].append({"label": label, "value": val})
    return spec


# ---------------------------------------------------------------- 纯文本表单
# 窄屏格式：每行材料两行，第一行是只读的「编号 名称 规格 单位」，第二行只填 长/数
FORM_HEADER = """# {title} 数据填写表
# 只改 长= 和 数= 的数字
# 留空=沿用原图，编号别动
[表格]
"""

KEYMAP = {"名": 0, "名称": 0, "型": 1, "型号": 1, "型号规格": 1,
          "长": 2, "长度": 2, "数": 3, "数量": 3, "单": 4, "单位": 4}


def make_form(objs, title="图纸"):
    spec = make_spec(objs, None)
    lines = []
    rows = spec["table"]["rows"] if spec["table"] else []
    for i, row in enumerate(rows, 1):
        cells = [str(c).replace("|", "/").strip() for c in row] + [""] * 5
        name, model, length, qty, unit = cells[:5]
        lines.append(" ".join(f"{i:02d} {name} {model} {unit}".split()))
        lines.append(f"长={length} 数={qty}")
    out = FORM_HEADER.format(title=title) + "\n".join(lines) + "\n\n[项目信息]\n"
    for item in spec["info"]:
        out += f"{item['label']}={item['value']}\n"
    return out


def parse_form(path, base=None):
    base = base or {"page_width_mm": 594.0,
                    "table": {"columns": list(TABLE_LABELS), "rows": []}, "info": []}
    rows = [list(r) for r in base["table"]["rows"]] if base.get("table") else []
    infos = {it["label"]: it["value"] for it in base.get("info", [])}
    order = [it["label"] for it in base.get("info", [])]

    section, cur, seq = "table", None, 0
    for raw in open(path, encoding="utf-8-sig").read().splitlines():
        line = raw.replace("\uff5c", "|").strip()
        if not line or line.startswith("#"):
            continue
        low = line.replace(" ", "")
        if low.startswith("[项目信息]"):
            section = "info"
            continue
        if low.startswith("[表格]"):
            section = "table"
            continue

        if section == "info":
            parts = re.split(r"[|：:]|=", line, maxsplit=1)
            if len(parts) == 2:
                infos[parts[0].strip()] = parts[1].strip()
            continue

        m = re.match(r"^(\d+)\b", line)
        if m:
            cur = int(m.group(1)) - 1
            while len(rows) <= cur:
                rows.append(["", "", "", "", ""])
            continue

        if "|" in line:                      # 兼容宽表格式
            parts = [p.strip() for p in line.split("|")]
            if parts and re.fullmatch(r"\d+", parts[0]):
                parts = parts[1:]
            parts += [""] * (5 - len(parts))
            rows[seq % len(rows)] = parts[:5] if rows else parts[:5]
            seq += 1
            continue

        for k, v in re.findall(r"([^\s=]+)=([^\s]*)", line):
            ci = KEYMAP.get(k.strip())
            if ci is None or cur is None:
                continue
            v = v.strip()
            if v:                            # 空值 = 沿用原图
                rows[cur][ci] = v

    spec = {"page_width_mm": base.get("page_width_mm", 594.0),
            "table": {"columns": base["table"]["columns"] if base.get("table") else list(TABLE_LABELS),
                      "rows": rows},
            "info": [{"label": lb, "value": infos.get(lb, "")} for lb in order]}
    return spec


# ---------------------------------------------------------------- Excel 表单
def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def make_xlsx(objs, path, title="图纸"):
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

    spec = make_spec(objs, None)
    wb = Workbook()
    ws = wb.active
    ws.title = "材料清单"

    headers = ["序号", "名称", "型号规格", "长度", "数量", "单位"]
    thin = Side(style="thin", color="B0B0B0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    ws.append(headers)
    for c in range(1, len(headers) + 1):
        cell = ws.cell(1, c)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DDEBF7")
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border

    rows = spec["table"]["rows"] if spec["table"] else []
    for i, row in enumerate(rows, 1):
        cells = [str(c).replace("|", "/").strip() for c in row] + [""] * 5
        ws.append([i] + cells[:5])
        for c in range(1, len(headers) + 1):
            cell = ws.cell(i + 1, c)
            cell.border = border
            cell.alignment = Alignment(
                horizontal="center" if c in (1, 4, 5, 6) else "left",
                vertical="center")

    for col, w in zip("ABCDEF", (6, 22, 18, 10, 10, 8)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:F{max(len(rows) + 1, 2)}"

    ws2 = wb.create_sheet("项目信息")
    ws2.append(["字段", "值"])
    for c in (1, 2):
        cell = ws2.cell(1, c)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DDEBF7")
        cell.border = border
    for r, item in enumerate(spec["info"], 2):
        ws2.cell(r, 1, item["label"]).border = border
        ws2.cell(r, 2, item["value"]).border = border
    ws2.column_dimensions["A"].width = 14
    ws2.column_dimensions["B"].width = 24

    ws3 = wb.create_sheet("说明")
    ws3["A1"] = f"{title} 数据表"
    ws3["A3"] = "1. 在「材料清单」里改 长度 / 数量（其它列也可改），空单元格=沿用原图"
    ws3["A4"] = "2. 不要删除或改动「序号」列，行数需与图纸一致"
    ws3["A5"] = "3. 在「项目信息」里改「值」列"
    ws3["A6"] = "4. 保存后交回，直接按本表出 PDF"
    ws3.column_dimensions["A"].width = 70

    wb.properties.creator = ""
    wb.properties.lastModifiedBy = ""
    wb.properties.title = title
    wb.save(path)
    strip_watermarks(path)


HDR_MAP = {"名称": 0, "型号规格": 1, "型号": 1, "长度": 2, "数量": 3, "单位": 4}


def apply_grid(grid, base=None):
    """把任意二维单元格（Excel/飞书）按表头映射写回 base 的表格行，空值沿用原值。"""
    base = base or {"page_width_mm": 594.0,
                    "table": {"columns": list(TABLE_LABELS), "rows": []}, "info": []}
    rows = [list(r) for r in base["table"]["rows"]] if base.get("table") else []

    hdr_row, colmap, idx_col = None, {}, None
    for r in range(min(len(grid), 9)):
        found = {}
        for c, v in enumerate(grid[r]):
            v = _fmt(v).replace(" ", "")
            if v in HDR_MAP:
                found[c] = HDR_MAP[v]
            elif v in ("序号", "编号", "No"):
                idx_col = c
        if len(found) >= 3:
            hdr_row, colmap = r, found
            break
    if hdr_row is None:
        raise ValueError("找不到表头（名称/型号规格/长度/数量/单位）")

    seq = 0
    for r in range(hdr_row + 1, len(grid)):
        row = grid[r]
        vals = {ci: _fmt(row[c]) for c, ci in colmap.items() if c < len(row)}
        if not any(vals.values()):
            continue
        raw_idx = _fmt(row[idx_col]) if idx_col is not None and idx_col < len(row) else ""
        target = int(raw_idx) - 1 if re.fullmatch(r"\d+", raw_idx) else seq
        seq += 1
        while len(rows) <= target:
            rows.append(["", "", "", "", ""])
        for ci, v in vals.items():
            if v:
                rows[target][ci] = v
    return rows


def parse_xlsx(path, base=None):
    from openpyxl import load_workbook

    base = base or {"page_width_mm": 594.0,
                    "table": {"columns": list(TABLE_LABELS), "rows": []}, "info": []}
    infos = {it["label"]: it["value"] for it in base.get("info", [])}
    order = [it["label"] for it in base.get("info", [])]

    wb = load_workbook(path, data_only=True)
    ws = wb["材料清单"] if "材料清单" in wb.sheetnames else wb.worksheets[0]
    grid = [[ws.cell(r, c).value for c in range(1, ws.max_column + 1)]
            for r in range(1, ws.max_row + 1)]
    rows = apply_grid(grid, base)

    if "项目信息" in wb.sheetnames:
        ws2 = wb["项目信息"]
        for r in range(2, ws2.max_row + 1):
            label = _fmt(ws2.cell(r, 1).value)
            value = _fmt(ws2.cell(r, 2).value)
            if label in infos and value:
                infos[label] = value

    return {"page_width_mm": base.get("page_width_mm", 594.0),
            "table": {"columns": base["table"]["columns"] if base.get("table") else list(TABLE_LABELS),
                      "rows": rows},
            "info": [{"label": lb, "value": infos.get(lb, "")} for lb in order]}


def strip_watermarks(path):
    """删除 Office 文件中被注入的「含 AI 生成」水印绘图部件。

    环境会在保存 xlsx/docx/pptx 后追加 xl/drawings/drawing_wm*.xml（形状名 AILabel），
    这里同时移除部件、关系项、工作表中的 <drawing> 引用和 Content_Types 声明。
    """
    import posixpath, zipfile
    if not zipfile.is_zipfile(path):
        return 0
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        data = {n: z.read(n) for n in names}

    removed = {n for n in names
               if "drawing_wm" in n
               or (n.startswith(("xl/drawings/", "word/", "ppt/")) and n.endswith(".xml")
                   and (b"AILabel" in data[n] or "含 AI 生成".encode() in data[n]))}
    if not removed:
        return 0

    # 1) 从 .rels 中删除指向水印部件的关系，记录被删的 rId
    drop_ids = {}
    for n in list(data):
        if not n.endswith(".rels"):
            continue
        owner = n.replace("_rels/", "").replace(".rels", "")
        owner_dir = posixpath.dirname(owner)
        xml = data[n].decode("utf-8")
        for m in re.finditer(r'<Relationship [^>]*Id="([^"]+)"[^>]*Target="([^"]+)"[^>]*/>', xml):
            rid, target = m.group(1), m.group(2)
            full = posixpath.normpath(posixpath.join(owner_dir, target))
            if full in removed:
                drop_ids.setdefault(owner, set()).add(rid)
                xml = xml.replace(m.group(0), "")
        data[n] = xml.encode("utf-8")

    # 2) 删除工作表/文档中的引用
    for owner, rids in drop_ids.items():
        if owner not in data:
            continue
        xml = data[owner].decode("utf-8")
        for rid in rids:
            xml = re.sub(r'<drawing[^>]*r:id="%s"[^>]*/>' % re.escape(rid), "", xml)
            xml = re.sub(r'<w:drawing[^>]*r:embed="%s"[^>]*>.*?</w:drawing>' % re.escape(rid),
                         "", xml, flags=re.S)
        data[owner] = xml.encode("utf-8")

    # 3) 删除 Content_Types 中的 Override 声明
    if "[Content_Types].xml" in data:
        ct = data["[Content_Types].xml"].decode("utf-8")
        for n in removed:
            ct = re.sub(r'<Override PartName="/%s"[^>]*/>' % re.escape(n), "", ct)
        data["[Content_Types].xml"] = ct.encode("utf-8")

    for n in removed:
        data.pop(n, None)

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n in names:
            if n in data:
                z.writestr(n, data[n])
    return len(removed)


def has_watermark(path):
    """返回文件中残留的水印标记（空列表表示干净）。"""
    try:
        import zipfile
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                hits = [n for n in z.namelist()
                        if "drawing_wm" in n or b"AILabel" in z.read(n)
                        or "含 AI 生成".encode() in z.read(n)]
                return hits
    except Exception:
        pass
    raw = open(path, "rb").read()
    return [k for k in (b"AILabel", "含 AI 生成".encode(), b"watermark")
            if k in raw]


# ---------------------------------------------------------------- 渲染
class Renderer:
    def __init__(self, objs, hs):
        self.objs, self.hs = objs, hs
        self.segs, self.texts, self.polys = [], [], []
        self.overrides = {}
        self.used = set()

    def emit(self, o, mat=(1, 0, 0, 1, 0, 0), dep=0):
        if dep > 6:
            return
        t = o.get("entity")
        a, b, c, d, e, f = mat

        def T(p):
            return (a * p[0] + b * p[1] + e, c * p[0] + d * p[1] + f)

        if t == "LINE":
            self.segs.append([T(o["start"]), T(o["end"])])
        elif t == "LWPOLYLINE":
            p = [T(q) for q in o.get("points", [])]
            if len(p) > 1:
                self.segs.append(p)
        elif t == "ARC":
            cx, cy, r = o["center"][0], o["center"][1], o["radius"]
            a0, a1 = o["start_angle"], o["end_angle"]
            if a1 <= a0:
                a1 += 2 * math.pi
            self.segs.append([T((cx + r * math.cos(a0 + (a1 - a0) * i / 24),
                                cy + r * math.sin(a0 + (a1 - a0) * i / 24))) for i in range(25)])
        elif t == "CIRCLE":
            cx, cy, r = o["center"][0], o["center"][1], o["radius"]
            self.segs.append([T((cx + r * math.cos(2 * math.pi * i / 32),
                                cy + r * math.sin(2 * math.pi * i / 32))) for i in range(33)])
        elif t == "ELLIPSE":
            cx, cy = o["center"][0], o["center"][1]
            mx, my, ra = o["sm_axis"][0], o["sm_axis"][1], o["axis_ratio"]
            a0, a1 = o["start_angle"], o["end_angle"]
            if a1 <= a0:
                a1 += 2 * math.pi
            self.segs.append([T((cx + mx * math.cos(a0 + (a1 - a0) * i / 32) - my * ra * math.sin(a0 + (a1 - a0) * i / 32),
                                cy + my * math.cos(a0 + (a1 - a0) * i / 32) + mx * ra * math.sin(a0 + (a1 - a0) * i / 32)))
                             for i in range(33)])
        elif t == "HATCH":
            for path in o.get("paths", []):
                q = [T((pp["point"][0], pp["point"][1]))
                     for pp in (path.get("polyline_paths") or []) if pp.get("point")]
                if len(q) <= 2:
                    continue
                solid = o.get("is_solid_fill")
                xs, ys = [p[0] for p in q], [p[1] for p in q]
                if solid and max(xs) - min(xs) < 4.0 and max(ys) - min(ys) < 4.0:
                    continue  # 过小的实心三角 = 打印黑点
                self.polys.append((q, solid))
        elif t in ("MTEXT", "TEXT"):
            raw = o.get("text") if t == "MTEXT" else o.get("text_value")
            key = o["handle"][-1]
            txt = self.overrides.get(key, clean(raw))
            if not txt:
                return
            if key in self.overrides:
                self.used.add(key)
            ip = o.get("ins_pt") or [o.get("x", 0), o.get("y", 0), 0]
            x, y = T((ip[0], ip[1]))
            h = o.get("text_height") or o.get("height") or 2.0
            if t == "MTEXT":
                xd = o.get("x_axis_dir") or [1, 0, 0]
                rot = math.degrees(math.atan2(xd[1], xd[0]))
                att = o.get("attachment", 1)
                va = "top" if att <= 3 else ("center" if att <= 6 else "bottom")
                ha = "left" if att in (1, 4, 7) else ("center" if att in (2, 5, 8) else "right")
            else:
                rot, va, ha = o.get("rotation") or 0.0, "baseline", "left"
            self.texts.append((x, y, txt, max(1.0, h * math.hypot(a, c)), rot, va, ha))
        elif t in ("INSERT", "DIMENSION_ALIGNED", "DIMENSION_LINEAR", "DIMENSION_RADIUS",
                   "DIMENSION_DIAMETER", "DIMENSION_ANG2LN", "LEADER"):
            if t == "INSERT":
                ip = o["ins_pt"]
                s = o.get("scale") or [1, 1, 1]
                r = o.get("rotation", 0.0)
                m2 = (math.cos(r) * s[0], -math.sin(r) * s[1], math.sin(r) * s[0], math.cos(r) * s[1], ip[0], ip[1])
                m = (a * m2[0] + b * m2[2], a * m2[1] + b * m2[3], c * m2[0] + d * m2[2],
                     c * m2[1] + d * m2[3], a * m2[4] + b * m2[5] + e, c * m2[4] + d * m2[5] + f)
            else:
                m = mat
            bhref = o.get("block_header") if t == "INSERT" else o.get("block")
            if not isinstance(bhref, list):
                return
            bh = self.hs.get(bhref[-1])
            if not bh:
                return
            for eh in bh.get("entities", []):
                ch = self.hs.get(eh[-1])
                if ch:
                    self.emit(ch, m, dep + 1)

    def run(self):
        ms = next(o for o in self.objs
                  if o.get("object") == "BLOCK_HEADER" and o.get("name") == "*Model_Space")
        for eh in ms.get("entities", []):
            ch = self.hs.get(eh[-1])
            if ch:
                self.emit(ch)
        return self


def apply_spec(r, spec, objs):
    warnings, changed = [], {"table": 0, "info": 0}

    tb = find_table(objs)
    if spec.get("table") and tb:
        want_rows = spec["table"]["rows"]
        if len(want_rows) != len(tb["rows"]):
            warnings.append(f"表格行数不一致：图纸 {len(tb['rows'])} 行，配置 {len(want_rows)} 行，多余行已忽略")
        for ri, row in enumerate(tb["rows"]):
            if ri >= len(want_rows):
                continue
            for ci, cell in enumerate(row):
                if not cell or ci >= len(want_rows[ri]):
                    continue
                val = str(want_rows[ri][ci]).strip()
                if val and val != cell_text(cell):
                    r.overrides[cell["handle"][-1]] = val
                    changed["table"] += 1
    elif spec.get("table"):
        warnings.append("未在图纸中定位到材料清单表头（名称/型号规格/长度/数量/单位）")

    infos = {label: (o, sep, val) for o, label, sep, val in find_info(objs)}
    for item in spec.get("info", []):
        label, value = item["label"], str(item.get("value", "")).strip()
        if label not in infos:
            warnings.append(f"未找到项目信息字段：{label}")
            continue
        o, sep, old = infos[label]
        if value and value != old:
            r.overrides[o["handle"][-1]] = f"{label}{sep}{value}"
            changed["info"] += 1
    return changed, warnings


def overflow_report(r, spec, objs):
    warnings = []
    tb = find_table(objs)
    if not tb or not spec.get("table"):
        return warnings
    bounds = [-1e9] + [(tb["col_x"][i] + tb["col_x"][i + 1]) / 2 for i in range(len(tb["col_x"]) - 1)] + [1e9]
    for ri, row in enumerate(tb["rows"]):
        if ri >= len(spec["table"]["rows"]):
            break
        for ci, cell in enumerate(row):
            if not cell:
                continue
            new = r.overrides.get(cell["handle"][-1])
            if not new:
                continue
            h = (cell.get("text_height") or 2.0) * 0.55
            width = sum(1.6 if ord(ch) > 0x2e80 else 1.0 for ch in new) * h
            avail = (bounds[ci + 1] - bounds[ci]) * 0.98
            if width > avail * 1.05:
                warnings.append(f"第 {ri + 1} 行第 {ci + 1} 列「{new}」可能超出列宽")
    return warnings


def render(inv_path, spec, out, page_width_mm=594.0):
    objs, hs = load_inventory(inv_path)
    r = Renderer(objs, hs)
    changed, warn = apply_spec(r, spec, objs)
    r.run()
    warn += overflow_report(r, spec, objs)

    # 图幅以「实际绘制的文字」包围盒为准：图内存在远离图面的历史实体，
    # 用全部线段求包围盒会把图幅撑爆
    if r.texts:
        xs = [t[0] for t in r.texts]
        ys = [t[1] for t in r.texts]

        def dense(vals, gap):
            vals = sorted(vals)
            groups, cur = [], [vals[0]]
            for v in vals[1:]:
                if v - cur[-1] <= gap:
                    cur.append(v)
                else:
                    groups.append(cur)
                    cur = [v]
            groups.append(cur)
            g = max(groups, key=len)
            return g[0], g[-1]

        X0, X1 = dense(xs, 60.0)
        Y0, Y1 = dense(ys, 60.0)
    else:
        pts = [p for s in r.segs for p in s]
        X0, X1 = min(p[0] for p in pts), max(p[0] for p in pts)
        Y0, Y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    m = 8.0
    X0, X1, Y0, Y1 = X0 - m, X1 + m, Y0 - m, Y1 + m

    fig_w = page_width_mm / 25.4
    fig_h = fig_w * (Y1 - Y0) / (X1 - X0)
    pt_per_unit = fig_w / (X1 - X0) * 72.0

    fig = plt.figure(figsize=(fig_w, fig_h))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(X0, X1)
    ax.set_ylim(Y0, Y1)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.patch.set_facecolor("white")

    for q, solid in r.polys:
        if solid:
            ax.add_patch(Polygon(q, closed=True, facecolor="black", edgecolor="black",
                                 linewidth=0.3, zorder=1))
        else:
            ax.add_patch(Polygon(q, closed=True, facecolor="none", edgecolor="black",
                                 linewidth=0.25, hatch="///", zorder=1))
    for s in r.segs:
        ax.plot([p[0] for p in s], [p[1] for p in s], color="black", linewidth=0.35,
                solid_capstyle="butt", zorder=3)
    for x, y, txt, size, rot, va, ha in r.texts:
        ax.text(x, y, txt, fontsize=size * pt_per_unit, rotation=rot, rotation_mode="anchor",
                va=va, ha=ha, color="black", zorder=4)

    fig.savefig(out, format="pdf", facecolor="white")
    wm = has_watermark(out)
    return changed, warn, (X1 - X0, Y1 - Y0), wm


def feishu_rows(token, sheet_name):
    """读取飞书工作表，返回去掉空行、已 strip 的二维列表。"""
    cmd = ["lark-cli", "sheets", "+csv-get", "--spreadsheet-token", token,
           "--sheet-name", sheet_name, "--include-row-prefix=false", "--format", "json"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"读取飞书表格「{sheet_name}」失败：{r.stderr.strip()[:300]}")
    try:
        text = json.loads(r.stdout)["data"]["annotated_csv"]
    except Exception:
        text = r.stdout
    rows = [[c.strip() for c in row] for row in csv.reader(io.StringIO(text))]
    return [row for row in rows if any(row)]


def spec_from_feishu(token, objs):
    """从飞书在线表格读取：材料清单 + 项目信息，空单元格沿用图纸原值。"""
    base = make_spec(objs, None)
    rows = apply_grid(feishu_rows(token, "材料清单"), base)

    infos = {it["label"]: it["value"] for it in base["info"]}
    for r in feishu_rows(token, "项目信息"):
        if len(r) >= 2 and r[0] and r[1] and r[0] != "字段":
            infos[r[0]] = r[1]

    return {"page_width_mm": base.get("page_width_mm", 594.0),
            "table": {"columns": base["table"]["columns"], "rows": rows},
            "info": [{"label": k, "value": v} for k, v in infos.items()]}


def main():
    ap = argparse.ArgumentParser(description="DWG 图纸文字替换并输出 PDF")
    ap.add_argument("dwg")
    ap.add_argument("--init-spec", help="生成 JSON 配置模板到该路径")
    ap.add_argument("--init-form", help="生成手机可编辑的纯文本填写表到该路径")
    ap.add_argument("--init-xlsx", help="生成 Excel 数据表到该路径")
    ap.add_argument("--from-form", help="按纯文本填写表出图")
    ap.add_argument("--from-xlsx", help="按 Excel 数据表出图")
    ap.add_argument("--from-feishu", help="按飞书在线表格出图（传 spreadsheet token 或 URL）")
    ap.add_argument("--spec", help="按该 JSON 配置替换文字")
    ap.add_argument("--save-spec", help="把当前配置另存为 JSON")
    ap.add_argument("--out", help="输出 PDF 路径，默认与 DWG 同名")
    ap.add_argument("--workdir", default="work", help="中间文件目录")
    ap.add_argument("--inventory", help="已预解析的 inventory.json（跳过 dwgread）")
    a = ap.parse_args()

    if a.inventory:
        inv = a.inventory
    else:
        inv = ensure_inventory(a.dwg, a.workdir)
    objs, _ = load_inventory(inv)

    if a.init_form:
        title = os.path.splitext(os.path.basename(a.dwg))[0]
        title = title.split("_", 1)[-1].rstrip(")")
        txt = make_form(objs, title)
        open(a.init_form, "w", encoding="utf-8").write(txt)
        print(f"填写表已生成：{a.init_form}")
        if not (a.from_form or a.from_xlsx or a.from_feishu or a.spec):
            return

    if a.init_xlsx:
        title = os.path.splitext(os.path.basename(a.dwg))[0]
        title = title.split("_", 1)[-1].rstrip(")")
        make_xlsx(objs, a.init_xlsx, title)
        print(f"Excel 数据表已生成：{a.init_xlsx}")
        if not (a.from_form or a.from_xlsx or a.from_feishu or a.spec):
            return

    if a.init_spec:
        spec = make_spec(objs, None)
        spec["title"] = os.path.splitext(os.path.basename(a.dwg))[0]
        json.dump(spec, open(a.init_spec, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"配置模板已生成：{a.init_spec}（表格 {len(spec['table']['rows']) if spec['table'] else 0} 行，"
              f"信息 {len(spec['info'])} 项）")
        if not (a.from_form or a.from_xlsx or a.from_feishu or a.spec):
            return

    if a.from_form:
        base = make_spec(objs, None)          # 以图纸原值为底，空字段自动沿用
        spec = parse_form(a.from_form, base)
        spec["title"] = os.path.splitext(os.path.basename(a.dwg))[0]
        if a.save_spec:
            json.dump(spec, open(a.save_spec, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            print(f"配置已保存：{a.save_spec}")
    elif a.from_xlsx:
        base = make_spec(objs, None)
        spec = parse_xlsx(a.from_xlsx, base)
        spec["title"] = os.path.splitext(os.path.basename(a.dwg))[0]
        if a.save_spec:
            json.dump(spec, open(a.save_spec, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            print(f"配置已保存：{a.save_spec}")
    elif a.from_feishu:
        token = a.from_feishu.strip()
        if "/" in token:
            token = token.rstrip("/").split("/")[-1]
        spec = spec_from_feishu(token, objs)
        spec["title"] = os.path.splitext(os.path.basename(a.dwg))[0]
        if a.save_spec:
            json.dump(spec, open(a.save_spec, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            print(f"配置已保存：{a.save_spec}")
    elif a.spec:
        spec = json.load(open(a.spec, encoding="utf-8"))
    else:
        ap.error("需要 --spec、--from-form、--from-xlsx、--from-feishu 或 --init-*")

    out = a.out or os.path.splitext(a.dwg)[0] + ".pdf"
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    changed, warn, size, wm = render(inv, spec, out, spec.get("page_width_mm", 594.0))

    print(f"已输出 {out}（{size[0]:.0f} × {size[1]:.0f} 图形单位，宽 {spec.get('page_width_mm', 594)} mm）")
    print(f"替换：表格 {changed['table']} 格，项目信息 {changed['info']} 项")
    print("水印检查：" + ("未发现水印" if not wm else f"发现残留 {wm}"))
    for w in warn:
        print("  警告：" + w)


if __name__ == "__main__":
    main()
