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
├── README.md                          # Human-facing pipeline guide (with flow diagram + tree)
├── CLAUDE.md                          # This file (LLM agent guide)
├── GENERICIZATION_PLAN.md             # Audit of use-case-specific assumptions
├── .env.example                       # Committed template (op:// refs + defaults)
├── .env                               # Local copy, gitignored — devs `cp .env.example .env`
├── run                                # Wrapper: `op run --env-file=.env -- "$@"`
├── requirements.txt
│
├── contextual_retrieval.py            # Anthropic-style chunk-context helper (shared)
├── styling.py                         # Terminal banner/section/summary helpers (shared)
│
├── ingest/                            # All Pinecone-writing & data-prep scripts
│   ├── fetch-all-messages.py          # [Slack step 1] Slack API → JSON dump
│   ├── slack-to-pc.py                 # [Slack step 2] JSON → Pinecone (+ --namespace, --include-bots)
│   ├── scrape-articles.py             # [Articles step 0] live scrape → scraped JSON
│   ├── clean-articles-json.py         # [Articles step 1] scraped → cleaned
│   ├── articles-to-markdown.py        # [Articles step 2] LlamaParse → markdown JSON
│   └── articles-to-pc.py              # [Articles step 3] markdown JSON → Pinecone (+ --namespace)
│
├── diagnostics/                       # Read-only chunk-size sanity checks
│   ├── p90-calc-slack.py              # Slack thread length stats
│   └── p90-calc-articles.py           # Article + H2/H3 section size stats
│
├── data/                              # All raw / intermediate corpus files
│   ├── slack/
│   │   └── slack_<CHANNEL_ID>.json    # Slack export
│   └── articles/
│       ├── scraped_help_articles.json # After scrape-articles.py
│       ├── cleaned_help_articles.json # After clean-articles-json.py
│       ├── markdown_help_articles.json# After articles-to-markdown.py (Pinecone input)
│       └── .cache/                    # Per-URL HTML cache (gitignored)
│
├── eval/                              # Retrieval-quality eval harness
│   ├── run_eval.py
│   ├── questions.json                 # 20 labeled questions
│   ├── README.md
│   └── results/                       # Per-run JSON (gitignored except sweep keepers)
│
├── experiments/                       # Reproducible experiments + their write-ups
│   ├── article_chunk_sweep.py         # Re-runnable chunk-cap sweep harness
│   ├── article-chunk-cap-sweep.md     # Sweep finding: cap=1000 wins (80% R@5, MRR 0.688)
│   └── bot-self-ingestion-drift.md    # A/B finding: bot filter ≈ +20 R@1 / +0.113 MRR
│
├── n8n/                               # Bot workflow + system prompt + retrieval notes
│   ├── n8n-workflow.json              # The exported workflow (Webhook → boost → Ragnar)
│   ├── ragnar-system-prompt-with-citations.md  # Live system prompt
│   └── docs/
│       ├── n8n-ranking-guide.md       # Why code-boost, not Cohere; full implementation
│       └── n8n-code-snippets.md       # Ready-to-paste JS (boost, RRF, debug)
│
└── archive/                           # Past-iteration files, do not delete
    ├── pc-init.py                     # Old Pinecone sanity stub
    ├── articles/chunk_plan.json       # Old adaptive chunk config (replaced by header chunker)
    ├── articles-txt/*.txt             # Pre-LlamaParse raw articles
    └── message-fetches/*.json         # Old Slack export staging
```

All scripts in `ingest/`, `diagnostics/`, and `experiments/` inject the repo root into `sys.path` at startup so `contextual_retrieval` and `styling` resolve as top-level imports.

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

# Source-specific chunking — Slack (conversational) and articles (structured
# docs) tune independently. Values are EMPIRICALLY MEASURED on the corpus:
# - Slack: 1500/300 confirmed by p90-calc.py and eval (1-chunk-per-thread for ~95% of threads)
# - Articles: 1500/300 beat research-benchmark 3500/500 by 5 pts R@5 on this corpus
#   (smaller chunks = more discriminative embeddings for short specific queries;
#    contextual retrieval already handles cross-chunk context preservation)
SLACK_CHUNK_CHARS=1500
SLACK_CHUNK_OVERLAP=300
ARTICLE_CHUNK_CHARS=1500
ARTICLE_CHUNK_OVERLAP=300
```

`OPENAI_API_KEY` must resolve to a `sk-` or `sk-svcacct-` value (project keys `sk-proj-` are rejected). `CHUNK_PLAN_PATH` is no longer used (old adaptive plan is in `archive/`).

Any `op://` line can be replaced with a plain value at the user's discretion (escape hatch for "I don't want to install 1Password"). Mixing is also fine — there's no requirement that all four use 1P or all four be plain.

## Running scripts: the `./run` wrapper

`./run` is `exec op run --env-file=.env -- "$@"`. It resolves any `op://` references and launches the command with real env values for that one subprocess.

```bash
./run python ingest/fetch-all-messages.py
./run python ingest/slack-to-pc.py
./run python ingest/articles-to-pc.py
```

Both `slack-to-pc.py` and `articles-to-pc.py` accept `--namespace <name>` (CLI flag overrides env). `slack-to-pc.py` also accepts `--include-bots` for the bot-drift experiment. CLI flags exist because `op run --env-file=.env` overrides shell env vars with `.env` values — without the flag, a one-off `PINECONE_NAMESPACE=foo ./run python ...` would be silently overridden.

If `.env` contains zero `op://` references (user replaced them all with plain values), `python <script>.py` directly also works because `python-dotenv` loads `.env`. Otherwise the bare-`python` path will pass `op://...` strings through to API calls and 401 / fail.

`op signin` must be active when `./run` is used (Touch ID on macOS, ~30 min session).

## Pipelines

### Slack pipeline

1. `./run python ingest/fetch-all-messages.py` — pulls all messages + threads, writes `data/slack/slack_<CHANNEL_ID>.json`.
2. `./run python diagnostics/p90-calc-slack.py` *(optional)* — diagnostic. Prints char-length percentiles of `:recruitment:`-tagged threads.
3. `./run python ingest/slack-to-pc.py` — produces two vector types per thread, after filtering out system-event parents (channel_join, channel_name, etc.):
   - **`thread_synth`** (single vector): question (parent) + best 2 answers, prioritizing trusted users.
   - **Full thread chunks**: char-windowed conversation with overlap. With `CONTEXTUAL_RETRIEVAL=1`, each chunk gets a 1-2 sentence LLM-generated situating context prepended; synth vectors are not contextualized.

   Each vector carries `has_recruitment_reaction` (boolean) for n8n boost code — see Slack vector metadata schema below.

### Help center articles pipeline

0. `./run python ingest/scrape-articles.py` — fetches the listing pages in `HELP_CENTER_LISTING_URLS`, discovers article URLs (regex `SCRAPE_ARTICLE_URL_PATTERN`), saves full-page HTML for each into `data/articles/scraped_help_articles.json`. Polite delay between fetches; per-URL HTML cache in `data/articles/.cache/` (gitignored). Image `src` and link `href` attributes are absolutized so downstream steps don't break on relative paths. Use `--force` to bypass cache; `--limit N` for debugging.
1. `./run python ingest/clean-articles-json.py` — `data/articles/scraped_help_articles.json` → `data/articles/cleaned_help_articles.json`. Strips noise patterns, tracks image positions.
2. `./run python ingest/articles-to-markdown.py` — uses LlamaParse to convert images and clean structure into markdown. Writes `data/articles/markdown_help_articles.json`. **Requires `LLAMA_CLOUD_API_KEY`.**
3. `./run python diagnostics/p90-calc-articles.py` *(optional)* — diagnostic. Reports H2/H3 section size distribution to validate `CHUNK_CHARS`.
4. `./run python ingest/articles-to-pc.py` — chunks markdown on H2/H3 headers (then char-caps oversize sections at `ARTICLE_CHUNK_CHARS` with `ARTICLE_CHUNK_OVERLAP`), embeds, deletes prior vectors for the same `doc_id`, upserts. With `CONTEXTUAL_RETRIEVAL=1` (default), each chunk gets a 1-2 sentence LLM-generated situating context prepended before embedding.

## Chunking Strategy

- **Slack threads:** char-window with overlap. `chunk_strategy = "chars{N}_overlap{M}"`.
- **Help center:** markdown-header-aware. Split on `## ` / `### `, then char-cap any oversize section. `chunk_strategy = "md_headers_cap{N}_overlap{M}"`. This replaces the old adaptive standard/long_doc bucketing in `archive/articles/chunk_plan.json`.

## Contextual Retrieval (Anthropic-style)

When `CONTEXTUAL_RETRIEVAL=1` (default), every chunk is sent to `gpt-4o-mini` along with its full source document; the LLM returns a 1–2 sentence context that is **prepended to the chunk text before embedding**. The original (un-prepended) chunk text is what's stored in `metadata.text`, so the answering LLM at query time gets clean content.

Applied to: all article chunks; full-thread Slack chunks. **Not** applied to Slack `thread_synth` vectors (already self-contextualized).

`metadata.contextual_retrieval` is `true`/`false` per vector so you can filter or compare runs. See `RAG_IMPROVEMENTS_PLAN.md` (gitignored) for the broader plan.

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

**Slack ingest filtering and signals:**
- Parents whose `subtype` is in `{channel_join, channel_leave, channel_name, channel_topic, channel_purpose, channel_archive, channel_unarchive, thread_broadcast}` are dropped before any vector is built — these system events used to pollute retrieval with embeddings of strings like `"<@U082LELDMBN> has joined the channel"`.
- **Bot/app messages are filtered out of every reply list** (predicate: `bot_id` set, `app_id` set, or `subtype == "bot_message"`). The Ragnar bot itself answers in this channel, so without the filter its own answers — generated *from* the index — would be re-embedded and re-indexed, creating a closed-loop feedback amplifier. On the current corpus this drops 347+ replies authored by the bot.
- `has_recruitment_reaction` (boolean) — true if the parent or any reply has the `:recruitment:` reaction. This is the **only** quality signal surfaced in metadata at ingest time. Rationale:
  - `:recruitment:` is a deliberate curation tag applied by the SME owner to mark threads as verified content for RAG. Its presence is the verified-content signal.
  - Multiplicity is NOT signal: additional `:recruitment:` reactions on the same thread are typically other team members echoing the SME's tag — they don't add verification beyond the first. So presence-or-not, not count.
  - Other reactions (`:+1:`, `:raised_hands:`, `:rocket:`, etc.) are explicitly NOT counted as quality signals. They fire ambiguously on launch announcements, sympathy, agreement, and "thanks" — corrupting any signal they'd contribute. Popularity is not relevance.
  - n8n boost code is the right place to apply weight: multiply scores by a constant when `has_recruitment_reaction == true`.

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

The exported workflow lives at `n8n/n8n-workflow.json`. It is **not** a Vector Store Tool agent — it's an explicit retrieval pipeline so the boost code can run between Pinecone and the LLM:

```
[Slack Webhook] → [Bot vs. User] → [Fetch Slack Thread] → [Parse Context]
  → [Classifier: question?] → [Pinecone top 20] → [Metadata Boost (Code)]
  → [top 10 chunks] → [Ragnar agent] → [Send thread reply]
```

The **Metadata Boost** node returns a single item with a `chunks` array (not 10 items) — emitting 10 items caused the agent to run 10× and broke `.item` pairing. Downstream nodes reference it via `.first()`, not `.item`. Boost code keys on `has_recruitment_reaction` (×1.40), `has_trusted` (×1.30), plus smaller multipliers for `has_images`, `synth`, and multi-SME threads.

See:
- `n8n/docs/n8n-ranking-guide.md` — full implementation guide (why code-boost, not Cohere)
- `n8n/docs/n8n-code-snippets.md` — ready-to-use JS templates

Recommended flow: `[Webhook] → [Pinecone: top 20] → [Code: boost on metadata] → [top 10] → [LLM]`.

## Pinecone Index

- Dimension: 1536 (matches `text-embedding-3-small`)
- Namespaces: separate logical collections (e.g., `recruitment-rag`)
- All scripts verify `index.dimension == 1536` on startup.

## Common Tasks

**Re-ingest Slack from scratch:**
```bash
./run python ingest/fetch-all-messages.py     # writes data/slack/slack_<CHANNEL_ID>.json
./run python ingest/slack-to-pc.py
```

**Re-ingest help articles from scraped JSON:**
```bash
./run python ingest/clean-articles-json.py
./run python ingest/articles-to-markdown.py   # costs LlamaParse credits
./run python ingest/articles-to-pc.py         # auto-deletes prior vectors per doc_id
```

**Validate chunk sizing before ingest:**
```bash
./run python diagnostics/p90-calc-slack.py
./run python diagnostics/p90-calc-articles.py
```

**Measure retrieval quality:**
```bash
./run python eval/run_eval.py                              # writes eval/results/<timestamp>.json
./run python eval/run_eval.py --namespace foo --output x.json
```

**Sweep article chunk caps:**
```bash
./run python experiments/article_chunk_sweep.py            # caps 1000..3500; reports winner
```

## Security Notes

- Real secrets for all four API keys (`OPENAI_API_KEY`, `SLACK_TOKEN`, `PINECONE_API_KEY`, `LLAMA_CLOUD_API_KEY`) live only in 1Password (vault `Employee`). The `.env` holds `op://` references that `op run` resolves into env vars for the lifetime of one subprocess.
- `.env` is gitignored; `.env.example` is the committed template. This protects against the escape-hatch case where a dev pastes plaintext into their local `.env`.
- `archive/pc-init.py` previously had a hardcoded Pinecone key; now reads from `.env`. Project has never left local machine, so the old key was not rotated.
- Keep `archive/` out of version control (it contains historical artifacts including older Slack export staging).
- `.env.local` is the conventional escape hatch for per-dev overrides; keep it gitignored.
