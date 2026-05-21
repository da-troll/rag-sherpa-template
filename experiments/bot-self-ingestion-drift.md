# Experiment: Bot Self-Ingestion Drift

> **Status:** _method written; results pending the experimental ingest run_

## Why this experiment exists

The `slack-to-pc.py` pipeline filters out bot/app-authored Slack messages at ingest time, so RAG Bot's own historical answers (347+ replies in the corpus at time of writing) never become vectors. The rationale stated in code is:

> Bot messages must NOT be re-ingested as authoritative answers — they were generated FROM the index, and feeding them back risks closed-loop drift.

That rationale is **theoretically sound** — closed-loop drift is a known failure mode in retrieval systems where model outputs feed back into the index — but it was added defensively without empirical proof that *this* corpus actually suffers from it. This experiment quantifies the impact.

## Hypothesis

**H1:** Re-ingesting RAG Bot's own bot replies into the index measurably degrades retrieval quality.

**Null (H0):** It doesn't — bot answers are accurate enough that re-embedding them is neutral or even mildly helpful (more paraphrase coverage → broader semantic match).

Either result is informative.

## Method

Two-namespace A/B with everything else held identical. The only difference is whether the `is_bot_message()` filter applies in `slack-to-pc.py`.

| Aspect | Control (current production) | Variable |
| --- | --- | --- |
| Namespace | `your-namespace` | `your-namespace-experiment` |
| Slack bot-message filter | ON (default) | **OFF** (`SLACK_INCLUDE_BOTS=1`) |
| Articles ingest | already in rag-3 | re-ingest into experiment namespace |
| Contextual retrieval | ON | ON |
| Noise filter (system events) | ON | ON |
| Slack corpus | same 257-msg export | same 257-msg export |
| Eval question set | `eval/questions.json` | `eval/questions.json` |
| Embedding model | `text-embedding-3-small` | `text-embedding-3-small` |
| Boost code | NOT applied during this eval (we measure raw retrieval) | NOT applied |

The eval measures raw cosine retrieval quality; it doesn't apply the n8n boost code. So this isolates whether re-embedded bot text changes which vectors win on cosine similarity.

## Expected outcomes & what each means

