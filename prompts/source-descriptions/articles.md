# Source: Help center articles

The retrieved context may include chunks from structured documentation — help-center articles, internal wikis, or any markdown-formatted reference content ingested via `ingest/articles-to-pc.py`.

## What's in an article chunk's metadata

- `source = "helpcenter"`
- `doc_id` — stable identifier of the article (`hc::<key>::<sha1_prefix>`)
- `url` — canonical URL of the article (use this for citations)
- `title` — article title (use this as the citation display text)
- `chunk_index` — which chunk within the article (0-indexed)
- `chunk_strategy` — how the chunk was produced (e.g. `md_headers_cap1000_overlap300`)
- `has_images` — true if the source article had embedded screenshots
- `image_count` — number of images in the source article
- `word_count` — chunk word count
- `reading_time_minutes` — rough estimate
- `extracted_at` / `markdown_converted_at` / `ingested_at` — pipeline timestamps

## How to read an article chunk

Article chunks are produced from H2/H3-aware splits of structured markdown. Each chunk typically covers ONE coherent topic (one section of the article). When a chunk references UI elements (settings paths, button names), trust them — they came from official documentation that's been LlamaParse-converted from real screenshots.

For multi-step procedures that may span chunks, retrieval will usually surface both halves; combine them coherently in your answer.

## How to cite an article

Use the `url` field with the `title` as display text:

```
<{url}|{title}>
```

Add `(documentation)` as a quality marker since these chunks are from official docs:

```
Sources:
• <https://your-helpcenter.example.com/docs/getting-started|Getting started with Widget Manager> (documentation)
```

If `has_images: true` and the user's question is UI-related, mention that the source has screenshots — useful for the user to know they can see visuals:

```
Sources:
• <https://your-helpcenter.example.com/docs/widget-categories|Managing widget categories> (documentation, includes screenshots)
```
