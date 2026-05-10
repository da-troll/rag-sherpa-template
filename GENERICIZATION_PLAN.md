# Genericization Plan

This document audits the current codebase for use-case-specific assumptions ("Simployer recruitment Slack + Freshdesk help center → Pinecone → Ragnar bot") and lays out what would need to be abstracted to turn it into a reusable RAG ingestion toolkit.

Scope: **identification and design**, not implementation. The goal is to produce a clear-eyed blueprint a future contributor (or LLM) can execute against.

---

## 1. Severity rubric

Every finding is tagged with one of these:

- **🔴 Hard-coded** — a literal string, magic number, or branded identifier embedded in code that would break a new use case until edited.
- **🟠 Coupled** — a concept that's correctly parameterized but conflated with the use case (e.g., a single `TRUSTED_USERS` env var assumes one role of trust).
- **🟡 Architectural** — a design assumption that limits extensibility even if no literal needs replacing (e.g., "one Slack channel, one help center, one namespace").

---

## 2. Findings, organized by layer

### 2.1 Domain & branding strings

| Where | What | Severity |
| --- | --- | --- |
| `slack-to-pc.py:12` | Default `WORKSPACE_HOST = "https://simployer.slack.com"` | 🔴 |
| ~~`fetch-all-messages.py:9`~~ | ~~Live `xoxb-` Slack token committed in source~~ — **fixed**: now reads `os.getenv("SLACK_TOKEN")` from a 1Password reference resolved by `./run` (`op run`) | ✅ |
| `slack-to-pc.py:13–14` | Default Pinecone index `n8n-recruitment-rag-bot-1536` and namespace `recruitment-rag-2` | 🔴 |
| `slack-to-pc.py:11` | Default `SLACK_JSON_PATH = "slack_C08MGP5N8DA.json"` (channel ID baked into filename) | 🔴 |
| `clean-articles-json.py:18–27` | `EXCLUDE_IMAGE_PATTERNS` references `freshworks`, `simployer.*logo` | 🔴 |
| `clean-articles-json.py:30–74` | `NOISE_PATTERNS` is Freshdesk + Simployer specific (Norwegian/Swedish support footers, "Simployer One - Recruitment" navigation, Freshdesk cookie table, etc.) | 🔴 |
| `clean-articles-json.py:78–82` | `clean_title()` strips a literal `"Simployer One - Recruitment - "` prefix and `" : Simployer - Customer Support Portal"` suffix | 🔴 |
| `ragnar-system-prompt-with-citations.md` | Bot persona, tone, and capability description hard-tied to "Ragnar" / recruitment | 🔴 |
| `CLAUDE.md`, `README.md` | Documentation written around the recruitment use case throughout | 🟠 |
| Repo dir name `recruitment/` | Project root signals single-purpose usage | 🟠 |

**Action:** Move every literal into per-tenant config (YAML or JSON), not `.env`. `.env` is fine for secrets; tenant-specific *content rules* (noise patterns, title transforms, branding) belong in a structured config file the loader reads at startup.

---

### 2.2 Source-specific logic that should be plugins

The current scripts are **hard-coupled to two specific source types**. Adding a third (e.g., Notion, Confluence, Zendesk, Discord) means writing a new script from scratch and copy-pasting the embed/upsert plumbing.

| Concern | Current state | Severity |
| --- | --- | --- |
| Slack reaction tagging | `parent_has_recruitment` / `has_recruitment_reaction` literally search for `name == "recruitment"` (`slack-to-pc.py:83–94`) | 🔴 |
| The boolean `has_recruitment_reaction` is also a top-level **metadata field** on every Slack vector | The field name is the tag name | 🔴 |
| "Trusted user" concept | Singular env var `TRUSTED_USERS`, a single role. Real teams have multiple roles (SMEs, leads, on-call, escalations) with different boost weights | 🟠 |
| "Best answer" heuristic | `slack-to-pc.py:291–299` — top 2 replies, ranked by trusted-status then text length | 🟠 |
| `answer_like` heuristic | `slack-to-pc.py:221` — magic number `len(text) >= 200` | 🔴 (magic number) |
| Synth doc template | `slack-to-pc.py:307–312` — hardcoded `"Question (parent):\n...Best answers:\n..."` template assumes Q&A | 🟠 |
| Vector ID schemes | `slack:{channel}:thread:{ts}:chunk:{i}`, `help:{doc_id}:chunk:{i}`, `hc::` prefix — encoded by hand in two different scripts | 🟠 |
| `source` metadata values | Free-form strings `"slack"` / `"helpcenter"`. No registry, no typing. | 🟠 |
| Freshdesk-specific HTML cleaner | `clean-articles-json.py` is one giant Freshdesk-tuned regex bundle | 🔴 |
| LlamaParse for images | `articles-to-markdown.py:32–41` — model + parse mode hardcoded; no other image strategies possible | 🟠 |

