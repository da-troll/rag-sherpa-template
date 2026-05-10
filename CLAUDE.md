# CLAUDE.md

Guidance for Claude Code when working in this repo. Human-readable docs live in `README.md`.

## Project Overview

RAG ingestion pipeline that pushes two content sources into a Pinecone index:

1. **Slack** — exported messages/threads from a recruitment support channel
2. **Help center articles** — scraped from Freshdesk, cleaned, then converted to LlamaParse markdown

Embeddings: OpenAI `text-embedding-3-small` (1536 dim). Vector store: Pinecone.

## Repo Layout

```
.
├── README.md                          # Human-facing pipeline guide (with flow diagram)
├── CLAUDE.md                          # This file (LLM agent guide)
├── .env.example                       # Committed template (op:// refs + defaults)
├── .env                               # Local copy, gitignored — devs `cp .env.example .env`
├── run                                # Wrapper: `op run --env-file=.env -- "$@"`
├── requirements.txt
│
├── fetch-all-messages.py              # [Slack step 1] Slack API → JSON dump
├── slack_<CHANNEL_ID>.json            # Slack export (lives at repo root)
├── p90-calc.py                        # [Slack diagnostic] Thread length stats
├── slack-to-pc.py                     # [Slack step 2] JSON → Pinecone
│
├── clean-articles-json.py             # [Articles step 1] scraped → cleaned
├── articles-to-markdown.py            # [Articles step 2] LlamaParse → markdown JSON
├── articles-to-pc.py                  # [Articles step 3] markdown JSON → Pinecone
├── articles/
│   ├── scraped_help_articles.json     # Pipeline input (live)
│   ├── cleaned_help_articles.json     # Intermediate (live)
│   ├── markdown_help_articles.json    # Pinecone input (live)
│   └── p90-calc.py                    # Markdown-corpus diagnostic
│
├── ragnar-system-prompt-with-citations.md   # Live n8n bot system prompt
├── docs/
│   ├── n8n-ranking-guide.md
│   └── n8n-code-snippets.md
└── archive/                           # Past-iteration files, do not delete
    ├── pc-init.py                     # Old Pinecone sanity stub (now reads .env)
    ├── articles/chunk_plan.json       # Old adaptive chunk config (replaced by header chunker)
    ├── articles-txt/*.txt             # Pre-LlamaParse raw articles
    └── message-fetches/*.json         # Old Slack export staging
```

## Environment Configuration

`.env` is the single source of truth at runtime. Scripts always read via `os.getenv()` — they don't import `op` or talk to 1Password directly. The `./run` wrapper resolves `op://` lines via `op run` *before* the Python process starts; plain values pass through unchanged.

`.env` is gitignored; `.env.example` is the committed seed. New clones run `cp .env.example .env` and edit local values from there.

**Default `.env.example` contents (four API keys wired to 1Password, rest plain):**

```
# --- 1Password references (resolved by ./run wrapper) ---
OPENAI_API_KEY=op://Employee/uw6soelyqjqerwkogprxr7t4ia/api key
SLACK_TOKEN=op://Employee/fe7qwdxozznsegta7vdtkeyv7m/credential
PINECONE_API_KEY=op://Employee/575iu6gscslfu6dbmtgzvpi6hy/credential
LLAMA_CLOUD_API_KEY=op://Employee/lnhrdmv73rtdgb6ew53wqhmewu/credential

# --- Plain values (config, not secrets) ---
PINECONE_INDEX=
PINECONE_NAMESPACE=
SLACK_CHANNEL_ID=
SLACK_JSON_PATH=
SLACK_WORKSPACE_HOST=https://simployer.slack.com
TRUSTED_USERS={U082LELDMBN,...}
EMBED_MODEL=text-embedding-3-small
BATCH_SIZE=64
CHUNK_CHARS=3000            # H2/H3 char cap for articles-to-pc.py
CHUNK_OVERLAP=400
```

`OPENAI_API_KEY` must resolve to a `sk-` or `sk-svcacct-` value (project keys `sk-proj-` are rejected). `CHUNK_PLAN_PATH` is no longer used (old adaptive plan is in `archive/`).

Any `op://` line can be replaced with a plain value at the user's discretion (escape hatch for "I don't want to install 1Password"). Mixing is also fine — there's no requirement that all four use 1P or all four be plain.

## Running scripts: the `./run` wrapper

`./run` is `exec op run --env-file=.env -- "$@"`. It resolves any `op://` references and launches the command with real env values for that one subprocess.

```bash
./run python fetch-all-messages.py
./run python slack-to-pc.py
./run python articles-to-pc.py
```

If `.env` contains zero `op://` references (user replaced them all with plain values), `python <script>.py` directly also works because `python-dotenv` loads `.env`. Otherwise the bare-`python` path will pass `op://...` strings through to API calls and 401 / fail.

`op signin` must be active when `./run` is used (Touch ID on macOS, ~30 min session).

## Pipelines

### Slack pipeline

1. `./run python fetch-all-messages.py` — pulls all messages + threads, writes `message-fetches/slack_<CHANNEL_ID>.json`. Move it to repo root so other scripts find it (or set `SLACK_JSON_PATH`).
2. `./run python p90-calc.py` *(optional)* — diagnostic. Prints char-length percentiles of `:recruitment:`-tagged threads. Use to sanity-check `CHUNK_CHARS` for `slack-to-pc.py`.
3. `./run python slack-to-pc.py` — produces two vector types per thread:
   - **`thread_synth`** (single vector): question (parent) + best 2 answers, prioritizing trusted users.
   - **Full thread chunks**: char-windowed conversation with overlap.

