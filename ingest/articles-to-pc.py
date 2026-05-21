#!/usr/bin/env python3
"""
Ingest help articles (with LlamaParse markdown) to Pinecone.
Reads from markdown_help_articles.json with rich metadata.
"""
import os, sys, json, hashlib, datetime, re, argparse
from typing import List, Dict, Any, Tuple
from dotenv import load_dotenv, find_dotenv

import sys
from pathlib import Path
# Scripts live in a subfolder; expose the repo root so shared helpers
# (contextual_retrieval, styling) can be imported as top-level modules.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


load_dotenv(find_dotenv(usecwd=True), override=False)  # parent env (op run) wins over .env literals

from contextual_retrieval import contextualize_chunk, is_enabled as ctx_enabled
from styling import (
    banner, hr, summary, section,
    BOLD, DIM, RESET, CYAN, GREEN, YELLOW, RED,
    ARROW, CHECK, CROSS, DOT,
)

# ======== CONFIG ========
INPUT_JSON = "data/articles/markdown_help_articles.json"
PINECONE_INDEX = os.getenv("PINECONE_INDEX")
PINECONE_HOST = os.getenv("PINECONE_HOST")  # optional; bypass control-plane describe_index for scoped keys
PINECONE_NAMESPACE = os.getenv("PINECONE_NAMESPACE")
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "64"))

# Header-aware chunking: split markdown on ## / ### boundaries first,
# then char-cap any oversize section. Default 1000 chars / 300 overlap is
# the EMPIRICALLY-MEASURED optimum on this corpus from the 5-cap parameter
# sweep (see experiments/article_chunk_sweep.py):
#
#   cap   R@1   R@5   MRR@10
#   1000  60%   80%   0.688   ← winner
#   1500  60%   80%   0.675
#   2000  55%   75%   0.636
#   2500  55%   80%   0.631
#   3500  55%   80%   0.637
#
# Why smaller wins here: most eval queries target a specific paragraph;
# smaller chunks → more discriminative embeddings → tighter top-K. Contextual
# retrieval already preserves cross-chunk context, so larger caps don't earn
# their cost. Re-sweep when corpus shape shifts meaningfully.
MAX_CHARS = int(os.getenv("ARTICLE_CHUNK_CHARS", "1000"))
CHUNK_OVERLAP = int(os.getenv("ARTICLE_CHUNK_OVERLAP", "300"))
DELETE_BEFORE_INSERT = True

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")

if not OPENAI_API_KEY:
    sys.exit("OPENAI_API_KEY not set (check .env)")
if OPENAI_API_KEY.startswith("sk-proj-"):
    sys.exit("Project key (sk-proj-...) not supported. Use sk-... or sk-svcacct-...")
if not PINECONE_API_KEY:
    sys.exit("PINECONE_API_KEY not set (check .env)")

# --- clients ---
from openai import OpenAI
from pinecone import Pinecone

client = OpenAI(api_key=OPENAI_API_KEY)
pc = Pinecone(api_key=PINECONE_API_KEY)
if PINECONE_HOST:
    index = pc.Index(host=PINECONE_HOST)
    _init_mode = "PINECONE_HOST (control-plane bypass)"
    INDEX_DIM = None
else:
    index = pc.Index(PINECONE_INDEX)
    _init_mode = "via index lookup"
    INDEX_DIM = None

# Verify index dimension (best-effort; skipped silently if key has no control-plane scope)
MODEL_DIM = 1536
if not PINECONE_HOST:
    try:
        info = pc.describe_index(PINECONE_INDEX)
        INDEX_DIM = getattr(info, "dimension", None) or (info.get("dimension") if isinstance(info, dict) else None)
    except Exception:
        INDEX_DIM = None

if INDEX_DIM and INDEX_DIM != MODEL_DIM:
    sys.exit(f"Index '{PINECONE_INDEX}' dim {INDEX_DIM} != model dim {MODEL_DIM}")

# --- helpers ---
def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def create_doc_id(article_key: str) -> str:
    """Create stable doc_id from article key"""
    h = hashlib.sha1(article_key.encode("utf-8")).hexdigest()[:12]
    return f"hc::{article_key}::{h}"

_HEADER_RE = re.compile(r'^(#{2,3} .+)$', re.MULTILINE)

