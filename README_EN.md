# Open Kimi PPT · Single-model Codex edition

**1.4.0-codex.1 · 9 November 2026**

Based on the uploaded Open Kimi PPT 1.2.0 source. Retains PPTD, design references, the browser editor and PPTX export. The current edition uses **one active model at the user's current reasoning setting**, with measured templates, explicit layout checks and review of the actual exported PPTX.

The previous dedicated generation integration, request compiler and model routing have been removed. No model catalog, second model, subagent or generation service is required. Supplied local image assets remain supported.

## Quality contract

Every model and reasoning setting uses the same acceptance criteria. Model/effort values are opaque observations, including unknown future values and unset values. The plan never raises effort or selects another model. A lower setting does not relax font sizes, required content, page count or review requirements.

Scripts handle actual-font measurement, wrapping, bounded geometry, collision/overflow checks and evidence hashes. The active model handles content and design judgement. This reduces dependence on improvised geometry without claiming identical judgement or universally optimal results from every model.

Hard layout errors block acceptance. Findings requiring rendered inspection remain visible and require individual image evidence and explanations. A final-file review binds the actual PPTX, original final-render report and ordered page images by hash. A command succeeding is not a visual-review pass.

## Use this bundle

Open the unpacked directory in Codex and follow `CODEX_START.md` with the current model. For an explicit local skill installation:

```bash
node bin/open-kimi-ppt-skill.js install --target "$HOME/.agents/skills"
```

This replaces the same skill using staging and rollback. Do not use upstream `@latest` to install this adaptation. The portable `plugin.json` retains the original package identity.

## Create and check a source project

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

The optional `doctor` diagnoses local capabilities. Neither `init` nor `plan` requires a capability snapshot, a CLI model catalog or model-selection privileges. The active Codex host executes the plan; it is not an API runner.

Replace example content with the actual brief. Prepare a storyboard, source register and design tokens. The builder writes the layout policy, so record the design task after that file exists. Existing initialized workspaces are supported only when planned outputs do not collide.

The builder does not silently shrink text below policy, discard required facts or increase a fixed page count. Rewrite redundant wording, select another supported layout or rearrange content. Split pages only when permitted. Read `reference/layout-quality.md` for the supported spec and limitations.

An actual font with the required language coverage is necessary. Missing faces and glyphs fail explicitly. Local metrics do not prove identical typography in every Office application. Unsupported PPTD features can still be authored directly, but require the same audit and actual rendered review.

`asset_audit.py` inspects existing local assets and records their hash, geometry and review needs. It does not create images or send API requests.

## Review the final file

The existing source renderer/PPTX writer loads third-party Kimi code and supplies the deck and referenced media to its iframe. Use `--allow-remote-renderer` only when existing task authorization covers that processing:

```bash
python3 skills/open-kimi-ppt/scripts/export_images.py /abs/deck/deck.pptd \
  --output /abs/deck/run/source-renders --allow-remote-renderer
python3 skills/open-kimi-ppt/scripts/export_pptx.py /abs/deck/deck.pptd \
  --output /abs/deck/deck.pptx --allow-remote-renderer
python3 skills/open-kimi-ppt/scripts/render_pptx.py /abs/deck/deck.pptx \
  --project-root /abs/deck --output /abs/deck/run/final-renders \
  --report /abs/deck/run/final-render-qa.json
```

Review source renders before exporting. Then review every page rendered from the actual PPTX and write final-output QA. The local helper uses an explicitly installed LibreOffice and PyMuPDF; it renders existing PPTX and does not implement offline PPTD export. Its report deliberately leaves visual review and playback unperformed.

Source screenshots cannot substitute for final-file renders. Changed sources, stale hashes, fake image bytes, wrong counts and incomplete page coverage cannot satisfy the workflow. Rendering, visual inspection, font coverage and target-application playback are reported separately.

## Dependencies and delivery

Node 18+ and Python 3.10+. Explicit requirements are provided for the base tools, local layout checks and optional final rendering. Scripts do not install dependencies or modify global Codex configuration. The remote export path additionally needs agent-browser, Chromium and websocket-client.

Exporters retain isolated downloads, referenced-only media, path containment, safe ZIP checks and owned output handling. Use fresh output paths for final rendering and for legacy QA directories without ownership markers.

Old PPTD sources remain usable, but old workflow completion records must be recreated and revalidated. See `UPGRADE_NOTES.md`, `VALIDATION.md` and `reference/codex-harness.md`. The original themes, references and license are retained. This source adaptation has not been published to npm or installed on your machine.
