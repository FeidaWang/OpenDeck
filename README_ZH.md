# Open Kimi PPT

[English](README.md) | [简体中文](README_ZH.md)

**这是什么：** 一个小工具，帮你（或你的 AI 助手）用一份简单的文字清单搭出幻灯片的“源文件”，并检查每一页的字有没有放不下、颜色看不看得清。

**你能得到什么：** 一个文件夹，里面是一种叫 PPTD 的幻灯片源文件（全是普通文本文件，可以用任何文本编辑器改），外加一份检查报告。

**你得不到什么：** 它不能生成 PowerPoint 文件（`.pptx`），也不能把幻灯片画成图片给你看。它不会画画，也不会把你的内容发到任何网站。

---

## 目录

1. [先认识几个词](#1-先认识几个词)
2. [你需要准备什么](#2-你需要准备什么)
3. [下载并安装](#3-下载并安装)
4. [做出你的第一份成果](#4-做出你的第一份成果)
5. [中文内容怎么做](#5-中文内容怎么做)
6. [让 AI 助手来用它（可选）](#6-让-ai-助手来用它可选)
7. [它做不到什么](#7-它做不到什么)
8. [常见报错与做法](#8-常见报错与做法)
9. [许可：能做什么，不能做什么](#9-许可能做什么不能做什么)
10. [给开发者：运行测试](#10-给开发者运行测试)

## 1. 先认识几个词

- **终端**：一个窗口，你在里面打一行命令，按回车，电脑就照做。本说明里所有灰色方框里的命令，都是整行复制进去、按回车。
- **Python**：一种编程语言。这个工具是用它写的，所以你的电脑上要装有它。你不需要会写。
- **虚拟环境**：Python 的一个“专用小房间”。工具需要的零件都装在房间里，不会弄乱你电脑上别的东西。想删掉，直接删掉房间文件夹 `.venv` 就行。
- **PPTD**：用文字描述幻灯片的格式。一个 `deck.pptd` 加一堆 `pages/01.page` 就是一份幻灯片。
- **Skill（技能）**：给 AI 编程助手（比如 Claude Code、Codex）安装的一份“说明书加工具包”。第 6 节会讲，不装也能用这个工具。

## 2. 你需要准备什么

> **关于验证范围：** 本说明里的每一步都在 **macOS** 上亲手跑过。Linux 的命令基本相同，但我没有试过。Windows 我没有试过，命令不保证能用。

先打开终端。macOS：同时按 `⌘` 和空格键，输入 `Terminal`，回车。

逐样确认。每个命令复制进去按回车，对照“你应该看到”：

**a. Git**（用来下载）

```bash
git --version
```

你应该看到类似 `git version 2.54.0` 的一行，数字不同没关系。如果提示找不到命令，先安装 Git（<https://git-scm.com/>）。

**b. Python，3.10 或更新**

```bash
python3 --version
```

你应该看到 `Python 3.10.x` 或更大的数，比如 `Python 3.12.13`。**如果是 3.9 或更低**（macOS 自带的常常是 3.9），先去看[第 8 节“Python 版本太低”](#python-版本太低)，再回来。

**c. Node.js（只有第 6 节要用，可以先不管）**

```bash
node --version
```

你应该看到 `v18` 或更大的版本号，比如 `v24.14.0`。

## 3. 下载并安装

**第 1 步：下载。**

```bash
git clone https://github.com/FeidaWang/OpenDeck.git
```

你应该看到以 `Cloning into 'OpenDeck'...` 开头的几行。

**第 2 步：走进下载好的文件夹。**

```bash
cd OpenDeck
```

没有输出就是成功。之后所有命令都在这个文件夹里运行。

**第 3 步：建“专用小房间”（虚拟环境）。** 这一步用的 Python 必须是 3.10 或更新。

```bash
python3 -m venv .venv
```

没有输出就是成功，文件夹里会多出一个 `.venv`。

**第 4 步：把需要的零件装进小房间。** 需要联网，几十秒。

```bash
.venv/bin/python -m pip install -r skills/open-kimi-ppt/requirements.txt
```

你应该在最后看到一行 `Successfully installed Pillow-… PyYAML-…`（版本号会不同）。结尾的 `[notice] A new release of pip is available` 可以忽略。

装完了。之后每次用，只要在 `OpenDeck` 文件夹里打开终端就行，不用重装。

## 4. 做出你的第一份成果

我们做一份 2 页的小幻灯片：封面加一页要点。

**第 1 步：写“文字清单”。** 下面整段复制进终端，按回车。它会在当前文件夹里创建一个叫 `my-spec.json` 的文件：

```bash
cat > my-spec.json <<'EOF'
{
  "version": 1,
  "title": "Our team offsite",
  "style": "neutral",
  "slide_count": 2,
  "slides": [
    {"layout": "cover", "title": "Our team offsite", "subtitle": "Plan for the first week of March"},
    {"layout": "bullets", "title": "What we need to decide", "bullets": ["Pick a location.", "Set a budget.", "Choose two dates."]}
  ]
}
EOF
```

没有输出就是成功。这份清单的意思是：一共 2 页；第 1 页是封面；第 2 页是一个标题加三条要点。`style` 可以换成 `warm` 或 `dark`，是三种配色。

**第 2 步：让工具搭出幻灯片。**

```bash
.venv/bin/python skills/open-kimi-ppt/scripts/layout_engine.py build --spec my-spec.json --output offsite-deck
```

你应该看到：

```text
{
  "status": "passed",
  "slide_count": 2,
  "manifest": "offsite-deck/deck.pptd",
  "report": "offsite-deck/run/layout-qa.json",
  "visual_review": "not_performed"
}
```

`status` 是 `passed`，意思是：文字都放得下，没有碰在一起，颜色对比够。`visual_review` 是 `not_performed`，意思是：**没有人（也没有程序）真的看过画面**，因为这个工具画不出来。

**第 3 步：看看它做了什么。**

```bash
ls offsite-deck offsite-deck/pages
```

你应该看到：

```text
offsite-deck:
deck.pptd
pages
run

offsite-deck/pages:
01.page
02.page
```

`deck.pptd` 是目录页，`pages/` 里每页一个文件，`run/` 里是检查报告和你的原始清单。它们都是文本文件，用文本编辑器就能打开。

**第 4 步：再单独检查一遍。** 你改过页面文件后，可以随时重新检查：

```bash
.venv/bin/python skills/open-kimi-ppt/scripts/layout_quality.py offsite-deck/deck.pptd
```

你应该看到一大段文字，其中有 `"status": "passed"`。

**试试“它不会偷偷缩小字”。** 把某一条要点换成非常长的一句话，再运行第 2 步（输出文件夹要换一个新名字，比如 `--output long-deck`）。你会看到类似：

```text
layout_engine: slide 2 / bullet: bullets need 380.0px, available 356.0px. Split or rewrite; page count and readable font size are preserved.
```

意思是：放不下。它不会把字缩到看不清，也不会删你的内容，而是让你改文字。

可以用的版式有：`cover`（封面）、`bullets`（要点）、`two_column`（两栏）、`metrics`（数字卡片）、`table`（表格）、`image`（图片）。每种需要哪些字段，见 [layout-quality.md](skills/open-kimi-ppt/reference/layout-quality.md) 里的表格。仓库里还有一份四页的例子：`examples/codex/layout-spec.json`。

## 5. 中文内容怎么做

中文需要一款能显示汉字的字体，而工具**不会自动找到**你电脑上的中文字体（在 macOS 上它找不到）。如果你把中文清单直接交给它，会看到：

```text
layout_engine: No exact usable regular/bold font pair covers this deck. Supply font.family, font.regular and font.bold with actual local files. …
```

意思是：请你明确告诉我用哪个字体文件。做法是在清单里加一个 `font` 块，写上字体名字，以及“常规体”和“粗体”两个字体文件的完整路径：

```json
"font": {
  "family": "DengXian",
  "regular": "/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts/Deng.ttf",
  "bold": "/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts/Dengb.ttf"
}
```

把它放在清单最外层，和 `"title"`、`"style"` 并列，别忘了前一行末尾的逗号。其余命令和第 4 节完全一样。

这个例子用的是 macOS 上 Microsoft PowerPoint 自带的“等线”字体，**只有装了 PowerPoint 的电脑才有这两个文件**。我用一份中文四页样例试过：构建成功，检查 `passed`，没有问题。没有装 PowerPoint 的话，你需要自己找一款有“常规”和“粗体”两个单独 `.ttf` 文件的中文字体（比如免费的 Noto Sans SC），把路径换成它们的。我没有试过 Noto Sans SC，不保证每款字体都能通过，原因见[第 8 节“字体的页码框放不下”](#字体的页码框放不下)。

## 6. 让 AI 助手来用它（可选）

如果你用 Claude Code 或 Codex 之类的 AI 编程助手，可以把这个工具装成它的“技能”，然后用一句话让它做幻灯片源文件。需要 Node.js（第 2 节 c）。

```bash
node bin/open-kimi-ppt-skill.js install --target "$HOME/.claude/skills"
```

你应该看到 `Installed open-kimi-ppt to …/.claude/skills/open-kimi-ppt`。给 Codex 用的话，把目录换成 `"$HOME/.codex/skills"`。

装好后，重开 AI 助手的会话，对它说：“用 open-kimi-ppt 做一份 6 页的幻灯片源文件，主题是……”

我只亲手跑过上面的安装命令（装到了一个临时文件夹），没有逐一在各家 AI 软件里实测它是否识别并正确使用这个技能。AI 做出来的内容，也请你自己核对事实。

## 7. 它做不到什么

- **不能生成 `.pptx`**（PowerPoint 文件）。它的成果是 PPTD 源文件。
- **不能把幻灯片画成图片，也不能让你预览。** 所以它所有的“通过”，都只来自对字体文件的实际测量和几何计算，比如字放得下放不下、有没有重叠、对比度够不够。**“好看不好看”它判断不了，别的软件打开后画成什么样它也保证不了。**
- **不能打开 `.pptx` 变回可编辑的源文件。**
- **不会联网上传你的内容。** 这个版本里没有任何把文稿送到第三方网页的功能。上游项目原来有一条这样的路径（把文稿放进 Kimi 的网页编辑器里导出），我已经把它整条删除了，因为它现在已经行不通，也修不了。
- 不会替你写事实。AI 助手可能写错数字，请自己核对。
- 字体需要你自己提供或确认（中文尤其）。

怎么得到 `.pptx`？这个项目帮不了你。你可以把 PPTD 文件里的内容，交给能导出 `.pptx` 的别的工具。

## 8. 常见报错与做法

### Python 版本太低

**症状 1：** 第 3 节第 4 步，装零件时出现：

```text
ERROR: Could not find a version that satisfies the requirement Pillow<14,>=12 …
ERROR: No matching distribution found for Pillow<14,>=12
```

**症状 2：** 运行 `node bin/open-kimi-ppt-skill.js codex doctor` 这类命令时出现：

```text
Error: Python 3.10+ is required for the Codex workflow. Set OPEN_KIMI_PPT_PYTHON to a Python executable path. No installation was attempted.
```

**原因：** 你的 `python3` 低于 3.10（macOS 自带的是 3.9.6，我在自己的机器上遇到的就是这个）。

**做法：**

1. 装一个新的 Python。到 <https://www.python.org/downloads/> 下载安装 3.12 或更新的版本，或用你熟悉的包管理器。这一步会改动你的系统，所以我没有在你的电脑上演示，也没有逐个安装方式去验证。
2. 装完后确认：`python3.12 --version` 显示 `Python 3.12.x`（安装后的命令名通常带版本号）。
3. 删掉旧小房间并用新 Python 重建，再回到第 3 节第 4 步：

```bash
rm -rf .venv
python3.12 -m venv .venv
```

我用一个 3.12 的 Python 验证了这条建房间的命令。`rm -rf .venv` 只会删掉当前文件夹里的 `.venv`，请先确认你在 `OpenDeck` 文件夹里。

### 找不到中文字体

见[第 5 节](#5-中文内容怎么做)。症状是 `No exact usable regular/bold font pair covers this deck`，做法是在清单里加 `font` 块，明确写出字体文件路径。

### 字体的页码框放不下

用某些字体（我试过微软雅黑）构建会失败：

```text
layout_engine: slide 1 / page_number: needs 29px height at 14px, but only 27.0px is available. Split or rewrite the content; text was not shrunk or truncated.
```

**原因：** 模板里页码框的高度是固定的 27 像素，这款字体的行高更大，页码放不下。这是工具本身的限制，不是你的错。

**做法：** 换一款行高小一点的字体，比如第 5 节的等线（我试过，能通过）。

### 文字放不下

```text
layout_engine: slide 2 / bullet: bullets need 380.0px, available 356.0px. …
```

**做法：** 缩短这页的文字，或者把内容拆给别的页。工具不会替你缩小字号，也不会偷偷多加页。

### 输出文件夹已经存在

```text
layout_engine: Refusing to add a deck to a project with existing manifests or page sources; choose a new project
```

**做法：** `--output` 换一个新名字。它不会覆盖你已有的幻灯片。

### 缺少零件

```text
layout_engine: No module named 'PIL'
```

**原因：** 你用的是系统自带的 `python3`，而不是装好零件的小房间。

**做法：** 命令里把 `python3` 换成 `.venv/bin/python`，就像本说明里写的那样。

## 9. 许可：能做什么，不能做什么

**用大白话说：可以免费使用、修改、分发，包括商业用途；只需要在你的副本里保留版权声明和许可声明。** 软件按“现状”提供，不附带任何担保。

这个项目采用 [MIT 许可证](https://opensource.org/license/mit)，全文在 [LICENSE](LICENSE)。

## 10. 给开发者：运行测试

```bash
.venv/bin/python -m unittest discover -s skills/open-kimi-ppt/tests -p 'test_*.py' -q
```

```bash
node --test
```

前一条应该以 `OK` 结尾，可能带着 `skipped=1`：那是因为可选的渲染零件（PyMuPDF）没装，对应的测试被跳过了，不影响结果。后一条应该显示 `fail 0`。测试不会联网，也不会安装任何东西。

更多背景：工作流和报告格式见 [codex-harness.md](skills/open-kimi-ppt/reference/codex-harness.md)，这个包能做和不能做的事见 [rendering-boundaries.md](skills/open-kimi-ppt/reference/rendering-boundaries.md)，内置的设计主题见 [theme_ZH.md](theme_ZH.md)，改动记录见 [CHANGELOG_ZH.md](CHANGELOG_ZH.md)。