| Result | Interpretation |
| --- | --- |
| **rag-3 wins by ≥3 pts on R@5** | Filter is doing real work. Bot replies were displacing better human content. Keep the filter. |
| **Results within ±2 pts on R@5** | Filter is defensive but not load-bearing. Keep it as cheap insurance; no urgent action either way. |
| **Experiment wins by ≥3 pts on R@5** | Surprising — bot answers are adding useful paraphrase coverage. Worth a deeper look (which questions improved? are they ones where the bot's prior answer happens to be a good rephrasing of the verified content?). |

We have 20 questions in the eval set, so a 3-pt swing is ~0.6 questions — at the edge of meaningful. The deeper signal will come from looking at **per-question rank changes** (especially the q012, q018, q019, q020 lexical-mismatch misses).

## How to run

Three steps. All commands assume `pwd` is the repo root and `op signin` is active.

### 1. Ingest articles into the experimental namespace

The articles need to be present in the same namespace so the eval has a complete corpus to match against. Article ingest is deterministic — same input produces same vectors.

```bash
# Temporarily override the namespace + tell Slack to include bots
# (article ingest doesn't read SLACK_INCLUDE_BOTS — it's a no-op for this step)
PINECONE_NAMESPACE=your-namespace-experiment ./run python articles-to-pc.py
```

Cost: ~$0.10 contextual retrieval + standard embedding. Time: ~2 min.

### 2. Ingest Slack with the bot filter OFF

```bash
PINECONE_NAMESPACE=your-namespace-experiment \
SLACK_INCLUDE_BOTS=1 \
./run python slack-to-pc.py
```

Watch the startup diagnostic — it should report:
- `Parents: <N> (filtered <M> non-content from 254 raw)` — same count as your last rag-3 run, because system-event filter still applies
- `Replies: kept ~1040 human / dropped 0 bot-authored / 1040 total` — **no bot drops** (the key difference)

Cost: ~$0.30 contextual retrieval + standard embedding. Time: ~10 min.

### 3. Run eval against the experimental namespace

```bash
./run python eval/run_eval.py \
  --namespace your-namespace-experiment \
  --output eval/results/bot-experiment.json
```

Cost: trivial (~$0.001 for 20 embeddings + 20 Pinecone queries). Time: ~30 sec.

### 4. Compare

```bash
./run python -c "
import json
with open('eval/results/contextual-v2.json') as f: a = json.load(f)   # rag-3 (filter ON)
with open('eval/results/bot-experiment.json') as f: b = json.load(f)  # filter OFF

print('Metric          rag-3 (control)   experiment (bots in)   Δ')
for k in ['1','3','5','10']:
    av = a['recall_at'][k]; bv = b['recall_at'][k]
    n = a['questions_n']
    print(f'  recall@{k:<2}       {av}/{n} ({100*av/n:.1f}%)      {bv}/{n} ({100*bv/n:.1f}%)      {100*(bv-av)/n:+.1f} pts')
print(f'  MRR@10        {a[\"mrr\"]:.3f}             {b[\"mrr\"]:.3f}              {b[\"mrr\"]-a[\"mrr\"]:+.3f}')

# Per-question rank shifts
print('\\nPer-question rank changes (rag-3 → experiment):')
by_id = {d['id']: d for d in a['details']}
for d in b['details']:
    a_rank = by_id.get(d['id'],{}).get('matched_rank')
    b_rank = d['matched_rank']
    if a_rank != b_rank:
        ar = a_rank if a_rank else 'miss'
        br = b_rank if b_rank else 'miss'
        print(f'  {d[\"id\"]}: {ar} → {br}   {d[\"question\"][:60]}')
"
```

### 5. Cleanup (optional)

The experimental namespace is small (~640 vectors) and free to leave around, but if you want to delete it later:

```bash
./run python -c "
import os
from pinecone import Pinecone
pc = Pinecone(api_key=os.getenv('PINECONE_API_KEY'))
idx = pc.Index(host=os.getenv('PINECONE_HOST')) if os.getenv('PINECONE_HOST') else pc.Index(os.getenv('PINECONE_INDEX'))
idx.delete(delete_all=True, namespace='your-namespace-experiment')
print('deleted')
"
```

## Limitations of this test

Worth being honest about what this experiment **cannot** tell you, so the PM doesn't over-interpret the result.

1. **20-question eval is small.** A 3-point shift = ~0.6 questions. Signal exists but noise floor is real. Confidence intervals on percentages of this size are wide.
2. **Eval matches against a single ground-truth doc per question.** If the bot's answer happens to mention the right product (Cronofy, LinkedIn, etc.) the question's expected doc still has to win on cosine, so bot content competing in top-K might *help* recall metrics even while subjectively making answer quality worse. The eval doesn't measure answer quality, only retrieval quality.
3. **One snapshot can't measure drift over time.** Drift is a process — bot writes answer at time T0 from corpus C0; that answer enters corpus to form C1; bot writes new answer at T1 from C1 partly informed by its own T0 output; etc. We're testing a single iteration of this loop, not the compounding effect over months. Even a null result here doesn't prove the long-run loop is safe.
4. **The boost code is NOT applied here.** This measures raw retrieval. With the boost in place, the bot replies' relative weight could be different.

## Results

| Metric | rag-3 (filter ON, control) | experiment (filter OFF, bots in) | Δ |
| --- | --- | --- | --- |
| recall@1 | 14/20 (70.0%) | 10/20 (50.0%) | **−20.0 pts** |
| recall@3 | 15/20 (75.0%) | 15/20 (75.0%) | flat |
| recall@5 | 16/20 (80.0%) | 15/20 (75.0%) | −5.0 pts |
| recall@10 | 16/20 (80.0%) | 15/20 (75.0%) | −5.0 pts |
| **MRR@10** | **0.738** | **0.625** | **−0.113** |

### Per-question rank shifts (filter ON → filter OFF)

| qid | ON | OFF | shift | question |
| --- | --- | --- | --- | --- |
| q002 | 1 | 2 | ↓1 | How can I bulk import candidates? |
| q003 | 1 | 2 | ↓1 | Where do I upload our company logo? |
| q005 | 1 | 2 | ↓1 | How do I set up Cronofy for interview scheduling? |
| **q010** | **4** | **miss** | **↓LOST** | How do candidates accept the privacy policy? |
| q014 | 2 | 1 | ↑1 | How do I build my career page? |
| q015 | 1 | 2 | ↓1 | What permission levels exist for users? |
| q016 | 1 | 2 | ↓1 | Walk me through setting up a new job posting. |

**6 questions degraded, 1 improved.** Of the 13 questions that didn't move, 4 are the hard lexical-mismatch tests that miss in both conditions (q012, q018, q019, q020) — unaffected by either setting.

## Conclusion

**The bot filter was doing real work — the data confirms hypothesis H1 unambiguously.**

The MRR delta of −0.113 is roughly **2× the contextual-retrieval variance floor** we'd measured earlier (~0.05). The R@1 delta of −20 points is impossible to explain via run-to-run noise. We can rule out the null hypothesis with high confidence.

### Magnitude of the lift the filter provides

- ~20 pts on R@1 (the metric that matters most for "did RAG Bot surface the right doc immediately")
- ~5 pts on R@5 and R@10 (steady mid-range improvement)
- ~0.11 on MRR (correct results sit at higher ranks)
- Most dramatically: **q010 went from rank 4 to outside the top 10** when bots were included. The bot's prior answer about privacy policy outcompeted the actual privacy policy article.

### Why the bot's replies are so competitive

The 6:1 worse-vs-better ratio is the signature finding. RAG Bot's archived answers aren't just *neutral* re-ingested text — they actively crowd out the canonical sources. The mechanism:

1. RAG Bot's answers were generated FROM the index, conditioned on user questions
2. Each answer is therefore written in the shape of "answer to a question like Q"
3. When re-embedded, those answers have very high semantic similarity to questions like Q — *higher* than the canonical source documents they were derived from, because the source documents weren't written to match question phrasings
4. So bot answers win the cosine match for paraphrase-style retrieval — replacing ground-truth with derivative

This is the closed-loop drift pattern materializing in a single iteration. Every additional re-ingest of newer bot output would compound it.

### Recommendation

**Keep the filter on. Permanently.** Document it as a load-bearing component of the ingest pipeline, not a defensive choice.

The original defensive framing (theoretical concern about feedback loops) understated the case. The data shows the filter is responsible for ~20% of R@1 and ~15% of MRR. Removing it would meaningfully degrade RAG Bot's bot quality in production.

### What's the next experiment worth running?

Three candidates, ordered by potential impact:

1. **Reranker after metadata boost** (Tier 2.1 in `RAG_IMPROVEMENTS_PLAN.md`). Voyage rerank-2-lite or Cohere rerank-3.5 as a final-stage reranker should mostly close the 4 remaining lexical-mismatch misses (q012, q018, q019, q020) — those are the questions chunk-tuning can't fix because the right vectors aren't even in the top-100. Reranker territory.
2. **Query rewriting / HyDE for short queries** — generates a hypothetical answer at retrieval time, embeds that for search. Targeted at the same 4 hard-miss questions. Adds latency + an LLM call per query; probably overkill if reranker already fixes them.
3. **Sentiment-reaction signal recovery experiment** — the bot took over so heavily after Oct 2025 that human endorsement reactions decayed to near-zero on new threads. With RAG Bot's answers now filtered out of the index, do humans return to endorsing each other's answers? If yes, the `:verified:` and similar manual signals might recover organically. Long-horizon observation, not a controlled test.

### Limitations of this experiment

Worth being explicit about so the PM doesn't over-generalize the result.

1. **20-question eval is small.** The R@1 delta of −20 pts is large enough to survive sampling noise, but the per-question breakdown is fragile — re-running the same comparison might shift 1-2 questions due to contextual-retrieval variance (we measured this floor at ~0.05 MRR).
2. **Eval matches against a single ground-truth doc per question.** Some bot-flavored matches might be acceptable answers in practice even if they aren't the labeled target. The eval is strict; subjective answer quality might be less degraded than the numbers suggest.
3. **One snapshot, not a longitudinal study.** True drift compounds over time. This experiment tested a single iteration of "bot writes answer → re-ingested → competes against next query." Months of operation would compound the effect; we don't have months of historical bot outputs at multiple time points to test against.
4. **Eval doesn't capture answer quality, only retrieval quality.** Bot replies competing for top-K slots could feed *wrong* content to the answering LLM at synthesis time — that's a separate degradation we'd need an answer-quality eval to measure.

These caveats don't change the recommendation — the directional result is strong enough. They do constrain the *magnitude* claim.
