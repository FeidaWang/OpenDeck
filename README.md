# Open Kimi PPT · Codex 单模型版

**版本：1.4.0-codex.1 · 2026-11-09**

基于上传的 Open Kimi PPT 1.2.0 源码，保留 PPTD、设计参考、浏览器编辑器和 PPTX 导出。当前版本用**当前单一模型、当前推理强度、统一验收标准**完成演示文稿，并增加真实字体测量、确定性模板、版式检查和最终 PPTX 渲染复核。

已移除上一版的专用生图集成、请求编译器和模型路由。核心流程不依赖图片生成、第二个模型、子代理或模型目录；已有图片仍可作为本地素材使用。

## 这版解决什么

| 需求 | 实现 |
| --- | --- |
| 只用一个模型 | 当前主线程顺序执行内容、设计、制稿、修复和审阅；不切换模型 |
| 任意推理强度 | 原样保留当前设置；未设置和未来未知名称也可使用，不设最低档位 |
| 减少排版偶然性 | 脚本负责实际字体测量、换行、模板几何和源稿检查 |
| 内容不因版面丢失 | 不通过偷偷删内容、缩到小字或改变固定页数来消除错误 |
| 检出错误后修复 | 硬错误阻止验收；需目视判断的条目逐项绑定页面图和处理说明 |
| 检查交付的文件 | 导出后再次渲染实际 PPTX，复核全部页面，并绑定文件哈希 |
| 中断后继续 | 保存计划、状态和证据；输入或文件变化使旧验收失效 |

统一标准能减少对模型临场排版判断的依赖，不能证明任意模型在任何任务上都有相同判断力。不同字体、应用和渲染器之间的绝对零错误也无法预先保证。本版保证的是：检查标准不因模型或强度降低，未完成的检查不会标成通过。

## 在 Codex 中使用

解压后在 Codex 打开项目，按 `CODEX_START.md` 的提示词使用当前模型即可。使用此包中的代码，避免上游 `@latest` 覆盖适配版。

需要显式安装本地 Skill 时：

```bash
node bin/open-kimi-ppt-skill.js install --target "$HOME/.agents/skills"
```

安装会替换同名 Skill，使用暂存和回滚保护。标准 `plugin.json` 保留了插件身份。发布包包括完整源码版和较小的代码版；完整源码版保留原始大体积示例与截图。

## 最小工作流

以下命令中的路径需替换为实际项目路径。`init` 的目录必须是新目录或空目录；布局构建器也支持向已经初始化且尚无页面输出的项目写入。

```bash
node bin/open-kimi-ppt-skill.js codex init \
  --brief examples/codex/brief.json --project /absolute/path/to/new-deck

python3 skills/open-kimi-ppt/scripts/layout_engine.py build \
  --spec examples/codex/layout-spec.json --output /absolute/path/to/new-deck

node bin/open-kimi-ppt-skill.js codex validate \
  /absolute/path/to/new-deck/deck.pptd \
  --output /absolute/path/to/new-deck/run/structural-qa.json

python3 skills/open-kimi-ppt/scripts/layout_quality.py check \
  /absolute/path/to/new-deck/deck.pptd \
  --output /absolute/path/to/new-deck/run/layout-qa.json
```

示例内容和字体适合运行演示；制作实际材料时应替换 brief 和 layout spec。先写 storyboard、sources 和 design tokens，再按计划记录完成状态。布局构建器会写入 `run/layout-policy.json`，该文件存在后再记录 design 任务。

`doctor` 是可选诊断，`--probe-codex` 是可选的只读 CLI 发现。没有 Codex CLI、模型列表或 capability JSON，仍然可以开始规划与源稿制作。计划不是后台模型执行器，任务由当前 Codex 主线程完成。

## 字体、模板与复杂页面

`layout_engine.py` 提供有边界的常用模板；具体格式和限制见 `skills/open-kimi-ppt/reference/layout-quality.md`。它使用真实字体测量并进行审计，字体缺失、字形缺失或内容容量不够会明确失败。中文页面需要实际可用的中文字形，不能以方框或静默替代字体通过检查。

用户指定的模板和风格优先。超出内置模板范围时可直接写 PPTD，保持原生文字、表格、图形和准确数据；同样需要版式审计和实际渲染。复杂效果、图表和有意叠放可能被标记为 `needs_review`，必须逐项检查。

已有照片、插图和标志由 `asset_audit.py` 检查本地文件、尺寸、透明度和哈希。它不生成图片，也不发送任何图片 API 请求。

## 两轮实际渲染

第一轮检查源稿在现有 Kimi renderer 中的表现；该 renderer 是第三方远程代码，`--allow-remote-renderer` 必须对应任务中已经成立的数据处理授权。

```bash
python3 skills/open-kimi-ppt/scripts/export_images.py /abs/deck/deck.pptd \
  --output /abs/deck/run/source-renders --allow-remote-renderer
python3 skills/open-kimi-ppt/scripts/export_pptx.py /abs/deck/deck.pptd \
  --output /abs/deck/deck.pptx --allow-remote-renderer
```

源稿通过目视检查后才能记录 export 完成。第二轮直接检查已经导出的 PPTX：

```bash
python3 skills/open-kimi-ppt/scripts/render_pptx.py /abs/deck/deck.pptx \
  --project-root /abs/deck --output /abs/deck/run/final-renders \
  --report /abs/deck/run/final-render-qa.json
```

此辅助工具用本机 LibreOffice 和 PyMuPDF 渲染已有 PPTX，不会把 PPTD 离线转换为 PPTX。渲染成功不等于审阅通过；当前模型仍要查看图片、处理问题并保存 final-output QA。仅有源稿截图、假 PNG、错误页数、变更后的 PPTX 或过期哈希均不足以完成验收。

PowerPoint、Keynote 的打开结果、字体嵌入和动画播放分别报告。本地 LibreOffice 的静态结果不能替代这些检查。

## 依赖与安全输出

Node.js 18+、Python 3.10+。通用依赖在 `requirements.txt`，本地布局依赖在 `requirements-layout.txt`，可选最终文件渲染依赖在 `requirements-render.txt`。脚本不会自动安装依赖或修改全局 Codex 配置。

现有远程导出依赖 agent-browser、Chromium 和 websocket-client。导出使用隔离下载、资源路径检查、所有权标记和暂存输出；不会扫描共享 Downloads 目录或删除任意非空文件夹。最终文件渲染同样要求新的输出位置。

## 迁移和验收记录

旧 PPTD/pages/media 可以继续使用。旧版计划和成功状态必须在新工作流中重新验收，不能直接升级成已通过。保留旧 QA 作为历史，给新渲染选择新输出目录。

- `CODEX_START.md`：可直接在 Codex 中使用的提示词。
- `UPGRADE_NOTES.md`：设计选择与迁移说明。
- `VALIDATION.md`：实际执行的测试、渲染范围和未执行项目。
- `skills/open-kimi-ppt/reference/codex-harness.md`：状态、证据和报告格式。

这是一份本地源码适配，尚未发布到 npm，也未替你安装到本机 Codex。原始许可、主题和格式参考保留。
