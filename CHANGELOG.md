# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/).

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
