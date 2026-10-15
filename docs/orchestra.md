# Orchestra — several Claude Code sessions on one repo, without stepping on each other

*A set of tools in the repo (three scripts, two slash commands, two pages, one section in
CLAUDE.md) and one habit: every session says which part it plays. Nothing here changes how a
teammate who ignores it works.*

## The problem it solves

One checkout, two sessions: both write the same files. One says "commit" while the other has a
half-edited file, and the half lands in the commit or falls out of it; one changes branch and
the other's work is gone; the dev server serves a file that is half of one change and half of
another. So you lock yourself into one session and cannot fix a screen while a bug is chased.

A shared "who is editing what" file does not fix this. Sessions do not watch files — a session
does nothing between prompts — and even perfect coordination leaves one HEAD, one index and one
set of files.

## What it is instead

**One git worktree per task, one session per worktree.** A worktree is a second, complete copy
of the project in its own folder — `.claude/worktrees/<name>/` — on its own branch, inside the
same git. Sessions in different folders cannot touch each other's files. Finished work is
merged into the **base branch** one branch at a time, where git shows any conflict honestly.

Two roles, one lane each:

| Role | Where | May | Never |
|---|---|---|---|
| **conductor** | the main checkout — one chat | open players, merge their branches into the base branch, run the gate, push | write feature code while players are open |
| **player** | a worktree — its own chat tab | edit inside its **lane**, commit on its own branch, run its own dev server on its own port | merge, push, touch files outside the lane |

Every session says which it is in the first line of every reply — the **sign-on**:

```
🎼 Orchestra · player login-form · lane: the sign-in screen · branch login-form
🎼 Orchestra · conductor · dev-alex · 2 players open
```

No sign-on means the session is not on the system. That is the whole check.

**One baton.** Any chat opened on the main folder *could* act as conductor, and two of them
would collide exactly the way two sessions in one checkout do. So the main checkout carries
`.claude/conductor` (gitignored) with one session name in it. A main-folder session whose name
is not in that file signs on as **bystander** — `🎼 Orchestra · bystander · main checkout — the
conductor is <name>; I change nothing here` — and answers questions only. The baton moves only
through `/conductor`: it asks *"do you give this session the conductor role?"*, and writes the
file on a yes. Nothing else writes it.

**One base branch per developer.** Each developer works on a long-lived branch of their own
(`ORCHESTRA_DEVS` in `.orchestra.conf`: `alex=dev-alex sam=dev-sam`). `/conductor`
recognises the developer from the git identity and writes `.orchestra.local` (gitignored) with
`ORCHESTRA_BASE=<that branch>`: on this machine, players branch from it and merge back into it.
The committed `ORCHESTRA_BASE` in `.orchestra.conf` is the shared integration branch — here
`main`, which deploys to staging — and the release path is *developer branch → `main`
(staging) → `prod`*. Nobody's players ever touch `main` directly.

## A day, step by step

1. **Conductor tab, morning:** a chat on the main folder, one word: `/conductor`. It warns
   about loose files (they only block opening players), moves to the developer's branch, offers
   to pull staging into it if `main` has moved, asks for the baton, and on a yes signs on
   with `0 players open` and says what happens next. From here on there are no more slash
   commands to remember — the conductor acts on plain words (the desk, below).
2. **Open a player:** talk the task through in the conductor tab until it is one sentence and
   one lane. Say "ready" (or the conductor offers). It prints two boxes to paste into a **new
   chat tab on the main folder**, in order: first `/player login-form "the sign-in screen"` in
   **auto mode** (the bootstrap runs a dozen commands; auto mode does not stop at each one) —
   ~20 s later the tab signs on; then the task brief in **plan mode** (Shift+Tab), so the player
   shows its plan before touching a file. Repeat per task; one lane each.
3. **Work:** move between tabs. Each works in its own folder, on its own port, and asks its own
   questions.
4. **Check, one at a time:** in the tab that says `walk-ready`, follow its numbered steps on its
   port. The user closes a step by seeing it work — not the session's screenshots.
5. **Commit in that tab.** The commit lands on the player's branch, never on the base branch.
6. **Conductor tab:** "merge login-form". It reports what would land and whether it conflicts,
   merges with `--no-ff`, runs the gate on the merged tree, then offers to remove the worktree.
   One branch at a time. Once the worktree is removed, the conductor says **"close the
   login-form tab now"** — and only then does that tab close (rule in the desk page, "When a
   tab closes").
7. **Evening:** one push of the developer's branch; when it is worth showing, the developer's
   branch goes to `main` (staging), and from there to `prod` by your project’s own deploy rules.
8. **Tomorrow's leftovers:** a player that is not finished is re-entered with the same
   `/player <name>` — the worktree and branch are still there.

Five things to remember: **one lane per tab · no sign-on = not on the system · merge and push
only in the conductor's tab · a player's tab closes only when the conductor says its worktree is
removed · the check and the deploy are serial, the writing is not.**

## The conductor's desk — plain words

Each player keeps four one-line files in its own worktree (`.claude/lane`, `.claude/session`,
`.claude/status`, `.claude/port` — gitignored, never committed), and the conductor reads them.
The procedures are in [docs/orchestra-desk.md](orchestra-desk.md); `/conductor` reads that page
once, and CLAUDE.md sends the conductor back to it whenever it is not in context. What to say:

