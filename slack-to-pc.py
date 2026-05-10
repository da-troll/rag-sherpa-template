#!/usr/bin/env python3
import sys, os, json, math, time, datetime
from typing import List, Dict, Any
from dotenv import load_dotenv, find_dotenv
import json as _json

load_dotenv(find_dotenv(usecwd=True), override=True)

# ======== CONFIG ========
# Load all config from .env
SLACK_JSON_PATH = os.getenv("SLACK_JSON_PATH", "slack_C08MGP5N8DA.json")
WORKSPACE_HOST  = os.getenv("SLACK_WORKSPACE_HOST", "https://simployer.slack.com")
PINECONE_INDEX  = os.getenv("PINECONE_INDEX", "n8n-recruitment-rag-bot-1536")
PINECONE_NAMESPACE = os.getenv("PINECONE_NAMESPACE", "recruitment-rag-2")
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "64"))
CHUNK_CHARS = int(os.getenv("CHUNK_CHARS", "1500"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "300"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")

# ========================

# --- clients ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    sys.exit("OPENAI_API_KEY not set (check .env or export it)")

# sanity guard so we never hit a silent 401 again
if OPENAI_API_KEY.startswith("sk-proj-"):
    sys.exit("You supplied a project key (sk-proj-...). Use your service account key (sk-svcacct-...) or a standard sk- key.")
if not (OPENAI_API_KEY.startswith("sk-svcacct-") or OPENAI_API_KEY.startswith("sk-")):
    sys.exit("OPENAI_API_KEY has an unexpected prefix. Expected sk-svcacct- or sk-.")

from openai import OpenAI
client = OpenAI(api_key=OPENAI_API_KEY)

from pinecone import Pinecone

if not PINECONE_API_KEY: sys.exit("PINECONE_API_KEY not set (check .env)")
pc = Pinecone(api_key=PINECONE_API_KEY)
index = pc.Index(PINECONE_INDEX)

try:
    info = pc.describe_index(PINECONE_INDEX)
    dim = getattr(info, "dimension", None) or (info.get("dimension") if isinstance(info, dict) else None)
    print(f"[init] index='{PINECONE_INDEX}' dim={dim} namespace='{PINECONE_NAMESPACE}'")
except Exception as e:
    print(f"[init][WARN] describe_index failed: {e}")

def _parse_trusted(env_val: str):
    if not env_val:
        return set()
    s = env_val.strip()
    # JSON array?
    try:
        arr = _json.loads(s)
        if isinstance(arr, list):
            return set(str(x).strip() for x in arr if str(x).strip())
    except Exception:
        pass
    # braces or csv
    s = s.strip("{}")
    return set(u.strip() for u in s.split(",") if u.strip())

TRUSTED = _parse_trusted(os.getenv("TRUSTED_USERS"))
print(f"[trusted] SMEs loaded: {sorted(TRUSTED)}")

# ---------- helpers ----------
def ts_to_iso(ts: str) -> str:
    try:
        return datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc).isoformat()
    except Exception:
        return None

def permalink(channel_id: str, ts: str) -> str:
    return f"{WORKSPACE_HOST}/archives/{channel_id}/p{ts.replace('.','')}"

def is_parent(msg: Dict[str, Any]) -> bool:
    ts = msg.get("ts")
    return (msg.get("thread_ts") or ts) == ts

def parent_has_recruitment(msg: Dict[str, Any]) -> bool:
    for r in (msg.get("reactions") or []):
        if (r.get("name") or "").lower() == "recruitment":
            return True
    return False

def has_recruitment_reaction(msg: Dict[str, Any]) -> bool:
    """Check if a message has the :recruitment: reaction"""
    for r in (msg.get("reactions") or []):
        if (r.get("name") or "").lower() == "recruitment":
            return True
    return False

def collect_parent_threads(messages: List[Dict[str, Any]]):
    """
    Return list of (parent, replies).
    Uses embedded 'replies' if present; otherwise reconstructs from flat messages.
    """
    parents = [m for m in messages if is_parent(m)]
    has_embedded = any(isinstance(m.get("replies"), list) and m["replies"] for m in messages)

    if has_embedded:
        pairs = []
        for p in parents:
            replies = [r for r in (p.get("replies") or []) if r.get("ts") != p.get("ts")]
            replies.sort(key=lambda x: float(x["ts"]))
            pairs.append((p, replies))
        return pairs

    # fallback: reconstruct by thread_ts
    by_thread = {}
    for m in messages:
        tts = m.get("thread_ts") or m.get("ts")
        by_thread.setdefault(tts, []).append(m)
    pairs = []
    for p in parents:
        tts = p["ts"]
        replies = [r for r in by_thread.get(tts, []) if r.get("ts") != p.get("ts")]
        replies.sort(key=lambda x: float(x["ts"]))
        pairs.append((p, replies))
    return pairs

def build_thread_doc(parent: Dict[str, Any], replies: List[Dict[str, Any]]) -> Dict[str, Any]:
    # stitch readable text
    blocks = []
    authors = set()
    def add_line(m):
        who = m.get("user") or m.get("bot_id") or "unknown"
        authors.add(who)
        t_iso = ts_to_iso(m.get("ts"))
        reacts = ""
        if m.get("reactions"):
            reacts = " " + " ".join([f":{rr['name']}:x{rr.get('count',0)}" for rr in m["reactions"]])
        text = (m.get("text") or "").strip()
        blocks.append(f"[{t_iso}] <{who}>\n{text}{reacts}".strip())

    add_line(parent)
    for r in replies:
        add_line(r)

    text = "\n\n".join(blocks)
    all_ts = [parent.get("ts")] + [r.get("ts") for r in replies]
    ts_first = min(all_ts, key=lambda x: float(x))
    ts_last  = max(all_ts, key=lambda x: float(x))

    # Calculate trusted repliers and count
    trusted_repliers = [r.get("user") for r in replies if r.get("user") in TRUSTED]
    trusted_count = len(trusted_repliers)

    return {
        "text": text,
        "authors": sorted(list(authors)),
        "ts_first": ts_first,
        "ts_last": ts_last,
        "message_count": 1 + len(replies),
        "trusted_repliers": trusted_repliers,
        "trusted_count": trusted_count,
    }

def chunk_text(s: str, max_chars=CHUNK_CHARS, overlap=CHUNK_OVERLAP):
    if len(s) <= max_chars:
        yield (0, s)
        return
    start = 0; idx = 0
    while start < len(s):
        end = min(len(s), start + max_chars)
        yield (idx, s[start:end])
        if end == len(s): break
        start = end - overlap
        idx += 1

def embed_texts(texts: List[str]) -> List[List[float]]:
    res = client.embeddings.create(model=EMBED_MODEL, input=texts)
    return [d.embedding for d in res.data]

def upsert_vectors(payloads):
    try:
        res = index.upsert(vectors=payloads, namespace=PINECONE_NAMESPACE)
        # Pinecone serverless returns {'upserted_count': N}
        count = (res or {}).get("upserted_count")
        print(f"[upsert] ns='{PINECONE_NAMESPACE}' batch={len(payloads)} upserted={count}")
        return res
    except Exception as e:
        print(f"[upsert][ERROR] {e}", file=sys.stderr)
        raise

def fetch_one(id_):
    try:
        got = index.fetch(ids=[id_], namespace=PINECONE_NAMESPACE)
        # Works with both object and dict shapes
        vectors = getattr(got, "vectors", None)
        if vectors is None and isinstance(got, dict):
            vectors = got.get("vectors")
        if not vectors:
            print(f"[fetch] no vectors returned for id={id_}")
            return None

        v = vectors.get(id_)
        if v is None:
            print(f"[fetch] id not found in fetch result: {id_}")
            return None

        # v may be a dict or object
        md = getattr(v, "metadata", None) if not isinstance(v, dict) else v.get("metadata")
        return md or {}
    except Exception as e:
        print(f"[fetch][ERROR] {e}", file=sys.stderr)
        return None
    
def add_line_meta(m: Dict[str, Any], index_in_thread: int) -> Dict[str, Any]:
    """Per-message features for later roll-up onto the chunk metadata."""
    author = m.get("user") or m.get("bot_id") or "unknown"
    text = (m.get("text") or "").strip()
    return {
        "author": author,
        "is_parent": index_in_thread == 0,
        "msg_index": index_in_thread,
        "author_trusted": author in TRUSTED,
        "answer_like": (index_in_thread > 0 and len(text) >= 200)
    }
    


# ---------- main ----------
def main():
    with open(SLACK_JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    channel_id = data.get("channel_id") or "UNKNOWN"
    messages = data.get("messages") or []

    # collect parents + replies
    pairs = collect_parent_threads(messages)

    # diagnostics
    parents_total = len(pairs)
    parents_with_recruitment = sum(1 for p, _ in pairs if parent_has_recruitment(p))
    print(f"Parents: {parents_total} | parent has :recruitment:: {parents_with_recruitment}")

    # select all threads (no reaction-based filtering)
    selected = pairs
    print(f"Threads selected: {len(selected)}")

    # build thread docs, chunk, embed, upsert
    vector_batch = []
    texts_for_batch, meta_for_batch, ids_for_batch = [], [], []

    total_threads = 0
    total_chunks  = 0
    first_vec_id  = None  # for verification fetch

    for p, replies in selected:
        thread_chunk_count = 0
        total_threads += 1
        doc = build_thread_doc(p, replies)
        thread_ts = p["ts"]

        # per-message features (for boosts and grouping later)
        per_msg = [add_line_meta(p, 0)] + [add_line_meta(r, i+1) for i, r in enumerate(replies)]
        thread_has_trusted = any(x["author_trusted"] for x in per_msg)
        thread_has_answer  = any(x["answer_like"] for x in per_msg)

        # Check if parent or any reply has :recruitment: reaction
        thread_has_recruitment_reaction = has_recruitment_reaction(p) or any(
            has_recruitment_reaction(r) for r in replies
        )

        base_meta = {
            "source": "slack",
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "permalink": permalink(channel_id, thread_ts),
            "authors": doc["authors"],
            "ts_first": ts_to_iso(doc["ts_first"]),
            "ts_last": ts_to_iso(doc["ts_last"]),
            "message_count": doc["message_count"],
            "chunk_strategy": f"chars{CHUNK_CHARS}_overlap{CHUNK_OVERLAP}",
            "has_trusted": thread_has_trusted,
            "has_answer_like": thread_has_answer,
            "has_recruitment_reaction": thread_has_recruitment_reaction,
            "trusted_repliers": doc.get("trusted_repliers", []),
            "trusted_count": doc.get("trusted_count", 0),
        }

            # --- synthetic thread doc (Q + best answers) ---
        try:
            parent_text = (p.get("text") or "").strip()
            # pick best answers: trusted replies first, then longest
            replies_ranked = sorted(
                replies,
                key=lambda r: (
                    (1 if (r.get("user") or "") in TRUSTED else 0),
                    len((r.get("text") or ""))
                ),
                reverse=True
            )
            best = replies_ranked[:2]  # take top 2 replies

            best_blocks = []
            for r in best:
                who = r.get("user") or r.get("bot_id") or "unknown"
                tag = "trusted" if who in TRUSTED else "member"
                best_blocks.append(f"[{tag} {who}]\n{(r.get('text') or '').strip()}")

            synth_text = (
                "Question (parent):\n"
                + parent_text
                + "\n\n---\n\nBest answers:\n"
                + ("\n\n".join(best_blocks) if best_blocks else "(no replies)")
            ).strip()

            # upsert the synthetic as a single vector (no chunking)
            synth_id = f"slack:{channel_id}:thread:{thread_ts}:synth"
            synth_meta = {
                **base_meta,
                "doc_type": "thread_synth",
                "synth": True,
                "chunk_index": -1,
                "text": synth_text,
            }
            ids_for_batch.append(synth_id)
            texts_for_batch.append(synth_text)
            meta_for_batch.append(synth_meta)
        except Exception as e:
            print(f"[synth][WARN] thread_ts={thread_ts} -> {e}")


        thread_chunk_count = 0  # reset for each thread

        for idx, chunk in chunk_text(doc["text"], max_chars=CHUNK_CHARS, overlap=CHUNK_OVERLAP):
            vec_id = f"slack:{channel_id}:thread:{thread_ts}:chunk:{idx}"
            if first_vec_id is None:
                first_vec_id = vec_id  # remember a sample id

            ids_for_batch.append(vec_id)
            texts_for_batch.append(chunk)
            meta_for_batch.append({**base_meta, "chunk_index": idx, "text": chunk})

            thread_chunk_count += 1

            if len(texts_for_batch) >= BATCH_SIZE:
                try:
                    embs = embed_texts(texts_for_batch)
                    vector_batch = [{"id": i, "values": e, "metadata": m}
                                    for i, e, m in zip(ids_for_batch, embs, meta_for_batch)]
                    upsert_vectors(vector_batch)
                    total_chunks += len(texts_for_batch)
                except Exception as e:
                    print(f"[batch][ERROR] thread_ts={thread_ts} batch_size={len(texts_for_batch)} -> {e}", file=sys.stderr)
                    raise
                finally:
                    ids_for_batch, texts_for_batch, meta_for_batch, vector_batch = [], [], [], []

        # after the for-loop finishes for that thread
        print(f"[trust] thread_ts={thread_ts} trusted_count={base_meta['trusted_count']} trusted={base_meta['trusted_repliers']}")
        print(f"[thread] {thread_ts} → chunks={thread_chunk_count}")



    # flush remaining
    if texts_for_batch:
        try:
            embs = embed_texts(texts_for_batch)
            vector_batch = [{"id": i, "values": e, "metadata": m}
                            for i, e, m in zip(ids_for_batch, embs, meta_for_batch)]
            upsert_vectors(vector_batch)
            total_chunks += len(texts_for_batch)
        except Exception as e:
            print(f"[flush][ERROR] batch_size={len(texts_for_batch)} -> {e}", file=sys.stderr)
            raise
        finally:
            ids_for_batch, texts_for_batch, meta_for_batch, vector_batch = [], [], [], []

    print(f"[done] threads={total_threads} chunks_upserted={total_chunks}")

    # verify one known id
    if first_vec_id:
        md = fetch_one(first_vec_id)
    if md is not None:
        preview = (md.get("text") or "")[:200].replace("\n"," ")
        print(f"[verify] id='{first_vec_id}' has_text={bool(md.get('text'))} "
              f"source={md.get('source')} chunk_index={md.get('chunk_index')}")
        print(f"[verify] text preview: {preview}")



if __name__ == "__main__":
    main()
