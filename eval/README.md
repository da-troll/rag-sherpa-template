# Eval harness

Measures retrieval quality of the recruitment RAG pipeline against a labeled question set. Used to compare changes (e.g., contextual retrieval, embedding model upgrades) against a baseline namespace.

## Files

- `questions.json` — labeled questions. Each entry has a `question` and a `match` spec describing which vector's metadata identifies a "correct" hit.
- `run_eval.py` — embeds each question, queries Pinecone in a namespace, reports recall@1/3/5/10 and MRR.
- `results/` — gitignored. Stores per-run JSON dumps with full top-K results for debugging.

## Quick start

```bash
# Run against the current namespace (whatever PINECONE_NAMESPACE points at)
./run python eval/run_eval.py

# Compare baseline (recruitment-rag-2) vs new namespace (recruitment-rag-3)
./run python eval/run_eval.py --namespace recruitment-rag-2 --output eval/results/baseline.json
./run python eval/run_eval.py --namespace recruitment-rag-3 --output eval/results/contextual.json
```

## Question format

```json
{
  "id": "q001",
  "question": "How do I bulk import candidates?",
  "match": {"doc_id": "hc::simployer_one___recruitment___importing_candidates::5771b0a93c3d"},
  "notes": "import flow"
}
```

Every key in `match` must be present in the matched vector's metadata. Strings use substring match; other types use equality. So you can match by `doc_id` (articles), `thread_ts` (Slack threads), `source: "slack"` (any Slack hit), or any combination.

## What the eval measures

It measures **retrieval quality only** — embedding similarity → Pinecone top-K → did the labeled vector appear?

It does NOT apply your n8n metadata boost (`has_trusted`, `has_recruitment_reaction`, recency decay). That's intentional — when comparing changes that affect embeddings (contextual retrieval, new model), you want the boost out of the picture so the signal is clean. Boost effects can be measured separately later by extending the harness.

## Expanding the question set

The committed starter set (~20 questions) covers the 16 help articles, with a mix of literal phrasings and lexical-mismatch questions. To get to a robust 30–50 questions, add:

- Real questions from your `:recruitment:`-tagged Slack threads (use the parent message text). For each, find which thread or article actually answered it and label `match: {"thread_ts": "..."}` or `match: {"doc_id": "..."}`.
- Edge cases: ambiguous questions, multi-doc questions, questions that should fall back gracefully.
- Negative tests: questions outside the corpus, where you expect *no* good match (these can use `"match_negative": true` once that's added — TBD).

The point is to capture how *real users* phrase things, including the gap between their words and the article's words.

## Interpreting results

- **Recall@5** is the headline number. n8n returns top-5 to the LLM, so a vector not in top-5 is invisible at answer time.
- **MRR** rewards getting the right vector higher in the list — useful for tracking ranking quality, not just inclusion.
- **Recall@1** is the strict version: did the very top hit answer the question?
- **Recall@10** measures *coverage* — is the right doc even in the index? If R@10 is bad, you likely have an embedding/chunking problem, not a ranking problem.

A baseline → contextual run that shifts R@5 from, say, 70% to 90% would be a strong signal that contextual retrieval is paying off.
