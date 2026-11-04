# 井架装箱清单出图 · 交接文档

> 2026-11-04 · 从 Trae 会话迁移到本地/新会话用

## 1. 项目目标

四种规格（3/4/5/6 层）铝合金井架装箱清单图纸。只改**材料清单表格文字**和**表格下方项目信息**（生产编号/颜色/项目名称），不改任何图形。输出矢量 PDF，需要时可出 DWG。

最终形态：老板手机上改飞书在线表格 → 飞书群发「6层」→ 自动出 PDF 发回群。零成本部署在 GitHub Actions（公开仓库，5 分钟轮询）。

## 2. 已完成

### 出图核心（已验证）
- Skill：`.trae/skills/dwg-sheet-text-pdf/`
  - `scripts/pipeline.py` — DWG→JSON→按配置替换文字→矢量 PDF
  - `scripts/watch.py` — 会话内飞书群监听（迁移后可弃用，由 deploy/runner.py 接替）
  - `SKILL.md` — 用法与已固化的坑
  - `feishu_map.json` — 会话监听用的映射
- 6 层图纸已完整解析：`deploy/dwg/6层装箱清单.dwg` + `6层装箱清单.inventory.json`（LibreDWG 预解析，**免装 libredwg**）
- 表格 51 行数据 + 3 项项目信息，坐标定位（表格 x≈27150–27230, y≈4095–4280）

### 数据入口（三种，共用 apply_grid 逻辑）
| 入口 | 命令参数 |
|---|---|
| Excel | `--from-xlsx 文件.xlsx` |
| 飞书在线表格 | `--from-feishu <token或URL>` |
| JSON spec | `--spec 文件.json` |

生成模板：`--init-spec` / `--init-xlsx`。Excel 生成后自动调用 `strip_watermarks()` 清除 AILabel 水印。

### 飞书侧（已建好）
- 在线表格：https://my.feishu.cn/sheets/CGxlsUcWch03HMt6K8YcyxyvnSh
  （工作表：材料清单 51 行 / 项目信息 3 行 / 说明）
- 群「装箱清单出图」：`oc_ef3238d3693e9fd352b19fc5ac345660`
- 邀请链接：`https://applink.feishu.cn/client/chat/chatter/add_by_link?link_token=b7cpf6e3-107b-4b35-9a26-f00e08424d1b`
- 群简介已写入表格链接；置顶需手动（bot 无置顶权限，error 230006）

### 部署包（GitHub Actions，待上线）
- `.github/workflows/sheet2pdf.yml` — cron `*/5`，公开仓库不限时长；跑完提交 state.json 防重复
- `deploy/runner.py` — 纯 HTTP 调飞书 API（不依赖 lark-cli），含**模板结构比对**
- `deploy/config.json` — 群 id + 层数映射（目前只有 6层）
- `deploy/templates/6层.json` — header/rows/info 结构约定；手工字段不被自动同步覆盖
- `deploy/README.md` — 完整部署步骤 + 排错表

### 结构同步机制（已自检通过）
每轮先比对在线表格表头/行数/信息标签与模板 → 有差异则回写模板 + 群通知 + **本轮拒绝出图**。自检：加一列「备注」+一行数据 → 正确报出两处差异。

## 3. 已固化的坑（改代码前必读）

1. **纵向文字**：该 DWG 的 MTEXT 方向存在 `x_axis_dir` 而非 `rotation`，必须按 x_axis_dir 算角度
2. **对齐**：MTEXT attachment 1–9 分别映射左/中/右上/中/下，不能全按左对齐
3. **角分号**：源字符是 U+02B9，思源宋体无字形 → 转 U+2032（′），并挂 DejaVu Serif 回退。度是 °（U+00B0），分是 ′，别搞混
4. **CAD 码**：`%%P`→±、`%%D`→°、`˃˂`→`><`
5. **黑点**：块内 <4×4 单位的实心填充是剖切符号三角，220dpi 下糊成黑点，已过滤
6. **水印**：openpyxl 生成的 xlsx 会被注入 AILabel 文本框，保存后必须跑 `strip_watermarks()`
7. **指令匹配**：`(\d+)\s*层[。.!！]?` fullmatch，先去掉 `@\S+`；说明文字含「6层」不能误触发
8. **回执文案不能带「N层」**，否则被自己当成新指令死循环
9. **消息去重**：message_id 存 state 文件（会话版 `.feishu_state.json`，deploy 版 `deploy/state.json`）
10. **DWG 结构异常**：原文件有 `Invalid type 0x31, expected 0x5 ENDBLK`，全量 DXF 导出会截断 → 一律走 inventory.json 路线，不要重新全量导出
11. **飞书消息结构**：列表在 `data.messages`，content 是 JSON 字符串不是对象

