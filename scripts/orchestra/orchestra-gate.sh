#!/usr/bin/env bash
# Orchestra — the gate the conductor runs on the MERGED tree.
#
# Every player gates its own branch, but two branches that are each green can be red together.
# This runs, in the main checkout, the ORCHESTRA_GATES rules whose path prefix the given range
# touched:
#
#     scripts/orchestra/orchestra-gate.sh                       # what the last merge brought in (HEAD^1..HEAD)
#     scripts/orchestra/orchestra-gate.sh origin/main..HEAD     # everything not yet pushed
#
# Rules live in .orchestra.conf, one per line: `<prefix>|<command>`. The command runs from the
# repo root with $FILES = the touched files under that prefix. Exit 0 = green for what was
# touched; non-zero = red, with the failing rule named.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/orchestra-conf.sh"
RANGE="${1:-HEAD^1..HEAD}"
cd "$ORCHESTRA_MAIN" || exit 2
say() { printf 'gate: %s\n' "$*"; }

files=$(git diff --name-only "$RANGE" 2>/dev/null) || { say "bad range '$RANGE'"; exit 2; }
[ -n "$files" ] || { say "range $RANGE touches nothing"; exit 0; }
say "range $RANGE · $(printf '%s\n' "$files" | wc -l | tr -d ' ') files"

red=0; ran=0
while IFS= read -r rule; do
  [ -n "$rule" ] || continue
  prefix="${rule%%|*}"; cmd="${rule#*|}"
  hit=$(printf '%s\n' "$files" | grep "^$prefix" || true)
  [ -n "$hit" ] || continue
  ran=$((ran+1))
  say "▶ $prefix ($(printf '%s\n' "$hit" | wc -l | tr -d ' ') files): $cmd"
  if FILES="$(printf '%s ' $hit)" bash -c "$cmd"; then
    say "✓ $prefix"
  else
    say "❌ $prefix — $cmd"; red=1
  fi
done <<< "$ORCHESTRA_GATES"

[ "$ran" -gt 0 ] || say "no gate rule matches what was touched — nothing to run (docs/config only?)"
[ "$red" -eq 0 ] && say "green" || say "RED"
exit $red
