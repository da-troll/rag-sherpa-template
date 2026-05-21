#!/usr/bin/env python3
"""
Diagnostic: report size distribution of help-center articles AND of the
H2/H3 sections inside them, to validate CHUNK_CHARS in articles-to-pc.py.

This is a sanity check, not a config generator. Run it after
articles-to-markdown.py to see whether your chunk cap is reasonable
for the current corpus.
"""
import json, re, os, sys
from pathlib import Path

INPUT_JSON = "markdown_help_articles.json" if Path("markdown_help_articles.json").exists() \
    else "data/articles/markdown_help_articles.json"

HEADER_RE = re.compile(r'^(#{2,3} .+)$', re.MULTILINE)

def percentile(sorted_vals, q):
    if not sorted_vals:
        return 0
    if q <= 0: return sorted_vals[0]
    if q >= 100: return sorted_vals[-1]
    k = (len(sorted_vals) - 1) * (q / 100.0)
    f = int(k); c = min(f + 1, len(sorted_vals) - 1)
    return int(round(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)))

def split_sections(md: str):
    matches = list(HEADER_RE.finditer(md))
    if not matches:
        return [md.strip()] if md.strip() else []
    out = []
    if matches[0].start() > 0:
        prefix = md[:matches[0].start()].strip()
        if prefix: out.append(prefix)
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        sec = md[m.start():end].strip()
        if sec: out.append(sec)
    return out

def report(name, lengths):
    if not lengths:
        print(f"{name}: no data"); return
    arr = sorted(lengths)
    print(f"{name}: n={len(arr)} | mean={sum(arr)//len(arr):,} | "
          f"p50={percentile(arr,50):,} | p75={percentile(arr,75):,} | "
          f"p90={percentile(arr,90):,} | p95={percentile(arr,95):,} | "
          f"max={arr[-1]:,}")

def main():
    if not os.path.exists(INPUT_JSON):
        sys.exit(f"Not found: {INPUT_JSON}. Run articles-to-markdown.py first.")
    with open(INPUT_JSON, "r", encoding="utf-8") as f:
        articles = json.load(f)

    doc_lengths, section_lengths = [], []
    for _, art in articles.items():
        md = (art.get("content") or {}).get("markdown", "")
        if not md.strip():
            continue
        doc_lengths.append(len(md))
        for sec in split_sections(md):
            section_lengths.append(len(sec))

    print(f"Corpus: {INPUT_JSON}\n")
    report("Whole articles", doc_lengths)
    report("H2/H3 sections", section_lengths)

    if section_lengths:
        sl = sorted(section_lengths)
        p90 = percentile(sl, 90)
        suggested = max(2000, min(6000, int(round(p90 / 500) * 500)))
        overlap = int(round(suggested * 0.15 / 100) * 100)
        print(f"\nSuggested CHUNK_CHARS≈{suggested}, CHUNK_OVERLAP≈{overlap}")
        print("(Section p90 sets the cap; overlap ~15% covers fallback char-splits.)")

if __name__ == "__main__":
    main()
