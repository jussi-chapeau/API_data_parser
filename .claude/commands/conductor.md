---
description: "Orchestra — make this tab the conductor: recognise the developer, move to their branch, take the baton on the user's yes, then act on plain words (no more slash commands)"
argument-hint: "[developer]   — e.g. /conductor, or /conductor alex to skip the identity guess"
---

Make **this session** the Orchestra conductor for the developer at this keyboard. The system is
[docs/orchestra.md](../../docs/orchestra.md); the desk is
[docs/orchestra-desk.md](../../docs/orchestra-desk.md). Plain language, short output, the
user's language in chat. Run **only in the main checkout** — if `pwd` is under
`.claude/worktrees/`, say so and stop: a player cannot conduct.

**Read `$ARGUMENTS` yourself — do not shell-split it.** Empty means "work out who I am".

Do this in order, and say what you found in one line per step:

1. **Loose files?** `git status --porcelain`. Anything uncommitted or untracked is a
   **warning, not a stop**: list it in one line and say *"players cannot open until these are
   committed or stashed — we sort them out after the sign-on."* Carry on. Then
   `git fetch origin --prune`.
2. **Who is at the keyboard?** `. scripts/orchestra/orchestra-conf.sh` — `ORCHESTRA_DEVS` is
   `key=branch` pairs (`alex=dev-alex sam=dev-sam`). If `$ARGUMENTS` names a key, use it.
   Otherwise lower-case `git config user.name` and `git config user.email` and find the key
   they contain. Exactly one match ⇒ that developer. None or several ⇒ ask with
   `AskUserQuestion`, one option per developer — never guess.
3. **The developer's branch** (call it DEV). In order:
   - local DEV exists ⇒ `git checkout DEV`, then `git pull --ff-only` if it tracks a remote;
   - only `origin/DEV` exists ⇒ `git checkout -b DEV --track origin/DEV`;
   - neither ⇒ `git checkout ORCHESTRA_BASE` (the shared base from `.orchestra.conf`, e.g.
     `main`), `git pull --ff-only`, then `git checkout -b DEV`. Say plainly: *"DEV did not
     exist — created from <base> at <sha>, not pushed."* Pushing it is a separate question.
   If git refuses the switch because of loose files, say which files, stay on the current
   branch and carry on — the baton does not depend on the branch; the files get sorted out
   after the sign-on, then `/conductor` again.
4. **Is staging ahead?** STAGING is `ORCHESTRA_SHARED_BASE` (the committed base, `main`).
   First let the local STAGING catch up with origin: `git fetch origin STAGING:STAGING`
   (fast-forward only; if it refuses because local STAGING is ahead — unpushed work — that is
   fine, local is the newer one). Then `git log --oneline DEV..STAGING`. Empty ⇒ one line,
   *"in step with staging"*. Otherwise list them (author · subject, at most five) and ask with
   `AskUserQuestion`: **"Staging is N commits ahead — pull it into DEV now?"** — "Yes, pull" /
   "Not now". On yes: `git merge STAGING --no-edit` into DEV; say what came in. On a conflict,
   name the files and stop there with the merge paused — resolve by hand with the user, never
   with `-X ours` / `-X theirs`. If players are open, say which ones now need "update". On
   "Not now": carry on; the desk's "pull staging" does the same later.
5. **Pin the base for this machine:** write `.orchestra.local` at the repo root with exactly
   one line, `ORCHESTRA_BASE=DEV`. It is gitignored. Prove it: re-source
   `scripts/orchestra/orchestra-conf.sh` and echo `$ORCHESTRA_BASE` — it must print DEV. From
   here on players branch from DEV and merge into DEV; nobody's players touch `main`.