def _char_split(s: str, max_chars: int, overlap: int) -> List[str]:
    """Sliding-window char chunks with overlap. Used as fallback / cap."""
    if len(s) <= max_chars:
        return [s]
    out = []
    start = 0
    while start < len(s):
        end = min(len(s), start + max_chars)
        out.append(s[start:end])
        if end == len(s):
            break
        start = end - overlap
    return out

def chunk_markdown(text: str, max_chars: int, overlap: int) -> List[Tuple[int, str]]:
    """Split markdown on H2/H3 headers, then char-cap oversize sections."""
    text = text.strip()
    if not text:
        return []

    matches = list(_HEADER_RE.finditer(text))
    if not matches:
        return [(i, c) for i, c in enumerate(_char_split(text, max_chars, overlap))]

    sections: List[str] = []
    if matches[0].start() > 0:
        prefix = text[:matches[0].start()].strip()
        if prefix:
            sections.append(prefix)
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section = text[m.start():end].strip()
        if section:
            sections.append(section)

    chunks: List[str] = []
    for sec in sections:
        if len(sec) <= max_chars:
            chunks.append(sec)
        else:
            chunks.extend(_char_split(sec, max_chars, overlap))
    return list(enumerate(chunks))

def embed_batch(texts: List[str]) -> List[List[float]]:
    """Embed texts using OpenAI"""
    resp = client.embeddings.create(model=EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]

def delete_doc(doc_id: str):
    """Delete existing document by doc_id — silent on success. Also silent on
    404 'Namespace not found' which is expected on the very first article when
    ingesting into a freshly-created namespace (the namespace materializes on
    the first upsert; the delete that comes BEFORE it returns 404 harmlessly)."""
    try:
        index.delete(namespace=PINECONE_NAMESPACE, filter={"doc_id": {"$eq": doc_id}})
    except Exception as e:
        msg = str(e)
        if "Namespace not found" in msg or "(404)" in msg:
            return  # expected on fresh namespace; ignore
        print(f"\n  {RED}{CROSS} delete error{RESET} doc_id={doc_id}: {e}",
              file=sys.stderr, flush=True)

def upsert_vectors(vectors: List[Dict[str, Any]]):
    """Upsert vectors to Pinecone — silent on success; progress lines own the
    visual real estate. Errors still surface loudly."""
    try:
        res = index.upsert(vectors=vectors, namespace=PINECONE_NAMESPACE)
        return res
    except Exception as e:
        print(f"\n  {RED}{CROSS} upsert error{RESET}: {e}", file=sys.stderr, flush=True)
        raise

# --- main ---
def _parse_args():
    """CLI namespace override — bypasses op-run's .env precedence trap."""
    ap = argparse.ArgumentParser(description="Ingest help articles to Pinecone.")
    ap.add_argument("--namespace", default=None,
                    help="Override PINECONE_NAMESPACE (e.g. for experiments).")
    return ap.parse_args()


