---
name: "dwg-sheet-text-pdf"
description: "Reads AutoCAD DWG construction sheets, rewrites table values and project info from a JSON spec, and renders a clean vector PDF. Invoke when a user uploads DWG drawings that need text/table updates and PDF output."
---

# DWG 图纸文字替换与出图

把「只改文字、不动图形」的 DWG 批量改稿流程固化下来：解析 DWG → 抽成可编辑的 JSON 配置 → 定点替换表格数值与项目信息 → 输出矢量 PDF。

## 适用场景

- 同系列图纸（如 3/4/5/6 层装箱清单）共用一套版式，只有材料清单数值和项目基本信息不同
- 要求 PDF 可直接打印，图形（框架、尺寸、剖面）保持原样
- 不要求回写 DWG（本流程只输出 PDF；需要 DWG 时见文末说明）

## 依赖

- `dwgread`（LibreDWG）：`dwgread -O JSON -o inv.json drawing.dwg`
- Python 3 + matplotlib、Pillow
- 字体：`Noto Serif CJK SC`（替代 SimSun），`DejaVu Serif` 补角分号等缺失字形

## 用法

```bash
S=.trae/skills/dwg-sheet-text-pdf/scripts/pipeline.py

# 1) 生成配置模板（读取原图当前值，含表格全部行 + 项目信息）
python3 $S "图纸.dwg" --init-spec "图纸.spec.json"

# 2) 编辑 spec.json 里的 rows / info，然后出 PDF
python3 $S "图纸.dwg" --spec "图纸.spec.json" --out "图纸.pdf"

# 3) 批量：每份图纸一个 spec，用 shell 循环
for f in *.dwg; do python3 $S "$f" --spec "${f%.dwg}.spec.json" --out "${f%.dwg}.pdf"; done
```

## 数据填写表（Excel，推荐）

```bash
# 生成 Excel 数据表：材料清单 / 项目信息 / 说明 三个工作表
python3 $S "图纸.dwg" --init-xlsx "数据表/6层.xlsx"

# 按 Excel 出图；空单元格自动沿用图纸原值
python3 $S "图纸.dwg" --from-xlsx "数据表/6层.xlsx" --out "图纸.pdf"

# 需要留档时同时导出 JSON
python3 $S "图纸.dwg" --from-xlsx "数据表/6层.xlsx" --save-spec "图纸.spec.json" --out "图纸.pdf"
```

- 「材料清单」：序号 / 名称 / 型号规格 / 长度 / 数量 / 单位，六列全部可改
- 「项目信息」：字段 / 值，改值列即可
- 「说明」：给填写人的简短规则
- 行按「序号」对位，删行或改序号会导致错位；单元格留空 = 沿用原图
- 手机用 WPS / Excel App 可直接编辑

## 纯文本填写表（可选，无 Excel 时用）

```bash
python3 $S "图纸.dwg" --init-form "填写表/6层.txt"
python3 $S "图纸.dwg" --from-form "填写表/6层.txt" --out "图纸.pdf"
```

窄屏格式，每行材料两行，第一行只读上下文，第二行填 `长=` `数=`：

```
[表格]
01 立柱1 YS-JM01 90*90*40 根
长=3000 数=12
...
[项目信息]
生产编号=YS20230801
```

## spec.json 结构

```json
{
  "title": "6层装箱清单",
  "page_width_mm": 594,
  "table": {
    "columns": ["名称", "型号规格", "长度", "数量", "单位"],
    "rows": [
      ["立柱1 YS-JM01", "90*90*40", "3000", "12", "根"],
      ["立柱2 YS-JM01", "90*90*40", "2990", "8", "根"]
    ]
  },
  "info": [
    {"label": "生产编号", "value": "YS20230801"},
    {"label": "颜色", "value": "YS001磨砂黑"},
    {"label": "项目名称", "value": "碧水龙庭"}
  ]
}
```

- `rows` 按从上到下的顺序对应图中表格的数据行；列数与 `columns` 一致
- 行数必须与图纸一致。若规格导致行项目增减，需要同时调整表格线，属于另一类改动
- `info` 只替换标签后的值，分隔符（中文全角/半角冒号）沿用原图
- 值为空或与原文一致时不替换

## 渲染规则（已固化的坑）

