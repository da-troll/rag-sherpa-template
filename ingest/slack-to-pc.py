#!/usr/bin/env python3
import sys, os, json, math, time, datetime, argparse
from typing import List, Dict, Any
from dotenv import load_dotenv, find_dotenv
import json as _json

import sys
from pathlib import Path
# Scripts live in a subfolder; expose the repo root so shared helpers
# (contextual_retrieval, styling) can be imported as top-level modules.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


load_dotenv(find_dotenv(usecwd=True), override=False)  # parent env (op run) wins over .env literals

from contextual_retrieval import contextualize_chunk, is_enabled as ctx_enabled
from styling import (
    banner, hr, summary, section,
    BOLD, DIM, RESET, CYAN, BRIGHT_CYAN, GREEN, YELLOW, RED, GRAY,
    ARROW, CHECK, CROSS, DOT,
)

# ======== CONFIG ========
# Load all config from .env
SLACK_JSON_PATH = os.getenv("SLACK_JSON_PATH", "data/slack/slack_C0000000000.json")
WORKSPACE_HOST  = os.getenv("SLACK_WORKSPACE_HOST", "https://your-workspace.slack.com")
PINECONE_INDEX  = os.getenv("PINECONE_INDEX", "n8n-your-namespace-bot-1536")
PINECONE_HOST   = os.getenv("PINECONE_HOST")  # optional; bypass describe_index for scoped keys
PINECONE_NAMESPACE = os.getenv("PINECONE_NAMESPACE", "your-namespace")
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "64"))

# Chunk sizing — source-specific so Slack (short, conversational) and articles
# (structured technical docs) tune independently. Defaults follow best-practice
# heuristic: cap ≥ p90 of natural unit sizes (here: full thread text), with
# ~15-20% overlap. See `p90-calc.py` to re-derive after corpus shape shifts.
CHUNK_CHARS   = int(os.getenv("SLACK_CHUNK_CHARS", "1500"))
CHUNK_OVERLAP = int(os.getenv("SLACK_CHUNK_OVERLAP", "300"))
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
if PINECONE_HOST:
    index = pc.Index(host=PINECONE_HOST)
    _init_mode = "PINECONE_HOST (control-plane bypass)"
    _dim_info = None
else:
    index = pc.Index(PINECONE_INDEX)
    _init_mode = "via index lookup"
    try:
        info = pc.describe_index(PINECONE_INDEX)
        _dim_info = getattr(info, "dimension", None) or (info.get("dimension") if isinstance(info, dict) else None)
    except Exception:
        _dim_info = None

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

def parent_has_primary_tag(msg: Dict[str, Any]) -> bool:
    for r in (msg.get("reactions") or []):
        if (r.get("name") or "").lower() == "recruitment":
            return True
    return False

def has_primary_tag(msg: Dict[str, Any]) -> bool:
    """Check if a message has the :verified: reaction"""
    for r in (msg.get("reactions") or []):
        if (r.get("name") or "").lower() == "recruitment":
            return True
    return False

# System-event subtypes whose messages are not real content. They generate
# garbage embeddings (e.g. "<@U0000000001> has joined the channel") that
# pollute retrieval, so we drop them before any vector is built.
SKIP_SUBTYPES = {
    "channel_join", "channel_leave", "channel_name",
    "channel_topic", "channel_purpose",
    "channel_archive", "channel_unarchive",
    "thread_broadcast",  # duplicates a reply at top level — already covered by the source thread
}

# Quality signal: the boolean `has_primary_tag` is the ONLY quality
# signal we track at ingest time.
#
# Why presence-only (not count): the `:verified:` reaction is applied by
# the SME owner of this RAG corpus to mark threads as verified content.
# Additional `:verified:` reactions are typically other team members
# joining the SME's lead — they don't ADD verification, they just echo it.
# So multiplicity isn't quality signal, presence is.
#
# Why no other reactions: soft sentiment reactions (`:+1:`, `:raised_hands:`,
# `:rocket:`, etc.) have inconsistent signal-to-noise — they fire on launch
# announcements, casual agreements, and sympathy acknowledgments. Counting
# them would corrupt the clean curation signal.
#
# The boolean `has_primary_tag` is already computed below from the
# parent + replies. No additional metadata field is needed.

def _reaction_base_name(name: str) -> str:
    """Strip skin-tone modifiers so '+1::skin-tone-3' matches '+1'."""
    return (name or "").lower().split("::", 1)[0]

