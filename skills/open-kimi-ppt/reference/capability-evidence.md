# Capability and quality evidence

Release: 1.4.0-codex.1. Updated 9 November 2026.

## Evidence levels

| Claim | Required evidence |
| --- | --- |
| Current model / reasoning setting | Actual host observation, or null/omitted when not exposed |
| Desktop version | User report, kept separate from observed CLI version |
| Available local renderer | Executable discovery and a successful actual render |
| Source geometry check | Current layout report tied to source files and measured fonts |
| Source visual acceptance | Actual source images, page-by-page review and individual finding resolutions |
| Final-file visual acceptance | Current PPTX hash, original final-render report, actual final images and review |
| Font embedding | Inspection of the actual exported file and font coverage |
| Target-app playback | Actual playback in the named application |

The supplied desktop build `26.1103.60933` (released 9 November 2026, user-reported) is a target environment description. It does not establish installed tools, a model API identifier, image-reading ability, fonts or a successful local test.

No model-specific capability matrix is needed by the current workflow. Model and effort names remain opaque metadata. No model catalog or minimum reasoning strength is required; the same checks apply to all settings.

## Implementation evidence

- `scripts/codex_workflow.py`: serial plan, dependency checks, source/report/image hashes and final-file review gates.
- `scripts/layout_engine.py`: bounded spec-to-PPTD construction using measured fonts.
- `scripts/layout_quality.py`: source layout findings and actual font evidence.
- `scripts/render_pptx.py`: local rendering of an existing PPTX with explicit source provenance.
- `scripts/asset_audit.py`: existing local file inspection only.
- `VALIDATION.md` at the package root: tests actually executed and limitations.

Model judgement, a remote editor's current compatibility and a target application's rendering remain empirical questions. Source checks are not a benchmark of model quality. A successful local conversion is not a guarantee for every application, font or animation.
