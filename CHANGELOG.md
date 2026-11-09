# Changelog

This file lists changes in this derivative. The history of the upstream project is in its own repository.

## 1.6.0-claude.1 — 2026-11-09

### Changed

- The project is now called **OpenDeck** everywhere. Before this release the repository was named OpenDeck while the files still used the earlier name `open-kimi-ppt`.
- Package name and command: `open-kimi-ppt-skill` is now `opendeck` (`bin/opendeck.js`).
- Skill name and folder: `open-kimi-ppt` is now `opendeck` (`skills/opendeck/`). The installer writes to a folder named `opendeck`.
- Environment variable: `OPEN_KIMI_PPT_PYTHON` is now `OPENDECK_PYTHON`.

### Upgrading

- A skill installed under the old folder name `open-kimi-ppt` is not removed or renamed for you. Install again, then delete the old folder yourself.
- If you set `OPEN_KIMI_PPT_PYTHON`, set `OPENDECK_PYTHON` instead.
- PPTD projects you already made are unaffected.

## 1.5.0-claude.1 — 2026-11-09

### Removed

- The remote rendering and PPTX export path: `export_images.py`, `export_pptx.py`, `export_host.html`, their tests, the `websocket-client` dependency, and the `--allow-remote-renderer` flag. These sent the deck to a third-party web editor in an iframe. The editor now produces PPTX on its own servers, so the path could not be repaired inside this project.
- The local browser editor (`serve` command, `editor/`, `lib/`), which embedded the same remote editor.
- The `render`, `visual_review`, `export` and `final_output_review` workflow tasks. The task chain now ends at `layout`.
- Upstream release notes, validation logs and internal packaging notes.

### Changed

- This package no longer produces PPTX or page images. Its output is a PPTD project plus structural and measured-layout checks.
- Project reading moved to `pptd_project.py`.
- `status` now reports `visual_review: not_performed` and `pptx_export: not_available`; `complete` is true only when all tasks are done and no layout finding is open.
- Fixed two tests that failed on macOS because they compared `/var/...` with `/private/var/...`.

## 1.4.0-codex.1 — 2026-11-09

- Use one active host model at its unchanged reasoning setting for every task; no model catalog is required.
- Remove the dedicated image-generation integration; keep local asset inspection.
- Add measured template construction, font and glyph checks, overflow and collision checks, and stable review findings.