**Action:** Define a **`Source` plugin contract**. Each source plugin owns: its loader, its cleaner, its chunker (or chunker selector), its metadata schema extension, and its tag-to-boost mapping. The orchestrator calls plugins polymorphically. See target architecture in §4.

---

### 2.3 Metadata schema is tag-named, not tag-typed

Today, every Slack vector carries `has_recruitment_reaction` as a literal field. If a future tenant cares about `:bug:` or `:howto:` reactions, they have to either:

- Rename the field (breaking the n8n boosting code), or
- Add another field per tag (unbounded schema growth).

| Issue | Severity |
| --- | --- |
| Boost-signal fields named after the signal value (`has_recruitment_reaction`, `has_trusted`) instead of typed (`tags: ["recruitment"]`, `roles: ["sme"]`) | 🟠 |
| n8n boost code presumably keys on those exact field names (see `docs/n8n-ranking-guide.md`) | 🟠 |
| `trusted_repliers` / `trusted_count` — same problem at lower severity | 🟠 |

**Action:** Replace tag-as-field with **tag-as-value**:

```json
{
  "tags": ["recruitment"],
  "author_roles": ["sme"],
  "role_count": {"sme": 1, "lead": 0}
}
```

Boost configuration moves out of n8n JS and into a declarative config the boost step reads:

```yaml
boost:
  - { when: "tags contains 'recruitment'", weight: 1.5 }
  - { when: "role_count.sme > 0", weight: 1.3 }
```

---

### 2.4 External service coupling

| Service | Where assumed | Replaceable? | Severity |
| --- | --- | --- | --- |
| OpenAI embeddings | Both `*-to-pc.py` scripts call `OpenAI().embeddings.create` directly | Should be behind an `Embedder` interface (OpenAI / Voyage / Cohere / local sentence-transformers) | 🟠 |
| Pinecone | Both scripts import `Pinecone` directly and call `index.upsert` / `index.delete` / `index.fetch` | Should be behind a `VectorStore` interface (Pinecone / Qdrant / Weaviate / Chroma / pgvector) | 🟠 |
| LlamaParse | `articles-to-markdown.py` directly instantiates `LlamaParse(...)` | Should be one of several `ImageParser` strategies (LlamaParse / GPT-4V direct / Gemini / skip) | 🟠 |
| 1536 dimension | `articles-to-pc.py:43`, `slack-to-pc.py:46` validate against literal `1536` | Should derive from the configured embedder's reported dimension | 🔴 |
| `text-embedding-3-small` | Default model name in both ingest scripts | Embedder choice should be model-agnostic | 🟠 |
| `sk-proj-` rejection | `slack-to-pc.py:30–31`, `articles-to-pc.py:29–30` | OpenAI-specific; doesn't apply to other providers | 🟠 |

**Action:** Introduce three abstract interfaces — `Embedder`, `VectorStore`, `ImageParser` — and read the concrete impl from config. The 1536 check becomes `assert store.dim == embedder.dim` regardless of backend.

---

### 2.5 Pipeline shape assumptions

| Assumption | Where | Severity |
| --- | --- | --- |
| Exactly **one** Slack channel | `fetch-all-messages.py` reads a single `SLACK_CHANNEL_ID` | 🟡 |
| Exactly **one** help center corpus | `articles-to-pc.py` reads a single `INPUT_JSON` | 🟡 |
| All sources land in **one namespace** | No per-source namespace routing | 🟡 |
| Help articles have meaningful H2/H3 structure | `articles-to-pc.py` chunker assumes markdown headers; falls back to char-window | 🟡 (acceptable, but undocumented if false) |
| Slack threads are Q&A shaped | Synth doc template assumes question + answers | 🟡 |
| One language | Noise patterns and title transforms only handle English Freshdesk pages; cleaner has no i18n awareness | 🟡 |
| One tenant per repo checkout | All config in one `.env`, no tenant scoping | 🟡 |
| Manual orchestration | Each step is run by hand. Fine for now; not a blocker. | 🟡 (intentional) |

