# Recruitment RAG Bot

A small ingestion pipeline that turns two messy sources of internal knowledge — a Slack support channel and a your help center — into clean, searchable vectors in Pinecone. An n8n workflow then queries those vectors to power **RAG Bot**, our recruitment Q&A bot.

The pipeline is **manual and step-by-step**: each stage is a separate Python script that reads a file, does one thing, and writes the next file. You run them in order. No orchestration, no scheduler, no surprises.

---

## Pipeline at a glance

```
                        ┌─────────────────────────────┐
                        │  .env  +  1Password (op://) │
                        │  Pinecone · LlamaParse      │
                        │  · trusted users  (.env)    │
                        │  OpenAI · Slack  (1Password)│
                        └──────────────┬──────────────┘
                                       │
                                       ▼
        ┌──────────────────────────────────────────────────────┐
        │                                                      │
        ▼                                                      ▼
┌──────────────────┐                                ┌──────────────────────┐
│   SLACK TRACK    │                                │   HELP CENTER TRACK  │
└──────────────────┘                                └──────────────────────┘
        │                                                      │
        ▼                                                      ▼
┌──────────────────────────┐                ┌─────────────────────────────────┐
│ 1. fetch-all-messages.py │                │ 0. scrape-articles.py           │
│  Slack API → JSON dump   │                │  live help-center → scraped JSON│
└────────────┬─────────────┘                └─────────────────┬───────────────┘
             │                                                │
             │                                                ▼
             │                       data/articles/scraped_help_articles.json
             │                                                │
             │                                                ▼
             │                            ┌─────────────────────────────────┐
             │                            │ 1. clean-articles-json.py       │
             │                            │  scraped → cleaned (strip noise)│
             │                            └─────────────────┬───────────────┘
             │                                              │
             ▼                                              ▼
data/slack/slack_<CHANNEL_ID>.json    data/articles/cleaned_help_articles.json
             │                                                │
             ▼                                                ▼
┌──────────────────────────┐                ┌─────────────────────────────────┐
│ 2. p90-calc-slack.py     │                │ 2. articles-to-markdown.py      │
│  (diagnostic, optional)  │                │  LlamaParse: images → markdown  │
│  thread size stats       │                │  (costs LlamaParse credits)     │
└────────────┬─────────────┘                └─────────────────┬───────────────┘
             │                                                │
             │                                                ▼
             │                       data/articles/markdown_help_articles.json
             │                                                │
             │                                                ▼
             │                            ┌─────────────────────────────────┐
             │                            │ 3. p90-calc-articles.py (opt.)  │
             │                            │  Diagnose H2/H3 section sizes   │
             │                            │  → validate ARTICLE_CHUNK_CHARS │
             │                            └─────────────────┬───────────────┘
             │                                              │
             ▼                                              ▼
┌──────────────────────────┐                ┌─────────────────────────────────┐
│ 3. slack-to-pc.py        │                │ 4. articles-to-pc.py            │
│  · synthetic Q+A vector  │                │  · split on H2/H3 headers       │
│  · full thread chunks    │                │  · char-cap oversize sections   │
│  · trusted-user metadata │                │  · embed + upsert to Pinecone   │
└────────────┬─────────────┘                └─────────────────┬───────────────┘
             │                                                │
             └────────────────────┬───────────────────────────┘
                                  │
                                  ▼
                       ┌──────────────────────┐
                       │   Pinecone index     │
                       │   (one namespace)    │
                       │  text-embedding-3    │
                       │     -small / 1536d   │
                       └──────────┬───────────┘
                                  │
                                  ▼
                       ┌──────────────────────┐
                       │   n8n workflow       │
                       │   (RAG Bot bot)       │
                       │  retrieve → boost    │
                       │  → top 5 → LLM       │
                       └──────────────────────┘
```

---

## What you need before running anything

### Python environment

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Secrets — `.env` is the only file you edit

