# Importing the n8n workflow

This template ships a sanitized n8n workflow (`n8n-workflow.json`) that you import into your own n8n instance and wire up to your own credentials. The workflow is NOT a Vector Store Tool agent — it's an explicit retrieval pipeline so a JavaScript Code node can re-rank Pinecone results before the LLM sees them.

```
[Slack Webhook] → [Bot vs. User] → [Fetch Slack Thread] → [Parse Context]
  → [Classifier: question?] → [Pinecone top 20] → [Metadata Boost (Code)]
  → [top 10 chunks] → [RAG Agent] → [Send thread reply]
```

This guide walks you through getting from a fresh import to a working workflow in under 15 minutes.

---

## 1. Import the workflow

In n8n: **Workflows → Import from File** → select `n8n-workflow.json` from this repo.

You'll see 20 nodes connected. Several will show "Credentials not configured" warnings — that's expected, you'll fix them in step 2.

## 2. Create the four credential entries

The workflow references credentials by **name** (not by ID — IDs are stripped from the template). When the names match, n8n auto-wires them.

Create these credentials in **Credentials → New**:

| Credential type | Required name | What to put in it |
| --- | --- | --- |
| **OpenAI** | `YOUR_OPENAI_CREDENTIAL` | An OpenAI API key (used for embeddings, classification, and the answer LLM) |
| **Pinecone** | `YOUR_PINECONE_CREDENTIAL` | Your Pinecone API key |
| **Slack OAuth2 API** | `YOUR_SLACK_OAUTH_CREDENTIAL` | A Slack app token with `chat:write`, `chat:write.public`, `conversations:read`, `conversations:history`, `users:read` scopes |

The exact names matter — if you call your OpenAI credential something else, you'll need to manually re-select it in each node that uses it (there are four: `Embeddings OpenAI (Retrieval)`, `OpenAI Chat Model (Agent Brain)`, `OpenAI Chat Model (Classification Call)`, and the Pinecone node uses the embeddings model implicitly).

After creating each credential, **re-open the workflow** — n8n auto-binds when the placeholder name matches.

## 3. Set the Slack channel

Open these two nodes and replace `C0000000000` with your actual Slack channel ID:

- **Fetch Slack Thread** → "Channel ID" parameter
- **Send thread reply** → "Channel" parameter

(Alternatively: set an n8n environment variable `SLACK_CHANNEL_ID` and change both node parameters to `={{ $env.SLACK_CHANNEL_ID }}`.)

## 4. Set the Pinecone namespace

Open the **Pinecone Vector Store (Retrieval)** node. Replace `your-namespace` with your actual Pinecone namespace (the same one you ingested vectors into via `ingest/articles-to-pc.py --namespace ...`).

## 5. Set the Pinecone index

Open the same **Pinecone Vector Store (Retrieval)** node. Replace `your-pinecone-index` with your actual index name. The index must have **dimension 1536** if you're using OpenAI's `text-embedding-3-small` (the template's default).

## 6. Set the bot's own Slack user ID (filter loop)

Open **Bot vs. User Check**. There's a condition checking the message author against the bot's Slack user ID — currently a placeholder. Replace it with your bot's actual Slack user ID. Find this by visiting the bot's profile in Slack → "View full profile" → look at the URL or member ID.

This filter prevents the bot from responding to its own messages — without it, the bot can answer its own answer and loop forever.

## 7. Set the Webhook path

Open the **Webhook** node. Set a webhook path of your choice (e.g. `slack-event`). After saving and activating the workflow, n8n shows the public URL.

In Slack: **App → Event Subscriptions** → paste the webhook URL. Subscribe to `message.channels` events.

## 8. (Optional) Customize the system prompt

The **RAG Agent** node's "System Message" parameter holds a generic prompt that references `RAG Bot` as the bot name. To customize:

- Open `prompts/persona.example.md`, edit bot name, scope, voice.
- Concatenate `persona.example.md` + `prompts/citation-format.md` + `prompts/source-descriptions/*.md` into one string.
- Paste into the **RAG Agent** node's "System Message" parameter.

This is the most impactful single edit for changing the bot's personality. See `prompts/README.md`.

## 9. (Optional) Tune boost weights

The **Metadata Boost** node has a CONFIG block at the top of its JavaScript:

```javascript
const W_PRIMARY_TAG        = 1.40;
const W_TRUSTED_AUTHOR     = 1.30;
const W_MULTI_TRUSTED      = 1.05;
const W_SYNTHETIC_SUMMARY  = 1.10;
const W_HAS_IMAGES         = 1.05;
const FINAL_TOP_K          = 10;
```

These are starting defaults. Re-run `eval/run_eval.py` after tweaks to measure impact — never tune on vibes.

## 10. Activate and test

Click **Active** in the top-right of the workflow editor. Send a Slack message in your configured channel. Watch the n8n execution log.

Common first-run issues:

| Symptom | Likely cause |
| --- | --- |
| Webhook fires but no Pinecone hits | Namespace mismatch (step 4) or no vectors ingested yet |
| "Credentials not configured" persists | Credential name doesn't match placeholder (step 2) |
| Bot responds to its own messages → infinite loop | Bot user ID not set (step 6) |
| Empty reply, no agent run | Classifier returned non-`question` — that's the routing working as intended |
| Double `<@user>` mention in reply | Agent prompt is prepending `<@...>` itself — remove that instruction from the system prompt |

---

## What happens if you skip some steps

The minimum to get a reply: steps 1, 2, 3, 4, 5, 7. The bot will answer using generic prompts and default boosts — it'll work, just not personalized.

Steps 6, 8, 9 are quality improvements you can defer until after the initial smoke test.
