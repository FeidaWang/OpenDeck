# What this package can and cannot do

Contents: toolchain; explicit setup; what it cannot do; what stays editable; verification record.

## Toolchain

- Python **3.10+** for the workflow, local asset and layout modules; Node **18+** for the installer CLI.
- PyYAML for PPTD parsing; Pillow for image inspection and font measurement.
- Optional: a separately installed LibreOffice (`soffice`) and PyMuPDF for `render_pptx.py`, which renders a PPTX that you already have. It is not part of the task chain.

The diagnostic and help commands do not install dependencies. Missing modules yield an actionable error. Choose a project virtual environment and explicitly install requirements when the task authorises setup:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r skills/opendeck/requirements.txt
```

Windows uses `.venv\Scripts\python.exe`. The CLI bridge also accepts `OPENDECK_PYTHON` as the path to the chosen interpreter. Do not embed quotes inside the variable's value.

## What it cannot do

- **It cannot render slides to images.** There is no renderer for PPTD in this package, so nobody, including the model, can look at a finished page here.
- **It cannot write PPTX.** An earlier version sent the deck to a third-party web editor to produce PPTX and page images. That path was removed because it no longer works and could not be repaired inside this project: the web editor produces PPTX on its own servers, not in the browser. Nothing in this package sends a deck to a third party.
- **It cannot import PPTX.** No PPTX-to-PPTD importer is bundled.
- Everything it reports about layout comes from measuring real font files and checking geometry. That finds overflow, collisions, contrast and missing glyphs. It does not judge whether a slide looks good, and it cannot prove how another application will draw the file.

## What stays editable

The deliverable is a PPTD project: `deck.pptd`, `pages/*.page` and `media/`. Text, numbers, tables and shapes in the pages are plain YAML and stay editable in any text editor. Images are separate files in `media/`.

## Verification record

Keep source structure, measured layout, visual inspection, font coverage and playback separate. A check that was not performed is `not_performed`, never implicitly passed. In this package visual inspection, PPTX export and playback are always `not_performed`.

Layout findings with severity `review` stay open in `status` as `unresolved_layout_issues`, and they keep `complete` false until the source is changed so the finding no longer appears. Hard `failed` findings cannot be recorded.
