# Deterministic editable layouts and source checks

This path makes geometry, typography, source checks and review gates independent of the selected model and reasoning effort. The current model supplies a concise, factual content specification. Local code selects a bounded template, measures the actual local font files, preserves the specified page count and emits editable PPTD objects. It does not select another model, change effort, call an image model or install dependencies.

A source-layout pass means the supported source checks passed. It does **not** certify how any application will draw the deck, another machine's fonts, application playback or editorial quality. This package cannot render the deck, so visual review stays with a person.

## Build a deck

```bash
python skills/opendeck/scripts/layout_engine.py build \
  --spec examples/codex/layout-spec.json \
  --output /absolute/path/to/project
```

`--output-dir` is an alias for `--output`. `--asset-root DIR` optionally sets the directory for relative source image and font paths; its default is the specification file's directory. The bundled specification matches the four-slide `examples/codex/brief.json`.

The destination can be new or an already initialized workflow project with no manifest or page source files. Existing `run/brief.json`, plan, state and design tokens remain intact. The builder refuses to overwrite any planned output, including an existing layout policy or report. To revise an already built deck, use a new project or edit the editable sources and rerun checks; this command has no destructive overwrite flag.

When using `codex_workflow.py init`, build before recording the design stage complete: the builder creates the design stage's `run/layout-policy.json` output. Create and review the storyboard, design tokens, source inventory and asset records required by the workflow; then record stages in their prescribed order. A successful build does not mark those stages complete automatically.

Outputs are:

- `deck.pptd` and `pages/01.page`, `pages/02.page`, etc.
- `media/slide-001.png` or `.jpg` when an image template uses a source asset.
- `run/layout-spec.json`, an exact JSON content-specification copy.
- `run/layout-policy.json`, the selected actual font paths, margin policy and caption roles.
- `run/layout-qa.json`, the deterministic source-layout report.

The entire deck is staged and checked before publication. Publication uses exclusive file creation into an existing initialized project, rolls back files it created if publication fails, and never replaces existing files. As with any multi-file write into an existing directory, process or machine interruption can leave an incomplete set; rerun source checks before continuing.

## Specification

```json
{
  "version": 1,
  "title": "Project decision",
  "style": "warm",
  "slide_count": 2,
  "slides": [
    {"layout": "cover", "title": "Choose the next step", "subtitle": "An editable proposal"},
    {"layout": "bullets", "title": "Make ownership clear", "bullets": ["Name the decision.", "Assign the next step.", "Agree a review date."], "source": "Source: supplied project brief"}
  ]
}
```

The canvas is fixed at **960 × 540** (16:9). The PPTD specification uses px for coordinates and defines a text-size unit as one point in PPTX. This route intentionally exposes a few validated layouts, instead of asking the model to invent arbitrary geometry. `warm`, `neutral` and `dark` are the three supported palettes. Unknown specification fields are rejected.

`title` and a nonempty `slides` array are required. `version` defaults to `1`. If supplied, integer `slide_count` must equal the array length. The builder accepts 1–100 slides and never adds, removes or merges pages. Text fields must contain nonempty strings; numbers and dates in metric/table text must be supplied as their intended display strings. Optional text fields must also be valid nonempty strings when present; false, zero, null and empty strings are errors, rather than silently discarded content.

Every slide requires `layout` and `title`; all layouts accept an optional single-line `source` footer. Layout-specific fields are:

| Layout | Fields | Bounds and intended use |
| --- | --- | --- |
| `cover` | Optional `subtitle`, `eyebrow` | Title up to two measured lines; subtitle up to three; eyebrow one. |
| `bullets` | `bullets`: 1–8 strings | Each item is an editable text box with a separate dot; combined measured height must fit. |
| `two_column` | `columns`: exactly two `{heading, bullets}` objects | Two equal cards; each heading up to two lines; each bullet list must fit its card. |
| `metrics` | `metrics`: two or three `{value, label, detail?}` objects | Values one line, labels up to two lines, optional detail up to four lines. Values are supplied facts, never calculated or invented. |
| `table` | `headers`, `rows`, optional `column_weights` | 1–5 columns, 1–8 complete rows. All cells are editable text over native rectangle cells. Column weights must be positive and match the header count; minimum column width is 104 px. Total measured row height must fit. |
| `image` | `image`: relative asset path; optional `body` | Local PNG/JPEG up to 20 MiB, copied without modification. Image aspect ratio is retained. Body and labels stay editable. |

Counts are maximum allowed inputs, not promises that arbitrary prose will fit. For example, eight long bullets or eight multi-line table rows will fail with an actionable message. Text is never reduced automatically, clipped, replaced with ellipses or silently deleted. If the user fixes the page count, rewrite or reprioritize with their content intent preserved. If the brief permits additional slides, revise the explicit specification and agreed page count before building again.

Editable numeric charts, complex diagrams, arbitrary positioning, mixed fonts and native PPTD table/chart elements remain available through hand-authored PPTD. They are outside this template builder. The checker identifies unsupported constructs for explicit rendered review; it does not pretend to measure their internal geometry.

## Typography and fonts

Templates use 48 px cover titles, 36 px page titles, 24 px body text, 22 px column text/labels, 20 px metric detail/table headers, 18 px table cells and 14 px source/page-number captions. They use explicit measured paragraph breaks, disable automatic rewrapping, reserve additional space around text, and use 48 px main margins with a 24 px minimum content safety boundary. These values do not change with model or reasoning effort.