**Action:** None of these are urgent. The fix is structural: support **multiple `Source` instances per run**, each with its own config block. A run becomes "ingest tenant X using sources A, B, C". Single-source runs are just `len(sources) == 1`.

---

### 2.6 File and path conventions

| Convention | Issue | Severity |
| --- | --- | --- |
| `slack_<CHANNEL_ID>.json` lives at repo root | Multi-channel = filename collisions are unlikely but the convention is fragile | 🟠 |
| `articles/scraped_help_articles.json`, `articles/cleaned_help_articles.json`, `articles/markdown_help_articles.json` | The folder name `articles/` and the file names hardcode the use case | 🔴 |
| `clean-articles-json.py:14–15` | Input/output paths hardcoded as module-level constants, not args | 🔴 |
| `articles-to-markdown.py:20–21` | Same — `INPUT_JSON` / `OUTPUT_JSON` literal | 🔴 |
| `articles-to-pc.py:14` | Same — `INPUT_JSON = "articles/markdown_help_articles.json"` | 🔴 |
| `archive/` | Fine — already separates dead code | ✅ |

**Action:** All scripts should accept `--config <path>` (and optionally `--source <name>`), with paths derived from config. Use a standard layout like `data/<tenant>/<source>/{raw,cleaned,parsed}.json` — explicit and tenant-scoped.

---

### 2.7 The system prompt

`ragnar-system-prompt-with-citations.md` is the answer-time persona. It:

- Names the bot ("Ragnar")
- Names the domain ("recruitment")
- Describes the supported sources by name ("Slack", "help center articles")
- Defines a citation format
- Probably encodes tone/voice

| Issue | Severity |
| --- | --- |
| Persona, domain, and source list all baked into one file | 🔴 |
| Citation format embedded in prose, not parameterized | 🟠 |

**Action:** Split into:

- `prompts/persona.md` — tenant-specific (bot name, voice, scope statement)
- `prompts/citation-format.md` — generic, references metadata field names
- `prompts/source-descriptions/<source>.md` — one per source plugin, contributed by the plugin

The runtime concatenates these, so adding a new source ships with its own description block.

---

## 3. Quick-win cleanups (do first, low effort)

These remove hardcoded literals without any architectural change. Doing them up front makes the bigger refactor easier to audit.

1. ~~**Remove the live `xoxb-` token** from `fetch-all-messages.py:9`.~~ **Done** as part of the 1Password migration described in §3a below.
2. **Delete the channel-ID-shaped default** for `SLACK_JSON_PATH` (`slack-to-pc.py:11`). Make it required.
3. **Delete the `n8n-recruitment-rag-bot-1536` and `recruitment-rag-2` defaults** for Pinecone index/namespace. Make them required.
4. **Replace `len(text) >= 200`** (`slack-to-pc.py:221`) with an env-driven threshold, and document the heuristic.
5. **Move noise/exclude patterns out of `clean-articles-json.py` source** into `config/cleaners/freshdesk.yaml` (or similar). The Python file becomes a YAML-driven cleaner.
6. **Move LlamaParse model + flags out of `articles-to-markdown.py:32–41`** into config.
7. **Derive `MODEL_DIM`** from the OpenAI client response instead of hardcoding `1536`.

None of these require new abstractions. They're all "lift literals into config".

---

## 3a. Secrets architecture (already in place — must carry into the template)

### Current design

This project resolves **all four API-key secrets** (OpenAI, Slack, Pinecone, LlamaParse) from 1Password via `op run`. Non-secret config (index/namespace names, channel IDs, chunk sizes, trusted-user lists) stays in `.env` as plain values because they're user/team-specific, not sensitive.

The architecture deliberately keeps `.env` as the **single source of truth that scripts read from**:

