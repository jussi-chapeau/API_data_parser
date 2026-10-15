#!/usr/bin/env python3
"""Orchestra desk watcher — the conductor's ears (docs/orchestra.md).

One stdout line per event, polled every 5 s: a player changes phase/doing, writes its session
name, makes a commit, appears or disappears; and a warning when two players touch the same file
(committed on their branch or dirty in their tree). Meant to run under Claude Code's Monitor tool
from the conductor session (the desk's "watch"); harmless to run by hand.

Reads ORCHESTRA_BASE from `.orchestra.conf` at the main checkout (default: main), then lets
`.orchestra.local` (the developer's own base branch, written by /conductor) override it.
"""
import glob
import os
import subprocess
import sys
import time

ROOT = subprocess.run(["git", "worktree", "list", "--porcelain"], capture_output=True, text=True).stdout.split("\n")[0].split(" ", 1)[1].strip()
WT = os.path.join(ROOT, ".claude", "worktrees")


def conf_base():
    base = "main"
    # .orchestra.conf first, .orchestra.local last — the local (per-developer) value wins.
    for name in (".orchestra.conf", ".orchestra.local"):
        try:
            for line in open(os.path.join(ROOT, name), encoding="utf-8"):
                line = line.strip()
                if line.startswith("ORCHESTRA_BASE="):
                    base = line.split("=", 1)[1].strip().strip('"').strip("'") or base
        except OSError:
            pass
    return base


BASE = conf_base()


def sh(cmd, cwd=ROOT):
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=20).stdout.rstrip()
    except Exception:
        return ""


def field(path, key):
    try:
        for line in open(path, encoding="utf-8"):
            if line.startswith(key + ":"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "-"


def snapshot():
    snap = {}
    for w in sorted(glob.glob(os.path.join(WT, "*/"))):
        n = os.path.basename(w.rstrip("/"))
        st = os.path.join(w, ".claude", "status")
        sess = "-"
        try:
            sess = open(os.path.join(w, ".claude", "session"), encoding="utf-8").readline().strip() or "-"
        except OSError:
            pass
        head = sh(["git", "rev-parse", "--short", "HEAD"], cwd=w) or "-"
        snap[n] = {"phase": field(st, "phase"), "doing": field(st, "doing"), "sess": sess, "head": head, "dir": w}
    return snap


def emit(line):
    print(line, flush=True)


def touched(n, w):
    """Files this player changed vs the base branch: committed on its branch + dirty in its tree."""
    files = set(l for l in sh(["git", "diff", "--name-only", f"{BASE}...{n}"]).splitlines() if l)
    for line in sh(["git", "status", "--porcelain"], cwd=w).splitlines():
        if len(line) > 3:
            files.add(line[3:].split(" -> ")[-1].strip())
    return {f for f in files if not f.startswith(".claude/")}


reported = set()


def collisions(snap):
    names = sorted(snap)
    sets = {n: touched(n, snap[n]["dir"]) for n in names}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for f in sorted(sets[a] & sets[b]):
                key = (a, b, f)
                if key not in reported:
                    reported.add(key)
                    emit(f"⚠️ lane collision: {a} and {b} both touch {f}")


prev = snapshot()
try:
    collisions(prev)
except Exception as e:  # noqa: BLE001
    print(f"[stderr] collision check failed: {e}", file=sys.stderr, flush=True)
while True:
    time.sleep(5)
    try:
        cur = snapshot()
    except Exception as e:  # noqa: BLE001
        print(f"[stderr] snapshot failed: {e}", file=sys.stderr, flush=True)
        continue
    for n, c in cur.items():
        p = prev.get(n)
        if p is None:
            emit(f"🎼 new player: {n} · phase: {c['phase']} · {c['doing']}")
            continue
        if (c["phase"], c["doing"]) != (p["phase"], p["doing"]):
            emit(f"🎼 {n} → {c['phase']} · {c['doing']}")
        if c["sess"] != p["sess"]:
            emit(f"🎼 {n} · session: {c['sess']}")
        if c["head"] != p["head"]:
            ahead = len([l for l in sh(["git", "log", "--oneline", f"{BASE}..{n}"]).splitlines() if l])
            subj = sh(["git", "log", "-1", "--format=%s"], cwd=c["dir"])
            emit(f"🎼 {n} · new commit {c['head']} ({ahead} ahead of {BASE}): {subj}")
    for n in prev:
        if n not in cur:
            emit(f"🎼 player gone: {n}")
    try:
        collisions(cur)
    except Exception as e:  # noqa: BLE001
        print(f"[stderr] collision check failed: {e}", file=sys.stderr, flush=True)
    prev = cur
