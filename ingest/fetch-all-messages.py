#!/usr/bin/env python3
import os, time, json, csv, datetime, sys, requests
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(usecwd=True), override=False)  # parent env (op run) wins over .env literals

# ========= CONFIG =========
# SLACK_TOKEN is resolved by `op run` from 1Password; SLACK_CHANNEL_ID lives in .env.
SLACK_TOKEN = os.getenv("SLACK_TOKEN")
CHANNEL_ID  = os.getenv("SLACK_CHANNEL_ID")
WRITE_CSV   = False
LIMIT       = 200
# ==========================

if not SLACK_TOKEN:
    sys.exit("SLACK_TOKEN not set. Run via `./run python fetch-all-messages.py` so 1Password resolves it.")
if not CHANNEL_ID:
    sys.exit("SLACK_CHANNEL_ID not set (check .env).")

API_BASE = "https://slack.com/api"
HEADERS  = lambda token: {"Authorization": f"Bearer {token}"}

def api_get(method, token, params):
    """GET wrapper with 429 handling and timeout."""
    url = f"{API_BASE}/{method}"
    while True:
        try:
            r = requests.get(url, headers=HEADERS(token), params=params, timeout=30)
        except requests.exceptions.Timeout:
            print(f"[WARNING] Request to {method} timed out after 30s, retrying...")
            time.sleep(2)
            continue
        except requests.exceptions.RequestException as e:
            print(f"[ERROR] Request to {method} failed: {e}")
            raise

        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", "1"))
            print(f"[RATE LIMIT] Waiting {wait}s before retry...")
            time.sleep(wait + 1)
            continue
        r.raise_for_status()
        data = r.json()
        if not data.get("ok", False):
            err = data.get("error", "unknown_error")
            raise RuntimeError(f"{method} failed: {err}")
        return data

def paginate_conversations_history(token, channel_id, oldest=None, latest=None, limit=LIMIT):
    """Yield all messages in a channel, oldest→newest (unchanged)."""
    cursor = None
    while True:
        params = {"channel": channel_id, "limit": limit}
        if cursor: params["cursor"] = cursor
        if oldest: params["oldest"] = oldest
        if latest: params["latest"] = latest
        data = api_get("conversations.history", token, params)
        for msg in data.get("messages", []):
            yield msg
        cursor = (data.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break

def paginate_conversations_replies(token, channel_id, thread_ts, limit=LIMIT):
    """Yield all messages in a thread (unchanged)."""
    cursor = None
    first_page = True
    while True:
        params = {"channel": channel_id, "ts": thread_ts, "limit": limit}
        if cursor: params["cursor"] = cursor
        data = api_get("conversations.replies", token, params)
        msgs = data.get("messages", [])
        # Skip the parent on pages after the first to avoid duplicates
        if not first_page and msgs and msgs[0].get("ts") == thread_ts:
            msgs = msgs[1:]
        for msg in msgs:
            yield msg
        cursor = (data.get("response_metadata") or {}).get("next_cursor")
        first_page = False
        if not cursor:
            break

def ts_to_iso(ts):
    try:
        return datetime.datetime.utcfromtimestamp(float(ts)).replace(tzinfo=datetime.timezone.utc).isoformat()
    except Exception:
        return None

def collect_channel_with_threads(token, channel_id):
    """Return list of parent messages with an added 'replies' array."""
    print("[1/2] Fetching parent messages...")
    parents = []
    for msg in paginate_conversations_history(token, channel_id):
        msg["_iso_ts"] = ts_to_iso(msg.get("ts"))
        parents.append(msg)

    print(f"[1/2] Found {len(parents)} parent messages")
    print("[2/2] Fetching thread replies...")

    for idx, msg in enumerate(parents, 1):
        thread_ts = msg.get("thread_ts") or msg.get("ts")
        has_thread = (thread_ts == msg.get("ts")) and (msg.get("reply_count", 0) > 0)

        if has_thread:
            reply_count = msg.get("reply_count", 0)
            print(f"  [{idx}/{len(parents)}] Fetching thread {thread_ts[:10]}... ({reply_count} replies)")
            replies = []
            for r in paginate_conversations_replies(token, channel_id, thread_ts):
                if r.get("ts") == msg.get("ts"):  # skip parent duplicate
                    continue
                r["_iso_ts"] = ts_to_iso(r.get("ts"))
                replies.append(r)
            replies.sort(key=lambda x: float(x["ts"]))
            msg["replies"] = replies
        else:
            msg["replies"] = []

    print(f"[2/2] Completed fetching all threads")
    parents.sort(key=lambda x: float(x["ts"]))
    return parents

def write_json(output_path, channel_id, messages):
    out = {
        "channel_id": channel_id,
        "exported_at": datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).isoformat(),
        "message_count": len(messages),
        "messages": messages,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

def write_csv(csv_path, messages):
    """
    Flatten to CSV: one row per message (parents and replies).
    Columns: kind(parent|reply), thread_parent_ts, ts, iso_ts, user, text
    """
    rows = []
    for m in messages:
        rows.append({
            "kind": "parent",
            "thread_parent_ts": m.get("ts"),
            "ts": m.get("ts"),
            "iso_ts": m.get("_iso_ts"),
            "user": m.get("user") or m.get("bot_id") or "",
            "text": (m.get("text") or "").replace("\n", " ").strip()
        })
        for r in m.get("replies", []):
            rows.append({
                "kind": "reply",
                "thread_parent_ts": m.get("ts"),
                "ts": r.get("ts"),
                "iso_ts": r.get("_iso_ts"),
                "user": r.get("user") or r.get("bot_id") or "",
                "text": (r.get("text") or "").replace("\n", " ").strip()
            })
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["kind","thread_parent_ts","ts","iso_ts","user","text"])
        writer.writeheader()
        writer.writerows(rows)

def main():
    token = SLACK_TOKEN
    chan  = CHANNEL_ID
    json_out = f"data/slack/slack_{chan}.json"
    csv_out  = f"data/slack/slack_{chan}.csv"

    print(f"Fetching channel {chan} ...")
    msgs = collect_channel_with_threads(token, chan)
    print(f"Fetched {len(msgs)} parent messages.")
    print(f"Writing JSON -> {json_out}")
    write_json(json_out, chan, msgs)

    if WRITE_CSV:
        print(f"Writing CSV  -> {csv_out}")
        write_csv(csv_out, msgs)

    print("Done.")

if __name__ == "__main__":
    main()