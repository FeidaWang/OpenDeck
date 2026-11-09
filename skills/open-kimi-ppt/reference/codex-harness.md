# Codex single-model workflow and evidence

Version 2 uses one active host at its current reasoning setting. The plan does not run models, switch them, raise effort or create agents. All task owners are `current_host`; `max_parallel_workers` and `max_renderer_jobs` are 1.

## Start without discovery

```bash
python3 <skill>/scripts/codex_workflow.py plan --brief brief.json --output plan.json
python3 <skill>/scripts/codex_workflow.py init --brief brief.json --project /abs/deck
```

`init` accepts a new or empty directory. `--capabilities observations.json` is optional for either command. An omitted or unknown model/effort value does not change the plan or its gates. The snapshot may contain `current_model` and `current_effort` strings or null; values are retained exactly and never matched against a model catalog.

`doctor` reports local tools without installing dependencies. `--probe-codex` optionally uses the installed CLI's read-only app-server model list for diagnostic metadata. It is never a planning prerequisite. The user-reported desktop build is separate from observed CLI and tools.

## Task chain

| Task | Required primary outputs |
| --- | --- |
| story | `run/storyboard.json`, `run/sources.json` |
| design | `run/design-tokens.json`, `run/layout-policy.json` |
| assets | `run/assets.json` |
| pages | Ordered `pages/01.page` through the planned count |
| integrate | `deck.pptd` |
| structure | `run/structural-qa.json` |
| layout | `run/layout-qa.json` |
| render | `run/render-qa.json` |
| visual_review | `run/visual-qa.json` |
| export | `deck.pptx`, `run/export-qa.json` |
| final_output_review | `run/final-output-qa.json` |

Each task depends on the preceding task. Write the required artifacts, then record the actual state:

```bash
python3 <skill>/scripts/codex_workflow.py record /abs/deck story done
python3 <skill>/scripts/codex_workflow.py record /abs/deck render blocked \
  --note "The authorized source renderer is unavailable in this environment"
python3 <skill>/scripts/codex_workflow.py status /abs/deck
```

Allowed record states are `pending`, `running`, `done` and `blocked`. A blocked state needs a concrete reason. `--evidence relative/path` can attach additional existing files. Changes to an input or artifact invalidate downstream completion. Updating the verifier implementation also invalidates recorded layout acceptance and its downstream tasks. A locked state is not overwritten by a concurrent writer.

The layout builder can populate an initialized workspace before its tasks are recorded. It writes the policy needed by the design task. Empty asset sets use `{"files": [], "assets": []}`; there is no required generation step.

## Automated structure and layout reports

```bash
python3 <skill>/scripts/codex_workflow.py validate /abs/deck/deck.pptd \
  --output /abs/deck/run/structural-qa.json
python3 <skill>/scripts/codex_workflow.py layout /abs/deck/deck.pptd \
  --output /abs/deck/run/layout-qa.json
```

The layout CLI exits 0 for `passed`, 1 for `failed` or `needs_review`, and 2 for execution errors. A nonzero review result must be read, not discarded. The equivalent `layout_quality.py check` is documented in `layout-quality.md`.

Recording structure reruns structural validation against current source. Recording layout reruns the actual auditor, compares source and font evidence and retains all findings. A layout task can be recorded as performed with `needs_review`, but the state still exposes its unresolved findings. `failed` and hard-error findings cannot complete that task or be resolved by a visual override.

## Common evidence rules

All report paths are project-relative and contained. Every report has integer `slide_count` equal to the plan and a `files` array of `{ "path": "...", "sha256": "..." }` objects. Evidence hashes must match current nonempty files. An evidence filename or claimed status alone is insufficient.

Actual page images must decode, be static PNG/JPEG/WebP, cover every planned page in order and have the expected canvas aspect ratio and at least the source canvas pixel dimensions. A small contact sheet or a text file with a PNG suffix cannot stand in for page images. Contact sheets are useful for review but are not the primary page evidence.

## Source rendering and source visual review

`run/render-qa.json` is written from actual source renderer output. Required fields:

- `status: "passed"`, `render_source: "source_pptd"`, `slide_count`.
- `files` containing the actual ordered page image hashes.
- `pages: [{"page": 1, "path": "run/source-renders/page-001.png"}, ...]`.

