# Recruitment RAG Bot

A small ingestion pipeline that turns two messy sources of internal knowledge — a Slack support channel and a Freshdesk help center — into clean, searchable vectors in Pinecone. An n8n workflow then queries those vectors to power **Ragnar**, our recruitment Q&A bot.

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
│ 1. fetch-all-messages.py │                │ 1. clean-articles-json.py       │
│  Slack API → JSON dump   │                │  scraped → cleaned (strip noise)│
└────────────┬─────────────┘                └─────────────────┬───────────────┘
             │                                                │
             ▼                                                ▼
   slack_<CHANNEL_ID>.json              articles/cleaned_help_articles.json
             │                                                │
             ▼                                                ▼
┌──────────────────────────┐                ┌─────────────────────────────────┐
│ 2. p90-calc.py (optional)│                │ 2. articles-to-markdown.py      │
│  Diagnose thread sizes   │                │  LlamaParse: images → markdown  │
│  → suggest CHUNK_CHARS   │                │  (costs LlamaParse credits)     │
└────────────┬─────────────┘                └─────────────────┬───────────────┘
             │                                                │
             │                                                ▼
             │                          articles/markdown_help_articles.json
             │                                                │
             │                                                ▼
             │                            ┌─────────────────────────────────┐
             │                            │ 3. articles/p90-calc.py (opt.)  │
             │                            │  Diagnose H2/H3 section sizes   │
             │                            │  → validate CHUNK_CHARS         │
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
                       │   (Ragnar bot)       │
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
| `OPENAI_API_KEY` | `op://Employee/uw6soelyqjqerwkogprxr7t4ia/api key` |
| `SLACK_TOKEN` | `op://Employee/fe7qwdxozznsegta7vdtkeyv7m/credential` |
| `PINECONE_API_KEY` | `op://Employee/575iu6gscslfu6dbmtgzvpi6hy/credential` |
| `LLAMA_CLOUD_API_KEY` | `op://Employee/lnhrdmv73rtdgb6ew53wqhmewu/credential` |

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
./run python fetch-all-messages.py
./run python slack-to-pc.py
./run python articles-to-pc.py
```

If `.env` contains *only* plain values (no `op://` refs), you can skip `./run` and call `python <script>.py` directly — `python-dotenv` loads `.env` natively. Either path works.

For one-off overrides without editing `.env`, drop them in `.env.local` (gitignored) or prefix the call with `FOO=bar ./run python ...`.

---

## Slack track — step by step

### Step 1 — pull messages from Slack

```bash
./run python fetch-all-messages.py
```

Reads `SLACK_TOKEN` and `SLACK_CHANNEL_ID` from `.env`. Walks the channel, follows every thread, and writes the result to `message-fetches/slack_<CHANNEL_ID>.json`. Handles pagination and rate limits.

After it finishes, **move the JSON file to the repo root** (or point `SLACK_JSON_PATH` at it):

```bash
mv message-fetches/slack_<CHANNEL_ID>.json .
```

### Step 2 *(optional)* — sanity-check chunk size

```bash
./run python p90-calc.py
```

Looks only at threads tagged with the `:recruitment:` reaction, computes their character-length distribution, and prints a suggested `CHUNK_CHARS` / `CHUNK_OVERLAP`. Skip this unless you've changed the corpus shape.

### Step 3 — embed and upload to Pinecone

```bash
./run python slack-to-pc.py
```

For each `:recruitment:`-tagged thread, this script writes **two kinds of vectors**:

- **One synthetic summary** that joins the question (parent message) with the best two replies. Trusted users' replies are favored. This vector is what the bot retrieves when it wants a clean question/answer pair.
- **Full-thread chunks**, char-windowed with overlap, so longer back-and-forth conversations don't get lost.

Every vector carries metadata that the n8n bot uses for ranking: `has_trusted`, `has_recruitment_reaction`, `trusted_count`, `permalink`, etc.

---

## Help center track — step by step