# Experimental override: set SLACK_INCLUDE_BOTS=1 to DISABLE the bot-message
# filter — bot replies (notably RAG Bot's own historical answers) will be
# re-ingested as if they were human content. Only intended for the
# self-ingestion drift A/B experiment documented in
# `experiments/bot-self-ingestion-drift.md`. Default is "0" (filter ON).
_INCLUDE_BOT_MESSAGES = os.getenv("SLACK_INCLUDE_BOTS", "0") == "1"

def is_bot_message(msg: Dict[str, Any]) -> bool:
    """True if a Slack message is from a bot/app, not a human.

    Bot messages are filtered out at ingest time so they can't be re-embedded
    as authoritative answers — they were generated FROM the index, and feeding
    them back risks closed-loop drift. Currently filters 347+ of RAG Bot's own
    replies in the recruitment channel.

    When the experimental SLACK_INCLUDE_BOTS=1 env var is set, this function
    always returns False (filter OFF) — the bot replies pass through into the
    index. Used only to A/B-measure whether the filter is doing real work.
    """
    if _INCLUDE_BOT_MESSAGES:
        return False
    return bool(msg.get("bot_id") or msg.get("app_id")
                or msg.get("subtype") == "bot_message")

def collect_parent_threads(messages: List[Dict[str, Any]]):
    """
    Return list of (parent, human_replies).

    Parents whose subtype is in SKIP_SUBTYPES are filtered out so they never
    become vectors. Bot/app replies are filtered out of every reply list — they
    came from the bot reading the index and posting back, so re-ingesting them
    would create a closed-loop feedback amplifier.

    Uses embedded 'replies' if present; otherwise reconstructs from flat messages.
    """
    parents = [m for m in messages
               if is_parent(m)
               and m.get("subtype") not in SKIP_SUBTYPES
               and not is_bot_message(m)]
    has_embedded = any(isinstance(m.get("replies"), list) and m["replies"] for m in messages)

    if has_embedded:
        pairs = []
        for p in parents:
            replies = [r for r in (p.get("replies") or [])
                       if r.get("ts") != p.get("ts") and not is_bot_message(r)]
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
        replies = [r for r in by_thread.get(tts, [])
                   if r.get("ts") != p.get("ts") and not is_bot_message(r)]
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
    """Silent on success — progress lines own the visual real estate.
    Errors still surface loudly."""
    try:
        res = index.upsert(vectors=payloads, namespace=PINECONE_NAMESPACE)
        return res
    except Exception as e:
        print(f"\n  {RED}{CROSS} upsert error{RESET}: {e}", file=sys.stderr, flush=True)
        raise

def fetch_one(id_):
    try:
        got = index.fetch(ids=[id_], namespace=PINECONE_NAMESPACE)
        vectors = getattr(got, "vectors", None)
        if vectors is None and isinstance(got, dict):
            vectors = got.get("vectors")
        if not vectors:
            return None
        v = vectors.get(id_)
        if v is None:
            return None
        md = getattr(v, "metadata", None) if not isinstance(v, dict) else v.get("metadata")
        return md or {}
    except Exception as e:
        print(f"\n  {RED}{CROSS} fetch error{RESET}: {e}", file=sys.stderr, flush=True)
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
def _parse_args():
    """CLI overrides — required because op-run injects .env values over
    shell-exported env vars, so `PINECONE_NAMESPACE=foo ./run ...` is silently
    ineffective. CLI args bypass the env entirely."""
    ap = argparse.ArgumentParser(description="Ingest Slack threads to Pinecone.")
    ap.add_argument("--namespace", default=None,
                    help="Override PINECONE_NAMESPACE (use to target a non-production namespace).")
    ap.add_argument("--include-bots", action="store_true",
                    help="Disable the bot-message filter (re-ingests RAG Bot's own replies). "
                         "Equivalent to SLACK_INCLUDE_BOTS=1.")
    return ap.parse_args()


