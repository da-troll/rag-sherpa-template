#!/usr/bin/env python3
"""
Eval harness for the RAG retrieval layer.

Loads `questions.json`, embeds each question with the same model used at ingest
time, queries Pinecone, and reports recall@k and MRR against the labeled
ground-truth `match` metadata.

Designed to measure RETRIEVAL QUALITY in isolation (no n8n, no LLM, no
metadata boost). That's what we want when comparing pre/post contextual
retrieval — we don't want boost variations confounding the signal.

Usage:
    ./run python eval/run_eval.py                       # default namespace from .env
    ./run python eval/run_eval.py --namespace your-namespace   # explicit baseline
    ./run python eval/run_eval.py --top-k 10 --output eval/results/baseline.json
"""
import os, sys, json, argparse, datetime
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(usecwd=True), override=False)  # parent env (op run) wins over .env literals

# styling.py lives at the repo root; eval/ is one level deeper.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from styling import (
    banner, hr, summary, section, visible_len,
    BOLD, DIM, RESET, CYAN, GREEN, YELLOW, RED,
    ARROW, CHECK, CROSS, DOT, BLOCK, SHADE,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
QUESTIONS_PATH = Path(__file__).resolve().parent / "questions.json"

PINECONE_INDEX = os.getenv("PINECONE_INDEX")
PINECONE_HOST = os.getenv("PINECONE_HOST")  # optional; bypasses describe_index lookup (works with scoped keys)
PINECONE_NAMESPACE_DEFAULT = os.getenv("PINECONE_NAMESPACE")
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
if not OPENAI_API_KEY:
    sys.exit("OPENAI_API_KEY not set (run via ./run).")
if not PINECONE_API_KEY:
    sys.exit("PINECONE_API_KEY not set (run via ./run).")