| 问题 | 处理 |
|---|---|
| MTEXT 不带 rotation 字段 | 用 `x_axis_dir` 计算旋转角，否则纵向尺寸数字会横躺 |
| 文字位置偏 | 按 MTEXT `attachment`(1–9) 映射 左/中/右 + 上/中/下 |
| `%%P` / `%%D` / `%%C` | 转成 ± / ° / Ø |
| `\fSimSun|b0;...` 等格式码 | 正则清除 |
| `˃` `˂`（U+02C2/C3） | 转成 `>` `<` |
| 角分号 `ʹ`（U+02B9） | 转成 `′`（U+2032），由 DejaVu Serif 补字形；思源宋体无此字 |
| 剖面图角上黑点 | 跳过包围盒 < 4×4 单位的实心填充三角 |
| 图幅 | 按所有实际绘制实体的包围盒 + 5 单位留白，宽固定 594 mm |

## 飞书在线表格（老板手机端入口）

```bash
python3 $S "图纸.dwg" --from-feishu <spreadsheet_token 或 URL> --out "图纸.pdf"
```

- 表格需含三个工作表：`材料清单`（序号/名称/型号规格/长度/数量/单位）、`项目信息`（字段/值）、`说明`
- 读取用 `lark-cli sheets +csv-get --format json`，取 `data.annotated_csv`，过滤空行
- 行按「序号」对位，空单元格沿用图纸原值；解析逻辑与 Excel 共用 `apply_grid()`
- 触发方式：群里收到「6层」→ 定位对应表格 → 执行本命令 → 把 PDF 发回会话

## 飞书指令监听（发「N层」自动出图）

```bash
python3 $S/../scripts/watch.py          # 持续监听，默认 15 秒轮询
python3 $S/../scripts/watch.py --once   # 只处理一条未处理指令
```

- 配置 `feishu_map.json`：`chat_id` + `layers`（层数 → spreadsheet token / dwg / 输出路径）
- 监听到会话里出现「6层」「4层」等文字，自动读对应在线表格出图，发回 PDF + 回执
- 指令必须是整条消息只包含「N层」（容忍句号），避免说明书里的例句被当成指令
- 回执文案不能含「数字+层」，否则会被自己当成新指令（已规避）
- 指令解析：`im +chat-messages-list` 返回 `data.messages`，`content` 是纯字符串（不是 body JSON）

## 关于发到微信

个人微信没有开放的文件收发接口，任何“微信机器人”都是逆向协议，不能用于正式流程。
可行做法只有三种：

1. 发到飞书群/私聊，老板装飞书（手机端体验与微信一致）
2. 生成 PDF → 上传云盘取分享链接 → 用服务号模板消息推到微信，老板点链接看 PDF（不是附件）
3. 发邮件，微信里能收到提醒并打开附件

本 skill 只负责前两步的产物；投递方式可按需外接。

## 水印清理（必须执行）

生成环境会在保存 Office 文件后注入「含 AI 生成」水印：每个工作表追加
`xl/drawings/drawing_wm*.xml`（形状名 `AILabel`），并写入关系项与 `<drawing>` 引用。

- `make_xlsx()` 保存后自动调用 `strip_watermarks(path)`：删除水印部件、`.rels` 关系、
  工作表中的 `<drawing r:id>` 引用以及 `[Content_Types].xml` 中的 Override 声明
- 任何新生成的 Office 文件都应单独调用一次 `strip_watermarks(path)`
- 用 `has_watermark(path)` 校验，返回空列表表示干净
- PDF 由 matplotlib 生成，本身无水印；出图后会自动扫描 `AILabel` / `含 AI 生成` / `watermark`
  并打印「水印检查：未发现水印」

## 输出与校验

脚本会打印：

- 替换数量（表格 / 信息）
- 未匹配项（标签找不到、行数不一致）
- 字宽溢出警告（新值估算宽度超过列宽 1.05 倍）

交付前还应目检：表格数值、项目信息三行、纵向标注方向、顶部公差表对齐、角分号字形。

## 限制

- 不回写 DWG。LibreDWG 写 DWG 会降级为 R2000，且本类图纸存在块结构异常，全量 DXF 导出会截断
- 若必须交付 DWG：用 ODA File Converter 做高保真 DWG↔DXF 转换后再由 CAD 另存，或在 AutoCAD 中以 PDF 为参照重绘
- 字体非原版 SimSun，字形接近但不等同；要求 100% 原字形需提供 `simsun.ttc`
