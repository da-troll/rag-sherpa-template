#!/usr/bin/env python3
"""
Diagnostic: analyze primary-tagged Slack thread lengths to inform
SLACK_CHUNK_CHARS / SLACK_CHUNK_OVERLAP for ingest/slack-to-pc.py. Read-only.

Only looks at threads carrying the configured PRIMARY_REACTION_TAG
(default "verified") — those are the canonical "this is good content"
threads, so chunk sizing should be optimized for their shape.
"""
import os, sys, json
import numpy as np
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(usecwd=True), override=False)  # parent env (op run) wins over .env literals

SLACK_JSON_PATH = os.getenv("SLACK_JSON_PATH", "data/slack/slack_C0000000000.json")
PRIMARY_REACTION_TAG = os.getenv("PRIMARY_REACTION_TAG", "verified").lower()
if not os.path.exists(SLACK_JSON_PATH):
    sys.exit(f"SLACK_JSON_PATH not found: {SLACK_JSON_PATH} (set in .env)")

def has_primary_tag(msg):
    for r in (msg.get("reactions") or []):
        if (r.get("name") or "").lower().split("::", 1)[0] == PRIMARY_REACTION_TAG:
            return True
    return False

def is_parent(m):
    ts = m.get("ts")
    return (m.get("thread_ts") or ts) == ts

def length_stats():
    with open(SLACK_JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    msgs = data.get("messages", [])

    # map thread_ts → messages
    by_t = {}
    for m in msgs:
        tts = m.get("thread_ts") or m.get("ts")
        by_t.setdefault(tts, []).append(m)
    for k in by_t:
        by_t[k].sort(key=lambda x: float(x["ts"]))

    # select parent messages with the primary curation reaction
    parents = [m for m in msgs if is_parent(m) and has_primary_tag(m)]

    pairs = []
    for p in parents:
        thread_ts = p["ts"]
        replies = [r for r in by_t.get(thread_ts, []) if r.get("ts") != p["ts"]]
        pairs.append((p, replies))

    lengths = []
    for p, r in pairs:
        blocks = []
        def add(m):
            txt = (m.get("text") or "").strip()
            reacts = ""
            if m.get("reactions"):
                reacts = " " + " ".join([f":{rr['name']}:x{rr.get('count',0)}"
                                         for rr in m["reactions"]])
            blocks.append(txt + reacts)
        add(p)
        for x in r: add(x)
        thread_text = "\n\n".join(blocks)
        lengths.append(len(thread_text))

    if not lengths:
        print(f"No threads found with the :{PRIMARY_REACTION_TAG}: reaction.")
        return

    arr = np.array(lengths)
    pct = lambda q: int(np.percentile(arr, q))
    print(f"Threads tagged :{PRIMARY_REACTION_TAG}:: {len(arr)}")
    print(f"mean chars: {int(arr.mean())} | p50: {pct(50)} | p75: {pct(75)} | "
          f"p90: {pct(90)} | p95: {pct(95)} | max: {arr.max()}")

    # Suggest config
    target = min(6000, max(1500, pct(90)))
    suggested_chunk = int(round(target / 500) * 500)
    suggested_overlap = int(round(suggested_chunk * 0.2 / 100) * 100)
    print(f"SUGGESTED -> CHUNK_CHARS={suggested_chunk}, CHUNK_OVERLAP={suggested_overlap}")

if __name__ == "__main__":
    length_stats()
