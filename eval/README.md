# Eval harness

Measures retrieval quality of the RAG pipeline against a labeled question set. Used to compare changes (e.g., contextual retrieval, embedding model upgrades, chunk-size tuning) against a baseline namespace. See `HOW_TO_LABEL.md` for guidance on building your own question set.

## Files

- `questions.json` — labeled questions. Each entry has a `question` and a `match` spec describing which vector's metadata identifies a "correct" hit.
- `run_eval.py` — embeds each question, queries Pinecone in a namespace, reports recall@1/3/5/10 and MRR.
- `results/` — gitignored. Stores per-run JSON dumps with full top-K results for debugging.

## Quick start

```bash
# Run against the current namespace (whatever PINECONE_NAMESPACE points at)
./run python eval/run_eval.py

# Compare baseline (your-namespace) vs new namespace (your-namespace)
./run python eval/run_eval.py --namespace your-namespace --output eval/results/baseline.json
./run python eval/run_eval.py --namespace your-namespace --output eval/results/contextual.json
```

## Question format

```json
{
  "id": "q001",
  "question": "How do I create my first widget?",
  "match": {"doc_id": "hc::getting_started::a1b2c3d4e5f6"},
  "notes": "onboarding flow"
}
```

Every key in `match` must be present in the matched vector's metadata. Strings use substring match; other types use equality. So you can match by `doc_id` (articles), `thread_ts` (Slack threads), `source: "slack"` (any Slack hit), or any combination.

## What the eval measures

It measures **retrieval quality only** — embedding similarity → Pinecone top-K → did the labeled vector appear?

It does NOT apply your n8n metadata boost (`tags` membership, `author_roles`, recency decay). That's intentional — when comparing changes that affect embeddings (contextual retrieval, new model, chunk-size sweep), you want the boost out of the picture so the signal is clean. Boost effects can be measured separately by extending the harness.

## Expanding the question set

The committed starter set is 3 synthetic questions matched to the 3 sample articles — just enough to verify the harness runs. **Replace it with your own as soon as you have real data ingested.** See `HOW_TO_LABEL.md` for a full guide to building a real question set; the short version:

- Aim for 15–25 questions minimum for meaningful signal; 50+ for confident A/B comparisons.
- Pull real user questions from your support channel or product analytics — synthetic questions written by the corpus author tend to over-fit.
- Mix specific vs. generic, paraphrase variants, hard negatives, and questions that span multiple article sections.
- For each, find which thread or article actually answers it and label `match: {"doc_id": "..."}` or `match: {"thread_ts": "..."}`.

## Interpreting results

- **Recall@5** is the headline number. n8n returns top-5 to the LLM, so a vector not in top-5 is invisible at answer time.
- **MRR** rewards getting the right vector higher in the list — useful for tracking ranking quality, not just inclusion.
- **Recall@1** is the strict version: did the very top hit answer the question?
- **Recall@10** measures *coverage* — is the right doc even in the index? If R@10 is bad, you likely have an embedding/chunking problem, not a ranking problem.

A baseline → contextual run that shifts R@5 from, say, 70% to 90% would be a strong signal that contextual retrieval is paying off.
