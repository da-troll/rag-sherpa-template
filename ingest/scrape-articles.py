#!/usr/bin/env python3
"""
Scrape public help-center articles into data/articles/scraped_help_articles.json.

Step 0 of the help-center pipeline:
    scrape-articles.py  ->  data/articles/scraped_help_articles.json
                        ->  clean-articles-json.py
                        ->  articles-to-markdown.py
                        ->  articles-to-pc.py

Approach:
- Read one or more listing-page URLs from $HELP_CENTER_LISTING_URLS (comma-sep).
- For each listing page, find article URLs matching /articles/\\d+-... .
- Fetch each article and dump the FULL HTML response. The downstream cleaner
  (clean-articles-json.py) already strips chrome via NOISE_PATTERNS, so we
  don't need to find an article-body selector — let it stay generic.
- Polite: SCRAPE_DELAY_SECONDS between requests; retry on 5xx/429 with backoff.
- Resumable: per-URL cache in articles/.cache/<sha1>.html. Skip cached unless
  --force is passed. Fast no-op on reruns.

Output shape matches the existing scraped JSON contract consumed by
clean-articles-json.py:
    {
        "<article_url>": {
            "metadata":     {"url", "title", "meta_description", "meta_keywords", ...},
            "text_content": {"headers": {"h1": [...], "h2": [...], "h3": [...]}},
            "raw_html":     "<full HTML>"
        }, ...
    }
"""
import os, sys, re, json, time, argparse, hashlib
from pathlib import Path
from urllib.parse import urljoin, urlparse
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(usecwd=True), override=False)

import requests
from bs4 import BeautifulSoup

LISTING_URLS_ENV = os.getenv("HELP_CENTER_LISTING_URLS", "")
DEFAULT_DELAY = float(os.getenv("SCRAPE_DELAY_SECONDS", "1.5"))
USER_AGENT = os.getenv("SCRAPE_USER_AGENT",
                       "Mozilla/5.0 (compatible; recruitment-rag-scraper/0.1; +https://github.com/da-troll)")

OUTPUT_JSON = Path("data/articles/scraped_help_articles.json")
CACHE_DIR = Path("data/articles/.cache")

# Article URL pattern — overridable for non-Freshdesk help centers (Zendesk, Notion, etc.)
# Default matches Freshdesk's /support/solutions/articles/<id>-<slug> shape.
ARTICLE_URL_PATTERN = os.getenv(
    "SCRAPE_ARTICLE_URL_PATTERN",
    r'/(?:[a-z]{2,5}/)?support/solutions/articles/\d+[^"#?]*'
)
ARTICLE_URL_RE = re.compile(ARTICLE_URL_PATTERN)


def fetch(url: str, *, timeout: int = 30, max_retries: int = 4) -> str:
    """GET with retry/backoff. Returns response.text or raises."""
    delay = 1.0
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                wait = int(r.headers.get("Retry-After", delay))
                print(f"  [retry] {r.status_code} on {url} — sleeping {wait}s (attempt {attempt}/{max_retries})")
                time.sleep(wait)
                delay = min(delay * 2, 30)
                continue
            r.raise_for_status()
            return r.text
        except (requests.RequestException, requests.Timeout) as e:
            if attempt == max_retries:
                raise
            print(f"  [retry] {type(e).__name__} on {url} — sleeping {delay}s (attempt {attempt}/{max_retries})")
            time.sleep(delay)
            delay = min(delay * 2, 30)
    raise RuntimeError(f"unreachable retry exit for {url}")


def cache_path_for(url: str) -> Path:
    return CACHE_DIR / (hashlib.sha1(url.encode("utf-8")).hexdigest()[:16] + ".html")


def get_html(url: str, *, force: bool, delay: float) -> str:
    """Cached fetch. Honors --force to bypass cache."""
    cp = cache_path_for(url)
    if cp.exists() and not force:
        return cp.read_text(encoding="utf-8")
    html = fetch(url)
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(html, encoding="utf-8")
    time.sleep(delay)  # only sleep after a real fetch
    return html


def discover_article_urls(listing_urls: list[str], *, force: bool, delay: float) -> list[str]:
    """For each listing page URL, return all article URLs found on it (de-duped, sorted)."""
    found: set[str] = set()
    for lu in listing_urls:
        print(f"[listing] {lu}")
        html = get_html(lu, force=force, delay=delay)
        base = f"{urlparse(lu).scheme}://{urlparse(lu).netloc}"
        for m in ARTICLE_URL_RE.findall(html):
            found.add(urljoin(base, m))
        print(f"  found {len(found)} unique article URLs so far")
    return sorted(found)


