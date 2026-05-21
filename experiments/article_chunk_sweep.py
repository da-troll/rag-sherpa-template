#!/usr/bin/env python3
"""
Article chunk-cap parameter sweep.

For each cap value in CAPS_TO_TEST: re-ingest all 16 articles at that cap,
run the eval, record recall@K + MRR. At the end, print a side-by-side
comparison table and announce the empirical winner.

Slack vectors stay constant across the sweep (they're not touched), so each
eval is apples-to-apples: only the article chunking differs.

Usage:
    ./run python experiments/article_chunk_sweep.py

Outputs:
    eval/results/sweep-cap-<NNNN>.json   one per cap, for later inspection
    console comparison table
"""
from __future__ import annotations
import os, sys, json, subprocess
from pathlib import Path

import sys
from pathlib import Path
# Scripts live in a subfolder; expose the repo root so shared helpers
# (contextual_retrieval, styling) can be imported as top-level modules.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# styling.py lives at repo root; experiments/ is one level deeper.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from styling import (
    banner, section, summary, hr,
    BOLD, DIM, RESET, CYAN, GREEN, YELLOW, RED,
    ARROW, CHECK, CROSS, BLOCK, SHADE,
)

# --- CONFIG ---
CAPS_TO_TEST = [1000, 1500, 2000, 2500, 3500]
OVERLAP = 300  # held constant across the sweep
NAMESPACE = os.getenv("PINECONE_NAMESPACE", "your-namespace")
RESULTS_DIR = REPO_ROOT / "eval" / "results"


def run_step(cmd: list[str], env: dict) -> None:
    """Run a subprocess inheriting (and possibly overriding) env, streaming output."""
    result = subprocess.run(cmd, env=env, cwd=REPO_ROOT)
    if result.returncode != 0:
        sys.exit(f"  {RED}{CROSS}{RESET} subprocess failed: {' '.join(cmd)}")


def main():
    # Banner
    banner(
        title="article chunk-cap sweep",
        fields=[
            ("caps",      f"{CAPS_TO_TEST}"),
            ("overlap",   f"{OVERLAP}  {DIM}(held constant){RESET}"),
            ("namespace", f"{BOLD}{NAMESPACE}{RESET}"),
            ("note",      f"{DIM}Slack vectors untouched; only articles re-ingest per cap{RESET}"),
        ],
    )
    print()

    results = []
    for i, cap in enumerate(CAPS_TO_TEST, 1):
        print(section(f"[{i}/{len(CAPS_TO_TEST)}]  cap = {cap}"))

        env = {
            **os.environ,
            "ARTICLE_CHUNK_CHARS":   str(cap),
            "ARTICLE_CHUNK_OVERLAP": str(OVERLAP),
        }

        # Article ingest at this cap
        print(f"  {CYAN}{ARROW}{RESET} ingesting articles…", flush=True)
        run_step([sys.executable, "articles-to-pc.py"], env=env)

        # Eval against the same namespace
        out_path = RESULTS_DIR / f"sweep-cap-{cap}.json"
        print(f"  {CYAN}{ARROW}{RESET} running eval → {out_path.name}", flush=True)
        run_step(
            [sys.executable, "eval/run_eval.py",
             "--namespace", NAMESPACE,
             "--output",    str(out_path)],
            env=env,
        )

        with open(out_path) as f:
            data = json.load(f)
        results.append({"cap": cap, "data": data})
        print()

    # ─── comparison table ───────────────────────────────────────────────
    print(section("sweep results"))
    n = results[0]["data"]["questions_n"]

    # Header
    print(f"  {DIM}{'cap':>6}  {'R@1':>7}  {'R@3':>7}  {'R@5':>7}  {'R@10':>7}  {'MRR@10':>8}{RESET}")
    print(f"  {DIM}{'─'*6}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*8}{RESET}")

    best_r5 = max(r["data"]["recall_at"]["5"] for r in results)
    best_mrr = max(r["data"]["mrr"] for r in results)

    for r in sorted(results, key=lambda x: x["cap"]):
        cap = r["cap"]; d = r["data"]
        ra = {k: 100 * d["recall_at"][k] / n for k in ("1","3","5","10")}
        mrr = d["mrr"]

        is_best_r5 = d["recall_at"]["5"] == best_r5
        is_best_mrr = abs(mrr - best_mrr) < 1e-6

        cap_s  = f"{BOLD}{cap:>6}{RESET}"
        r5_s   = f"{GREEN}{BOLD}{ra['5']:>5.1f}%{RESET}" if is_best_r5 else f"{DIM}{ra['5']:>5.1f}%{RESET}"
        mrr_s  = f"{GREEN}{BOLD}{mrr:>7.3f}{RESET}"      if is_best_mrr else f"{DIM}{mrr:>7.3f}{RESET}"
        print(f"  {cap_s}  {ra['1']:>5.1f}%  {ra['3']:>5.1f}%  {r5_s}  {ra['10']:>5.1f}%  {mrr_s}")

    print()

    # ─── winner summary ────────────────────────────────────────────────
    # Primary metric: recall@5; tiebreaker: MRR@10
    winner = max(results, key=lambda r: (r["data"]["recall_at"]["5"], r["data"]["mrr"]))
    w = winner["data"]
    summary(
        headline=f"empirical winner: cap = {winner['cap']}  /  overlap = {OVERLAP}",
        lines=[
            f"recall@5  {BOLD}{100*w['recall_at']['5']/n:.1f}%{RESET}  {DIM}({w['recall_at']['5']}/{n}){RESET}",
            f"MRR@10    {BOLD}{w['mrr']:.3f}{RESET}",
            f"detail    eval/results/sweep-cap-{winner['cap']}.json",
        ],
    )

    # If the winner isn't already the current production setting, suggest the env change
    current_cap = int(os.getenv("ARTICLE_CHUNK_CHARS", "1500"))
    if winner["cap"] != current_cap:
        print()
        print(f"  {YELLOW}{ARROW}{RESET} To make this the production setting, update {BOLD}.env{RESET}:")
        print(f"      ARTICLE_CHUNK_CHARS={winner['cap']}")
        print(f"      ARTICLE_CHUNK_OVERLAP={OVERLAP}")
        print(f"  {YELLOW}{ARROW}{RESET} Then re-ingest articles into {NAMESPACE}:")
        print(f"      ./run python articles-to-pc.py")


if __name__ == "__main__":
    main()
