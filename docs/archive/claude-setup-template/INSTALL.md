# Installing this template in a new project

This package is the generic version of a documentation/instruction setup that's worked well in
practice: one root `CLAUDE.md` for behavior rules, a `docs/` folder as the architecture source of
truth, and a `CHANGELOG.md` tied to deploys. Delete this file once you've filled in the rest.

## 1. Copy the files

Copy everything except this `INSTALL.md` into the root of the new project:

```
your-project/
├── CLAUDE.md
├── CHANGELOG.md
├── .claude/
│   └── settings.local.json
└── docs/
    ├── README.md
    ├── ARCHITECTURE.md
    ├── GOTCHAS.md
    └── DECISIONS.md
```

## 2. Fill in `CLAUDE.md` first

This is the only file Claude Code loads automatically every session — everything else is reached
*through* it. In order of importance:

1. **"What this project is"** — the one paragraph that matters most. Write it last if it helps, but
   make it precise: what the system does, for whom, and the current scoped milestone (not the whole
   long-term vision).
2. **Persona line** ("You are acting as an expert ___") — pick the domain that matches the actual
   work (backend engineer, data scientist, mobile engineer, etc.). This shapes tone and what gets
   flagged as a concern.
3. **Architecture** section + module map — even a rough first pass is fine; it gets refined as the
   codebase grows.
4. Delete **"Dev / runtime architecture"** entirely if the project doesn't have a meaningful split
   between where code is written and where it runs. Keep and fill it in if it does (e.g. local dev +
   a remote GPU box, or a CI-only integration environment).
5. Leave **"Hard-won lessons"** and **"Future direction"** mostly empty at the start — they're meant
   to accumulate, not be front-loaded with speculation.
6. **"How to work in this repo"** is written generic on purpose — read it once, adjust wording to
   taste, but the underlying rules (docs-first, ask-before-commit, changelog-per-deploy, file-size
   cap, one-fix-at-a-time, verify-don't-claim, no-secrets-in-files) are the actual value of this
   template. Don't cut them without a reason.

## 3. Fill in `docs/`

- `docs/README.md` — the index. Add one row per topic doc as you create them; keep the "one-line
  summary" current.
- `docs/ARCHITECTURE.md` — mirror of `CLAUDE.md`'s architecture section, but longer-form; this is
  what actually gets kept in sync with the code over time.
- `docs/GOTCHAS.md` and `docs/DECISIONS.md` — leave empty. Add entries the first time something
  non-obvious actually happens; don't pre-populate with guesses.
- Add new topic docs as the project grows (one file per subsystem/integration/deploy target), and
  list each one in `docs/README.md`.

## 4. `CHANGELOG.md`

Delete the placeholder `YYYY-MM-DD` entry once you have a first real deploy to log. One entry per
deploy going forward — that's the whole discipline.

## 5. `.claude/settings.local.json`

Starts with empty permissions. Add specific allowlist entries as needed (e.g.
`"WebFetch(domain:example.com)"`) rather than broad grants — this file is usually gitignored per-user,
so it's fine to leave mostly empty and let each person configure their own.

## What this template deliberately leaves out

- No user-level (`~/.claude/CLAUDE.md`) instructions — everything is repo-scoped so it travels with
  the project and works the same for anyone who clones it.
- No skills or subagent definitions — add `.claude/skills/` or `.claude/agents/` only if/when a
  workflow is repetitive enough to be worth codifying.
- No hooks — add `.claude/settings.json` hooks only for things that must never be skipped (e.g. a
  linter that has to run before every commit).
