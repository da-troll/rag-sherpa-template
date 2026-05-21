# How to build your eval question set

The eval harness (`eval/run_eval.py`) is the only way you'll know whether a change actually helped retrieval. Without a labeled question set, "did this improve recall?" becomes vibes-driven. With one, every chunking tweak, prompt change, or boost-weight adjustment becomes empirically falsifiable.

The template ships with three synthetic questions (`eval/questions.json`) matched to the three sample articles in `data/articles/markdown_help_articles.sample.json`. That's enough to verify the harness *runs* but nowhere near enough to draw conclusions from. Replace them with your own set as soon as you have real data ingested.

## Schema

```json
[
  {
    "id": "q001",
    "question": "How do I create my first widget?",
    "match": { "doc_id": "hc::getting_started::a1b2c3d4e5f6" },
    "notes": "Onboarding question — answered by 'Creating your first widget'."
  }
]
```

Required fields per entry:

- **`id`** — short stable string (`q001`, `q002`, …). Lets you cross-reference across runs.
- **`question`** — the user-facing question, exactly as you'd expect it to be asked. Don't pre-canonicalize spelling, capitalization, or punctuation — your eval should reflect realistic queries.
- **`match.doc_id`** — the doc_id of the article that *should* be retrieved. For help-center articles, doc_ids look like `hc::<article_key>::<sha1_prefix>`. Easiest way to find one: run `ingest/articles-to-pc.py` once and inspect Pinecone, or `print(create_doc_id("your_article_key"))` from a Python REPL.
- **`notes`** — free text reminder for future you about *why* this question was chosen. Crucial when results regress and you can't remember what edge case the question was probing.

## How many questions to label

- **3–5**: smoke-test only. Tells you the harness runs and your top doc is roughly findable.
- **15–25**: minimum for real signal. Recall@5 differences of ~5–10 points become meaningful (not noise).
- **50+**: confident A/B between two ingestion variants. Below this, single-question score flips can swing the percentages.

The original tenant this template was built from labeled 20 questions and got reliable signal — that's a reasonable target.

## How to choose questions

Pick questions across these axes:

1. **Specific vs. generic.** "What's the password reset URL?" (specific — should retrieve one chunk) and "How does access control work in this product?" (generic — should retrieve several related chunks).
2. **Paraphrase variation.** Include two questions about the same article with different phrasing. Catches embedding fragility.
3. **Hard negatives.** Pick a question where the *right* article and a *plausibly-related-but-wrong* article both exist in your corpus. Tests whether your retrieval can distinguish them.
4. **Edge cases.** Questions where the answer spans multiple H2 sections of one article. Tests your chunk-overlap settings.
5. **Real user questions.** If you have logs from a deployed bot, sample 5–10 actual user queries and label them. Synthetic questions written by the same person who built the corpus tend to over-fit your assumptions.

## How to label a question

Read your corpus. Open the article that best answers the question. Find its `doc_id`. That's the match.

If two articles both arguably answer the question, pick the *most direct* one as the canonical match — but consider whether the question is genuinely ambiguous and should be reworded.

If no article in your corpus answers it, the question shouldn't be in the eval (yet). Either remove it or write the missing article first.

## Re-labeling after re-ingest

`doc_id` is derived from the article key (`hc::<key>::<sha1(key)[:12]>`). If you rename article keys (e.g., during a help-center URL restructure), the sha1 prefix changes and every match in your eval set stale. Re-run the doc_id derivation and update `eval/questions.json`. Plan ahead: don't rename keys casually.

## How to interpret results

`eval/run_eval.py` prints recall@{1,3,5,10} and MRR@10. The numbers themselves don't matter — *changes* do.

- Before any tuning: establish a baseline. Save the output to `eval/results/baseline.json` (this path is gitignored except for `sample.json`).
- After each change: re-run, compare to baseline. A 3-point R@5 improvement on a 20-question set is real signal. Less than that is probably noise — re-run with the same config to confirm.
- MRR is more sensitive to rank-1 changes than recall@5. Watch both.

## Sample result

See `eval/results/sample.json` for the JSON shape the runner produces. Per-question detail makes it easy to spot which questions regressed when a global metric moves.
