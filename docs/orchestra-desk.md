# Orchestra — the conductor's desk, step by step

*What the conductor does when the user says a plain word. `/conductor` reads this page once;
CLAUDE.md sends the conductor back here whenever it is not in context. The system itself is
[docs/orchestra.md](orchestra.md).*

**Who may act.** Only a session in the **main checkout** whose name is in `.claude/conductor`
(compare with the first line of `ListAgents`). Anyone else is a bystander and answers questions
only. A player has no desk. BASE below is `ORCHESTRA_BASE` as
`scripts/orchestra/orchestra-conf.sh` resolves it (`.orchestra.local` over `.orchestra.conf`) —
on a developer's machine that is their own branch, e.g. `dev-alex`.

**Tone.** Plain language, short output, the user's language in chat, English in every file.
Every reply starts with `🎼 Orchestra · conductor · <branch> · N players open`. The conductor
writes no feature code while players are open; it discusses, briefs, merges, gates, pushes.
Every commit, merge, push, worktree removal and branch deletion is its own question — never
bundled, never implied by an earlier yes.

## "board" · "status" · "who is waiting" — read-only

For every worktree under `.claude/worktrees/` (`git worktree list --porcelain`), read its files
— `.claude/lane`, `.claude/session`, `.claude/status`, `.claude/port` — and its git state, then
print one row per player:

| player | lane | phase · doing (updated) | branch | ahead of BASE | dirty | port | session |
|---|---|---|---|---|---|---|---|

- *ahead*: `git log --oneline BASE..<name> | wc -l` — commits waiting to be merged.
- *dirty*: `git -C <worktree> status --porcelain | wc -l` — uncommitted files in that player.
- *behind*: also say if BASE has commits the player lacks (`git log --oneline <name>..BASE |
  wc -l`) — that is what "update" fixes.
- Missing `.claude/status` or `.claude/session` ⇒ tell the user to type a bare `/player` in
  that tab to refresh it.

Under the table, one line per player that needs the user: `blocked` phases with their `doing`,
and `walk-ready` ones. Nothing else.

## "ready" · "give me the boxes" — hand a talked-through task to a new tab

Only after the task has been discussed in this tab and is down to **one sentence and one
lane** (which screen / endpoint / module; which files it may touch). If it is not yet, say
what is still open and stop. Offer this yourself the moment the task is that clear — the user
should not have to ask. Pick a short kebab-case name with the user. Check that
`.claude/worktrees/<name>` does not already exist and that the main checkout is clean and on
BASE (a player branches from HEAD); if not, say so — the user fixes that first.

Then print, in the user's language and in plain words, exactly two paste-boxes:

1. **Box 1 — new chat tab on the main folder, AUTO mode (Shift+Tab until it says auto),
   paste as the first message:** a fenced block containing only
   `/player <name> "<lane>"`. Say why auto: the bootstrap runs a dozen commands and auto mode
   does not stop at each one. Say what to expect: ~20 s, then the tab signs on with 🎼.
2. **Box 2 — same tab, once it has signed on, switch to PLAN mode (Shift+Tab until it says
   plan), paste:** a fenced block with the task brief, **in English**, ≤ 15 lines: goal (what
   the user will see work), the lane (files it may touch; everything else is off-limits —
   stop and say so), done-when (the numbered walkthrough it must end with), and any decision
   already made here that the player must not re-open. Say why plan: the player shows its plan
   before touching a file, and the user approves it there.

Nothing else in the reply. The conductor writes the brief and never the code; the modes are
the user's to switch — remind, do not claim to have set them.

## "watch" — the conductor's ears (armed at sign-on, re-armed on every expiry)

A session does nothing between prompts, so the conductor hears players only through the desk
watcher: a Monitor on `python3 -u scripts/orchestra/orchestra-desk.py 2>&1` (description
"Orchestra desk: player status/commit changes", the longest timeout). `/conductor` arms it;
it expires every 30 minutes at most and a closed session loses it — **re-arm on every expiry
notice and after any restart, without being asked.** Each event is one line: a player's phase
or `doing` changed, it committed, it appeared or vanished, or ⚠️ two players touch one file.
Tell the user only what changes what they do next (walk-ready, blocked, a conflict warning).
On "watch", also say which players are open and what each waits for (their `.claude/status`).

## "tell <name>: …" — one line to one player

Read `.claude/session` in that player's worktree and call `SendMessage` to that name with the
notice, prefixed `🎼 conductor:`. Notices are **information, never tasks** — "docs moved,
re-read docs/README.md before writing any doc", "BASE merged into your branch", "give up port
8101". If the notice would be a task or a question, refuse and say: that goes to the user, in
the player's tab. If the file is missing, say the player has to run a bare `/player` first.

## "merge <name>" — one player's branch into BASE

1. Sign on, then check: main checkout clean (`git status --porcelain`) and on BASE; the
   player's `.claude/status` phase is `committed` (or the user says it is done); the player's
   worktree has no dirty files. If any fails, STOP and say which.
