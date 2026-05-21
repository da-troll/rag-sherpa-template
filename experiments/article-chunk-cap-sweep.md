# Experiment: Article Chunk-Cap Sweep

> **Status:** completed on the corpus this template was built from. Winner there: **cap = 1000 chars**.
>
> **Re-run on your own corpus.** Findings below are illustrative — the optimal cap depends on your article shape (section length distribution, average chunk discriminability). The methodology, harness, and decision framework all generalize. Use `experiments/article_chunk_sweep.py` to sweep your own corpus.

## Why this experiment exists

The `articles-to-pc.py` header-aware chunker splits markdown on `## / ###` boundaries first, then char-caps any oversize section. The cap value (`ARTICLE_CHUNK_CHARS`) is a hyperparameter — too small fragments natural sections, too large dilutes embedding precision.

The prior values we'd shipped (1500, then 3500) were chosen from heuristics rather than measurement:
- **1500** came from initially conflating Slack and articles chunk config in a shared env var.
- **3500** came from research-benchmark guidance: "set cap ≥ p90 of natural section sizes" (the p90 of H2/H3 sections in this corpus is 3281 chars).

When 3500 measurably regressed the eval (R@5: 80% → 75%, MRR: 0.658 → 0.608 vs the 1500 baseline), the obvious question became: is the winning cap somewhere between 1500 and 3500, or actually below 1500? The benchmark wasn't a reliable predictor for *this* corpus, so we'd need to sweep.

## Method

5 cap values: **1000, 1500, 2000, 2500, 3500**. Overlap held constant at **300** across all runs. Each cap re-ingests all 16 articles into `your-namespace` (the per-doc `DELETE_BEFORE_INSERT` cleanly replaces prior vectors) and runs the same 20-question eval set.

Slack vectors are untouched across the sweep, so each eval is apples-to-apples: only article chunking differs.

Harness: `experiments/article_chunk_sweep.py`. Each cap's full eval result is preserved at `eval/results/sweep-cap-<NNNN>.json` for inspection.

**Total cost:** ~$0.25 (5 article re-ingests × ~$0.05 contextual-retrieval each) + ~3 min compute.

## Results

| cap | R@1 | R@3 | R@5 | R@10 | MRR@10 |
| --- | --- | --- | --- | --- | --- |
| **1000** | **60.0%** | 75.0% | **80.0%** | 80.0% | **0.688** ← winner |
| 1500 | 60.0% | 70.0% | 80.0% | 80.0% | 0.675 |
| 2000 | 55.0% | 70.0% | 75.0% | 80.0% | 0.636 |
| 2500 | 55.0% | 70.0% | 80.0% | 80.0% | 0.631 |
| 3500 | 55.0% | 75.0% | 80.0% | 80.0% | 0.637 |

### Three patterns to notice

**1. R@10 is flat at 80% across every cap.** The same 4 questions (q012, q018, q019, q020 — the deliberately-hard lexical-mismatch tests) miss regardless of chunking. Chunking is not their bottleneck; reranking or query rewriting would be.

**2. R@1 cleanly splits at the 1500/2000 boundary:** 60% at caps ≤1500, 55% at caps ≥2000. Smaller chunks consistently win at rank-1 precision.

**3. MRR is the cleanest signal:** decreases nearly monotonically as cap grows (0.688 → 0.675 → 0.636 → 0.631 → 0.637). Even when R@5 is tied, the correct answers sit at higher ranks with smaller chunks.

### Per-question rank table

```
qid    1000   1500   2000   2500   3500
q001      1      1      1      1      1
q002      2      1      1      1      1
q003      1      2      2      2      2
q004      1      1      1      1      1
q005      2      2      2      3      3
q006      1      4      3      3      3
q007      1      1      1      1      1
q008      1      1      1      1      1
q009      1      1      1      1      1
q010      4      4      4      4      3
q011      1      1      1      1      1
q012   miss   miss   miss   miss   miss
q013      1      1      1      1      1
q014      2      1      1      1      1
q015      1      1      7      5      4
q016      1      1      1      1      1
q017      1      1      1      1      1
q018   miss   miss   miss   miss   miss
q019   miss   miss   miss   miss   miss
q020   miss   miss   miss   miss   miss
```

Two questions worth pulling out:

- **q015 (user_roles, 2159 chars total):** rank 1 at caps ≤1500, then **crashes to rank 7 at cap=2000**. The article fits in one chunk at cap≥2000 but gets split into two chunks at smaller caps. The split creates a tighter "permissions" sub-chunk that wins the cosine match against the question "What permission levels exist for users?". Smaller chunks are more discriminative when queries target specific sub-sections.
- **q014 (creating_your_career_page):** improves from rank 2 → rank 1 as cap grows from 1000 to 1500. A counter-example: this question benefits from the slightly larger context that 1500 provides. Not every question wants the smallest chunks; the answer is corpus-dependent. cap=1000 wins the AVERAGE.

## Decision

Switch to **`ARTICLE_CHUNK_CHARS=1000`, `ARTICLE_CHUNK_OVERLAP=300`** as the production setting. Lift over 1500 is small but real (+0.013 MRR, identical R@5), and the trend across the sweep is consistent enough that the choice is defensible beyond noise.

`.env`, `.env.example`, and the `articles-to-pc.py` default are all updated to 1000.

## Caveats / what this experiment can't prove

1. **20-question eval is small.** MRR delta of 0.013 between 1000 and 1500 is at the noise floor; the larger margins (cap=1000 vs cap=2500) are more solid.
2. **Eval matches single ground-truth doc per question.** Some questions might have multiple acceptable answers in the corpus; the eval can't credit alternate retrievals.
3. **Eval doesn't measure answer quality, only retrieval quality.** Small chunks could in theory hurt downstream answer generation by feeding the LLM less surrounding context per chunk — though contextual retrieval mitigates this.
4. **One static snapshot.** As the corpus grows or shifts, this winner can drift. Re-sweep when:
   - Average article length changes meaningfully
   - Question set grows past ~50 (more statistical power)
   - The embedding model changes (different model = different discriminative behavior)

## How to re-run later

```bash
./run python experiments/article_chunk_sweep.py
```

Edit `CAPS_TO_TEST` at the top of the script to test different ranges (e.g., narrow around the previous winner, or extend to even smaller values like 500 or 750).

## Why benchmark guidance didn't predict the winner here

Research consensus (Vecta benchmark Feb 2026, NVIDIA on FinanceBench) favors **~512 tokens (~2000 chars) with 15% overlap** as the most-robust baseline across document corpora. That guidance is correct on AVERAGE — but it averages across very different document types (legal filings, financial reports, code, conversational, etc.).

For this specific corpus — short technical help articles, with short specific queries, ingested under a contextual-retrieval pipeline that already preserves cross-chunk context — the optimal is smaller. Benchmarks should suggest hypotheses; the eval set is what decides.