- No script imports `op` or queries any password manager directly. Every script just calls `os.getenv("X")`.
- `./run` is a thin wrapper: `exec op run --env-file=.env -- "$@"`. It resolves `op://` lines into real env values *before* launching the Python subprocess; plain values pass through unchanged.
- `.env` is gitignored. `.env.example` is the committed seed (op:// refs + sensible defaults for plain config); new clones run `cp .env.example .env`. This means even if a dev pastes plaintext into their local `.env` as the escape hatch, it can't accidentally reach git.
- A user can replace any `op://` line in `.env` with a plain value and it works immediately. Mixing plain values and `op://` refs in the same `.env` is supported.
- If `.env` ends up entirely plain, users can drop `./run` and call `python <script>.py` directly — `python-dotenv` loads `.env` natively. The 1Password CLI is never a hard dependency.

This decoupling matters: it means **the password manager isn't a blocker**. A teammate who can't or won't install `op` can run the project just fine.

Today's literal references (project-specific, vault `Employee`):

- `OPENAI_API_KEY` → `op://Employee/uw6soelyqjqerwkogprxr7t4ia/api key`
- `SLACK_TOKEN` → `op://Employee/fe7qwdxozznsegta7vdtkeyv7m/credential`
- `PINECONE_API_KEY` → `op://Employee/575iu6gscslfu6dbmtgzvpi6hy/credential`
- `LLAMA_CLOUD_API_KEY` → `op://Employee/lnhrdmv73rtdgb6ew53wqhmewu/credential`

Auth: developer's own `op signin` session (Touch ID on macOS). For headless/CI use, swap to `OP_SERVICE_ACCOUNT_TOKEN` — same `op run` invocation, no code changes.

### What needs genericizing for the company template

| Issue | Severity |
| --- | --- |
| The `op://Employee/<item-id>/...` references hardcode one developer's vault and item IDs. | 🔴 |
| There's no documented "shape" for the 1P items a reuser needs to create (category, field labels). Today it's implicit (API_CREDENTIAL with `credential` field, except OpenAI which uses `api key`). | 🟠 |
| The `./run` wrapper hardcodes `op run`. Other teams use `chamber`, `direnv`, `bw` (Bitwarden CLI), `pass`, AWS Secrets Manager, or nothing. | 🟠 |
| Touch ID + 30-min session cadence is a macOS+desktop-app assumption. | 🟠 |

### Proposed template treatment

The template README should be **explicit that 1Password is one option, not a requirement**. The pattern generalizes to any CLI-driven password manager whose `*-run` command resolves a reference syntax in `.env` into env vars before launching a subprocess. Bitwarden's `bws run`, `chamber exec`, `aws-vault exec`, and `direnv` (with `op` plugins) all fit this shape.

1. **Document the contract, not the tool.** State that `.env` may contain reference URIs (e.g., `op://...`, `bws://...`, `chamber://...`) or plain values, and that the chosen `./run` wrapper resolves them. Then ship one reference implementation (`./run` calling `op run`) and explain how to swap.
2. **Provide alternative wrappers** under e.g. `tools/run-op`, `tools/run-bws`, `tools/run-plain`. Picking one is a `cp tools/run-op ./run` away. The Python scripts never know which backend resolved the secrets.
3. **Use item names, not IDs**, in the committed `.env.example`: `op://<YOUR_VAULT>/OpenAI API Key/api key` etc. Document the expected item shape (category = `API_CREDENTIAL`, exact field labels) so reusers either point at existing items or create matching ones from the spec. (`.env` itself is gitignored — see point 4.)
4. **Always preserve the escape hatch.** The README must call out that any reference URI in `.env` can be replaced with a plain value at the user's risk, and that the project still works without any password manager installed. This keeps adoption friction near zero — reusers can set up the project end-to-end on day one and migrate to a secret manager later.
5. **Document the service-account path** for headless / CI / n8n use, but keep it strictly optional.
6. **Keep the "secrets vs config" split.** The four API keys are secrets and belong in a manager (or plain `.env` at user risk). Index names, channel IDs, chunk sizes, trusted-user lists are config and stay plain in `.env`.

This belongs in the genericization roadmap as a **separate workstream** from the source-plugin refactor — the secrets layer is orthogonal to how sources are abstracted.

---

## 4. Target architecture (the abstraction layer)

This is what the codebase should look like *after* genericization. It's a sketch, not a contract — the point is to show how the findings above collapse into a small number of seams.

```
┌────────────────────────────────────────────────────────────────┐
│  config/<tenant>.yaml                                          │
│  ─ embedder, vector_store, namespace                           │
│  ─ sources: [ {type: slack, ...}, {type: freshdesk, ...} ]     │
│  ─ tag_rules, role_rules                                       │
│  ─ prompts (persona, citation, source descriptions)            │
└─────────────────────────────┬──────────────────────────────────┘
                              │
                              ▼
              ┌───────────────────────────────┐
              │   Orchestrator (CLI)          │
              │   `rag ingest <tenant>`       │
              └────────────┬──────────────────┘
                           │
        ┌──────────────────┼─────────────────────┐
        ▼                  ▼                     ▼
   ┌──────────┐      ┌──────────┐          ┌──────────┐
   │ Source A │      │ Source B │   ...    │ Source N │
   │ (Slack)  │      │(Freshdesk)│         │ (...)    │
   └────┬─────┘      └────┬─────┘          └────┬─────┘
        │                 │                     │
        │  Each implements:                     │
        │   ─ load() → raw docs                 │
        │   ─ clean(raw) → text + metadata      │
        │   ─ chunk(text) → chunks              │
        │   ─ tag_extractor(doc) → tags/roles   │
        │   ─ id(doc, chunk) → vector_id        │
        │                                       │
        ▼                                       ▼
              ┌───────────────────────────────┐
              │  Common pipeline:             │
              │  embed(chunks)                │
              │  upsert(vectors, namespace)   │
              └───────────────────────────────┘
                              │
                              ▼
              ┌───────────────────────────────┐
              │  VectorStore (interface)      │
              │  ─ Pinecone / Qdrant / ...    │
              └───────────────────────────────┘
```

### Five seams to introduce

1. **`Source` plugin** — a class implementing `load`, `clean`, `chunk`, `tag_extractor`, `id`. Slack and Freshdesk become two such plugins. Existing scripts are gutted; their guts move into source-specific subclasses.
2. **`Embedder` interface** — abstracts model + dimension. OpenAI is the first impl.
3. **`VectorStore` interface** — abstracts upsert/delete/fetch. Pinecone is the first impl.
4. **`ImageParser` interface** *(optional)* — only used by sources that need it (Freshdesk). LlamaParse is the first impl.
5. **`TagModel`** — typed representation of `tags`, `author_roles`, and per-role counts on every vector. Replaces `has_recruitment_reaction`, `has_trusted`, `trusted_count`, etc.

### Config as the only thing that changes per tenant

A new tenant should require **zero code edits**: write a YAML file, drop in a `prompts/persona.md`, optionally write a tenant-specific cleaner ruleset. If a tenant needs a fundamentally new source type (say, GitHub issues), they ship a new `Source` plugin — but reuse everything else.

---

## 5. Out of scope (deliberately)

- **Multi-tenant runtime isolation.** This plan targets reusability across one-off projects, not a hosted multi-tenant service. Tenants are configs, not runtime objects.
- **Automated orchestration / scheduling.** The current "manual stepping" is a feature for now; cron/Airflow/n8n triggers can wrap the CLI later.
- **Re-architecting the n8n bot itself.** The boost code in n8n needs a one-time rewrite to consume the new typed `tags` / `role_count` schema, but the broader n8n setup is fine.
- **Replacing Pinecone or OpenAI right now.** The interfaces should *enable* swapping, but the first implementation can keep both.

---

## 6. Suggested execution order

If/when this is picked up:

1. **Quick wins (§3).** No new abstractions. Removes the most embarrassing literals.
2. **Lift cleaner rules to YAML.** Single biggest win for source-agnosticism.
3. **Introduce `TagModel` and migrate metadata schema.** This is a breaking change for the n8n boost code; do it before adding new sources.
4. **Extract `Embedder` and `VectorStore` interfaces.** Wrap existing OpenAI/Pinecone code; no behavior change.
5. **Refactor Slack and Freshdesk into `Source` plugins.** This is where the existing scripts get dissolved.
6. **Add a third source as the validation test.** Until a third source actually plugs in cleanly, the abstraction isn't proven.
7. **Rename the repo / publish as a package.** Last step. Don't pre-commit to a name until the architecture stabilizes.