The `.env` file is the single source of truth for all configuration. **No script reads from 1Password directly** — they all just `os.getenv()`. The `./run` wrapper resolves any `op://` references in `.env` *before* the script starts; plain values pass through unchanged.

This means you have two ways to fill in any secret:

1. **Recommended:** point `.env` at a 1Password item and let `./run` resolve it at command time. The default `.env` does this for the four API keys below.
2. **Escape hatch:** paste the plain value directly into `.env` instead. Works fine — at your own risk if the file ever escapes your machine.

**API keys wired to 1Password by default:**

| Variable | 1Password reference (in `.env`) |
| --- | --- |
| `OPENAI_API_KEY` | `op://<YOUR_VAULT>/OpenAI API Key/api key` |
| `SLACK_TOKEN` | `op://<YOUR_VAULT>/Slack Bot Token/credential` |
| `PINECONE_API_KEY` | `op://<YOUR_VAULT>/Pinecone API Key/credential` |
| `LLAMA_CLOUD_API_KEY` | `op://<YOUR_VAULT>/LlamaCloud API Key/credential` |

**Plain values you fill in yourself in `.env` (not secrets, just config):**

- `PINECONE_INDEX`, `PINECONE_NAMESPACE`
- `TRUSTED_USERS={U123,U456}` — Slack IDs whose replies get prioritized
- `SLACK_CHANNEL_ID`, `SLACK_JSON_PATH`, `SLACK_WORKSPACE_HOST`
- Tuning knobs: `EMBED_MODEL`, `BATCH_SIZE`, `CHUNK_CHARS`, `CHUNK_OVERLAP`

`OPENAI_API_KEY` must resolve to a `sk-` or `sk-svcacct-` value; project keys (`sk-proj-`) are rejected.

### First-time setup

```bash
cp .env.example .env            # seed your local env file
brew install 1password-cli      # if you don't have it yet
op signin                       # one-time per shell session
```

`.env` is gitignored. `.env.example` is the committed template — it ships with the four `op://` references and sensible defaults for the plain-config values. Edit your local `.env` to fill in tenant-specific values (Pinecone index/namespace, Slack channel ID, trusted users, etc.).

On macOS with the 1Password desktop app, `op` uses Touch ID — one prompt per ~30-min session. If you'd rather not use 1Password at all, replace the four `op://` lines in your `.env` with the actual key values and skip the `op signin` step.

### Running the scripts

The `./run` wrapper at the repo root does `op run --env-file=.env -- "$@"`, which resolves the `op://` references and launches your command with real values in env:

```bash
./run python ingest/fetch-all-messages.py
./run python ingest/slack-to-pc.py
./run python ingest/articles-to-pc.py
```

If `.env` contains *only* plain values (no `op://` refs), you can skip `./run` and call `python <script>.py` directly — `python-dotenv` loads `.env` natively. Either path works.

For one-off overrides without editing `.env`, drop them in `.env.local` (gitignored) or prefix the call with `FOO=bar ./run python ...`.

---

## Slack track — step by step

### Step 1 — pull messages from Slack

```bash
./run python ingest/fetch-all-messages.py
```

Reads `SLACK_TOKEN` and `SLACK_CHANNEL_ID` from `.env`. Walks the channel, follows every thread, and writes the result to `data/slack/slack_<CHANNEL_ID>.json`. Handles pagination and rate limits.

### Step 2 *(optional)* — sanity-check chunk size

```bash
./run python diagnostics/p90-calc-slack.py
```

Looks only at threads tagged with the `:verified:` reaction, computes their character-length distribution, and prints a suggested `SLACK_CHUNK_CHARS` / `SLACK_CHUNK_OVERLAP`. Skip this unless you've changed the corpus shape.

### Step 3 — embed and upload to Pinecone

```bash
./run python ingest/slack-to-pc.py
```

