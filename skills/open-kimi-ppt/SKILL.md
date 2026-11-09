---
name: open-kimi-ppt
description: Create and revise editable presentations using the current Codex model at its current reasoning setting. Use for slide decks, presentation redesigns and PPTD projects; measure layouts, preserve content and verify the actual exported PPTX.
---

# Open Kimi PPT for Codex

Create a clear, coherent presentation with editable text, numbers, tables and diagrams. Deliver the PPTD source and an actual PPTX when the available renderer can complete the job. Read only the references needed for the current task.

## One model, the same acceptance standard

Use the current host model for the entire job: story, design, pages, repair and review. Preserve the user's reasoning setting. Do not choose a preferred model, raise effort, start subagents, call a second model API or require a model catalog. Model and effort names, including unknown future values, are optional observations only. `config/codex-profiles.json` defines one serial workflow.

Every setting uses the same content, layout and final-file checks. A lower setting changes neither the typography policy nor the acceptance criteria. Use the scripts to reduce arithmetic, wrapping and state-management work. The model remains responsible for faithful content and good design choices; no setting is claimed to have identical judgement or universally optimal output.

The workflow is a host-executed plan, not a model runner. `doctor` is optional diagnostics. `plan` and `init` work without a capability snapshot. Missing rendering or image-reading capability must be recorded honestly; it is not a reason to force a different model.

## Understand the deck and preserve its requirements

Read the supplied material, audience, purpose, language, template and requested slide count. Make reasonable choices for omitted styling details. Verify time-sensitive factual claims when needed and allowed. Never invent metrics, citations, customers or outcomes to fill a page.

Write a brief with `title`, `audience`, `goal` and `slide_count`; include language, facts and constraints when known. Start a new project:

```bash
python3 <skill>/scripts/codex_workflow.py init \
  --brief /abs/brief.json --project /abs/deck
```

Write `run/storyboard.json`, `run/sources.json` and `run/design-tokens.json`. Plan one communicative purpose per page, a coherent narrative and a consistent typographic hierarchy. Preserve the user's selected design system; otherwise select one suitable system. Avoid unnecessary labels and repeated decorative panels.

Use readable body text, deliberate whitespace, aligned edges and restrained colour. Distinguish proposed plans from observed facts. Keep chart values and evidence labels accurate and editable. Use supplied photos and logos with correct proportions. A strong deck can be built entirely from native text and shapes; there is no required generation service.

## Prefer measured, bounded layouts

Read [layout-quality.md](reference/layout-quality.md). For a new deck, express content in a layout spec and use the deterministic builder:

```bash
python3 <skill>/scripts/layout_engine.py build \
  --spec /abs/layout-spec.json --output /abs/deck
```

The output must be new, empty or an initialized workspace with no colliding deck/page/policy outputs. The builder writes editable PPTD pages and the layout policy. Record the design task after that policy exists. Use the bundled example as a schema example, not as a factual template for unrelated content.

The builder measures an actual font face, wraps text, uses bounded template geometry and checks the result. It must not silently shrink below policy, remove required content or increase a fixed slide count. When content does not fit, shorten redundant wording while preserving meaning, change the template or rearrange content within the permitted pages. Split pages only when the brief permits it. Keep an unresolved capacity conflict visible.

Select fonts with the needed language coverage. Missing families, styles or glyphs are failures requiring an available suitable font or the actual font files. A fallback or tofu glyph is not a successful font check. Local font metrics do not establish identical metrics in another renderer.

For layouts outside the builder's supported templates, author native PPTD using [pptd.md](reference/pptd.md), then run the same checks. Unsupported rich text, transforms, charts and intentional overlaps require actual rendered review; do not erase or bulk-dismiss findings to get a green result.

Keep project-relative pages under `pages/` and media under `media/`. Use `asset_audit.py audit` for existing local assets. It verifies files and records dimensions, hashes and inspection requirements; it never creates images or sends API requests. Provenance declarations remain declarations until checked.

