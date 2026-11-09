# Current-source snapshot

This distribution includes `source-snapshot.bundle`, a Git bundle containing one clean snapshot of the current code and textual documentation. It contains no superseded integration history or removal patch.

To obtain a Git checkout in a new directory:

```bash
git clone --branch main source-snapshot.bundle /absolute/path/to/new-checkout
```

The bundle has been pushed to an isolated local Git mirror and verified before packaging. This does not publish the adaptation to the upstream repository or npm.

The full-source ZIP also preserves original large example presentations and documentation screenshots. These historical assets are excluded from the smaller code ZIP and its Git snapshot. Some original example layouts have known visual defects; consult `VALIDATION.md`. They are reference material, not acceptance samples for the new builder.

Use `CODEX_START.md` and the bundled four-page specification for current usage. `RELEASE.json` records the snapshot commit and distribution scope. The versioned code, scripts, references and tests are the complete modification; no account installation is implied.
