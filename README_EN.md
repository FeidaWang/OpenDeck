# Open Kimi PPT

[简体中文](README.md) | [English](README_EN.md)

> **Unofficial project, not affiliated with Kimi or Moonshot AI.** It is a derivative of the open-source project [open-kimi-ppt-skill](https://github.com/binaryify/open-kimi-ppt-skill) (by binaryify, MIT license).

**What it is:** a small tool that helps you (or your AI assistant) turn a simple text list into the "source files" of a slide deck, and checks every page for text that does not fit and colours that are hard to read.

**What you get:** a folder of slide source files in a format called PPTD (plain text files you can edit in any text editor), plus a check report.

**What you do not get:** it cannot make a PowerPoint file (`.pptx`), and it cannot draw the slides as pictures for you to look at. It does not draw anything, and it does not send your content to any website.

---

## Contents

1. [A few words first](#1-a-few-words-first)
2. [What you need](#2-what-you-need)
3. [Download and install](#3-download-and-install)
4. [Make your first result](#4-make-your-first-result)
5. [Chinese (and other non-Latin) content](#5-chinese-and-other-non-latin-content)
6. [Let an AI assistant use it (optional)](#6-let-an-ai-assistant-use-it-optional)
7. [What it cannot do](#7-what-it-cannot-do)
8. [Common errors and what to do](#8-common-errors-and-what-to-do)
9. [License: what you can and cannot do](#9-license-what-you-can-and-cannot-do)
10. [For developers: run the tests](#10-for-developers-run-the-tests)

## 1. A few words first

- **Terminal:** a window where you type one line, press Enter, and the computer does it. Every command in a grey box below is meant to be copied whole into the terminal and run with Enter.
- **Python:** a programming language. The tool is written in it, so it has to be installed on your computer. You do not need to write any.
- **Virtual environment:** a "private little room" for Python. The parts the tool needs live in that room and do not disturb anything else on your computer. To remove it, delete the `.venv` folder.
- **PPTD:** a way to describe slides in text. One `deck.pptd` plus a few `pages/01.page` files make a deck.
- **Skill:** a "manual plus toolbox" that you install into an AI coding assistant (for example Claude Code or Codex). Section 6 covers it; you can use the tool without it.

## 2. What you need

> **What has been verified:** every step here was run by hand on **macOS**. Linux commands are mostly the same but I have not tried them. I have not tried Windows, so the commands may not work there.

Open a terminal first. On macOS: press `⌘` and the space bar together, type `Terminal`, press Enter.

Check each item. Copy the command, press Enter, and compare with "You should see":

**a. Git** (for downloading)

```bash
git --version
```

You should see a line like `git version 2.54.0`; a different number is fine. If the command is not found, install Git first (<https://git-scm.com/>).

**b. Python 3.10 or newer**

```bash
python3 --version
```

You should see `Python 3.10.x` or higher, for example `Python 3.12.13`. **If it says 3.9 or lower** (the Python that ships with macOS is often 3.9), read ["Python is too old"](#python-is-too-old) in section 8 first, then come back.

**c. Node.js (only needed for section 6; skip it for now)**

```bash
node --version
```

You should see `v18` or higher, for example `v24.14.0`.

## 3. Download and install

**Step 1: download.**

```bash
git clone https://github.com/FeidaWang/OpenDeck.git
```

You should see a few lines starting with `Cloning into 'OpenDeck'...`.

**Step 2: go into the downloaded folder.**

```bash
cd OpenDeck
```

No output means it worked. Run every later command inside this folder.

**Step 3: build the private room (virtual environment).** The Python used here must be 3.10 or newer.

```bash
python3 -m venv .venv
```

No output means it worked; a `.venv` folder appears.

**Step 4: put the needed parts into the room.** This needs the internet and takes a few tens of seconds.

```bash
.venv/bin/python -m pip install -r skills/open-kimi-ppt/requirements.txt
```

At the end you should see a line `Successfully installed Pillow-… PyYAML-…` (version numbers will differ). A trailing `[notice] A new release of pip is available` can be ignored.

That is the whole installation. Next time, just open a terminal in the `OpenDeck` folder; there is nothing to reinstall.

## 4. Make your first result

We will make a small 2-slide deck: a cover and one page of bullet points.

**Step 1: write the "text list".** Copy the whole block into the terminal and press Enter. It creates a file called `my-spec.json` in the current folder:

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

No output means it worked. The list says: 2 slides in total; slide 1 is a cover; slide 2 is a title with three bullet points. `style` can be changed to `warm` or `dark`; those are the three colour schemes.

**Step 2: let the tool build the deck.**

```bash
.venv/bin/python skills/open-kimi-ppt/scripts/layout_engine.py build --spec my-spec.json --output offsite-deck
```

You should see:

```text
{
  "status": "passed",
  "slide_count": 2,
  "manifest": "offsite-deck/deck.pptd",
  "report": "offsite-deck/run/layout-qa.json",
  "visual_review": "not_performed"
}
```

`status: passed` means the text fits, nothing collides and the colour contrast is enough. `visual_review: not_performed` means **nobody (and no program) has actually looked at the picture**, because this tool cannot draw it.

**Step 3: see what it made.**

```bash
ls offsite-deck offsite-deck/pages
```

You should see:

```text
offsite-deck:
deck.pptd
pages
run

offsite-deck/pages:
01.page
02.page
```

`deck.pptd` is the table of contents, `pages/` holds one file per slide, and `run/` holds the check report and your original list. They are all text files that any text editor can open.

**Step 4: check it again on its own.** After you edit a page file, you can re-check it at any time:

```bash
.venv/bin/python skills/open-kimi-ppt/scripts/layout_quality.py offsite-deck/deck.pptd
```

You should see a long block of text that contains `"status": "passed"`.

**Try "it never quietly shrinks the text".** Replace one bullet with a very long sentence and run step 2 again (use a new output folder name, such as `--output long-deck`). You will see something like:

```text
layout_engine: slide 2 / bullet: bullets need 380.0px, available 356.0px. Split or rewrite; page count and readable font size are preserved.
```

That means it does not fit. The tool will not shrink the text until it is unreadable and will not delete your content; it asks you to change the words.

Available layouts are `cover`, `bullets`, `two_column`, `metrics`, `table` and `image`. The fields each one needs are in the table in [layout-quality.md](skills/open-kimi-ppt/reference/layout-quality.md). The repository also has a four-page example: `examples/codex/layout-spec.json`.

## 5. Chinese (and other non-Latin) content

Chinese needs a font that can show Chinese characters, and the tool **does not find the Chinese fonts on your computer by itself** (on macOS it finds none). If you give it a Chinese list directly, you will see:

```text
layout_engine: No exact usable regular/bold font pair covers this deck. Supply font.family, font.regular and font.bold with actual local files. …
```

It is asking you to name the font file to use. Add a `font` block to your list with the font's name and the full paths of its "regular" and "bold" font files:

```json
"font": {
  "family": "DengXian",
  "regular": "/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts/Deng.ttf",
  "bold": "/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts/Dengb.ttf"
}
```

Put it at the outer level of the list, next to `"title"` and `"style"`, and do not forget the comma at the end of the line before it. All other commands are exactly as in section 4.

This example uses the DengXian font that ships with Microsoft PowerPoint on macOS, so **the two files exist only on computers that have PowerPoint**. I tried it with a four-slide Chinese sample: it built, the check said `passed`, with no issues. Without PowerPoint you need to find a Chinese font that comes as separate "regular" and "bold" `.ttf` files (for example the free Noto Sans SC) and use their paths. I have not tried Noto Sans SC, and not every font will pass; see ["The page-number box is too small for the font"](#the-page-number-box-is-too-small-for-the-font) in section 8.

## 6. Let an AI assistant use it (optional)

If you use an AI coding assistant such as Claude Code or Codex, you can install this tool as its "skill" and then ask it in one sentence to make deck source files. This needs Node.js (section 2, c).

```bash
node bin/open-kimi-ppt-skill.js install --target "$HOME/.claude/skills"
```

You should see `Installed open-kimi-ppt to …/.claude/skills/open-kimi-ppt`. For Codex, change the folder to `"$HOME/.codex/skills"`.

After installing, start a new session in your assistant and say: "Use open-kimi-ppt to make a 6-slide deck source on …"

I have run only the install command above (into a temporary folder). I have not tested every AI application to see whether it recognises and uses the skill correctly. Please check any facts the AI writes.

## 7. What it cannot do

- **It cannot make `.pptx`** (PowerPoint) files. Its result is PPTD source files.
- **It cannot draw slides as pictures or give you a preview.** So every "passed" it reports comes only from real measurement of the font files and geometry: does the text fit, does anything overlap, is the contrast enough. **It cannot judge whether a slide looks good, and it cannot promise how other software will draw it.**
- **It cannot open a `.pptx` and turn it back into editable source.**
- **It does not upload your content.** This version has no feature that sends a document to a third-party web page. The upstream project used to have such a path (putting the deck into Kimi's web editor to export it); I removed it completely because it no longer works and could not be repaired.
- It does not write facts for you. An AI assistant may get numbers wrong, so check them yourself.
- You have to provide or confirm the fonts (Chinese especially).

How do you get a `.pptx`? This project cannot help with that. You can hand the content of the PPTD files to another tool that exports `.pptx`.

## 8. Common errors and what to do

### Python is too old

**Symptom 1:** in section 3, step 4, installing the parts fails with:

```text
ERROR: Could not find a version that satisfies the requirement Pillow<14,>=12 …
ERROR: No matching distribution found for Pillow<14,>=12
```

**Symptom 2:** running a command such as `node bin/open-kimi-ppt-skill.js codex doctor` fails with:

```text
Error: Python 3.10+ is required for the Codex workflow. Set OPEN_KIMI_PPT_PYTHON to a Python executable path. No installation was attempted.
```

**Cause:** your `python3` is older than 3.10 (the one that ships with macOS is 3.9.6, which is what I hit on my own machine).

**What to do:**

1. Install a newer Python. Download 3.12 or newer from <https://www.python.org/downloads/>, or use a package manager you know. This changes your system, so I did not demonstrate it on your computer and I did not verify each way of installing.
2. After installing, confirm with `python3.12 --version`; it should print `Python 3.12.x` (the installed command name usually carries the version).
3. Delete the old room and build it again with the new Python, then go back to section 3, step 4:

```bash
rm -rf .venv
python3.12 -m venv .venv
```

I verified the room-building command with a Python 3.12. `rm -rf .venv` only deletes the `.venv` in the current folder, so make sure you are inside the `OpenDeck` folder first.

### No Chinese font found

See [section 5](#5-chinese-and-other-non-latin-content). The symptom is `No exact usable regular/bold font pair covers this deck`; the fix is to add a `font` block that names the font files explicitly.

### The page-number box is too small for the font

With some fonts (I tried Microsoft YaHei) the build fails:

```text
layout_engine: slide 1 / page_number: needs 29px height at 14px, but only 27.0px is available. Split or rewrite the content; text was not shrunk or truncated.
```

**Cause:** the page-number box in the template has a fixed height of 27 pixels, and this font's line is taller, so the page number does not fit. This is a limit of the tool, not your mistake.

**What to do:** pick a font with a smaller line height, for example DengXian from section 5 (I tried it and it passes).

### The text does not fit

```text
layout_engine: slide 2 / bullet: bullets need 380.0px, available 356.0px. …
```

**What to do:** shorten the text on that slide, or move some of it to another slide. The tool will not shrink the text for you and will not quietly add pages.

### The output folder already exists

```text
layout_engine: Refusing to add a deck to a project with existing manifests or page sources; choose a new project
```

**What to do:** give `--output` a new name. It will not overwrite a deck you already have.

### Missing parts

```text
layout_engine: No module named 'PIL'
```

**Cause:** you used the system `python3`, not the room where the parts were installed.

**What to do:** replace `python3` with `.venv/bin/python` in the command, as written in this guide.

## 9. License: what you can and cannot do

**In plain words: you may look at it, use it and change it for free, but you may not use it for a commercial purpose.** If you want to use it commercially, you need separate permission from the author.

The project uses the [PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0); the full text is in [LICENSE](LICENSE).

We call it "**source-available**", not "open source". The difference: an open-source license in the usual sense (such as those approved by the OSI) must allow everyone to use the software, commercial use included. This license forbids commercial use, so it is not an OSI open-source license. GitHub may label it `Other`; that is normal.

The upstream project's code is under the MIT license. MIT requires the original author's copyright notice to stay, so the upstream part remains under MIT; its text is in [LICENSES/MIT-upstream.txt](LICENSES/MIT-upstream.txt). Which parts fall under which license is in [NOTICE](NOTICE).

## 10. For developers: run the tests

```bash
.venv/bin/python -m unittest discover -s skills/open-kimi-ppt/tests -p 'test_*.py' -q
```

```bash
node --test
```

The first should end with `OK`; the second should show `fail 0`. The tests do not use the network and install nothing.

More background: the workflow and report formats are in [codex-harness.md](skills/open-kimi-ppt/reference/codex-harness.md), what this package can and cannot do is in [rendering-boundaries.md](skills/open-kimi-ppt/reference/rendering-boundaries.md), the built-in design themes are in [theme_EN.md](theme_EN.md), and the change log is [CHANGELOG_EN.md](CHANGELOG_EN.md).