### Help center articles pipeline

1. `./run python clean-articles-json.py` — `articles/scraped_help_articles.json` → `articles/cleaned_help_articles.json`. Strips noise patterns, tracks image positions.
2. `./run python articles-to-markdown.py` — uses LlamaParse to convert images and clean structure into markdown. Writes `articles/markdown_help_articles.json`. **Requires `LLAMA_CLOUD_API_KEY`.**
3. `./run python articles/p90-calc.py` *(optional)* — diagnostic. Reports H2/H3 section size distribution to validate `CHUNK_CHARS`.
4. `./run python articles-to-pc.py` — chunks markdown on H2/H3 headers (then char-caps oversize sections at `CHUNK_CHARS` with `CHUNK_OVERLAP`), embeds, deletes prior vectors for the same `doc_id`, upserts.

## Chunking Strategy

- **Slack threads:** char-window with overlap. `chunk_strategy = "chars{N}_overlap{M}"`.
- **Help center:** markdown-header-aware. Split on `## ` / `### `, then char-cap any oversize section. `chunk_strategy = "md_headers_cap{N}_overlap{M}"`. This replaces the old adaptive standard/long_doc bucketing in `archive/articles/chunk_plan.json`.

## Vector Metadata

### Slack vectors

```json
{
  "source": "slack",
  "channel_id": "<id>",
  "thread_ts": "<timestamp>",
  "permalink": "https://simployer.slack.com/archives/<channel>/p<ts>",
  "authors": ["U123", "U456"],
  "ts_first": "<iso>",
  "ts_last": "<iso>",
  "message_count": 5,
  "chunk_index": 0,
  "chunk_strategy": "chars1500_overlap300",
  "has_trusted": true,
  "has_answer_like": true,
  "has_recruitment_reaction": true,
  "trusted_repliers": ["U082LELDMBN"],
  "trusted_count": 1,
  "text": "<chunk>",
  "doc_type": "thread_synth",
  "synth": true
}
```

### Help center vectors

```json
{
  "source": "helpcenter",
  "doc_id": "hc::<article_key>::<sha1_12>",
  "url": "https://...",
  "title": "Article title",
  "chunk_index": 0,
  "chunk_strategy": "md_headers_cap3000_overlap400",
  "has_images": true,
  "image_count": 6,
  "word_count": 225,
  "reading_time_minutes": 1,
  "extracted_at": "<iso>",
  "markdown_converted_at": "<iso>",
  "ingested_at": "<iso>",
  "text": "<chunk>"
}
```

## Trusted User System

`TRUSTED_USERS` env var lists SME Slack IDs. Their replies are prioritized in synthetic thread summaries. `has_trusted`, `trusted_repliers`, and `trusted_count` are emitted in metadata for downstream boosting.

## Recruitment Reaction Tracking

`has_recruitment_reaction` is true when the parent or any reply has a `:recruitment:` reaction. Reaction strings are also embedded in the chunk text (e.g., `:recruitment:x3`).

## n8n Retrieval

Metadata fields `has_trusted` and `has_recruitment_reaction` are designed for **code-based boosting in n8n**, not external rerankers like Cohere. See:
- `docs/n8n-ranking-guide.md` — full implementation guide
- `docs/n8n-code-snippets.md` — ready-to-use JS templates

Recommended flow: `[Chat Trigger] → [Pinecone: top 20] → [Code: boost on metadata] → [top 5] → [LLM]`.

## Pinecone Index

- Dimension: 1536 (matches `text-embedding-3-small`)
- Namespaces: separate logical collections (e.g., `recruitment-rag`)
- All scripts verify `index.dimension == 1536` on startup.

## Common Tasks

**Re-ingest Slack from scratch:**
```bash
./run python fetch-all-messages.py
mv message-fetches/slack_<CHANNEL_ID>.json .
./run python slack-to-pc.py
```

**Re-ingest help articles from scraped JSON:**
```bash
./run python clean-articles-json.py
./run python articles-to-markdown.py    # costs LlamaParse credits
./run python articles-to-pc.py          # auto-deletes prior vectors per doc_id
```

**Validate chunk sizing before ingest:**
```bash
./run python p90-calc.py                # Slack
./run python articles/p90-calc.py       # Articles
```

## Security Notes

- Real secrets for all four API keys (`OPENAI_API_KEY`, `SLACK_TOKEN`, `PINECONE_API_KEY`, `LLAMA_CLOUD_API_KEY`) live only in 1Password (vault `Employee`). The `.env` holds `op://` references that `op run` resolves into env vars for the lifetime of one subprocess.
- `.env` is gitignored; `.env.example` is the committed template. This protects against the escape-hatch case where a dev pastes plaintext into their local `.env`.
- `archive/pc-init.py` previously had a hardcoded Pinecone key; now reads from `.env`. Project has never left local machine, so the old key was not rotated.
- Keep `archive/` out of version control (it contains historical artifacts including older Slack export staging).
- `.env.local` is the conventional escape hatch for per-dev overrides; keep it gitignored.
