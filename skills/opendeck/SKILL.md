---
name: opendeck
description: Create and revise editable PPTD presentation projects using the current host model at its current reasoning setting. Use for slide decks, presentation redesigns and PPTD projects; measure layouts and preserve content. Produces PPTD source and local layout checks only; it cannot render or export PPTX.
---

# OpenDeck

Create a clear, coherent presentation with editable text, numbers, tables and diagrams. Deliver the PPTD source project with its structural and layout check results. This package cannot render slides to images or write PPTX; say so plainly instead of implying otherwise. Read only the references needed for the current task.

## One model, the same acceptance standard

Use the current host model for the entire job: story, design, pages, repair and review. Preserve the user's reasoning setting. Do not choose a preferred model, raise effort, start subagents, call a second model API or require a model catalog. Model and effort names, including unknown future values, are optional observations only. `config/codex-profiles.json` defines one serial workflow.

Every setting uses the same content and layout checks. A lower setting changes neither the typography policy nor the acceptance criteria. Use the scripts to reduce arithmetic, wrapping and state-management work. The model remains responsible for faithful content and good design choices; no setting is claimed to have identical judgement or universally optimal output.

The workflow is a host-executed plan, not a model runner. `doctor` is optional diagnostics. `plan` and `init` work without a capability snapshot. This package has no renderer, so visual review is never claimed; report it as `not_performed`.

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

For layouts outside the builder's supported templates, author native PPTD using [pptd.md](reference/pptd.md), then run the same checks. Unsupported rich text, transforms, charts and intentional overlaps stay as `needs_review` findings that only a person looking at a real rendering can settle; do not erase or bulk-dismiss findings to get a green result.

Keep project-relative pages under `pages/` and media under `media/`. Use `asset_audit.py audit` for existing local assets. It verifies files and records dimensions, hashes and inspection requirements; it never creates images or sends API requests. Provenance declarations remain declarations until checked.

The package has no PPTX-to-PPTD importer and no PPTD-to-PPTX writer. Preserve original PPTX files; if the task needs a PPTX, say that this package cannot produce one.

## Check the source

```bash
python3 <skill>/scripts/codex_workflow.py validate /abs/deck/deck.pptd \
  --output /abs/deck/run/structural-qa.json
python3 <skill>/scripts/layout_quality.py check /abs/deck/deck.pptd \
  --output /abs/deck/run/layout-qa.json
```

Structural checks validate the supported PPTD subset and local resources. Layout checks cover measurable geometry, typography, overflow, contrast and collisions. `failed` means a hard issue must be repaired. `needs_review` means specific findings stay open: this package cannot render the deck, so they are reported to the user, never marked resolved.

Make focused repairs, then repeat the affected checks and refresh dependent evidence. Continue while useful repairs are available; if blocked, report the specific unresolved issue.

## Resume and deliver

Use `codex_workflow.py record`, `status` and `run/state.json`. Check status when resuming after interruption. Changed planning inputs or source artifacts invalidate downstream completion. Read [codex-harness.md](reference/codex-harness.md) for the report contracts and [rendering-boundaries.md](reference/rendering-boundaries.md) for dependencies and what this package cannot do.

Deliver the PPTD source project and a concise QA status: structure and measured layout results, plus any open `needs_review` findings. State that visual review and PPTX export were not performed. The workflow is complete only when every task is done and no layout finding remains open.

Optional references: [slides_categories.md](reference/slides_categories.md), [fonts.md](reference/fonts.md), the selected `reference/design_system/` file, and [general-poster.md](reference/general-poster.md) for posters. Do not fetch upstream `@latest` to install this adaptation.