def _absolutize_urls(soup: BeautifulSoup, base_url: str) -> None:
    """Mutate `soup` so <img src>, <a href>, <link href>, <source srcset> all
    point at absolute URLs. Robustness against future help-center variations
    that use relative paths for images/attachments."""
    for tag, attr in [("img", "src"), ("a", "href"), ("link", "href"),
                      ("source", "src"), ("video", "src"), ("iframe", "src")]:
        for el in soup.find_all(tag):
            v = el.get(attr)
            if v and not v.startswith(("http://", "https://", "data:", "mailto:", "#")):
                el[attr] = urljoin(base_url, v)
    # srcset (multiple URLs in one attribute)
    for el in soup.find_all(attrs={"srcset": True}):
        parts = []
        for piece in el["srcset"].split(","):
            piece = piece.strip()
            if not piece:
                continue
            url, *rest = piece.split(None, 1)
            if not url.startswith(("http://", "https://", "data:")):
                url = urljoin(base_url, url)
            parts.append(" ".join([url] + rest))
        el["srcset"] = ", ".join(parts)


def parse_article(url: str, html: str) -> dict:
    """Build the per-article dict in the shape clean-articles-json.py expects.

    Flexible to future variations:
    - Title: tries <h1>, then og:title, then <title>. Empty allowed.
    - Meta tags optional.
    - Image src/srcset and link hrefs absolutized so the markdown step
      downstream can fetch them regardless of relative-path quirks.
    """
    soup = BeautifulSoup(html, "html.parser")
    _absolutize_urls(soup, url)

    # Title: prefer <h1>, then og:title, then <title>
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        ogt = soup.find("meta", attrs={"property": "og:title"})
        if ogt:
            title = ogt.get("content", "").strip()
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()

    def meta(name=None, prop=None):
        if name:
            m = soup.find("meta", attrs={"name": name})
        else:
            m = soup.find("meta", attrs={"property": prop})
        return (m.get("content", "") if m else "")

    headers = {f"h{i}": [h.get_text(strip=True) for h in soup.find_all(f"h{i}")]
               for i in range(1, 4)}

    # Image inventory — visibility for the scrape report and downstream sanity.
    img_urls = sorted({i["src"] for i in soup.find_all("img") if i.get("src")
                       and not i["src"].startswith("data:")})

    return {
        "metadata": {
            "url": url,
            "title": title,
            "meta_description": meta(name="description") or meta(prop="og:description"),
            "meta_keywords": meta(name="keywords"),
            "og_title": meta(prop="og:title"),
            "scraped_image_count": len(img_urls),
        },
        "text_content": {"headers": headers},
        "images": [{"src": u} for u in img_urls],
        "raw_html": str(soup),  # absolutized version
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listing", action="append", default=None,
                    help="Listing page URL to crawl. Repeatable. Defaults to $HELP_CENTER_LISTING_URLS.")
    ap.add_argument("--force", action="store_true",
                    help="Bypass per-URL cache; re-fetch every page.")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                    help=f"Seconds between fetches (default {DEFAULT_DELAY})")
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap number of articles fetched (debugging)")
    args = ap.parse_args()

    listing_urls = args.listing or [u.strip() for u in LISTING_URLS_ENV.split(",") if u.strip()]
    if not listing_urls:
        sys.exit("No listing URLs. Pass --listing or set HELP_CENTER_LISTING_URLS in .env.")

    print(f"Listing URLs: {len(listing_urls)}")
    print(f"Delay:        {args.delay}s between fetches")
    print(f"Cache dir:    {CACHE_DIR}/  (force={args.force})")
    print("=" * 80)

    article_urls = discover_article_urls(listing_urls, force=args.force, delay=args.delay)
    if args.limit:
        article_urls = article_urls[:args.limit]
    print(f"\nArticles to fetch: {len(article_urls)}")
    print("=" * 80)

    out: dict = {}
    failed: list[tuple[str, str]] = []
    total_images = 0
    for i, url in enumerate(article_urls, 1):
        try:
            html = get_html(url, force=args.force, delay=args.delay)
            entry = parse_article(url, html)
            out[url] = entry
            title = entry["metadata"]["title"] or "(no title)"
            n_img = entry["metadata"]["scraped_image_count"]
            total_images += n_img
            print(f"  [{i}/{len(article_urls)}] {title[:60]:<60}  imgs={n_img}")
        except Exception as e:
            print(f"  [{i}/{len(article_urls)}] FAILED {url}: {e}")
            failed.append((url, str(e)))

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print()
    print("=" * 80)
    print(f"Wrote {len(out)} articles → {OUTPUT_JSON}")
    print(f"Total images discovered: {total_images}")
    if failed:
        print(f"Failed: {len(failed)}")
        for url, err in failed:
            print(f"  - {url}: {err}")


if __name__ == "__main__":
    main()