CLI flags: `--namespace <name>` overrides `PINECONE_NAMESPACE`; `--include-bots` disables the bot-filter (only used by the bot-drift experiment). The flags exist because `op run` reads `.env` *after* shell vars, so a plain `PINECONE_NAMESPACE=foo ./run ...` gets silently overridden — the flag wins.

For each thread, this script writes **two kinds of vectors**:

- **One synthetic summary** that joins the question (parent message) with the best two replies. Trusted users' replies are favored. This vector is what the bot retrieves when it wants a clean question/answer pair. Synth vectors are contextualized too — the prefix improves retrieval on semantic-paraphrase queries while `metadata.text` keeps the literal Q+A for citation.
- **Full-thread chunks**, char-windowed with overlap, so longer back-and-forth conversations don't get lost. When `CONTEXTUAL_RETRIEVAL=1` (default), these are prepended with a short LLM-generated context before embedding.

Before any vector is built, the script **filters out:**
- Non-content parent messages (channel_join, channel_name changes, etc.) — system events whose embeddings would pollute retrieval with garbage like `"<@U...> has joined the channel"`.
- **Bot/app-authored replies** — RAG Bot's own answers were originally generated *from* the index. We empirically measured a **20-point R@1 drop and 0.113 MRR drop** when this filter was disabled (see `experiments/bot-self-ingestion-drift.md`). The filter checks `bot_id`, `app_id`, and `subtype == "bot_message"`.

The experimental override `SLACK_INCLUDE_BOTS=1` (or `--include-bots` CLI flag) disables the filter — used only by the bot-drift A/B experiment and not for production.

Every vector carries metadata the n8n bot uses for ranking:
- `has_trusted` / `trusted_count` / `trusted_repliers` — SME participation signal (curation-independent)
- `has_primary_tag` — explicit `:verified:` tag (depends on someone tagging threads)
- `has_primary_tag` — true when the parent or any reply carries the `:verified:` reaction. This is the **only** quality signal in metadata. Presence-only (not count): additional `:verified:` reactions are typically other team members echoing the SME's tag — they don't add verification. Other reactions (`:+1:`, `:raised_hands:`, etc.) are not used as signal at all — they fire too ambiguously on announcements and sympathy to be reliable for retrieval. The n8n boost code is where the weight gets applied (multiply by a constant when this flag is true).
- `ts_last` / `ts_first` — for recency decay in boost
- `permalink` — for citations

---

## Help center track — step by step

The articles arrive as scraped HTML/JSON, full of cookie banners and screenshots. They go through four transforms before they're worth embedding.

### Step 0 — scrape live articles

```bash
./run python ingest/scrape-articles.py
```

Fetches the listing-page URLs in `HELP_CENTER_LISTING_URLS`, discovers all linked articles, and writes the full HTML of each into `data/articles/scraped_help_articles.json`. Polite (1.5s default delay between requests, retry-with-backoff on 5xx/429). Resumable — per-URL HTML cache lives in `data/articles/.cache/` (gitignored), so re-runs are essentially free unless you pass `--force`. Image `src` and link `href` attributes are absolutized so downstream steps don't choke on relative paths.

