# 更新记录

本文件只记录这个衍生版本的改动。上游项目的历史在它自己的仓库里。

## 1.6.0-claude.1 — 2026-11-09

### 变更

- 项目名统一为 **OpenDeck**。此前仓库叫 OpenDeck，文件里用的却还是早先的名字 `open-kimi-ppt`。
- 包名与命令：`open-kimi-ppt-skill` 改为 `opendeck`（`bin/opendeck.js`）。
- 技能名与目录：`open-kimi-ppt` 改为 `opendeck`（`skills/opendeck/`）。安装程序写入的文件夹名也是 `opendeck`。
- 环境变量：`OPEN_KIMI_PPT_PYTHON` 改为 `OPENDECK_PYTHON`。

### 升级须知

- 以旧文件夹名 `open-kimi-ppt` 装过的技能不会被自动删除或改名。请重新安装，再自己删掉旧文件夹。
- 如果你设置过 `OPEN_KIMI_PPT_PYTHON`，请改设 `OPENDECK_PYTHON`。
- 已经做好的 PPTD 项目不受影响。

## 1.5.0-claude.1 — 2026-11-09

### 删除

- 远程渲染与 PPTX 导出：`export_images.py`、`export_pptx.py`、`export_host.html`、对应测试、`websocket-client` 依赖和 `--allow-remote-renderer` 参数。它们会把文稿送进第三方网页编辑器的 iframe。现在这个编辑器在它自己的服务器上生成 PPTX，所以这条路在本项目里修不好。
- 本地网页编辑器（`serve` 命令、`editor/`、`lib/`），它内嵌的是同一个远程编辑器。
- 工作流里的 `render`、`visual_review`、`export`、`final_output_review` 四个任务。任务链现在到 `layout` 为止。
- 上游发布说明、验证日志和内部打包说明。

### 变更

- 本项目不再产出 PPTX 或页面图片。产出是 PPTD 项目，加上结构与实测版式检查。
- 项目读取逻辑移到 `pptd_project.py`。
- `status` 现在固定报告 `visual_review: not_performed` 和 `pptx_export: not_available`；只有所有任务完成且没有未解决的版式发现时，`complete` 才为真。
- 修了两个在 macOS 上失败的测试（把 `/var/...` 和 `/private/var/...` 当成不同路径）。

## 1.4.0-codex.1 — 2026-11-09

- 每个任务都用当前主线程的模型和推理设置，不需要模型目录。
- 移除专用生图集成，保留本地素材检查。
- 增加实测字体的模板构建、字形与字体检查、溢出与碰撞检查，以及稳定编号的待复核发现。