def main():
    global PINECONE_NAMESPACE, _INCLUDE_BOT_MESSAGES
    args = _parse_args()
    if args.namespace:
        PINECONE_NAMESPACE = args.namespace
    if args.include_bots:
        _INCLUDE_BOT_MESSAGES = True

    with open(SLACK_JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    channel_id = data.get("channel_id") or "UNKNOWN"
    messages = data.get("messages") or []
    exported_at = data.get("exported_at", "?")

    # ---- corpus stats (computed BEFORE banner so they can appear inside it) ----
    raw_parents = sum(1 for m in messages if is_parent(m))
    raw_replies = sum(1 for m in messages for _ in (m.get("replies") or []))
    bot_replies_total = sum(1 for m in messages
                            for r in (m.get("replies") or []) if is_bot_message(r))
    pairs = collect_parent_threads(messages)
    parents_total = len(pairs)
    kept_replies = sum(len(r) for _, r in pairs)
    parents_with_recruitment = sum(1 for p, _ in pairs if parent_has_primary_tag(p))
    threads_with_recruitment = sum(
        1 for p, r in pairs
        if has_primary_tag(p) or any(has_primary_tag(x) for x in r)
    )

    # ---- banner ----
    dim_txt = f"{_dim_info}d" if _dim_info else "unknown"
    banner(
        title="slack ▸ pinecone",
        fields=[
            ("source",    f"{SLACK_JSON_PATH}  {DIM}(exported {exported_at[:10]}){RESET}"),
            ("channel",   channel_id),
            ("index",     f"{PINECONE_INDEX}  {DIM}{dim_txt}{RESET}"),
            ("namespace", f"{BOLD}{PINECONE_NAMESPACE}{RESET}"),
            ("init",      _init_mode),
            ("embedding", f"{EMBED_MODEL}"),
            ("chunking",  f"chars={CHUNK_CHARS} overlap={CHUNK_OVERLAP}"),
            ("contextual",f"{GREEN}ON{RESET}" if ctx_enabled() else f"{YELLOW}OFF{RESET}"),
            ("trusted",   f"{len(TRUSTED)} user(s): {DIM}{', '.join(sorted(TRUSTED))}{RESET}"),
        ],
    )

    # ---- corpus diagnostics ----
    print()
    print(section("corpus"))
    print(f"  {DIM}parents{RESET}     {BOLD}{parents_total}{RESET} kept "
          f"{DIM}(filtered {raw_parents - parents_total} non-content from {raw_parents} raw){RESET}")
    print(f"  {DIM}replies{RESET}     {BOLD}{kept_replies}{RESET} human "
          f"{DIM}(dropped {bot_replies_total} bot-authored from {raw_replies} total){RESET}")
    print(f"  {DIM}signals{RESET}     "
          f":verified: on parent {BOLD}{parents_with_recruitment}{RESET}  ·  "
          f"anywhere in thread {BOLD}{threads_with_recruitment}{RESET}")
    print()

    # select all threads (no reaction-based filtering)
    selected = pairs
    print(section(f"ingesting {len(selected)} threads"))
    print(f"  {DIM}each {GREEN}{DOT}{RESET}{DIM} = one contextual-retrieval LLM call (synth + per chunk){RESET}")
    print()

    # build thread docs, chunk, embed, upsert
    vector_batch = []
    texts_for_batch, meta_for_batch, ids_for_batch = [], [], []

    total_threads = 0
    total_chunks  = 0
    first_vec_id  = None  # for verification fetch

    for thread_index, (p, replies) in enumerate(selected, 1):
        thread_chunk_count = 0
        total_threads += 1
        doc = build_thread_doc(p, replies)
        thread_ts = p["ts"]

        # Per-thread progress header — printed without newline so the styled
        # progress glyphs from contextualize_chunk land on the same line.
        # Leading "\r\033[2K" forces the cursor to column 0 and clears any
        # residual partial-line state (e.g. from an OpenAI retry that
        # printed something quirky), so the prefix always renders cleanly.
        sys.stdout.write("\r\033[2K")
        print(f"  {DIM}[{thread_index:>3}/{len(selected)}]{RESET} {CYAN}{ARROW}{RESET} "
              f"{DIM}ts={thread_ts}{RESET} replies={len(replies):>2}  ",
              end="", flush=True)

        # per-message features (for boosts and grouping later)
        per_msg = [add_line_meta(p, 0)] + [add_line_meta(r, i+1) for i, r in enumerate(replies)]
        thread_has_trusted = any(x["author_trusted"] for x in per_msg)
        thread_has_answer  = any(x["answer_like"] for x in per_msg)

        # Check if parent or any reply has :verified: reaction
        thread_has_primary_tag = has_primary_tag(p) or any(
            has_primary_tag(r) for r in replies
        )

        # Quality signal: `has_primary_tag` (boolean) is computed
        # below in `base_meta` from `parent_has_primary_tag(p) or any(...)`.
        # It's the ONLY curation signal we surface — presence not count.

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
            "has_primary_tag": thread_has_primary_tag,
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

            # upsert the synthetic as a single vector (no chunking).
            # Apply contextual retrieval to the embedding so semantic-paraphrase
            # queries match better; metadata.text stays as the literal Q+A so
            # the answering LLM gets clean content for citations.
            synth_for_embedding = contextualize_chunk(doc["text"], synth_text)
            synth_id = f"slack:{channel_id}:thread:{thread_ts}:synth"
            synth_meta = {
                **base_meta,
                "doc_type": "thread_synth",
                "synth": True,
                "chunk_index": -1,
                "contextual_retrieval": ctx_enabled(),
                "text": synth_text,
            }
            ids_for_batch.append(synth_id)
            texts_for_batch.append(synth_for_embedding)
            meta_for_batch.append(synth_meta)
        except Exception as e:
            print(f"[synth][WARN] thread_ts={thread_ts} -> {e}")


        thread_chunk_count = 0  # reset for each thread

        for idx, chunk in chunk_text(doc["text"], max_chars=CHUNK_CHARS, overlap=CHUNK_OVERLAP):
            vec_id = f"slack:{channel_id}:thread:{thread_ts}:chunk:{idx}"
            if first_vec_id is None:
                first_vec_id = vec_id  # remember a sample id

            # Anthropic-style contextual retrieval: contextualize on the full
            # thread doc, embed the contextualized text, but keep the raw chunk
            # in metadata.text for the answering LLM.
            chunk_for_embedding = contextualize_chunk(doc["text"], chunk)

            ids_for_batch.append(vec_id)
            texts_for_batch.append(chunk_for_embedding)
            meta_for_batch.append({
                **base_meta,
                "chunk_index": idx,
                "contextual_retrieval": ctx_enabled(),
                "text": chunk,
            })

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

        # Pad the inline dot stream to a fixed visible width so the summary
        # columns line up across rows. 1 dot per LLM call (synth + each chunk).
        DOT_COL_WIDTH = 12
        dots_written = 1 + thread_chunk_count
        sys.stdout.write(" " * max(0, DOT_COL_WIDTH - dots_written))

        # Close the per-thread inline line with a fixed-column summary.
        # Chunk count is right-aligned to 2 digits so "1 chunks" / "12 chunks"
        # both produce the same width. The `:verified:` chip is intentionally
        # not displayed — the boolean lives in metadata for n8n boost code, but
        # adding it inline made the terminal layout noisy and inconsistent.
        _tc = base_meta['trusted_count']
        _trust_chip = f"{GREEN}trust={_tc}{RESET}" if _tc else f"{DIM}trust=0{RESET}"
        print(f"  {GREEN}{CHECK}{RESET} {BOLD}{thread_chunk_count:>2}{RESET} chunks "
              f"{DIM}+{RESET} {BOLD}1{RESET} synth  {DIM}|{RESET}  "
              f"{_trust_chip}", flush=True)



    # flush remaining
    if texts_for_batch:
        try:
            embs = embed_texts(texts_for_batch)
            vector_batch = [{"id": i, "values": e, "metadata": m}
                            for i, e, m in zip(ids_for_batch, embs, meta_for_batch)]
            upsert_vectors(vector_batch)
            total_chunks += len(texts_for_batch)
        except Exception as e:
            print(f"\n  {RED}{CROSS} flush error{RESET} batch_size={len(texts_for_batch)} → {e}",
                  file=sys.stderr, flush=True)
            raise
        finally:
            ids_for_batch, texts_for_batch, meta_for_batch, vector_batch = [], [], [], []

    print()
    summary(
        headline="slack ingest complete",
        lines=[
            f"threads processed   {BOLD}{total_threads}{RESET}",
            f"chunks upserted     {BOLD}{total_chunks}{RESET}  {DIM}(synth vectors counted){RESET}",
            f"namespace           {BOLD}{PINECONE_NAMESPACE}{RESET}",
        ],
    )

    # verify one known id — sanity check that what we upserted is actually queryable
    if first_vec_id:
        md = fetch_one(first_vec_id)
        if md:
            preview = (md.get("text") or "")[:160].replace("\n"," ")
            print()
            print(section("verification fetch"))
            print(f"  {DIM}id{RESET}      {first_vec_id}")
            print(f"  {DIM}source{RESET}  {md.get('source')}  {DIM}chunk_index{RESET}={md.get('chunk_index')}")
            print(f"  {DIM}preview{RESET} {DIM}{preview}{RESET}")



if __name__ == "__main__":
    main()