def metadata_matches(meta: dict, match_spec: dict) -> bool:
    """A vector matches a question if every (k, v) in match_spec is satisfied
    by meta[k]. Substring match on strings; equality on others."""
    for k, expected in match_spec.items():
        actual = meta.get(k)
        if actual is None:
            return False
        if isinstance(expected, str):
            if str(expected) not in str(actual):
                return False
        else:
            if actual != expected:
                return False
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--namespace", default=PINECONE_NAMESPACE_DEFAULT,
                        help="Pinecone namespace to query (default: $PINECONE_NAMESPACE)")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--output", default=None,
                        help="Path to write detailed JSON results (default: eval/results/<namespace>_<ts>.json)")
    parser.add_argument("--questions", default=str(QUESTIONS_PATH))
    args = parser.parse_args()

    if not args.namespace:
        sys.exit("--namespace not provided and PINECONE_NAMESPACE not set")

    from openai import OpenAI
    from pinecone import Pinecone
    client = OpenAI(api_key=OPENAI_API_KEY)
    pc = Pinecone(api_key=PINECONE_API_KEY)
    if PINECONE_HOST:
        index = pc.Index(host=PINECONE_HOST)
    else:
        index = pc.Index(PINECONE_INDEX)

    with open(args.questions) as f:
        questions = json.load(f)

    # Show the questions path as a short relative form for the banner.
    try:
        _q_display = str(Path(args.questions).resolve().relative_to(REPO_ROOT))
    except ValueError:
        _q_display = args.questions
    banner(
        title="eval ▸ retrieval quality",
        fields=[
            ("questions", f"{BOLD}{len(questions)}{RESET}  {DIM}from {_q_display}{RESET}"),
            ("index",     f"{PINECONE_INDEX}"),
            ("namespace", f"{BOLD}{args.namespace}{RESET}"),
            ("embedding", f"{EMBED_MODEL}"),
            ("top_k",     f"{args.top_k}"),
        ],
    )
    print()
    print(section(f"running {len(questions)} queries"))
    print()

    detailed = []
    hits_at = {1: 0, 3: 0, 5: 0, 10: 0}
    reciprocal_ranks = []

    # Fixed-column widths so every per-question row aligns vertically.
    # Status column is wide enough to hold "✓ rank 10" (9 visible chars).
    STATUS_COL_W = 9
    ID_COL_W = 4  # all question ids are "q001"–"q020"

    def _pad_visible(s: str, w: int) -> str:
        """Pad a (possibly ANSI-colored) string with trailing spaces to `w`
        visible columns."""
        return s + " " * max(0, w - visible_len(s))

    for q_index, q in enumerate(questions, 1):
        # Live "in-progress" line — the user sees something the moment the
        # query starts. The `\r\033[2K` lets us rewrite this line cleanly with
        # the final result once the query returns.
        sys.stdout.write("\r\033[2K")
        sys.stdout.write(
            f"  {DIM}[{q_index:>2}/{len(questions)}]{RESET} "
            f"{CYAN}{ARROW}{RESET} "
            f"{DIM}{q['id']:<{ID_COL_W}}{RESET}  "
            f"{DIM}embed+query…{RESET}"
        )
        sys.stdout.flush()

        emb = client.embeddings.create(model=EMBED_MODEL, input=q["question"]).data[0].embedding
        res = index.query(vector=emb, top_k=args.top_k, namespace=args.namespace,
                          include_metadata=True)

        matched_rank = None
        top_meta_preview = []
        for i, m in enumerate(res.matches):
            meta = m.metadata or {}
            top_meta_preview.append({
                "rank": i + 1,
                "score": round(m.score, 4),
                "id": m.id,
                "doc_id_or_thread_ts": meta.get("doc_id") or meta.get("thread_ts") or "",
                "title_or_text_preview": (meta.get("title") or meta.get("text", "")[:80])[:80],
            })
            if matched_rank is None and metadata_matches(meta, q["match"]):
                matched_rank = i + 1

        if matched_rank:
            for k in hits_at:
                if matched_rank <= k:
                    hits_at[k] += 1
            reciprocal_ranks.append(1.0 / matched_rank)
            # rank right-aligned to 2 chars so "✓ rank  1" and "✓ rank 10" align
            status_styled = f"{GREEN}{CHECK} rank {matched_rank:>2}{RESET}"
        else:
            reciprocal_ranks.append(0.0)
            status_styled = f"{RED}{CROSS} miss{RESET}"

        status_padded = _pad_visible(status_styled, STATUS_COL_W)

        # Replace the live "embed+query…" placeholder with the final row.
        sys.stdout.write("\r\033[2K")
        print(
            f"  {DIM}[{q_index:>2}/{len(questions)}]{RESET} "
            f"{CYAN}{ARROW}{RESET} "
            f"{DIM}{q['id']:<{ID_COL_W}}{RESET}  "
            f"{status_padded}  {DIM}·{RESET}  {q['question']}",
            flush=True,
        )
        detailed.append({
            "id": q["id"],
            "question": q["question"],
            "match_spec": q["match"],
            "matched_rank": matched_rank,
            "top_results": top_meta_preview,
        })

    n = len(questions)
    mrr = sum(reciprocal_ranks) / n

    def _bar(frac: float, width: int = 24) -> str:
        filled = int(round(frac * width))
        return f"{GREEN}{BLOCK * filled}{RESET}{DIM}{SHADE * (width - filled)}{RESET}"

    # Fixed-width label column so the bar always starts at the same column,
    # regardless of whether the label is "recall@10" (9 chars) or "MRR@10"
    # (6 chars). Value column right-aligned to 6 chars so "55.0%" and "0.658"
    # both sit at the same right edge.
    LABEL_W = 10
    VAL_W = 6

    def _row(label: str, frac: float, value_str: str, count_str: str = "") -> str:
        line = f"{label:<{LABEL_W}}  {_bar(frac)}  {BOLD}{value_str:>{VAL_W}}{RESET}"
        if count_str:
            line += f"  {DIM}{count_str}{RESET}"
        return line

    print()
    summary(
        headline=f"eval complete  ·  namespace={args.namespace}",
        lines=[
            _row("recall@1",  hits_at[1]/n,  f"{100*hits_at[1]/n:.1f}%",  f"({hits_at[1]}/{n})"),
            _row("recall@3",  hits_at[3]/n,  f"{100*hits_at[3]/n:.1f}%",  f"({hits_at[3]}/{n})"),
            _row("recall@5",  hits_at[5]/n,  f"{100*hits_at[5]/n:.1f}%",  f"({hits_at[5]}/{n})"),
            _row("recall@10", hits_at[10]/n, f"{100*hits_at[10]/n:.1f}%", f"({hits_at[10]}/{n})"),
            _row(f"MRR@{args.top_k}", mrr, f"{mrr:.3f}"),
        ],
    )

    out_path = args.output
    if not out_path:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = f"eval/results/{args.namespace}_{ts}.json"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({
            "index": PINECONE_INDEX,
            "namespace": args.namespace,
            "embed_model": EMBED_MODEL,
            "top_k": args.top_k,
            "questions_n": n,
            "recall_at": hits_at,
            "mrr": sum(reciprocal_ranks) / n,
            "details": detailed,
        }, f, indent=2)
    print(f"\nWrote details: {out_path}")


if __name__ == "__main__":
    main()
