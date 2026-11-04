# 零成本部署（GitHub Actions · 公开仓库）

群里发「6层」→ Actions 每 5 分钟轮询 → 读飞书在线表格 → 出 PDF → 发回群。

## 关于公开仓库

仓库设为 **Public**，图纸不涉密，代码和 DWG 会公开可见。公开仓库的 Actions **不限时长**，所以可以 5 分钟一次。

GitHub 的定时任务不保证准时，实际常在 5–15 分钟后触发。急用时到 Actions 页面点 `Run workflow` 立即执行。

## 目录

```
.github/workflows/sheet2pdf.yml   定时任务
.trae/skills/dwg-sheet-text-pdf/  出图脚本（pipeline.py）
deploy/
  runner.py                       轮询、读表、出图、回传
  config.json                     群 id + 层数映射
  templates/6层.json              模板（结构 + 手工配置）
  state.json                      已处理消息 id
  dwg/6层装箱清单.dwg
  dwg/6层装箱清单.inventory.json  预解析数据，免装 libredwg
out/                              产物（PDF 不提交）
```

## 一、准备

1. GitHub 新建**公开**仓库，把上面内容推上去
2. 飞书开放平台建自建应用，开权限：
   - `sheets:spreadsheet:readonly`
   - `im:message:send_as_bot`
   - `im:resource`
3. 发布应用，把机器人拉进群「装箱清单出图」
4. 仓库 Secrets 填 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`

## 二、跑起来

Actions → `sheet2pdf` → `Run workflow`。第一次看日志确认：
取到消息、读到表格、无报错。

之后群里发「6层」（整条消息只写层数，带 @ 也行），最多约 15 分钟收到 PDF。

## 三、模板与结构同步

`deploy/templates/<层>.json` 是这一层图纸和表格的约定：

```json
{
  "header": ["序号", "名称", "型号规格", "长度", "数量", "单位"],
  "rows": 51,
  "info": ["生产编号", "颜色", "项目名称"]
}
```

规则：

- **每轮先比对**在线表格的表头、行数、项目信息标签
- 有差异 → 自动写回模板，并在群里通知「结构已变更」，**本轮不出图**
- 结构一致后才处理「N层」指令，防止表被改坏还照旧出图
- 模板里的其它字段（比如以后加的 `dwg` 路径）在自动同步时不会被覆盖，可以手工维护

手工改模板也有效：改完提交，下一轮以你的版本为准；如果与在线表格不一致，会再触发一次同步通知。

## 四、加一层（三/四/五层）

1. `deploy/dwg/` 放 `<层>.dwg` 和 `<层>.inventory.json`
2. 建对应的在线表格，把 `spreadsheet` token 填进 `config.json` 的 `layers`
3. 复制一份 `templates/6层.json` 改名成 `<层>.json`，行数按实际

## 五、排错

| 现象 | 原因 |
|---|---|
| `99991663` 或 token 报错 | App ID/Secret 错，或应用未发布 |
| 发文件失败 | 机器人不在群里，或没开 `im:resource` |
| 读表格失败 | 机器人没表格权限 |
| PDF 中文方块 | 漏装 `fonts-noto-cjk` |
| 一直不出图 | 检查 Actions 是否被 60 天无活动规则停用 |

## 六、维护

GitHub 对长期无活动的仓库会停用定时任务。workflow 每次运行都会提交
`state.json`，属于仓库活动；若发现停用，去 Actions 页面重新启用即可。
