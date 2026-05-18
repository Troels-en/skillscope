# Contributing

Thanks for considering a contribution.

## Ground rules

- **Zero runtime dependencies.** skillscope runs on a plain Python 3.9+ install.
  Do not add third-party imports to `skillscope.py`.
- **Keep it one file.** The tool is intentionally a single script. Resist
  splitting it into a package unless it genuinely outgrows that.
- Match the existing style: 4-space indent, `snake_case`, minimal comments.

## Before opening a pull request

```bash
python -m unittest discover -s tests -v   # all tests pass
python skillscope.py --out /tmp/r.html    # CLI still runs
```

Add a test for any behaviour you change or add. CI runs the same two commands
on Python 3.9 and 3.12.

## Reporting bugs

Open an issue with the skill frontmatter that triggered it (redact anything
private) and what you expected the report to show.
