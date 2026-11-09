# Capability and quality evidence

Release: 1.5.0-claude.1. Updated 9 November 2026.

## Evidence levels

| Claim | Required evidence |
| --- | --- |
| Current model / reasoning setting | Actual host observation, or null/omitted when not exposed |
| Desktop version | User report, kept separate from observed CLI version |
| Available local renderer | Executable discovery and a successful actual render |
| Source geometry check | Current layout report tied to source files and measured fonts |
| Visual acceptance | Not available in this package: it has no renderer |
| PPTX output | Not available in this package: it has no PPTX writer |

The supplied desktop build `26.1103.60933` (released 9 November 2026, user-reported) is a target environment description. It does not establish installed tools, a model API identifier, image-reading ability, fonts or a successful local test.

No model-specific capability matrix is needed by the current workflow. Model and effort names remain opaque metadata. No model catalog or minimum reasoning strength is required; the same checks apply to all settings.

## Implementation evidence

- `scripts/codex_workflow.py`: serial plan, dependency checks and source/report hashes for the structure and layout gates.
- `scripts/layout_engine.py`: bounded spec-to-PPTD construction using measured fonts.
- `scripts/layout_quality.py`: source layout findings and actual font evidence.
- `scripts/render_pptx.py`: optional local rendering of a PPTX you already have, outside the task chain.
- `scripts/pptd_project.py`: reads a PPTD project and keeps every file reference inside it.
- `scripts/asset_audit.py`: existing local file inspection only.

Model judgement and a target application's rendering remain empirical questions. Source checks are not a benchmark of model quality.
