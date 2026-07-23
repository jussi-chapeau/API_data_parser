# Architecture

## What this project is

[Same one-paragraph description as the root `CLAUDE.md`'s "What this project is" — keep the two in
sync whenever either changes.]

## Module map

The authoritative list of top-level modules/packages and what each owns. Update this table in the
same change that adds, renames, or removes a module — this is the first thing an agent reads to
understand where something lives.

| Module / directory | Owns |
|---|---|
| `[path/to/module]` | [what it's responsible for, in one line] |
| `[path/to/module]` | [what it's responsible for, in one line] |

## Import / dependency rules

[Any rules that keep the codebase importable/runnable in every environment it needs to run in — e.g.
"heavy dependencies (torch, native bindings, cloud SDKs) must be imported inside functions, never at
module load time, so this module still imports cleanly with no GPU / no network / no credentials."
Delete if not applicable.]

## Dev/runtime split

[If the project has a non-trivial split between where code is developed and where it runs — see the
root `CLAUDE.md`'s "Dev / runtime architecture" section — restate the concrete details here: what
runs where, what's disposable vs. persistent, what a fresh environment does on startup. Delete this
section if there's no such split.]