def main():
    global PINECONE_NAMESPACE
    args = _parse_args()
    if args.namespace:
        PINECONE_NAMESPACE = args.namespace

    try:
        with open(INPUT_JSON, 'r', encoding='utf-8') as f:
            articles = json.load(f)
    except FileNotFoundError:
        sys.exit(f"ERROR: {INPUT_JSON} not found. Run articles-to-markdown.py first.")

    # ---- banner ----
    dim_txt = f"{INDEX_DIM}d" if INDEX_DIM else "unknown"
    banner(
        title="articles ▸ pinecone",
        fields=[
            ("source",    f"{INPUT_JSON}"),
            ("articles",  f"{BOLD}{len(articles)}{RESET}"),
            ("index",     f"{PINECONE_INDEX}  {DIM}{dim_txt}{RESET}"),
            ("namespace", f"{BOLD}{PINECONE_NAMESPACE}{RESET}"),
            ("init",      _init_mode),
            ("embedding", f"{EMBED_MODEL}"),
            ("chunking",  f"md-headers · cap={MAX_CHARS} · overlap={CHUNK_OVERLAP}"),
            ("contextual",f"{GREEN}ON{RESET}" if ctx_enabled() else f"{YELLOW}OFF{RESET}"),
        ],
    )
    print()
    print(section(f"ingesting {len(articles)} articles"))
    print(f"  {DIM}each {GREEN}{DOT}{RESET}{DIM} = one contextual-retrieval LLM call per chunk{RESET}")
    print()

    total_chunks = 0
    total_articles = 0

    for article_index, (article_key, article) in enumerate(articles.items(), 1):
        metadata = article.get('metadata', {})
        content = article.get('content', {})

        title = metadata.get('title', article_key)
        url = metadata.get('url', '')
        markdown_text = content.get('markdown', '')

        if not markdown_text or len(markdown_text.strip()) < 50:
            print(f"  {DIM}[{article_index:>2}/{len(articles)}]{RESET} {YELLOW}skip{RESET} "
                  f"{title} {DIM}— empty or too short{RESET}", flush=True)
            continue

        # Per-article progress line — printed without newline so the styled
        # progress glyphs from contextualize_chunk land on the same line.
        # Leading "\r\033[2K" forces column 0 and clears residual state.
        sys.stdout.write("\r\033[2K")
        title_short = title if len(title) <= 50 else title[:47] + "…"
        print(f"  {DIM}[{article_index:>2}/{len(articles)}]{RESET} {CYAN}{ARROW}{RESET} "
              f"{BOLD}{title_short:<50}{RESET}  {DIM}{len(markdown_text):>5}c{RESET}  ",
              end="", flush=True)

        doc_id = create_doc_id(article_key)

        # Delete existing chunks for this doc
        if DELETE_BEFORE_INSERT:
            delete_doc(doc_id)

        # Prepare batches
        buf_texts, buf_ids, buf_meta = [], [], []
        chunk_count = 0

        # Chunk and prepare metadata
        for idx, chunk in chunk_markdown(markdown_text, max_chars=MAX_CHARS, overlap=CHUNK_OVERLAP):
            vec_id = f"help:{doc_id}:chunk:{idx}"

            # Anthropic-style contextual retrieval: prepend a short situating
            # context to what we EMBED, but keep the original chunk in metadata
            # so the answering LLM gets clean content.
            chunk_for_embedding = contextualize_chunk(markdown_text, chunk)

            chunk_meta = {
                "source": "helpcenter",
                "doc_id": doc_id,
                "url": url,
                "title": title,
                "chunk_index": idx,
                "chunk_strategy": f"md_headers_cap{MAX_CHARS}_overlap{CHUNK_OVERLAP}",
                "contextual_retrieval": ctx_enabled(),

                # Rich metadata for ranking
                "has_images": metadata.get('has_images', False),
                "image_count": metadata.get('image_count', 0),
                "word_count": metadata.get('word_count', 0),
                "reading_time_minutes": metadata.get('reading_time_minutes', 0),

                # Timestamps
                "extracted_at": metadata.get('extracted_at', ''),
                "markdown_converted_at": metadata.get('markdown_converted_at', ''),
                "ingested_at": iso_now(),

                # Content (original chunk, not the contextualized one)
                "text": chunk,
            }

            buf_texts.append(chunk_for_embedding)
            buf_ids.append(vec_id)
            buf_meta.append(chunk_meta)

            # Batch upsert when buffer full
            if len(buf_texts) >= BATCH_SIZE:
                embs = embed_batch(buf_texts)
                vectors = [{"id": i, "values": e, "metadata": m}
                          for i, e, m in zip(buf_ids, embs, buf_meta)]
                upsert_vectors(vectors)
                chunk_count += len(buf_texts)
                buf_texts, buf_ids, buf_meta = [], [], []

        # Flush remaining
        if buf_texts:
            embs = embed_batch(buf_texts)
            vectors = [{"id": i, "values": e, "metadata": m}
                      for i, e, m in zip(buf_ids, embs, buf_meta)]
            upsert_vectors(vectors)
            chunk_count += len(buf_texts)

        total_chunks += chunk_count
        total_articles += 1
        print(f"  {GREEN}{CHECK}{RESET} {BOLD}{chunk_count}{RESET} chunks", flush=True)

    print()
    summary(
        headline="articles ingest complete",
        lines=[
            f"articles ingested   {BOLD}{total_articles}{RESET}",
            f"chunks upserted     {BOLD}{total_chunks}{RESET}",
            f"namespace           {BOLD}{PINECONE_NAMESPACE}{RESET}",
        ],
    )

if __name__ == "__main__":
    main()