2. Preview: `git log --oneline BASE..<name>` and `git diff --stat BASE...<name>`. Dry-run the
   conflict question: `git merge-tree --write-tree --name-only BASE <name>` (git ≥ 2.38) — its
   `CONFLICT` lines name the files. (The old three-argument `merge-tree` prefixes conflict
   markers with `+`, so grepping it for `^<<<<<<<` reports "clean" when it is not.) — say "clean" or name the files that would conflict.
3. **PAUSE.** *"<n> commits, <m> files, <clean | conflicts in …>. Merge into BASE?"* Wait for
   the user's explicit yes — every merge is its own question.
4. `git merge --no-ff <name> -m "Merge player <name>: <lane>"`. On conflict, resolve by hand
   with the user watching, never with `-X theirs`/`-X ours`.
5. Run the merge-time gate: `scripts/orchestra/orchestra-gate.sh` (what the merge brought in).
   Red ⇒ say which rule failed and stop; the fix is a player's, not the conductor's.
6. Report the merge commit. Before offering the removal, compare the key NAMES in the
   worktree's `.env` with the main checkout's (`grep -o '^[A-Z_]*='`, never values). A key a
   player added lives only in its worktree and dies with it — name each one and have the user
   copy it into the main `.env` first. Then ask: *"Remove the worktree and branch?"* — on yes:
   `git worktree remove .claude/worktrees/<name> && git branch -d <name>`.
   Push is a separate question, asked only if the user raises it.
7. **Tell the user to close that tab — explicitly, in its own line, every time:**
   *"Close the <name> tab now (session <session name>)."* Say it right after the removal, not
   before: until then the tab holds the player's conversation and is the only place to fix its
   work. After the removal it must go — a tab left open can still write into the removed
   folder (a stray `.env` reappeared once), and the desk watcher then reports a ghost player.
   If a ghost folder appears anyway, check its key names against the main `.env`, then delete
   it on the user's yes.

### When a tab closes — the whole rule

| The player is… | Its tab |
|---|---|
| working, blocked, walk-ready, walked | **keep open** — the work and its questions live there |
| committed, not yet merged | keep open — a red gate or a conflict is fixed there |
| merged, worktree **not** removed | keep open until the removal |
| merged **and** worktree removed | **close now** — the conductor says so by name |
| dropped with "close <name>" | close now, after the removal |

## Version numbers and the changelog — settled at merge, not in the player

Players that run in parallel all branch from the same BASE, so each one bumps the version to
the same "next" number and writes its changelog entry in the same place. Learned the hard way:
three players all committed the same version, which would have released twice under one number
and suppressed the release announcement. Rule: the conductor assigns the number **in merge
order**. Before merging the second and later players, "update" them first; the conflict in the
version file and the changelog is then resolved by giving that player the next free number and
its own changelog section **above** the previous one — never by picking a side. Say the new
number in the "update" notice to the player.

## "update <name>" · "catch it up" — one player catches up with BASE

The player's branch gets BASE merged **into it** (never the other way):
`git -C .claude/worktrees/<name> merge BASE --no-edit`. Dirty files in the worktree are fine
as long as the merge does not touch them — git refuses by itself with *"local changes would be
overwritten"*; if it does, stop and say which files. On conflict, stop and report. Then tell
that player: *"BASE merged into your branch — re-read anything you rely on."*

## "pull staging" · "catch up with master" — BASE catches up with the shared branch

STAGING is `ORCHESTRA_SHARED_BASE` (the committed base in `.orchestra.conf`, `main`); BASE
is this developer's branch. `git fetch origin STAGING:STAGING` first (local staging
fast-forwards to origin; if it refuses because local is ahead with unpushed work, local is
the newer one — fine), then `git log --oneline BASE..STAGING` — empty ⇒ *"in step with
staging"*, stop. Otherwise list what would come in (author · subject) and **PAUSE**: *"N
commits — pull into BASE?"* On yes: `git merge STAGING --no-edit` into BASE, say what came in. On a conflict, name the files and stop with the merge
paused — resolve by hand with the user, never `-X ours` / `-X theirs`. If players are open,
say which ones now need "update" (they were opened on the older BASE). `/conductor` asks this
same question at sign-on when staging is ahead.

## "gate" — the merged tree, before a push

`scripts/orchestra/orchestra-gate.sh origin/BASE..HEAD` — everything not yet pushed, through
the rules in `.orchestra.conf`. Report green/red per rule. Never push from here.

## "push" — BASE to origin

Only after a green gate, and only as its own question: *"push <n> commits on BASE to origin?"*
On yes, `git push origin BASE`. Say what went live where (BASE deploys nowhere by itself; the
release path is developer branch → `main` (staging) → `prod`, by your project’s own deploy rules in
CLAUDE.md — that walk is a separate conversation).

## "close <name>" · "drop it" — drop a player without merging

Only on the user's explicit word. Show what would be lost (`git log --oneline BASE..<name>`,
dirty files), PAUSE, then `git worktree remove --force` and `git branch -D`.

## Handing the baton on

The baton moves only through `/conductor` in the tab that should hold it; this session never
writes `.claude/conductor` on its own. If the user says another tab is the conductor now, say
so in the sign-on from then on (`bystander`) and change nothing.