| Say | What happens |
|---|---|
| "board", "status", "who is waiting" | one row per player — lane, phase and what it is doing, commits waiting to merge, dirty files, whether it is behind the base branch, its port. Under it, only the players that need the user (blocked, walk-ready). |
| "ready", "give me the boxes" | the hand-off for a task talked through here: two paste-boxes for a new tab — the `/player` line (auto mode) and the task brief in English (plan mode) — with a one-line reminder of which mode each goes in. The conductor writes the brief; it writes no feature code. |
| "watch" | arms the desk watcher (a Monitor): one line per player event — phase change, new commit, session name, and ⚠️ when two players touch the same file. |
| "tell login-form: …" | one line to that player's session — information only. Never a task, never a question: those go to the user in the player's tab. Players never message back. |
| "merge login-form" | preview (commits, files, would-it-conflict), pause for the user's yes, `--no-ff` merge, the gate, then offer to remove the worktree. |
| "update login-form", "catch it up" | the base branch merged **into** the player's branch, so a player opened before another one landed sees that work. |
| "pull staging", "catch up with master" | `main` (the other developer's landed work) merged **into** the developer's branch — its own question, never silent; `/conductor` asks it at sign-on when staging is ahead. Open players then need "update". |
| "gate" | the merge-time gate on everything not yet pushed (`.orchestra.conf` rules). |
| "push" | the base branch to origin, after a green gate — its own question, never bundled with a merge. |
| "close login-form", "drop it" | drop a player without merging — shows what is lost, pauses. |

The status file is the contract: a player writes `phase: walk-ready` *before* handing over the
steps, `phase: committed` after the commit, `phase: blocked` with the reason in `doing:` when
it waits on the user. The board is only as true as that file.

## Rules that keep it cheap

- **Lanes do not overlap.** If two tasks touch the same file, the second waits for the first to
  merge. A conflict at merge time is the sign a lane was drawn wrong, not something to fix in
  the merge. The desk watcher warns the moment two players touch one file.
- **One port per folder.** A player's dev server runs on the port in its own `.claude/port`,
  from its own folder; nobody stops anybody else's.
- **Generated, committed artefacts are the conductor's.** A built bundle, a lockfile, a
  generated client: two players regenerating the same file conflict every time. Either stop
  committing it (build at deploy) or let only the conductor regenerate it at merge time.
- **Two green branches can be red together.** Every player runs its own tests, but the
  conductor runs the gate on the merged tree before any push (`orchestra-gate.sh`).
- **What does not parallelise:** the user's checking, the deploy, and the user. Expect writing
  time to shrink, not the day.

## The tools, and what each one does

| Piece | What it does |
|---|---|
| `.orchestra.conf` | the per-repo values: shared base branch, developers and their branches, dirs to clone, files to copy, the credential keys each developer needs (`ORCHESTRA_ENV_KEYS` — names only, checked at sign-on), first port, gate rules. Committed. |
| `.orchestra.local` | one line, `ORCHESTRA_BASE=<this developer's branch>`, written by `/conductor`. Gitignored — it is what makes the same repo serve two developers. |
| `scripts/orchestra/orchestra-conf.sh` | loads `.orchestra.conf`, then `.orchestra.local` on top: `ORCHESTRA_BASE` is the developer's branch, `ORCHESTRA_SHARED_BASE` the committed one (`main`). Every other script and both commands read them through it. |
| `scripts/orchestra/worktree-bootstrap.sh <name>` | creates `.claude/worktrees/<name>` on branch `<name>` **from the main checkout's HEAD**, refuses if the main checkout has loose files, clones the gitignored dirs (APFS clonefile, seconds) and copies the env files. Idempotent. |
| `scripts/orchestra/orchestra-desk.py` | the desk watcher (above). |
| `scripts/orchestra/orchestra-gate.sh [range]` | the merge-time gate: runs each `.orchestra.conf` rule whose path prefix the range touched. |
| `/conductor` | the only command a developer types: recognise the developer, move to their branch, write `.orchestra.local`, ask for the baton, sign on, read the desk page. |
| `/player <name> "<lane>"` | pasted from the conductor's box, never typed from memory: runs the bootstrap, enters the worktree, writes the four files, signs on. A bare `/player` inside a worktree refreshes them. |
| `.claude/settings.json` → `"worktree": {"baseRef": "head"}` | Claude Code's own `claude -w` / `EnterWorktree` branch from the current HEAD, not from the remote default branch. |

## Three things measured (measured on a real repo, Sep 2026), so nobody re-learns them

1. **Without the `baseRef` setting, a worktree branched from the 2022 "Initial commit", 4473
   commits behind the integration branch, with no CLAUDE.md in it.** The remote default branch
   can be a fossil. The setting is not optional.
2. **Memory follows the folder a session was opened on.** A session opened on the main folder
   and moved into a worktree keeps the project memory; a session opened *inside* a subfolder
   gets a separate one. So a player is always a tab opened on the main folder that then runs
   `/player`. Terminal fallback: `claude -w <name>` from the main folder.
3. **A fresh worktree has none of the gitignored files** — no `node_modules`, no `.venv`, no
   `.env`. Without the bootstrap, every new session starts with ten minutes of installing.

## Where the rule lives

- **This page** — the tools and the flow. Shared, in the repo.
- **[docs/orchestra-desk.md](orchestra-desk.md)** — what the conductor does on each plain-word
  request, step by step.
- **CLAUDE.md, section "Orchestra"** — the mandate every session reads on every prompt: sign on,
  stay in the lane, players never merge or push, conductor writes no feature code.
