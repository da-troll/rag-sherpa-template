#!/usr/bin/env python3
"""
Ingest help articles (with LlamaParse markdown) to Pinecone.
Reads from markdown_help_articles.json with rich metadata.
"""
import os, sys, json, hashlib, datetime, re
from typing import List, Dict, Any, Tuple
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(usecwd=True), override=True)

# ======== CONFIG ========
INPUT_JSON = "articles/markdown_help_articles.json"
PINECONE_INDEX = os.getenv("PINECONE_INDEX")
PINECONE_NAMESPACE = os.getenv("PINECONE_NAMESPACE")
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "64"))

# Header-aware chunking: split markdown on ## / ### boundaries first,
# then char-cap any oversize section so embeddings stay coherent.
MAX_CHARS = int(os.getenv("CHUNK_CHARS", "3000"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "400"))
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
index = pc.Index(PINECONE_INDEX)

# Verify index dimension
MODEL_DIM = 1536
try:
    info = pc.describe_index(PINECONE_INDEX)
    INDEX_DIM = getattr(info, "dimension", None) or (info.get("dimension") if isinstance(info, dict) else None)
    print(f"[init] index='{PINECONE_INDEX}' dim={INDEX_DIM} namespace='{PINECONE_NAMESPACE}'")
except Exception as e:
    print(f"[init][WARN] describe_index failed: {e}")
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
    """Delete existing document by doc_id"""
    try:
        index.delete(namespace=PINECONE_NAMESPACE, filter={"doc_id": {"$eq": doc_id}})
        print(f"  [delete] doc_id={doc_id}")
    except Exception as e:
        print(f"  [delete][WARN] {e}")

def upsert_vectors(vectors: List[Dict[str, Any]]):
    """Upsert vectors to Pinecone"""
    try:
        res = index.upsert(vectors=vectors, namespace=PINECONE_NAMESPACE)
        count = (res or {}).get("upserted_count")
        print(f"  [upsert] batch={len(vectors)} upserted={count}")
        return res
    except Exception as e:
        print(f"  [upsert][ERROR] {e}", file=sys.stderr)
        raise

# --- main ---
def main():
    print(f"Loading articles from {INPUT_JSON}...")

    try:
        with open(INPUT_JSON, 'r', encoding='utf-8') as f:
            articles = json.load(f)
    except FileNotFoundError:
        sys.exit(f"ERROR: {INPUT_JSON} not found. Run articles-to-markdown.py first.")

    print(f"Found {len(articles)} articles")
    print(f"Chunk strategy: markdown headers (H2/H3), char cap {MAX_CHARS}, overlap {CHUNK_OVERLAP}")
    print("=" * 80)

    total_chunks = 0
    total_articles = 0

    for article_key, article in articles.items():
        metadata = article.get('metadata', {})
        content = article.get('content', {})

        title = metadata.get('title', article_key)
        url = metadata.get('url', '')
        markdown_text = content.get('markdown', '')

        if not markdown_text or len(markdown_text.strip()) < 50:
            print(f"[skip] {title} - empty or too short")
            continue

        print(f"\n[{total_articles + 1}] {title}")
        print(f"  URL: {url}")
        print(f"  Length: {len(markdown_text)} chars")
        print(f"  Images: {metadata.get('image_count', 0)}")

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

            chunk_meta = {
                "source": "helpcenter",
                "doc_id": doc_id,
                "url": url,
                "title": title,
                "chunk_index": idx,
                "chunk_strategy": f"md_headers_cap{MAX_CHARS}_overlap{CHUNK_OVERLAP}",

                # Rich metadata for ranking
                "has_images": metadata.get('has_images', False),
                "image_count": metadata.get('image_count', 0),
                "word_count": metadata.get('word_count', 0),
                "reading_time_minutes": metadata.get('reading_time_minutes', 0),

                # Timestamps
                "extracted_at": metadata.get('extracted_at', ''),
                "markdown_converted_at": metadata.get('markdown_converted_at', ''),
                "ingested_at": iso_now(),

                # Content
                "text": chunk,
            }

            buf_texts.append(chunk)
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
        print(f"  ✓ {chunk_count} chunks ingested")

    print("\n" + "=" * 80)
    print("SUMMARY:")
    print("=" * 80)
    print(f"Articles ingested: {total_articles}")
    print(f"Total chunks: {total_chunks}")
    print(f"Index: {PINECONE_INDEX}")
    print(f"Namespace: {PINECONE_NAMESPACE}")

if __name__ == "__main__":
    main()
