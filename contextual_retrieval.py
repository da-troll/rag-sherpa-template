"""
Anthropic-style contextual retrieval helper.

For each chunk, asks an LLM to write 1-2 sentences situating the chunk in its
source document. The result is prepended to the chunk text *before* embedding,
so the embedding picks up document-level context that mid-document chunks
otherwise lose. The original (un-prepended) chunk text is what gets stored in
metadata and shown to the answering LLM.

Reference: https://www.anthropic.com/news/contextual-retrieval

Usage:
    from contextual_retrieval import contextualize_chunk
    text_for_embedding = contextualize_chunk(full_doc, chunk_text)
    # embed text_for_embedding; store original chunk_text in metadata.text

Env vars:
    CONTEXTUAL_RETRIEVAL=0    # disable (default: enabled)
    CONTEXTUAL_MODEL=gpt-4o-mini  # override model
"""
from __future__ import annotations
import os
from typing import Optional

_PROMPT = """<document>
{document}
</document>

Here is a chunk from the document:
<chunk>
{chunk}
</chunk>

Give a short, succinct (1-2 sentence) context that situates this chunk within the overall document, for the purpose of improving retrieval. Mention what topic or section the chunk covers and what surrounding context a reader would need. Answer ONLY with the context — no preamble, no quotes, no markdown."""

_client = None


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


def is_enabled() -> bool:
    return os.getenv("CONTEXTUAL_RETRIEVAL", "1") != "0"


def generate_context(document: str, chunk: str, model: Optional[str] = None) -> str:
    """Return a 1-2 sentence situating context for `chunk` within `document`.

    Returns empty string on any error (caller should fall back to the
    un-contextualized chunk so ingestion never fails on a single bad call).

    Prints a styled glyph to stdout per call so callers can show inline
    progress: green ● on success, red ✗ on failure (with WARN on next line).
    """
    import sys
    try:
        from styling import progress_glyph
    except Exception:
        progress_glyph = lambda success=True: ("." if success else "!")
    model = model or os.getenv("CONTEXTUAL_MODEL", "gpt-4o-mini")
    try:
        resp = _get_client().chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": _PROMPT.format(document=document, chunk=chunk)}],
            max_tokens=150,
            temperature=0.0,
        )
        sys.stdout.write(progress_glyph(success=True))
        sys.stdout.flush()
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        sys.stdout.write(progress_glyph(success=False))
        sys.stdout.flush()
        # Route warnings to stderr so they never interleave with the inline
        # stdout progress stream (would otherwise orphan a thread's prefix).
        print(f"\n  [context][WARN] {e}", file=sys.stderr, flush=True)
        return ""


def contextualize_chunk(document: str, chunk: str, model: Optional[str] = None) -> str:
    """Prepend generated context to `chunk`. Returns `chunk` unchanged if
    contextual retrieval is disabled or the LLM call fails."""
    if not is_enabled():
        return chunk
    ctx = generate_context(document, chunk, model)
    if not ctx:
        return chunk
    return f"[Context: {ctx}]\n\n{chunk}"
