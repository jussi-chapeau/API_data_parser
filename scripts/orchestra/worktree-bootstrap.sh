#!/usr/bin/env bash
# Orchestra — make a fresh git worktree runnable in seconds.
#
# Wired as Claude Code's `WorktreeCreate` hook (optional) and run by `/player`:
#
#     scripts/orchestra/worktree-bootstrap.sh <name>
#
# What it does, in order:
#   1. refuses if the main checkout has uncommitted or untracked files — a worktree branches
#      from HEAD, and loose edits never reach it, so the player would silently miss them
#   2. creates .claude/worktrees/<name> on branch <name> from the main checkout's HEAD
#      (never from the remote default branch — that can be years behind)
#   3. clones the gitignored directories in ORCHESTRA_CLONE_DIRS with APFS clonefile (`cp -c`),
#      seconds instead of minutes; plain copy where clonefile is unavailable
#   4. copies the gitignored files in ORCHESTRA_COPY_FILES
#
# Re-running on an existing worktree only tops up missing files (idempotent). As a hook it
# reads `{"name": ...}` on stdin and must print exactly one line on stdout: the worktree path.
# Everything else goes to stderr.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/orchestra-conf.sh"

say() { printf 'orchestra: %s\n' "$*" >&2; }
die() { say "$*"; exit 1; }

NAME="${1:-}"
if [ -z "$NAME" ] && [ ! -t 0 ]; then
  NAME=$(jq -r '.name // empty' 2>/dev/null || true)
fi
[ -n "$NAME" ] || die "usage: worktree-bootstrap.sh <name>  (or WorktreeCreate hook stdin {\"name\": ...})"
[[ "$NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "bad worktree name '$NAME' — letters, digits, . _ - only"

MAIN="$ORCHESTRA_MAIN"
DIR="$MAIN/.claude/worktrees/$NAME"

if git -C "$MAIN" worktree list --porcelain | grep -qx "worktree $DIR"; then
  say "worktree '$NAME' already exists — re-entering, topping up missing files"
else
  LOOSE=$(git -C "$MAIN" status --porcelain)
  if [ -n "$LOOSE" ]; then
    say "the main checkout has uncommitted or untracked files; a new worktree would not see them:"
    printf '%s\n' "$LOOSE" | sed 's/^/orchestra:     /' >&2
    die "commit (or stash) them in the conductor first, then run /player again"
  fi
  base=$(git -C "$MAIN" rev-parse --abbrev-ref HEAD)
  [ "$base" = "$ORCHESTRA_BASE" ] || say "note: the main checkout is on '$base', not on ORCHESTRA_BASE '$ORCHESTRA_BASE' — branching from HEAD anyway"
  mkdir -p "$MAIN/.claude/worktrees"
  if git -C "$MAIN" show-ref --verify --quiet "refs/heads/$NAME"; then
    say "branch '$NAME' already exists — checking it out (not re-branching from HEAD)"
    git -C "$MAIN" worktree add "$DIR" "$NAME" >&2
  else
    git -C "$MAIN" worktree add "$DIR" -b "$NAME" HEAD >&2
  fi
  say "created $DIR on branch '$NAME' from $(git -C "$MAIN" rev-parse --short HEAD) ($base)"
fi

clone_dir() {  # clone_dir <relative path>
  local rel="$1" src="$MAIN/$1" dst="$DIR/$1"
  if [ ! -d "$src" ]; then say "skip $rel (not in main checkout)"; return; fi
  if [ -d "$dst" ]; then say "keep $rel (already there)"; return; fi
  mkdir -p "$(dirname "$dst")"
  if cp -Rc "$src" "$dst" 2>/dev/null; then say "cloned $rel"; else say "clonefile unavailable — plain copy of $rel (slower)"; cp -R "$src" "$dst"; fi
}
copy_file() {  # copy_file <relative path>
  local rel="$1" src="$MAIN/$1" dst="$DIR/$1"
  if [ ! -f "$src" ]; then say "skip $rel (not in main checkout)"; return; fi
  if [ -f "$dst" ]; then return; fi
  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
  say "copied $rel"
}
for d in $ORCHESTRA_CLONE_DIRS; do clone_dir "$d"; done
for f in $ORCHESTRA_COPY_FILES; do copy_file "$f"; done
mkdir -p "$DIR/.claude"

# A cloned .venv still holds the main checkout's editable install, so `python -m <pkg>` in the
# player would run the MAIN checkout's code. Re-point it at this worktree's own source. Use the
# worktree's `python -m pip`, never `.venv/bin/pip`: that script's shebang names the main
# checkout's interpreter and would install into the main .venv. Runs on re-entry too.
if [ -x "$DIR/.venv/bin/python" ] && [ -f "$DIR/pyproject.toml" ]; then
  if "$DIR/.venv/bin/python" -m pip install -q -e "$DIR" --no-deps >/dev/null 2>&1; then
    say "editable install re-pointed at this worktree"
  else
    say "WARNING: could not re-point the editable install — python -m in this worktree may run the main checkout's code"
  fi
fi

say "ready."
printf '%s\n' "$DIR"
