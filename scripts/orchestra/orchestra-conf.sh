#!/usr/bin/env bash
# Orchestra — load .orchestra.conf from the MAIN checkout (works from inside any worktree),
# then .orchestra.local on top of it (gitignored; written by /conductor; holds this
# developer's own base branch, e.g. ORCHESTRA_BASE=dev-alex).
# Source this: `. scripts/orchestra/orchestra-conf.sh` — it sets ORCHESTRA_MAIN and the
# ORCHESTRA_* values, with defaults for anything the files leave out.
ORCHESTRA_MAIN=$(git worktree list --porcelain 2>/dev/null | awk 'NR==1 && $1=="worktree" {print $2}')
[ -n "$ORCHESTRA_MAIN" ] || { echo "orchestra: not inside a git repository" >&2; return 1 2>/dev/null || exit 1; }
ORCHESTRA_BASE=main
ORCHESTRA_DEVS=""
ORCHESTRA_ENV_KEYS=""
ORCHESTRA_CLONE_DIRS=""
ORCHESTRA_COPY_FILES=".env .claude/settings.json"
ORCHESTRA_PORT_BASE=8100
ORCHESTRA_GATES=""
if [ -f "$ORCHESTRA_MAIN/.orchestra.conf" ]; then
  # shellcheck disable=SC1091
  . "$ORCHESTRA_MAIN/.orchestra.conf"
else
  echo "orchestra: no .orchestra.conf at $ORCHESTRA_MAIN — using defaults (base '$ORCHESTRA_BASE')" >&2
fi
# The committed base is the shared integration branch (staging) — keep it before the override.
ORCHESTRA_SHARED_BASE=$ORCHESTRA_BASE
# The per-developer override wins: on this machine players branch from and merge into it.
if [ -f "$ORCHESTRA_MAIN/.orchestra.local" ]; then
  # shellcheck disable=SC1091
  . "$ORCHESTRA_MAIN/.orchestra.local"
fi
export ORCHESTRA_MAIN ORCHESTRA_BASE ORCHESTRA_SHARED_BASE ORCHESTRA_DEVS ORCHESTRA_ENV_KEYS ORCHESTRA_CLONE_DIRS ORCHESTRA_COPY_FILES ORCHESTRA_PORT_BASE ORCHESTRA_GATES