## 4. 外部账号与凭证

| 项 | 状态 |
|---|---|
| Trae 会话 lark-cli user token | **已过期（99991677）**，会话监听已停。迁移后不再用它 |
| 飞书自建应用 | **未建**（阻塞项，见下） |
| GitHub 小号 | **未注册**（阻塞项） |
| 飞书个人账号 | 已有，在线表格和群都在名下 |

## 5. 待办（按顺序）

### A. 飞书自建应用（手机可操作，~10 分钟）
1. https://open.feishu.cn/app → 创建企业自建应用
2. 记下 App ID / App Secret
3. 权限管理添加：`sheets:spreadsheet:readonly`、`im:message:send_as_bot`、`im:resource`、`im:message:readonly`、`im:message.group_msg`（最后一个是敏感权限，需管理员审批；缺它轮询读不到群消息）
4. 添加「机器人」应用能力 → 版本管理与发布 → 创建版本并发布
5. 把应用里的**机器人**拉进群「装箱清单出图」

### B. GitHub（~5 分钟）
1. 注册小号（只需邮箱，可能要求手机验证 + 2FA）
2. 新建 **Public** 仓库（名字中性，如 `pdf-tool-a1`）
3. Settings → Secrets → Actions → New repository secret：
   - `FEISHU_APP_ID`、`FEISHU_APP_SECRET`

### C. 推送与首跑
1. 拷贝本工作区（见 §6）到本地
2. `git init && git add .github deploy .trae && git commit && git push`
   （注意 `.gitignore` 排除 `out/`、`work/`、`__pycache__/`、`deploy/state.json` 可留空壳）
3. Actions 页手动 `Run workflow`，看日志确认：取到消息、读到表格、无报错
4. 群里发「6层」→ 5–15 分钟内收 PDF
5. 验收：改一格长度再发一次 → PDF 对应格变化；发「说明文字含6层」→ 不触发

### D. 扩层（等用户提供 3/4/5 层 DWG）
1. 每份跑 `dwgread -O JSON -o deploy/dwg/<层>.inventory.json <层>.dwg`
2. `pipeline.py <层>.dwg --init-xlsx` 生成数据表 → 建对应飞书在线表格
3. `deploy/config.json` 的 layers 加映射；`deploy/templates/` 复制 6层.json 改行数
4. **先确认行数是否仍是 51**——层数变了立柱/横挡行数可能变，变了要核对表格线

## 6. 迁移拷贝清单

整包拷 `/workspace`，最小集：

```
.github/workflows/sheet2pdf.yml
.trae/skills/dwg-sheet-text-pdf/     （不含 __pycache__）
deploy/                              （不含 __pycache__、work/）
数据填写表/6层装箱清单.xlsx           （Excel 入口样例）
```

**环境依赖**（deploy 模式）：
- Python 3.10+：`pip install matplotlib openpyxl requests`
- 字体：`fonts-noto-cjk`（Debian/Ubuntu）——缺了 PDF 中文变方块
- 不需要 libredwg（inventory 已预解析；新图纸才需要 `dwgread`）
- 不需要 lark-cli（runner.py 纯 HTTP）

**会话模式遗留**（仅 `.trae/.../watch.py` 用，可不管）：lark-cli + user token + `feishu_map.json`。

## 7. 快速验证命令（本地迁移后）

```bash
# 1. 出图链路（不碰飞书；显式传 inventory，免装 dwgread）
python3 .trae/skills/dwg-sheet-text-pdf/scripts/pipeline.py \
  deploy/dwg/6层装箱清单.dwg --inventory deploy/dwg/6层装箱清单.inventory.json \
  --from-xlsx 数据填写表/6层装箱清单.xlsx --out out/test.pdf
# 期望：已输出 out/test.pdf（…宽 594.0 mm）/ 替换：表格 0 格 / 水印检查：未发现水印

# 2. 结构比对自检（不碰飞书）
python3 deploy/runner.py --self-test 数据填写表/6层装箱清单.xlsx
# 期望：表格结构：表头 […6列]，数据 51 行…；模板差异：无

# 3. 结构变更检测（可选）
#    在 xlsx 里加一列/一行再跑上面命令，期望报出差异
```

## 8. 风险与已知限制

- GitHub cron 实际延迟 5–15 分钟；60 天无活动会停用定时任务（README 有说明）
- 公开仓库：代码 + DWG 公开可读（用户已确认图纸不涉密）
- 飞书 bot 不能置顶群消息（230006），需手动置顶那条带表格链接的消息
- 在线表格整行增删需走模板同步流程，同步那轮不出图
- 图框：源 DWG 无图框边线/标题栏，PDF 是「图纸内容本身」，用户已知
- 监听会话版与 deploy 版**不能同时开**，会重复发文件
