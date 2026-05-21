# Prompt templates

The RAG Agent's system prompt is split across several small files in this directory. The n8n workflow consumes them either by **inlining the concatenated result** into the agent node's "System Message" parameter (simpler — what the template ships with), or by **reading them dynamically at run time** (more flexible — requires n8n filesystem access; see "Dynamic loading" below).

## Files

| File | Role | Edit per tenant? |
| --- | --- | --- |
| `persona.example.md` | Bot name, scope, voice, personality | **Yes** — this is the most-tenant-specific file. Customize first. |
| `citation-format.md` | How the LLM cites retrieved chunks | No — generic, depends on metadata field names. |
| `source-descriptions/slack.md` | How Slack-source vectors look + how to cite them | No (unless you change the Slack ingest) |
| `source-descriptions/articles.md` | How article-source vectors look + how to cite them | No (unless you change the article ingest) |

## Concatenation order

Build the final system prompt by concatenating in this exact order:

```
1. persona.example.md
2. citation-format.md
3. source-descriptions/slack.md       (only if you have Slack vectors)
4. source-descriptions/articles.md    (only if you have article vectors)
```

Drop a `---` separator between each. Paste the result into the n8n **RAG Agent** node's "System Message" parameter.

A simple shell helper:

```bash
( cat prompts/persona.example.md; echo; echo '---'; echo;
  cat prompts/citation-format.md; echo; echo '---'; echo;
  cat prompts/source-descriptions/slack.md; echo; echo '---'; echo;
  cat prompts/source-descriptions/articles.md ) | pbcopy   # macOS clipboard
```

Then paste into the n8n node.

## Dynamic loading (optional)

If your n8n instance has filesystem access (most self-hosted setups do), you can have a Code node at the start of the workflow concatenate these files at runtime. Pros: prompt edits don't require re-pasting into n8n. Cons: requires that the repo files are accessible from the n8n host.

Sample loader (drop into a Code node before the RAG Agent):

```javascript
const fs = require('fs');
const path = require('path');
const base = '/path/to/this/repo/prompts';

const parts = [
  fs.readFileSync(path.join(base, 'persona.example.md'), 'utf8'),
  fs.readFileSync(path.join(base, 'citation-format.md'), 'utf8'),
  fs.readFileSync(path.join(base, 'source-descriptions/slack.md'), 'utf8'),
  fs.readFileSync(path.join(base, 'source-descriptions/articles.md'), 'utf8'),
];
return [{ json: { systemPrompt: parts.join('\n\n---\n\n') } }];
```

Then reference `={{ $('Load Prompt').first().json.systemPrompt }}` in the agent node.

## Adding a new source

When you add a new source type (e.g. Notion, Confluence, GitHub issues), ship a matching `source-descriptions/<source>.md` that describes:

1. What a vector from this source looks like (its metadata fields, content shape).
2. How the LLM should cite a chunk from this source (which field to link to, how to format the link).

Add it to the concatenation order. No code changes needed in the n8n agent node.
