# docs/ — [PROJECT NAME] documentation

Structured reference for the project. Each file owns **one topic** and stays under 250 lines (repo
rule — see the root `CLAUDE.md`). These docs are the **source of truth for architecture** — keep them
in sync with the code in the same change (the docs-first rule).

| Doc | What's in it |
|-----|--------------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | The module map, import rules, and the dev/runtime split (if any). |
| [GOTCHAS.md](GOTCHAS.md) | Hard-won lessons that cost real time — do not re-derive. |
| [DECISIONS.md](DECISIONS.md) | Chronological log of architecture/design decisions and why. |

Add one row per topic as the project grows — e.g. one file per major subsystem, one per external
integration, one per deploy target. Keep each one narrow: one file, one concern. When a topic doc
would exceed ~250 lines, split it along a clean boundary and add a row for the new file.

Superseded planning docs (if any) live in [archive/](archive/) — history only, do not extend from
there without a reason.

## The one-line summary

[One or two sentences: what this system does, end to end, in plain language.]
