#!/usr/bin/env python3
"""Sync customer feedback from Airtable `Palautteet` into Supabase `core.feedback`.

Source: base appGkn53DdtjFGmWV, table tblPTaUOxvIPLUu83, 1,344 records at time of writing.
The neighbouring table "Feedback" (tblWGxD5qyFskuU9M) is EMPTY -- it carries an OrderID field
and looks like the intended schema, but nothing was ever written to it. All real feedback is in
Palautteet, which has no order key, so this data cannot be joined to `orders`. See migration 049.

WHAT THIS DELIBERATELY DOES NOT SYNC
  `Sähköposti` -- a real email address on 1,342 of 1,344 records. It is the largest direct
  identifier in the table and nothing downstream needs it: the Airtable record id is a better
  primary key anyway. Same allowlist discipline as payments_stripe / payments_paytrail, where
  an unexpected new field from the provider is excluded by default rather than swept in.

  This is an ALLOWLIST, not a denylist. FIELD_MAP below is the complete set of fields that may
  reach Supabase. If Airtable gains a "customer name" column tomorrow, it is ignored until
  someone adds it here on purpose.

SCRUBBING
  The five free-text fields are scrubbed for emails, phone numbers, street addresses and IBANs
  before upload. Measured against the live table first rather than assumed: across 1,339 staff
  comments there are 0 emails, 0 phones, 0 addresses and exactly 2 personal names, so the
  scrubber is cheap insurance rather than load-bearing. `scrub_hits` records how much it did;
  it should stay near zero and a rise means the form started collecting something new.

  Personal NAMES are not scrubbed. There is no reliable way to distinguish a Finnish first name
  from an ordinary capitalised word without a name list, and a scrubber that mangles
  "Ystävällisiä" and "Muuttomiehet" would destroy the text while missing real names anyway.
  Two names in 1,339 comments is the measured residue; that is a review item, not a regex.

Usage:
  export AIRTABLE_API_KEY=... SUPABASE_URL=... SUPABASE_SERVICE_KEY=...
  python3 scripts/sync_airtable_feedback.py --dry-run
  python3 scripts/sync_airtable_feedback.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "appGkn53DdtjFGmWV"
TABLE = "tblPTaUOxvIPLUu83"

# Allowlist: Airtable field name -> our column. Anything absent here never leaves Airtable.
FIELD_MAP: dict[str, str] = {
    "Partner": "partner_raw",
    "Paikkakunta": "city",
    "Kuinka palvelu onnistui? Asteikolla 1-5.": "csat",
    "Mistä löysit meidät?": "found_us",
    "Mitä hyvää palvelussa oli?": "comment_good",
    "Mitä kehitettävää palvelussa oli?": "comment_improve",
    "Mitä mieltä olit työntekijöistä?": "comment_staff",
    "Muita huomioita?": "comment_other",
}
# These two have long names that vary; matched by prefix instead of exact string.
PREFIX_MAP = {
    "Kuinka todennäköisesti suosittelisit": "nps",
    "Missä muissa tilanteissa voisit käyttää": "comment_other_uses",
}
TEXT_COLS = {"comment_good", "comment_improve", "comment_staff", "comment_other",
             "comment_other_uses"}

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"(?:\+358|\b0)\s?\d(?:[\s-]?\d){6,10}\b")
STREET = re.compile(
    r"\b[A-ZÅÄÖ][a-zåäö]+(?:katu|tie|kuja|polku|väylä|kaari|raitti|rinne|puisto)\s*\d+[A-Za-z]?\b")
IBAN = re.compile(r"\bFI\d{2}(?:\s?\d){14}\b", re.I)


def scrub(text: str) -> tuple[str, int]:
    """Redact direct identifiers. Returns (clean_text, number_of_redactions)."""
    hits = 0
    for pattern, token in ((EMAIL, "[EMAIL]"), (IBAN, "[IBAN]"),
                           (PHONE, "[PHONE]"), (STREET, "[ADDRESS]")):
        text, n = pattern.subn(token, text)
        hits += n
    return text, hits


def airtable_records(key: str) -> list[dict]:
    out: list[dict] = []
    offset = None
    while True:
        params = {"pageSize": 100}
        if offset:
            params["offset"] = offset
        url = f"https://api.airtable.com/v0/{BASE}/{TABLE}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        out.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            return out


def to_int(v) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def build_row(rec: dict) -> tuple[dict, int]:
    f = rec.get("fields", {}) or {}
    row: dict = {"record_id": rec["id"]}
    for src, col in FIELD_MAP.items():
        if src in f:
            row[col] = f[src]
    for prefix, col in PREFIX_MAP.items():
        for k, v in f.items():
            if k.startswith(prefix):
                row[col] = v
                break

    row["csat"] = to_int(row.get("csat"))
    row["nps"] = to_int(row.get("nps"))

    hits = 0
    for col in TEXT_COLS:
        val = row.get(col)
        if isinstance(val, str) and val.strip():
            cleaned, n = scrub(val)
            row[col] = cleaned
            hits += n
        elif col in row:
            row[col] = None

    # Last Modified is 100% populated; `Month` is 21.8% filled and inconsistent, so it is unused.
    lm = f.get("Last Modified")
    row["submitted_at"] = lm or None
    row["scrub_hits"] = hits
    row["synced_at"] = datetime.now(timezone.utc).isoformat()
    return row, hits


def upsert(url: str, key: str, rows: list[dict]):
    """Write via the public.upsert_feedback RPC.

    PostgREST only exposes `public`, so posting straight at core.feedback returns 406. Exposing
    the `core` schema to get around that would publish every durable table on the REST API, so
    the write goes through a SECURITY DEFINER RPC instead -- the same approach migration 033
    took for GA4 events.
    """
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json"}
    data = json.dumps({"rows": rows}).encode("utf-8")
    req = urllib.request.Request(f"{url.rstrip('/')}/rest/v1/rpc/upsert_feedback",
                                 data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    at_key = os.environ.get("AIRTABLE_API_KEY")
    su, sk = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not at_key:
        print("ERROR: AIRTABLE_API_KEY required.", file=sys.stderr)
        return 2
    if not args.dry_run and not (su and sk):
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY required.", file=sys.stderr)
        return 2

    recs = airtable_records(at_key)
    rows, total_hits = [], 0
    for r in recs:
        row, hits = build_row(r)
        rows.append(row)
        total_hits += hits

    print(f"fetched {len(recs)} Airtable records")
    print(f"scrub redactions applied: {total_hits} across {sum(1 for r in rows if r['scrub_hits'])} rows")
    print(f"rows with a CSAT score   : {sum(1 for r in rows if r.get('csat') is not None)}")
    print(f"rows with an NPS score   : {sum(1 for r in rows if r.get('nps') is not None)}")
    print(f"rows naming a partner    : {sum(1 for r in rows if r.get('partner_raw'))}")

    # Never sync an email, even if a future field rename slips one past the allowlist.
    leaked = [r["record_id"] for r in rows
              if any(isinstance(v, str) and EMAIL.search(v) for k, v in r.items()
                     if k != "record_id")]
    if leaked:
        print(f"ABORT: {len(leaked)} rows still contain an email after scrubbing.", file=sys.stderr)
        return 1
    print("email check: clean")

    if args.dry_run:
        print("DRY RUN -- nothing written.")
        return 0

    written = 0
    for i in range(0, len(rows), 200):
        n = upsert(su, sk, rows[i:i + 200])
        written += n if isinstance(n, int) else 0
        print(f"  upserted {min(i + 200, len(rows))}/{len(rows)}")
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
