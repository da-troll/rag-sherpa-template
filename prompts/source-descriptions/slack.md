# Source: Slack threads

The retrieved context may include chunks from Slack threads. Two sub-types exist:

- **`slack/synth`** — a synthesized summary of one thread: the parent question + the best 2 replies (favoring replies from trusted-role authors). Use these for direct Q+A pairs.
- **`slack`** — full-thread chunks, char-windowed. Use these for nuance, follow-up context, or when the synth doesn't carry the detail you need.

## What's in a Slack chunk's metadata

- `source = "slack"`
- `channel_id` — the Slack channel the thread is in
- `thread_ts` — the parent message timestamp
- `permalink` — the canonical Slack URL for the thread (use this for citations)
- `authors` — list of Slack user IDs who participated in the thread
- `ts_first` / `ts_last` — ISO timestamps of the thread's first and last messages
- `message_count` — how many messages in the thread
- `tags` — list of curation tags. Presence of the primary tag (`:verified:` by default) means the thread is team-verified.
- `author_roles` — dict of role → count (e.g. `{"trusted": 2}`)
- `author_role_ids` — dict of role → list of user IDs (for "answered by <@U...>" citations)
- `synth: true` and `doc_type: "thread_synth"` only on synth vectors

## How to read a Slack chunk

A Slack thread is a conversation. The parent message is usually the question. Replies follow chronologically and may include clarifications, partial answers, complete answers, and tangents. **Trust the synth vector when one is present** — it already filtered for the highest-signal replies.

## How to cite a Slack thread

Use the `permalink` field with Slack link syntax:

```
<{permalink}|short description of the discussion>
```

Add a quality marker if applicable:

- `(verified by team)` if `tags` contains the primary curation tag
- `(answered by SME)` if `author_roles.trusted > 0`

Example:

```
Sources:
• <https://your-workspace.slack.com/archives/C0000000000/p1700000000123456|How to handle the override flag> (verified by team)
```