The resolver uses actual Pillow/FreeType metrics at four times the target size for fractional precision, including font ascenders/descenders. Wrapping preserves explicit line breaks and combining-mark groups, prefers English word boundaries, and can break CJK text or long unbroken strings by glyph. CJK wrapping support does not imply that a CJK font is installed.

Without an explicit font, the builder checks available regular/bold pairs and their glyph coverage. Latin candidates include DejaVu Sans, Arial and Noto Sans. For CJK content it also tries Noto Sans CJK SC, Microsoft YaHei and PingFang SC. The selected actual family is written into every text box and the policy. A user-requested family is never silently replaced.

Supply exact files when needed:

```json
"font": {
  "family": "Your Actual Font Family",
  "regular": "/absolute/path/to/Regular.ttf",
  "bold": "/absolute/path/to/Bold.ttf"
}
```

File metadata must match the family and face. Relative files resolve under the asset root. On Linux, exact installed-family discovery can use `fc-match`; known DejaVu paths are also checked. Known Arial paths are checked on macOS and Windows, and Microsoft YaHei paths on Windows. macOS discovery is not universal: if a usable face cannot be discovered, supply its actual path. The code uses collection face index **0 only**; a nonzero TTC/OTC face index, variable-font axes and synthetic bold/italic are not supported. Provide a suitable standalone face or use another explicitly chosen family when the requested collection face is not index 0. No font files are automatically copied or redistributed.

Missing families, style substitutions and detected missing glyphs are hard errors. Glyph detection compares the selected face's FreeType missing-glyph mask; it is not a complete certification of complex-script shaping, emoji composition or another renderer's fallback behavior. Install/provide the required licensed font through the user's normal process and rerun the build. Nothing is downloaded automatically. A Linux machine without a CJK-capable font correctly rejects Chinese content instead of emitting tofu boxes.

## Check arbitrary PPTD

```bash
python skills/opendeck/scripts/layout_quality.py check /absolute/path/to/project/deck.pptd \
  --output /absolute/path/to/project/run/layout-qa.json
```

Python interfaces:

```python
from layout_engine import build_deck, select_font
from layout_quality import audit_deck, check_layout

report = build_deck(spec_dict, output_path, asset_root)
report = audit_deck(manifest_path, policy=None)
# check_layout is an alias of audit_deck.
family, font_policy = select_font("Text to cover", requested=None, root=asset_root)
```

`audit_deck` loads adjacent `run/layout-policy.json` automatically. `policy` may also be a mapping or path to a JSON policy. Geometry uses the manifest's own canvas size, so the checker itself can inspect sizes other than the builder's 960 × 540. Source/report validation errors raise `LayoutError`; detected layout findings return a structured report. The leading `check` verb is optional for compatibility with the direct `layout_quality.py MANIFEST` form. Both forms return 0 only for `passed`, 1 for `failed` or `needs_review`, and 2 for invalid input/runtime prerequisites. Inspect the report's `status` to distinguish a hard failure from findings that can proceed to rendered review. Even exit code 0 is source approval only. The report output is replaceable derived evidence; source files are never modified by the checker.

Hard findings include nonfinite/negative dimensions, out-of-slide content, duplicate IDs, content crossing the configured safety margin, measurable text overflow, unsupported exact font faces, missing glyphs, stretched images, forbidden embedded text in shape objects, body text below 18 px, captions below 14 px, determinable low contrast, and unintended text intersections with foreground objects. `wrap: false`, `$` theme references, plain text and supported HTML runs are measured according to their source settings. The default caption role must be assigned explicitly in the policy; it is not inferred from a small font.

Rectangular backgrounds and earlier containers containing their content are distinguished from covering foreground shapes. Curved shapes require painted-coverage checks: an ellipse's bounding rectangle is not treated as an opaque rectangular background. Curved/complex boundaries that cannot be established conservatively require review. Solid foreground/background contrast uses 4.5:1 for ordinary text and 3:1 for large text. The checker considers text highlight backgrounds as well as page/card backgrounds; gradients, transparency and images can make contrast indeterminate.

Mixed rich-text font runs use conservative metrics and require review. Vertical text, unsupported HTML/CSS, formulas, native charts/tables, other unsupported elements, line strokes, complex shapes, transforms, text effects, animation, remote assets and unknown contrast all receive explicit review findings. Declared intentional overlaps also require rendered inspection; a policy entry never turns an unmeasured intersection into a silent pass.

## Evidence and resolution

The report has `schema_version: 1`, `kind: deterministic_source_layout`, `slide_count`, and:

- `status`: `failed` for any hard error; `needs_review` when only unverified/review findings remain; `passed` when none remain.
- `files`: project-relative source paths with SHA-256 hashes, including the manifest, pages, local images, policy and any declared `source_files`.
- `fonts`: actual family, face, absolute local file path and SHA-256 hash. `verified` means local family/style/file verification, not target-application verification.
- `policy_sha256`: a deterministic fingerprint of the effective policy, including policies supplied as mappings.
- `issues`: stable `id`, `severity` (`error` or `review`), `code`, 1-based integer `page`, `element` and a human-readable message. IDs follow `p001:element:code`; repeated same-tuple findings receive deterministic suffixes. Manifest-level findings may use page 0.
- `metrics`: measured line counts and text extents for inspected text boxes.
- `visual_review` and `target_application_review`: both `not_performed`.

The workflow reruns the checker before accepting evidence. A hard error cannot be waived. A `needs_review` report can be recorded, but its findings stay listed as unresolved and keep the workflow incomplete until the source changes so they no longer appear. Changing the source, effective policy or font bytes requires fresh checks.
