---
description: "Orchestra — make this tab a player: its own worktree and branch off the base branch, one lane, sign-on line"
argument-hint: "<name> \"<lane>\"   — e.g. login-form \"the sign-in screen\"; bare /player inside a worktree refreshes it"
---

Make **this session** an Orchestra player. The system is
[docs/orchestra.md](../../docs/orchestra.md) — read it before you act. Plain language, short
output. The base branch is `ORCHESTRA_BASE` as `scripts/orchestra/orchestra-conf.sh` resolves
it (`.orchestra.local` — this developer's own branch, written by `/conductor` — over
`.orchestra.conf`). This line is normally pasted from the conductor's box; the task brief
follows in the same tab, in plan mode.

**Read `$ARGUMENTS` yourself — do not shell-split it.** It holds a name and, in quotes, a lane:
`login-form "the sign-in screen"`. The name becomes the worktree folder and the branch; the lane
is the one area this player may edit. Bare `/player` is allowed only when this tab is already
inside a worktree (step 1).

Do this in order:

1. **Where am I?** `git worktree list --porcelain | head -1` names the main checkout. If the
   current directory is already under `.claude/worktrees/`, this tab is already a player: skip
   to step 5 to refresh its files, then sign on (step 6). Do not create anything.
2. **Is the main checkout clean?** `git status --porcelain` there. If anything is uncommitted or
   untracked, STOP and list it: *"the conductor has loose files — a new player would not see
   them. Commit or stash them in the conductor's tab, then run /player again."*
3. **Create and furnish the worktree** (one script does both, in seconds):
   ```bash
   scripts/orchestra/worktree-bootstrap.sh "<name>"
   ```
   It prints the worktree path on its last line. It refuses if the main checkout is dirty. If
   the worktree already exists it re-enters it and tops up missing files — that is how a
   half-finished player is resumed the next day.
4. **Enter it:** call `EnterWorktree` with that path. Then prove it: `pwd` is under
   `.claude/worktrees/<name>` and `git branch --show-current` is `<name>`.
5. **Write the four files the conductor reads** (inside the worktree; `.claude/*` is
   gitignored there, so they never reach a commit):
   - `.claude/lane` — the lane, one line. Keep the existing one when refreshing.
   - `.claude/session` — this session's name, so the conductor can send it a notice. Call
     `ListAgents`; its first line says *"This session is <name> [......]"* — write the
     `<name>` part, one line.
   - `.claude/status` — three lines, and **you keep them current at every phase change**:
     ```
     phase: planning
     doing: <one sentence, what you are on right now>
     updated: <YYYY-MM-DD HH:MM>
     ```
     Phases, in order: `planning` → `building` → `gate` (tests/lint running) → `walk-ready`
     (the change runs and numbered steps are handed over) → `walked` (the user checked it) →
     `committed` → `blocked` (waiting on the user; say what for in `doing`). The conductor's
     board is only as true as this file — update it *before* you tell the user, not after.
   - `.claude/port` — **this player's dev-server port**, one line. Keep the existing one when
     refreshing. Otherwise the first port from `ORCHESTRA_PORT_BASE` up that no other player has
     claimed and nothing is listening on:
     ```bash
     . scripts/orchestra/orchestra-conf.sh
     taken=$(find "$ORCHESTRA_MAIN/.claude/worktrees" -maxdepth 3 -path '*/.claude/port' -exec cat {} + 2>/dev/null)
     p=$ORCHESTRA_PORT_BASE; while grep -qx "$p" <<<"$taken" || lsof -nP -iTCP:$p -sTCP:LISTEN -t >/dev/null; do p=$((p+1)); done
     echo $p > .claude/port
     ```
     Whatever this player runs to show its work (the API, the dev server, a preview) runs on
     that port, from this folder. Two players on one port would serve each other's code.
6. **Sign on** — the first line of your reply, exactly this shape:
   `🎼 Orchestra · player <name> · lane: <lane> · branch <name>`
   then, on the second line: `port <port>`, then the lane rule in one sentence: *"I edit only
   inside <lane>; I never merge or push; anything I run, I run from this folder on my port."*
   Then ask what we are doing.

From here on, every reply in this tab starts with that sign-on line. Before any commit, say
it again and ask — the commit lands on branch `<name>`, never on the base branch. If a task
needs a file outside the lane, say so and stop; that is the conductor's call.

**Notices.** The conductor may send this session a one-line notice ("docs moved — re-read
docs/README.md before writing any doc", "base merged into your branch", "give up port 8101").
Act on it, say so in your next reply, and never message the conductor back — every question
goes to the user, in this tab.

This command creates a local worktree and branch and copies gitignored files. It pushes
nothing and never touches the base branch.
