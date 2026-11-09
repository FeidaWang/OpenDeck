# Release validation

**Release:** 1.4.0-codex.1
**Date:** 9 November 2026
**Target desktop:** 26.1103.60933, user-reported; not executed on the user's machine.

## Tests actually run on the final runtime sources

| Check | Observed result |
| --- | --- |
| `python3 -m unittest discover -s skills/open-kimi-ppt/tests -p 'test_*.py' -q` | **149 passed, 0 failed** |
| `node --test` | **27 passed, 0 failed** |
| Combined automated cases | **176 passed** |
| Portable plugin and Skill metadata | JSON/YAML valid; identity and version match; description lengths valid |
| Skill router references | All 7 linked local references present |
| `npm pack --dry-run --json` | All new runtime modules, layout reference, config and requirements included; caches excluded |
| `git diff --check` | Passed |
| Removed integration scan | No dedicated generation module, request compiler, example or active documentation references remain |

Python breakdown: **47 workflow**, **25 deterministic-layout**, **18 final-renderer**, **16 local-asset**, **30 PPTX-export** and **13 source-image-export** tests. Fixtures/mocks cover browser/protocol and failure cases; these are not claims of live Kimi service testing. npm emitted an environment proxy-option warning which did not affect the result.

Logs are included under `validation/`. The test suite does not invoke a generation API, install packages or change global Codex configuration.

## Layout and content regressions

The layout suite exercises all six templates in all three palettes and validates their payloads through the existing exporter parser. It checks real regular/bold font files, English/long-word/CJK wrapping mechanics, missing glyphs, content capacity, exact page counts, paths and preservation of existing output.

Independent review found six concrete cases that could previously be misclassified or lose content. They are fixed and covered: `wrap:false` single-line overflow; `$` theme font styles; vertical text requiring review; an ellipse bounding box incorrectly treated as painted coverage; unsupported embedded shape text; and falsey optional content such as numeric zero being silently omitted.

The final checker accepts both `layout_quality.py check MANIFEST` and the direct manifest syntax. It returns 0 only for `passed`, 1 for `failed` or `needs_review`, and 2 for invalid input/runtime prerequisites. Review findings still need actual per-page rendered inspection.

The standalone PPTX exporter runs a fresh layout preflight before browser access/output creation and rejects hard layout errors. Review-only output remains an unaccepted candidate until workflow reviews are completed.

## Single-model source acceptance

Two fresh projects were built and recorded using the final runtime source hashes, without a capabilities file or model catalog:

| Trial | Pages | Measured text boxes | Source structure | Source layout |
| --- | ---: | ---: | --- | --- |
| Six-template integration fixture | 6 | 46 | Passed | Passed, no findings |
| Exact bundled workshop brief/spec | 4 | 30 | Passed | Passed, no findings |

Both preserved the initialized state through build and completed `story` through `layout` using actual artifacts. Both correctly stopped with `ready: ["render"]` and `complete: false`. Source rendering, source visual review, PPTX export and final-output review remained pending. No screenshot, PPTX or visual pass was fabricated for these new source decks.

Seven metadata inputs (`null`, `none`, `low`, `high`, `ultra`, `unset`, and an unknown future string) retained their exact values and produced the same task chain, quality policy and one-worker execution. These are routing/policy tests, not a benchmark proving equal quality from seven reasoning settings or different models.

An overloaded content case required 5504 px of height in 356 px of available space and was rejected without shrinking, truncating or producing output. A Chinese-text case explicitly requesting DejaVu Sans was rejected for missing Chinese glyphs. CJK wrapping support is not a claim that this environment supplies a suitable CJK font.

## State and evidence acceptance

Workflow tests reject hard source/layout failures, missing/forged image evidence, insufficient image size, wrong slide counts, omitted layout findings, incomplete finding resolutions, stale source/font/report hashes and source-preview images reused as final-file evidence.

Final review requires the current actual PPTX, the immutable original final-render report and all its actual page images. File conversion alone never marks visual review passed. Target-application playback remains separate.

Layout verification fingerprints `codex_workflow.py`, `layout_quality.py`, `asset_audit.py` and `export_pptx.py`. A changed checker implementation or an older record without those fingerprints invalidates layout acceptance and downstream records, even when the deck source itself is unchanged. Fingerprint keys use module names and do not depend on the installation directory.

## Actual final-PPTX renderer smoke test

The original historical `example/yu7-ppt/yu7.pptx` was copied into an isolated trial project and rendered with **LibreOfficeDev 26.8.0.0.alpha0** and **PyMuPDF 1.26.6**. It produced **8 PNG pages, each 1920 × 1080**. The source/copy hashes matched and the original file was not changed. The helper's conversion report retained `visual_review: not_performed` and `playback: not_performed`.

The primary reviewer viewed the eight-page contact sheet and page 7 at full size. Page 7 visibly combines background illustration labels with foreground metric text, creating collisions and reduced readability. **This historical fixture therefore does not pass visual acceptance.** The successful conversion demonstrates actual file rendering; it is not a newly authored successful presentation or a zero-layout-error claim. The full-source package retains the historical example unchanged; it is not an acceptance template for the new builder.

This is why final-file rendering and actual review are separate required steps. The new source-layout checks alone cannot detect every label already baked into a background image.

## Environment and boundaries

Observed runtime: Node 24.22.1, Python 3.12.14, PyYAML 6.0.3, Pillow 13.0.0 and PyMuPDF 1.26.6. A `soffice` runtime wrapper was discovered and actually used; a previous check of only the `libreoffice` executable name had missed it. The actual wrapper's returned version is recorded above.

Not performed in this release:

- Live Kimi source rendering or PPTD-to-PPTX export for the new template trial decks.
- Installation or execution in the user's specific Codex desktop build.
- PowerPoint/Keynote application opening, full font-embedding verification or animation/media playback.
- A model-quality benchmark across all models and reasoning settings.
- Universal font discovery on macOS or support for nonzero TTC/OTC collection-face indices.

The source/geometry contract is intentionally bounded. Complex text, unsupported charts/tables, transforms, uncertain image backgrounds and declared overlaps require explicit rendered inspection. The workflow enforces required evidence and retains unperformed checks; it cannot mathematically certify an operator's judgement or promise identical rendering in all applications.

## Distribution integrity

The release keeps the original package/Skill identity and versioned editable format. The code ZIP excludes original large screenshots and presentations; the full-source ZIP preserves them. Both include a clean single-commit current-source Git snapshot, with no superseded integration history or old removal patch. The snapshot was synchronized to an isolated local Git mirror; nothing was published to npm or upstream.
