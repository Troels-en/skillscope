# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/).

## [0.2.3] - 2026-05-18

### Changed
- The plugin-skill issue list is now collapsed (a muted `<details>`), so the
  report's body matches its verdict instead of shouting un-editable findings.

## [0.2.2] - 2026-05-18

### Changed
- Recommended actions now separate **your** skills (personal + project, which
  you can edit) from **plugin** skills (which you cannot — they are listed for
  reference only). The verdict counts editable actions only, so plugin noise
  no longer inflates it.
- Broad-trigger detection no longer flags bare `any`/`every` — they
  false-positived on scoped phrases like "any edit under data/migrations/".
  Only strong signals (`must use`, `before any`, `always`, …) flag now.
- Collision threshold raised 0.18 → 0.25; below that it is shared vocabulary,
  not a real trigger conflict.

## [0.2.1] - 2026-05-18

### Changed
- Dark, dev-tool-grade report theme: refined typography, monospace numerals,
  a single accent colour, and a brand header. No gradients, no emoji.

## [0.2.0] - 2026-05-18

### Added
- Ranked **Recommended actions** block at the top of the report — turns the
  findings into a prioritized to-do list with a one-line verdict.
- Terminal verdict: running the CLI now prints the verdict and top actions to
  stdout, so you get the result without opening the HTML.
- `verdict` and `actions` fields in the `--json` output.

## [0.1.0] - 2026-05-18

### Added
- Initial release.
- Scans personal, project, and plugin `SKILL.md` files.
- Self-contained HTML report: per-skill triggers, body size, invocation mode,
  and a plain-language note.
- Trigger-collision detection via description-overlap scoring.
- Warnings for oversized bodies, over-broad triggers, and over-cap descriptions.
- Description-budget estimate against the context window.
- `--json` export and `--open` browser launch.
- Test suite (standard library `unittest`) and CI on Python 3.9 + 3.12.