The articles arrive as scraped HTML/JSON, full of cookie banners and screenshots. They go through three transforms before they're worth embedding.

### Step 1 — clean the scraped JSON

```bash
./run python clean-articles-json.py
```

Reads `articles/scraped_help_articles.json`. Strips repeated noise (cookie text, "Was this article helpful?" footers, navbar logos), tracks the position of every screenshot, and writes `articles/cleaned_help_articles.json`.

### Step 2 — convert to LlamaParse markdown

```bash
./run python articles-to-markdown.py
```

This is the expensive step (it calls the LlamaParse cloud API on every embedded screenshot to turn UI images into structured markdown). Output: `articles/markdown_help_articles.json`. **Don't re-run unless the source articles changed.**

### Step 3 *(optional)* — validate chunk size against the new corpus

```bash
./run python articles/p90-calc.py
```

Reports the size distribution of whole articles **and** of the H2/H3 sections inside them. The H2/H3 distribution is what actually matters — `CHUNK_CHARS` should sit near the section p90 so most sections become a single chunk and only the long ones get char-split.

### Step 4 — embed and upload to Pinecone

```bash
./run python articles-to-pc.py
```

Splits each article's markdown on `## ` and `### ` headers. Sections that fit under `CHUNK_CHARS` become a single chunk. Sections that don't get char-windowed with `CHUNK_OVERLAP`. Before each article uploads, the script deletes any existing vectors with the same `doc_id`, so re-runs are idempotent.

---

## Why header-aware chunking?

The earlier version of this pipeline used adaptive char chunking driven by `chunk_plan.json` — every article was bucketed as either "standard" (2000/400) or "long_doc" (6000/1200) based on its raw character count. That config is now in `archive/articles/chunk_plan.json` for reference.

We replaced it with **markdown-header-aware chunking** for one reason: now that LlamaParse gives us real markdown with `##` and `###` structure, splitting on headers produces chunks that each describe one thing. A char-window split would routinely cut a section in half, sending half the explanation to one chunk and half to another — so retrieval would pull a fragment that doesn't fully answer the question. Header splitting puts the boundaries where humans already drew them.

The char cap (`CHUNK_CHARS`) only kicks in for unusually long sections, which is rare. Run `articles/p90-calc.py` if you want to see the distribution for the current corpus.

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

## n8n / Ragnar bot

The bot itself lives in n8n. It queries Pinecone with the user's question, applies a small JavaScript Code node that boosts results based on `has_trusted` and `has_recruitment_reaction`, takes the top 5, and feeds them to the LLM along with the system prompt in `ragnar-system-prompt-with-citations.md`.

Implementation notes and copy-paste snippets:

- `docs/n8n-ranking-guide.md` — full guide, including why we don't use Cohere
- `docs/n8n-code-snippets.md` — ready-to-paste JS for the Code node

---

## File reference

| Path                                       | Role                                                |
| ------------------------------------------ | --------------------------------------------------- |
| `fetch-all-messages.py`                    | Slack step 1 — API pull                             |
| `slack-to-pc.py`                           | Slack step 3 — embed + upsert                       |
| `p90-calc.py`                              | Slack chunk-size diagnostic                         |
| `clean-articles-json.py`                   | Articles step 1 — clean scraped JSON                |
| `articles-to-markdown.py`                  | Articles step 2 — LlamaParse to markdown            |
| `articles-to-pc.py`                        | Articles step 4 — embed + upsert (header-aware)     |
| `articles/p90-calc.py`                     | Articles chunk-size diagnostic                      |
| `articles/scraped_help_articles.json`      | Pipeline input                                      |
| `articles/cleaned_help_articles.json`      | After step 1                                        |
| `articles/markdown_help_articles.json`     | After step 2 — what gets embedded                   |
| `ragnar-system-prompt-with-citations.md`   | Live n8n bot system prompt                          |
| `docs/`                                    | n8n integration notes                               |
| `archive/`                                 | Past iterations, kept for reference                 |
| `CLAUDE.md`                                | Agent-oriented spec of the same pipeline            |
