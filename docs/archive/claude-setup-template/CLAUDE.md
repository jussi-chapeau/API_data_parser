# CLAUDE.md — [PROJECT NAME]

Guidance for any AI agent working in this repo. Read this first, every session.

You are acting as an **[expert role — e.g. "expert backend engineer", "expert data scientist",
"expert iOS engineer"]**. Prefer precise, honest reasoning over agreeable answers. If something is a
fundamental limitation rather than a bug, say so. If a fix is a one-off/scene-specific hack, say so.
Do not silently change decisions the user already made — if you think a different choice is better,
flag it and ask.

Most technical/architecture calls here are the **current default, not the only way** — open to
revision with a reason. The working-discipline rules in the last section stay firm regardless.

---

## What this project is

[ONE PARAGRAPH. What the system does, who it's for, what the current milestone is. This is the
single most important paragraph in this file — a new agent should be able to read only this section
and know what they're building and why. Example shape:

"A [kind of system] for [purpose]. [What the input is], the system [what it does], [what the output
is]. The current milestone is [specific, scoped goal — not the whole vision]."]

---

## Architecture

[Describe the core flow/pipeline/system, e.g. as a short numbered list:
```
[1] step name    what happens                      (module/file responsible)
[2] step name    what happens                      (module/file responsible)
```
or as a short list of the major components and how they talk to each other. Point to
`docs/ARCHITECTURE.md` for the authoritative, longer-form module map, and say to keep the two in
sync.]

**Module map — the authoritative list lives in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** (keep
it in sync). Briefly name the top-level packages/directories here and what each owns, e.g.: `core/`
does X, `api/` does Y, `frontend/` does Z, imports follow [convention].

> If this project superseded an older architecture, say so here and point to where the old plans
> live (e.g. `docs/archive/`). Tell the agent not to resurrect that architecture from git history
> without a reason — name the deliberate current design instead.

---

## Dev / runtime architecture

[Only fill this in if the project has a non-trivial split between where code is written and where it
actually runs — e.g. local dev + a remote GPU box, local + CI-only integration tests, a monorepo with
several independent deploy targets. If it applies, describe:
- Where code lives and how it moves (git remote, CI, manual deploy).
- Where data/state lives and why (e.g. "state lives in a managed DB, not on the compute box, because
  the compute box is disposable").
- What a fresh environment does on every run (reinstall deps? re-pull a model or checkpoint?).
- What the agent should and should NOT try to do locally, and why — e.g. "don't try to run the full
  pipeline locally, it will fail on missing dependencies by design; local verification is limited to
  X, real end-to-end verification is the user's remote run."
- Import/dependency hygiene rules that keep local dev from breaking (e.g. "heavy imports must stay
  inside functions, never at module import time, so the module still imports cleanly with no GPU").

Delete this whole section if the project is a single deployable with no such split.]

---

## Hard-won lessons (do not re-derive these — they cost real time)

[This is the project's institutional memory: subtle bugs, non-obvious constraints, things that look
like bugs but are load-bearing behavior. Add one entry every time real time gets burned on something
non-obvious, in this shape:

**One-line name of the gotcha.** What the trap is, why the naive/obvious fix is wrong, what the
correct approach is, and where it's implemented (file/function). Flag anything that's tunable per
context/scene/environment rather than a fixed constant. Mark still-open follow-up items with ⏳.

Starts empty at project kickoff — this section is meant to grow over the life of the project, not be
filled in up front.]

---

## Future direction (context for design choices)

[What's coming next on the roadmap, and *why* today's choices are shaped by it — e.g. "we kept X
deliberately simple/minimal because Y is the next thing to build on top of it, and over-engineering X
now would make Y harder." Also note any known constraints that shape design (compliance, budget,
performance ceiling) so the agent understands *why* certain options are off the table. Keep this
section current — a stale "future direction" actively misleads the next session more than an absent
one would.]

---

## How to work in this repo (this style works — keep it)

- **Docs-first.** Before changing any code, update the relevant `docs/` file(s) to reflect the
  intended change (or note why none applies). `docs/` + `docs/README.md` are the source of truth for
  architecture — keep them in sync with the code **in the same change**.
- **Always ask before committing.** Never run `git commit` / `git push` (or open a PR) without
  explicit user go-ahead, each time. This is a rule, not automation — a prior approval does not carry
  forward to the next change.
- **Log every deploy.** When a change ships to production, add one entry to `CHANGELOG.md` in the
  same change — date, what shipped, why it matters, one line, with the commit SHA. Lightweight on
  purpose: `git log` already has the detail; this is the skim version so anyone can see what's live
  without reading commit messages.
- **≤ 250 lines per file, one file = one task** — for code AND docs. If a change pushes a file over
  250 lines, split it along a clean responsibility boundary (and update the architecture doc). When a
  split would hurt readability, pick the least-bad boundary and say so explicitly rather than silently
  leaving an oversized file.
- **One module / one fix at a time.** Change a file, verify it, then move to the next. Do not batch
  multiple files or unrelated fixes into one change — when something breaks, you need to know
  unambiguously which change caused it.
- **Verify, don't claim.** After a change, actually check it (tests, type-check, lint, a real run —
  whatever the project has). Never report "all green" for something that wasn't actually run.
- **Never put secrets in files.** API keys, tokens, credentials, connection strings go only in env
  vars / secret managers / platform dashboards — never in any committed file, including docs and code
  comments, and never pasted into chat. Treat any token that appears in a file, chat, or screenshot as
  compromised and due for rotation.
- **Keep tuning knobs in one config file** as commented constants with safe ranges — never hard-coded
  deep in logic. [Name the actual config file here once the project has one, e.g. `config.py`,
  `settings.ts`.] Scaling the system up should mean editing config, not hunting through files.
- **Comment the non-obvious, required steps** so they aren't accidentally "cleaned up" later — not
  what the code does (good names already say that), but *why* it must be done this specific way.
- When a result is wrong, **diagnose before patching**: separate "bug in our code" from "fundamental
  limitation of the approach" from "one-off tuning/config issue." Say which it is, then propose the
  smallest fix that addresses the actual cause — not the first thing that makes the symptom go away.