The package has no PPTX-to-PPTD importer. Preserve original PPTX files and use an actually available importer or presentation editor when that is the task.

## Check the source and inspect rendered pages

```bash
python3 <skill>/scripts/codex_workflow.py validate /abs/deck/deck.pptd \
  --output /abs/deck/run/structural-qa.json
python3 <skill>/scripts/layout_quality.py check /abs/deck/deck.pptd \
  --output /abs/deck/run/layout-qa.json
```

Structural checks validate the supported PPTD subset and local resources. Layout checks cover measurable geometry, typography, overflow, contrast and collisions. `failed` means a hard issue must be repaired. `needs_review` means specific findings remain open for rendered inspection; it is never a visual pass.

The existing source renderer and PPTX writer load Kimi's remote editor code and supply the deck and referenced media to a third-party iframe. Use them when that data processing is already authorized for the task. The explicit flag acknowledges the existing boundary and does not change host policy:

```bash
python3 <skill>/scripts/export_images.py /abs/deck/deck.pptd \
  --output /abs/deck/run/source-renders --allow-remote-renderer
```

Inspect every page at a readable scale; use a contact sheet for cross-page consistency and individual images for detailed checks. Check clipping, text fit, line breaks, reading order, alignment, contrast, image cropping, tables, charts, exact numbers and source labels. Compare with the requested template when relevant. Record the actual source image paths and hashes with `render_source: source_pptd`.

Resolve each static review finding individually using the corresponding page image and a concrete explanation. Hard layout errors must be repaired in source. Never add a blanket ignore list, forge a report or equate a successful command with visual review. Make focused repairs, then repeat the affected checks and refresh dependent evidence. Continue while useful repairs are available; if blocked, report the specific unresolved issue.

## Verify the actual final PPTX

Export only after the source review passes:

```bash
python3 <skill>/scripts/export_pptx.py /abs/deck/deck.pptd \
  --output /abs/deck/deck.pptx --allow-remote-renderer
python3 <skill>/scripts/render_pptx.py /abs/deck/deck.pptx \
  --project-root /abs/deck --output /abs/deck/run/final-renders \
  --report /abs/deck/run/final-render-qa.json
```

The final renderer uses an explicitly installed local LibreOffice and PyMuPDF. It converts the actual PPTX to page images, checks page counts and records the source PPTX hash. It does not inspect the visual result or export PPTD. An equivalent available renderer can supply the documented evidence format.

Open the final images and review every page again. Record `run/final-output-qa.json` only after this actual review. It must reference the original final-render report, current PPTX hash and all final page image hashes. Source-render screenshots cannot stand in for this step. If the final file changes, rerender and rereview it.

Keep font embedding and target-application playback separate from static rendering. The presence of a font part is not complete font coverage; static images cannot prove animations or media playback. Do not claim a universal absence of layout problems across applications and fonts.

## Resume and deliver

Use `codex_workflow.py record`, `status` and `run/state.json`. Check status when resuming after interruption. Changed planning inputs or source artifacts invalidate downstream completion. Read [codex-harness.md](reference/codex-harness.md) for the report contracts and [rendering-boundaries.md](reference/rendering-boundaries.md) for dependencies and export behavior.

Deliver the actual source project, PPTX and concise QA status. The workflow is complete only after the required source and final-output checks pass. If actual rendering or visual inspection cannot be performed, deliver the usable work with the unresolved check marked `blocked` or `not_performed`; do not label it visually accepted.

Optional references: [slides_categories.md](reference/slides_categories.md), [fonts.md](reference/fonts.md), the selected `reference/design_system/` file, and [general-poster.md](reference/general-poster.md) for posters. For manual editing, the local bundle's `node bin/open-kimi-ppt-skill.js serve` uses the existing remote editor. Do not fetch upstream `@latest` to install this adaptation.