To target a different help center, override `SCRAPE_ARTICLE_URL_PATTERN` (defaults to your help center provider's `/support/solutions/articles/...` shape). Use `--limit N` for debugging.

### Step 1 — clean the scraped JSON

```bash
./run python ingest/clean-articles-json.py
```

Reads `data/articles/scraped_help_articles.json`. Strips repeated noise (cookie text, "Was this article helpful?" footers, navbar logos), tracks the position of every screenshot, and writes `data/articles/cleaned_help_articles.json`.

### Step 2 — convert to LlamaParse markdown

```bash
./run python ingest/articles-to-markdown.py
```

This is the expensive step (it calls the LlamaParse cloud API on every embedded screenshot to turn UI images into structured markdown). Output: `data/articles/markdown_help_articles.json`. **Don't re-run unless the source articles changed.**

### Step 3 *(optional)* — validate chunk size against the new corpus

```bash
./run python diagnostics/p90-calc-articles.py
```

Reports the size distribution of whole articles **and** of the H2/H3 sections inside them. The H2/H3 distribution is what actually matters — `ARTICLE_CHUNK_CHARS` should sit near the section p90 so most sections become a single chunk and only the long ones get char-split.

### Step 4 — embed and upload to Pinecone

```bash
./run python ingest/articles-to-pc.py
```

Splits each article's markdown on `## ` and `### ` headers. Sections that fit under `ARTICLE_CHUNK_CHARS` become a single chunk. Sections that don't get char-windowed with `ARTICLE_CHUNK_OVERLAP`. Before each article uploads, the script deletes any existing vectors with the same `doc_id`, so re-runs are idempotent. Accepts `--namespace <name>` to override `PINECONE_NAMESPACE`.

When `CONTEXTUAL_RETRIEVAL=1` (default), each chunk is sent through `gpt-4o-mini` along with its parent article to generate a 1–2 sentence "where this chunk sits" context. The context is prepended to the chunk before embedding; the original chunk text is preserved in `metadata.text` for answer-time use. This is Anthropic's contextual-retrieval pattern — see `eval/` for measuring its impact.

---

## Why header-aware chunking?

The earlier version of this pipeline used adaptive char chunking driven by `chunk_plan.json` — every article was bucketed as either "standard" (2000/400) or "long_doc" (6000/1200) based on its raw character count. That config is now in `archive/articles/chunk_plan.json` for reference.

We replaced it with **markdown-header-aware chunking** for one reason: now that LlamaParse gives us real markdown with `##` and `###` structure, splitting on headers produces chunks that each describe one thing. A char-window split would routinely cut a section in half, sending half the explanation to one chunk and half to another — so retrieval would pull a fragment that doesn't fully answer the question. Header splitting puts the boundaries where humans already drew them.

The char cap (`ARTICLE_CHUNK_CHARS`) only kicks in for unusually long sections, which is rare. Run `diagnostics/p90-calc-articles.py` if you want to see the distribution for the current corpus.

**Empirical winner: cap = 1000.** A full sweep over caps `{1000, 1500, 2000, 2500, 3500}` (overlap held at 300) found cap=1000 wins on both R@5 (80%) and MRR@10 (0.688), beating the research-benchmark cap=3500 by 5 points R@5 and 0.080 MRR. Smaller chunks produce more discriminative embeddings for short specific queries; contextual retrieval already handles cross-chunk context preservation. See `experiments/article-chunk-cap-sweep.md` for the full table; re-run with `./run python experiments/article_chunk_sweep.py`.

---

## What's in `archive/`?

Past iterations of this pipeline. Nothing here is run by the current scripts, but the files are kept for reference rather than deleted:

| File / folder                          | What it was                                                       |
| -------------------------------------- | ----------------------------------------------------------------- |
| `archive/pc-init.py`                   | Tiny "is Pinecone reachable?" stub. Now reads from `.env`.        |
| `archive/articles/chunk_plan.json`     | The old adaptive-chunking config (standard vs long_doc).          |
| `archive/articles-txt/*.txt`           | Pre-LlamaParse raw article exports — superseded by markdown JSON. |
| `archive/message-fetches/*.json`       | A stale Slack export staging copy.                                |

---

## n8n / RAG Bot bot

The bot itself lives in n8n; the exported workflow is `n8n/n8n-workflow.json`. It's **not** a Vector Store Tool agent — it's an explicit retrieval pipeline so a small JavaScript Code node can boost results between Pinecone and the LLM:

```
[Slack Webhook] → [Bot vs. User] → [Fetch Slack Thread] → [Parse Context]
  → [Classifier: question?] → [Pinecone top 20] → [Metadata Boost (Code)]
  → [top 10 chunks] → [RAG Bot agent] → [Send thread reply]
```

The boost code keys on `has_primary_tag` (×1.40), `has_trusted` (×1.30), plus smaller multipliers for `has_images`, `synth`, and multi-SME threads. The node returns a **single item** with a `chunks` array (not 10 items, which would cause the agent to run 10× and break pairing); downstream references use `.first()` instead of `.item`.

Implementation notes and copy-paste snippets:

- `n8n/docs/n8n-ranking-guide.md` — full guide, including why we don't use Cohere
- `n8n/docs/n8n-code-snippets.md` — ready-to-paste JS for the Code node

---

## Project structure

```
recruitment/
├── README.md                  # This file — human-facing pipeline guide
├── CLAUDE.md                  # LLM-agent-oriented version of the same spec
├── GENERICIZATION_PLAN.md     # Audit of use-case-specific assumptions + roadmap
├── .env.example               # Committed seed: op:// refs + sensible plain defaults
├── run                        # Wrapper: `op run --env-file=.env -- "$@"`
├── requirements.txt
│
├── contextual_retrieval.py    # Anthropic-style chunk-context helper (shared by ingest)
├── styling.py                 # Terminal banner/section/summary helpers (shared)
│
├── ingest/                    # All scripts that write to Pinecone or transform data
│   ├── fetch-all-messages.py  # Slack step 1 — API pull → data/slack/slack_<ID>.json
│   ├── slack-to-pc.py         # Slack step 3 — embed + upsert (+ --namespace, --include-bots)
│   ├── scrape-articles.py     # Articles step 0 — live help-center crawl
│   ├── clean-articles-json.py # Articles step 1 — strip noise patterns
│   ├── articles-to-markdown.py# Articles step 2 — LlamaParse (costs credits)
│   └── articles-to-pc.py      # Articles step 4 — header-aware chunk + upsert (+ --namespace)
│
├── diagnostics/               # Read-only inspections — print stats, don't mutate
│   ├── p90-calc-slack.py      # Slack thread length percentiles
│   └── p90-calc-articles.py   # Article + H2/H3 section size percentiles
│
├── data/                      # Raw + intermediate corpus (gitignored content)
│   ├── slack/                 # Slack JSON dumps
│   └── articles/              # scraped → cleaned → markdown JSON + .cache/
│
├── eval/                      # Retrieval-quality harness — measure changes, don't guess
│   ├── run_eval.py            # Recall@K + MRR against questions.json
│   ├── questions.json         # 20 labeled questions w/ expected doc_id matches
│   ├── README.md              # How to run, interpret, extend
│   └── results/               # Per-run JSON (mostly gitignored)
│
├── experiments/               # Reproducible experiments + their write-ups
│   ├── article_chunk_sweep.py        # Sweep harness: re-ingest at N caps, re-eval
│   ├── article-chunk-cap-sweep.md    # Finding: cap=1000 wins on R@5 (80%) and MRR (0.688)
│   └── bot-self-ingestion-drift.md   # Finding: bot filter ≈ +20 R@1 / +0.113 MRR
│
├── n8n/                       # Bot workflow + system prompt + integration docs
│   ├── n8n-workflow.json                       # Exported workflow (import into n8n)
│   ├── system-prompt-with-citations.md  # Live n8n bot system prompt
│   └── docs/
│       ├── n8n-ranking-guide.md     # Code-boost vs. Cohere; full implementation
│       └── n8n-code-snippets.md     # Boost, RRF, debug — ready to paste
│
└── archive/                   # Past iterations — kept for reference, never run
    ├── pc-init.py             # Old Pinecone sanity stub
    ├── articles/              # Old chunk_plan.json (replaced by header-aware chunker)
    ├── articles-txt/          # Pre-LlamaParse raw articles
    └── message-fetches/       # Old Slack export staging
```

All scripts in `ingest/`, `diagnostics/`, and `experiments/` inject the repo root into `sys.path` at startup so `contextual_retrieval` and `styling` resolve as top-level imports.