6. **What the players will need:** for each entry of `ORCHESTRA_COPY_FILES` and
   `ORCHESTRA_CLONE_DIRS`, say whether it exists here. A missing one is not a stop — say what
   is missing in one line each (e.g. *"no `.env` — players start without API secrets"*) and
   offer to set it up together after the sign-on.
   Then the credentials: for each `<file>:KEY,KEY` line of `ORCHESTRA_ENV_KEYS`, check that
   the file has each key with a non-empty value — key NAMES only, **never read, print, echo or
   copy a value, not even truncated**, e.g.
   `grep -c '^KEY=.\+' <file>`. All present ⇒ one line, *"credentials complete"*. Otherwise
   name each missing key and where that developer gets their own (never share another
   person's):
   <!-- EDIT THIS TABLE for your project: one row per key in ORCHESTRA_ENV_KEYS, saying where
        that developer issues their OWN copy. Delete the example rows. Without it the conductor
        can only say "missing" and the developer has to go hunting. -->
   | Key | Where it comes from |
   |---|---|
   | `SUPABASE_URL` | Supabase → Project Settings → API (sama kaikille, ei salaisuus) |
   | `SUPABASE_SERVICE_KEY` | Supabase → Project Settings → API → `service_role` |
   | `SUPABASE_ACCESS_TOKEN` | supabase.com/dashboard/account/tokens → Generate new token (henkilökohtainen) |
   | `BACKOFFICE_API_URL` | AWS API Gateway; nykyinen arvo `docs/api/BACKOFFICE_ROUTE_ENDPOINT.md`:ssä |
   | `BACKOFFICE_API_KEY` | pyydä Jussilta. **Huom:** avain on yhä committoituna tähän julkiseen repoon, ks. `docs/GDPR_REVIEW.md` #6 — kierrätys kesken |
   | `AIRTABLE_API_KEY` | airtable.com/create/tokens → PAT. Scopet: `data.records:read`, `schema.bases:read` |
   | `STRIPE_API_KEY` | Stripe Dashboard → Developers → API keys (lukuoikeus riittää) |
   | `WINDSOR_API_KEY` | Windsor.ai → Account → API key |
   Offer to walk through the missing ones after the sign-on; the developer pastes the value
   into the file themselves.
7. **The baton.** Read `.claude/conductor` (may be missing). Your own name is the first line
   of `ListAgents` (*"This session is <name> [......]"*). If the file already names this
   session, skip the question. Otherwise say who holds it now, if anyone, and ask with
   `AskUserQuestion`: **"Do you give this session the conductor role?"** — options "Yes, this
   tab conducts" / "No". On **No**: sign on as bystander (`🎼 Orchestra · bystander · main
   checkout — the conductor is <name or nobody>; I change nothing here`) and stop. On
   **Yes**: write your name into `.claude/conductor`, one line. Never write it without that
   yes, never on your own initiative.
8. **Read the desk and arm the ears:** read [docs/orchestra-desk.md](../../docs/orchestra-desk.md)
   in full — it is what you do on each plain word from here on (board, ready, watch, tell,
   merge, update, gate, push, close). Then arm the desk watcher with the Monitor tool
   (`python3 -u scripts/orchestra/orchestra-desk.py 2>&1`, description "Orchestra desk: player
   status/commit changes", the longest timeout) and **re-arm it every time it expires** —
   without it the conductor is deaf: a session does nothing between prompts. Count the open
   players: worktrees under `.claude/worktrees/` in `git worktree list --porcelain`.
9. **Sign on and say what happens next.** First line, exactly:
   `🎼 Orchestra · conductor · DEV · N players open`
   then, in the user's language, this and only this:
   - *"From now on I track every player you open. Tell me what you want built; we talk it
     through; when it is one sentence and one lane I hand you two boxes to paste into a new
     tab — the first in auto mode, the second in plan mode."*
   - *"No more slash commands: say board, ready, merge <name>, update <name>, gate, push,
     close <name> in plain words."*
   - If N > 0, the board (desk page, first section) — the players are the user's open work.
   - Anything step 6 found missing, as an offer.

From here on, every reply in this tab starts with the sign-on line, and every commit, merge,
push, worktree removal and branch deletion is its own question to the user. The conductor
writes no feature code while players are open. Running `/conductor` again in this tab is
harmless — it re-checks and refreshes.

This command changes branch in the main checkout, writes two gitignored one-line files, may
create a local branch and, only on the user's yes, merges staging into it. It pushes nothing.
