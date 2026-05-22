```
██████╗░░█████╗░░██████╗░░░░░███████╗██╗░░██╗███████╗██████╗░██████╗░░█████╗░
██╔══██╗██╔══██╗██╔════╝░░░░░██╔════╝██║░░██║██╔════╝██╔══██╗██╔══██╗██╔══██╗
██████╔╝███████║██║░░███╗░░░░███████╗███████║█████╗░░██████╔╝██████╔╝███████║
██╔══██╗██╔══██║██║░░░██║░░░░╚════██║██╔══██║██╔══╝░░██╔══██╗██╔═══╝░██╔══██║
██║░░██║██║░░██║╚██████╔╝░░░░███████║██║░░██║███████╗██║░░██║██║░░░░░██║░░██║
╚═╝░░╚═╝╚═╝░░╚═╝░╚═════╝░░░░░╚══════╝╚═╝░░╚═╝╚══════╝╚═╝░░╚═╝╚═╝░░░░░╚═╝░░╚═╝

                          A N S W E R   A N Y T H I N G
```

# Multi-source RAG Slack bot template

A starter template for building a domain Q&A bot that ingests one or more text-based content sources into Pinecone and serves answers via an n8n workflow. Two source types ship by default:

1. **Slack threads** — discussions from a support channel (questions + answers, often with curation reactions).
2. **Structured articles** — markdown documentation (scraped from a help center, exported from Notion/Confluence, or any markdown corpus).

The pipeline is **manual and step-by-step**: each stage is a separate Python script that reads a file, does one thing, and writes the next file. You run them in order. No orchestration, no scheduler, no surprises.

The bot itself runs in n8n: retrieve top-K from Pinecone, re-rank with a small JS Code node using tenant-defined metadata signals, hand the top 10 chunks to an LLM, post the answer back to Slack.

---

## Project structure

```
rag-sherpa-template/
├── README.md                  # This file — human-facing template guide
├── CLAUDE.md                  # LLM-agent-oriented version of the same spec
├── LICENSE                    # MIT
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
├── data/                      # Your corpus lands here (contents gitignored;
│   ├── slack/                 #  ships with empty dirs + sample article JSON
│   └── articles/              #  for the smoke test)
│
├── eval/                      # Retrieval-quality harness — measure changes, don't guess
│   ├── run_eval.py            # Recall@K + MRR against questions.json
│   ├── questions.json         # 3 starter synthetic questions (REPLACE with your own)
│   ├── HOW_TO_LABEL.md        # Guide to building your own question set
│   ├── README.md              # How to run, interpret, extend
│   └── results/sample.json    # Example output format (other runs are gitignored)
│
├── experiments/               # Reproducible experiments + their write-ups
│   ├── article_chunk_sweep.py        # Sweep harness: re-ingest at N caps, re-eval
│   ├── article-chunk-cap-sweep.md    # Methodology + finding from the original corpus
│   └── bot-self-ingestion-drift.md   # Bot-filter A/B that proved the filter is load-bearing
│
├── prompts/                   # Modular system-prompt parts (concat → n8n agent node)
│   ├── persona.example.md           # Bot name, scope, voice — TENANT-OWNED
│   ├── citation-format.md           # Generic citation pattern
│   ├── source-descriptions/
│   │   ├── slack.md                 # How Slack vectors look + how to cite
│   │   └── articles.md              # How article vectors look + how to cite
│   └── README.md                    # Concat order + dynamic-loading option
│
└── n8n/                       # Bot workflow + Slack app + integration docs
    ├── n8n-workflow.json            # Exported workflow (import into n8n)
    ├── slack-app-manifest.json      # Pasteable Slack app manifest (minimum scopes)
    ├── README-import.md             # End-to-end setup guide (Slack app → n8n → live)
    └── docs/
        ├── n8n-ranking-guide.md     # Code-boost vs. Cohere; full implementation
        └── n8n-code-snippets.md     # Boost, RRF, debug — ready to paste
```

All scripts in `ingest/`, `diagnostics/`, and `experiments/` inject the repo root into `sys.path` at startup so `contextual_retrieval` and `styling` resolve as top-level imports.

---

## Make this your own

Five edits get you from a fresh clone to a working bot:

1. **Configure secrets and tenant values.** `cp .env.example .env`, then fill in placeholders (`<YOUR_VAULT>` 1Password references or plain values, your Pinecone index/namespace, your Slack channel ID and workspace host, your trusted-user Slack IDs).
2. **Customize the bot persona.** Edit `prompts/persona.example.md` — bot name, scope statement, voice. This is the single highest-leverage edit.
3. **Wire up n8n credentials.** Import `n8n/n8n-workflow.json` into your n8n instance; create three credentials with the placeholder names (`YOUR_OPENAI_CREDENTIAL`, `YOUR_PINECONE_CREDENTIAL`, `YOUR_SLACK_OAUTH_CREDENTIAL`). See `n8n/README-import.md` for the full 10-step walkthrough.
4. **Ingest your content.** Run the Slack and articles pipelines (described below). Optionally re-tune chunk sizes against your own corpus with `diagnostics/p90-calc-*.py`.
5. **Measure quality.** Replace `eval/questions.json` with a labeled set from your own data (see `eval/HOW_TO_LABEL.md`). Run `./run python eval/run_eval.py` to baseline retrieval quality before/after changes.

For deeper customization — boost weights, source plugins, prompt extensions — see `CLAUDE.md`.

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
- `tags` — list of curation tag names. Contains the primary tag (e.g. `"verified"`) when the parent or any reply carries the configured `PRIMARY_REACTION_TAG` reaction. This is the **only** curation signal in metadata. Presence-only (not count): additional same-tag reactions are typically other team members echoing the curator — they don't add verification. Other reactions (`:+1:`, `:raised_hands:`, etc.) are explicitly NOT counted — they fire too ambiguously on announcements and sympathy to be reliable.
- `author_roles` — dict of role name → count (e.g. `{"trusted": 2}`). Roles are extensible: add a new entry to `ROLE_DEFINITIONS` in `ingest/slack-to-pc.py` to surface additional role memberships (on-call, leads, escalations) without schema changes.
- `author_role_ids` — same shape as `author_roles` but carries the actual Slack IDs per role. Used for citation-time formatting like "answered by on-call engineer".
- `ts_last` / `ts_first` — for recency decay in boost.
- `permalink` — for citations.

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

## n8n / RAG Bot

The bot itself lives in n8n; the exported workflow is `n8n/n8n-workflow.json`. It's **not** a Vector Store Tool agent — it's an explicit retrieval pipeline so a small JavaScript Code node can boost results between Pinecone and the LLM:

```
[Slack Webhook] → [Bot vs. User] → [Fetch Slack Thread] → [Parse Context]
  → [Classifier: question?] → [Pinecone top 20] → [Metadata Boost (Code)]
  → [top 10 chunks] → [RAG Bot agent] → [Send thread reply]
```

The boost code keys on `tags` containing the primary tag (×1.40), `author_roles.trusted > 0` (×1.30), `author_roles.trusted > 1` (×1.05 multi-trusted), plus smaller multipliers for `has_images` and `synth`/`thread_synth`. The node returns a **single item** with a `chunks` array (not N items, which would cause the agent to run N× and break pairing); downstream references use `.first()` instead of `.item`.

Implementation notes and copy-paste snippets:

- `n8n/docs/n8n-ranking-guide.md` — full guide, including why we don't use Cohere
- `n8n/docs/n8n-code-snippets.md` — ready-to-paste JS for the Code node

