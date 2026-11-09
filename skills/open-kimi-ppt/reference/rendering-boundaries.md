# Rendering, export and compatibility

Contents: toolchain; explicit setup; remote boundary; downloads; editability; verification.

## Toolchain

- Python **3.10+** for workflow, local asset, layout and final-file rendering modules; Node **18+** for the existing CLI/editor.
- PyYAML for PPTD parsing; Pillow for image inspection/contact sheets; websocket-client for isolated browser downloads.
- An explicitly installed LibreOffice (`soffice`) and PyMuPDF for optional local rendering of an existing final PPTX.
- agent-browser **0.33.2 or later** and a supported Chromium browser for the existing remote export path.

The diagnostic and help commands do not install dependencies. Missing modules yield an actionable error. Choose a project virtual environment and explicitly install requirements when the task authorises setup:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r skills/open-kimi-ppt/requirements.txt
```

Windows uses `.venv\Scripts\python.exe`. The CLI bridge also accepts `OPEN_KIMI_PPT_PYTHON` as the path to the chosen interpreter. Do not embed quotes inside the variable's value.

Install agent-browser using an explicitly selected version compatible with the local environment, for example in a project-local npm toolchain. The package does not run `npm install -g ...@latest` or `pip --user` on your behalf. The historical minimum is not a claim that every newer browser release has been tested. Inspect `agent-browser --help` if its flags change.

## Remote boundary

The local server supplies a deck, page definitions and referenced media to a Kimi-origin iframe. The iframe loads remote code from `www.kimi.com` and `statics.moonshot.cn`; remote images/fonts may use additional origins. This source does not establish that remote JavaScript makes no further requests. Do not claim a confidentiality guarantee or a fully offline renderer.

Both exporters require `--allow-remote-renderer`. Add it when existing task/session permission already covers that data processing. It is a statement of known permission, not a host approval bypass. If permission is unresolved, finish the local editable source and explain the concrete export choice. Managed/employer material must follow the user's actual data-processing rules.

The original bridge imports a hash-named SDK module. It remains an upstream compatibility dependency. A local ZIP validation test cannot certify that URL or the public editor's current menu structure.

## Download and output handling

Exporters identify their own localhost host's browser context, direct downloads into a per-job temporary directory, and use a shared renderer lock. If the context/download API cannot be established, export stops with an error instead of scanning a user's shared Downloads folder. It does not delete shared Downloads files.

Media embedding uses referenced assets only. Project-relative resource paths must remain inside the deck after resolving symlinks. Missing images and escaping paths fail instead of silently uploading an unrelated file.

QA image export stages a complete new output before replacement. `--force` can replace only a directory marked as owned by this exporter for this manifest. It cannot delete the deck root, `pages/`, `media/`, an ancestor or another nonempty unowned directory. Archive extraction rejects duplicate names and unreasonable member/expanded sizes, and checks that the rendered page count matches the source.

PPTX export first reruns the measured layout audit and stops on hard errors before browser work. Review findings remain unaccepted until the required image inspection. It then verifies a nonempty actual PPTX ZIP, expected slide count, and valid transition placement. Fade defaults are inserted as root-level transitions in Office schema order. An existing output requires explicit `--force`.

## What remains editable

| Content | Expected treatment |
| --- | --- |
| Headings/body/citations | Native PPTD text objects; verify actual PPTX conversion |
| Exact charts/tables | Native chart/table/shape objects from source data; inspect exported values and rendering |
| Photo/illustration | Raster image asset; edit its source independently |
| Supplied SVG | Vector asset when supported; not a promise of separate editable PowerPoint shapes |
| Supplied GIF | Media file; playback support must be checked in the target app |
| Slide fade | Slide-level OOXML transition; separate from element animation |
| Element entrance/emphasis/path | PPTD animation feature supported by the chosen writer; verify actual playback |

Font embedding is best effort and subject to writer support and font licensing/availability. The presence of font parts does not prove every used face is embedded. When embedding is unavailable, report it and choose available fonts or supply the source; do not assert successful embedding based on requesting a UI switch.

## Actual final-file rendering

The optional `render_pptx.py` helper uses a new local LibreOffice profile to convert the actual PPTX to PDF and PyMuPDF to convert that PDF to PNG pages. It pins the source hash, verifies actual slide relationships/count, checks image geometry and publishes only into new or empty output paths. It does not implement PPTD-to-PPTX conversion and does not call the remote writer.

Explicit dependencies are listed in `requirements-render.txt`; LibreOffice is a separate application dependency. No setup is automatic. The default is 144 DPI with a 180-second limit. Limits include 200 slides, 20 million pixels per page, 500 million total pixels and 512 MiB of rendered image output. Unsupported linked document/media resources fail; ordinary static hyperlinks remain inert.

Successful conversion proves that this renderer produced page images from this source file. It does not establish visual acceptance or application playback. Inspect the actual final images and retain the original report. PowerPoint and Keynote may render differently; validate the intended application when that is a requirement.

## Verification record

Keep source structure, measured layout, source visual inspection, final PPTX structure, final PPTX visual inspection, font coverage and target-app playback separate. A missing check is `not_performed` or `blocked`, never implicitly passed. Required unperformed checks prevent workflow completion.

The original layout report retains review findings until they are individually resolved using source page images. Final-file review requires another set of images from the actual exported PPTX and the original final-render report, with all current hashes matching. The final renderer leaves visual and playback fields unperformed by design.

Do not infer live Kimi availability or Mac PowerPoint fidelity from offline tests. Read the package's `VALIDATION.md` for actual conversion tests and any observed visual failures. No automatic PPTX-to-PPTD importer is bundled; an export-only bridge cannot promise lossless template import.