The existing image exporter returns extracted paths and an overview; the host assembles this workflow report from those actual files. It must not invent page paths, hashes or images.

After inspecting every source page, `run/visual-qa.json` uses exactly the same ordered page paths and hashes, adds `status: "passed"` to each page and sets `review_method: "operator_attested"`. This is an attestation by the current model/operator who actually inspected the images, not an automated aesthetic score.

It also includes:

```json
{
  "layout_review": {
    "report_path": "run/layout-qa.json",
    "sha256": "ACTUAL_CURRENT_LAYOUT_REPORT_HASH",
    "resolutions": [
      {
        "issue_id": "ACTUAL_STABLE_FINDING_ID",
        "page": 2,
        "path": "run/source-renders/page-002.png",
        "status": "resolved",
        "explanation": "Specific observed result after inspecting this finding"
      }
    ]
  }
}
```

Add the layout report's hash to `files` as well. Every current review finding must appear exactly once with the correct page evidence and a meaningful explanation. If there are no review findings, use an explicit empty `resolutions` array. For a deck-wide finding, use `page: 0` and a `pages` array covering every page. There is no blanket dismissal or hard-error waiver.

## Export report

`run/export-qa.json` requires `status: "passed"`, the correct `slide_count`, `files` with the current `deck.pptx` hash and `transition` equal to `fade` or `none`. Record the actual writer result. `record export done` reparses the actual PPTX and verifies its slide count and transitions. `font_parts`, when supplied, must match inspection; a count alone does not establish complete font coverage.

The standalone PPTX exporter reruns layout checks before starting remote work and stops on hard errors. A review-only result can produce an unaccepted candidate for inspection; workflow completion still requires the source and final-output review gates. Successful file creation does not establish acceptance.

## Final PPTX render and review

`render_pptx.py` creates `run/final-render-qa.json` from the actual PPTX and a new set of page images. The immutable original report includes:

- `status: "passed"`, `kind: "final_pptx_render"`, `render_source: "final_pptx"`.
- `source_pptx: {"path": "deck.pptx", "sha256": "ACTUAL_HASH"}`.
- `renderer: {"name": "LibreOffice", "version": "ACTUALLY_OBSERVED_VERSION"}`.
- `slide_count`, `files` with source and page-image hashes, and ordered `pages` with page/path/hash.
- `visual_review: "not_performed"` and `playback: "not_performed"`.

After actually inspecting all final images, write a separate `run/final-output-qa.json`:

- Keep the exact `render_source`, `source_pptx`, `renderer`, `slide_count` and ordered final page paths.
- Set report and each page `status` to `passed`, and `review_method` to `operator_attested` only after inspection.
- Set `visual_review` to `operator_attested`; copying `not_performed` into a claimed pass is contradictory and rejected.
- Add `render_report: {"path": "run/final-render-qa.json", "sha256": "ACTUAL_REPORT_HASH"}`.
- Include all original renderer evidence files plus the original report itself in `files`.

The workflow reads the original report, verifies its current PPTX hash, renderer and page image evidence, and rejects source-preview path reuse. Identical bytes from genuinely separate source/final renders are permitted; fabricated provenance is not. Do not modify the original renderer report to claim a review happened.

Static review leaves playback unperformed. A separate playback attestation, when actually executed, needs status `passed`, review method `operator_attested`, the named application, notes and `source_pptx_sha256` matching the current file.

## Completion and migration

`status` exposes `layout_audit_status`, `unresolved_layout_issues`, `layout_issue_resolution`, `final_pptx_review_status` and separate `playback_review`. `complete` is true only when every required task is done and no layout review finding remains open. Playback is separately reported and is not invented from a static render.

Schema-v1 runs are refused with migration guidance. Preserve sources and previous evidence; use a new version-2 run and repeat the current checks. Do not edit a schema version or synthesize success records to bypass migration.

This evidence system can verify files, hashes, ordered coverage and state. It cannot mathematically verify the truth of an operator's visual judgement. The Skill requires actual page-by-page inspection and truthful reporting; unavailable visual capability leaves the check unfinished.
