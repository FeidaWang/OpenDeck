# Codex single-model workflow and evidence

Version 2 uses one active host at its current reasoning setting. The plan does not run models, switch them, raise effort or create agents. All task owners are `current_host`; `max_parallel_workers` is 1.

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

Each task depends on the preceding task. Write the required artifacts, then record the actual state:

```bash
python3 <skill>/scripts/codex_workflow.py record /abs/deck story done
python3 <skill>/scripts/codex_workflow.py record /abs/deck assets blocked \
  --note "The supplied logo file is missing"
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

This package has no renderer and no PPTX writer, so there is no page-image, visual-review, export or final-file evidence to record. Those checks are reported as `not_performed`.

## Completion and migration

`status` exposes `layout_audit_status`, `unresolved_layout_issues`, `visual_review` (always `not_performed`) and `pptx_export` (always `not_available`). `complete` is true only when every task is done and no layout review finding remains open.

Schema-v1 runs are refused with migration guidance. Preserve sources and previous evidence; use a new version-2 run and repeat the current checks. Do not edit a schema version or synthesize success records to bypass migration.

This evidence system can verify files, hashes and state. It cannot judge how a slide looks, because nothing here renders it.
