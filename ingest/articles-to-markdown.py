#!/usr/bin/env python3
"""
Convert cleaned help articles to markdown format with LlamaParse image parsing.
Uses LlamaParse to convert screenshot images into structured markdown representations.

Test-mode flags (recommended before a full run, since LlamaParse costs per page):
    --only <key>     Process only one article by its key (see cleaned JSON keys).
    --limit N        Process only the first N articles (debugging).
    --output PATH    Override output path. When --only or --limit is set WITHOUT
                     this flag, output defaults to data/articles/markdown_help_articles.test.json
                     so the production output is never accidentally overwritten.
"""
import os
import sys
import json
import argparse
import requests
import tempfile
from typing import Dict, Any, List
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

# Load environment
load_dotenv(find_dotenv(usecwd=True), override=False)  # parent env (op run) wins over .env literals

# ===== CONFIG =====
INPUT_JSON = "data/articles/cleaned_help_articles.json"
OUTPUT_JSON_DEFAULT = "data/articles/markdown_help_articles.json"
OUTPUT_JSON_TEST = "data/articles/markdown_help_articles.test.json"
LLAMA_CLOUD_API_KEY = os.getenv("LLAMA_CLOUD_API_KEY")

if not LLAMA_CLOUD_API_KEY:
    sys.exit("LLAMA_CLOUD_API_KEY not set (check .env). Get one from https://cloud.llamaindex.ai")

# LlamaParse imports
from llama_cloud_services import LlamaParse

# ===== SETUP LLAMAPARSE =====
print("Initializing LlamaParse for UI screenshot parsing...")
parser = LlamaParse(
    api_key=LLAMA_CLOUD_API_KEY,
    parse_mode="parse_page_with_agent",
    model="openai-gpt-4-1-mini",
    high_res_ocr=True,
    adaptive_long_table=True,
    outlined_table_extraction=True,
    output_tables_as_HTML=True,
    precise_bounding_box=True,
)


def download_image(image_url: str, temp_dir: str) -> str:
    """Download image from URL to temporary file."""
    try:
        response = requests.get(image_url, timeout=30)
        response.raise_for_status()

        # Determine extension
        content_type = response.headers.get('content-type', '')
        if 'png' in content_type:
            ext = '.png'
        elif 'jpeg' in content_type or 'jpg' in content_type:
            ext = '.jpg'
        else:
            ext = '.png'  # default

        # Save to temp file
        temp_file = os.path.join(temp_dir, f"image_{hash(image_url)}{ext}")
        with open(temp_file, 'wb') as f:
            f.write(response.content)

        return temp_file

    except Exception as e:
        print(f"  [WARNING] Failed to download {image_url[:50]}...: {e}")
        return None


def parse_image_with_llamaparse(image_path: str, context_before: str, context_after: str) -> str:
    """
    Use LlamaParse to convert screenshot into markdown representation.

    Args:
        image_path: Local path to downloaded image
        context_before: Text appearing before the image
        context_after: Text appearing after the image

    Returns:
        Markdown representation of the UI
    """
    try:
        # Parse image with LlamaParse
        result = parser.parse(image_path)

        # Get markdown documents
        markdown_docs = result.get_markdown_documents(split_by_page=False)

        if not markdown_docs:
            return None

        # Extract markdown text
        markdown_text = markdown_docs[0].text if markdown_docs else ""

        # Add context header
        enhanced_markdown = f"""### Screenshot Analysis

**Context Before**: {context_before[-200:] if len(context_before) > 200 else context_before}

**UI Elements and Layout**:
{markdown_text}

**Context After**: {context_after[:200] if len(context_after) > 200 else context_after}
"""

        return enhanced_markdown

    except Exception as e:
        print(f"  [WARNING] LlamaParse failed for {image_path}: {e}")
        return None


def build_markdown_from_blocks(
    blocks: List[Dict[str, Any]],
    images_metadata: List[Dict[str, Any]],
    article_title: str,
    temp_dir: str
) -> str:
    """
    Build markdown string from content blocks, inserting LlamaParse results.

    Args:
        blocks: Sequential content blocks (headers, paragraphs, images)
        images_metadata: Image metadata with context
        article_title: Title of the article
        temp_dir: Temporary directory for image downloads

    Returns:
        Complete markdown string with parsed UI representations
    """
    markdown_parts = []

    # Create lookup for image metadata by position
    images_by_position = {img['position']: img for img in images_metadata}

    for block in blocks:
        block_type = block['type']
        position = block['position']

        if block_type == 'header':
            level = block['level']
            text = block['text']
            markdown_parts.append(f"\n{'#' * level} {text}\n")

        elif block_type == 'paragraph':
            text = block['text']
            markdown_parts.append(f"{text}\n")

        elif block_type == 'image':
            # Get image metadata
            img_meta = images_by_position.get(position, {})
            img_url = block.get('src', img_meta.get('src', ''))

            # Skip if no URL or relative URL
            if not img_url.startswith('http'):
                continue

            print(f"  Parsing screenshot at position {position}...")

            # Download image
            image_path = download_image(img_url, temp_dir)
            if not image_path:
                continue

            # Get context
            context_before = img_meta.get('context_before', '')
            context_after = img_meta.get('context_after', '')

            # Parse with LlamaParse
            ui_markdown = parse_image_with_llamaparse(
                image_path,
                context_before,
                context_after
            )

            # Clean up downloaded file
            try:
                os.remove(image_path)
            except:
                pass

            # Only include if parsing succeeded
            if ui_markdown:
                markdown_parts.append(f"\n---\n")
                markdown_parts.append(f"**📸 UI Screenshot Parsed**\n\n")
                markdown_parts.append(ui_markdown)
                markdown_parts.append(f"\n\n*[View original screenshot]({img_url})*\n")
                markdown_parts.append(f"\n---\n\n")

    return '\n'.join(markdown_parts)


def convert_article_to_markdown(article_key: str, article: Dict[str, Any], temp_dir: str) -> Dict[str, Any]:
    """
    Convert a single article to markdown format with LlamaParse.

    Args:
        article_key: Key for the article
        article: Article data from cleaned JSON
        temp_dir: Temporary directory for image downloads

    Returns:
        New article structure with markdown body
    """
    metadata = article['metadata']
    content = article['content']

    title = metadata['title']
    print(f"\n[Converting] {title}")
    print(f"  Screenshots to parse with LlamaParse: {metadata['image_count']}")

    # Build markdown from content blocks
    markdown_body = build_markdown_from_blocks(
        content['blocks'],
        metadata['images'],
        title,
        temp_dir
    )

    # Create new article structure
    markdown_article = {
        "metadata": {
            "source": metadata['source'],
            "url": metadata['url'],
            "title": title,
            "description": metadata['description'],
            "keywords": metadata['keywords'],
            "word_count": metadata['word_count'],
            "char_count": len(markdown_body),
            "reading_time_minutes": metadata['reading_time_minutes'],
            "image_count": metadata['image_count'],
            "has_images": metadata['has_images'],
            "format": "markdown_with_llamaparse",
            "original_extracted_at": metadata['extracted_at'],
            "markdown_converted_at": datetime.utcnow().isoformat() + 'Z',
        },
        "content": {
            "markdown": markdown_body,
            "format": "markdown_with_llamaparse_ui_parsing"
        }
    }

    return markdown_article


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="Process only this article key (e.g. 'getting_started')")
    ap.add_argument("--limit", type=int, default=None, help="Process only the first N articles")
    ap.add_argument("--output", default=None, help="Override output JSON path")
    args = ap.parse_args()

    print(f"Loading cleaned articles from {INPUT_JSON}...")

    try:
        with open(INPUT_JSON, 'r', encoding='utf-8') as f:
            cleaned_articles = json.load(f)
    except FileNotFoundError:
        sys.exit(f"ERROR: {INPUT_JSON} not found. Run clean-articles-json.py first.")

    # Apply test-mode filters
    is_test_run = bool(args.only or args.limit)
    if args.only:
        if args.only not in cleaned_articles:
            available = ", ".join(list(cleaned_articles.keys())[:5]) + ", ..."
            sys.exit(f"ERROR: --only '{args.only}' not found. Available keys (first 5): {available}")
        cleaned_articles = {args.only: cleaned_articles[args.only]}
    elif args.limit:
        cleaned_articles = dict(list(cleaned_articles.items())[:args.limit])

    # Output path: explicit override > test default > production default
    if args.output:
        output_path = args.output
    elif is_test_run:
        output_path = OUTPUT_JSON_TEST
        print(f"[test mode] Writing to {output_path} (production output untouched)")
    else:
        output_path = OUTPUT_JSON_DEFAULT

    print(f"Found {len(cleaned_articles)} articles to convert")
    print(f"Output: {output_path}")
    print("=" * 80)

    markdown_articles = {}

    # Create temp directory for image downloads
    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"Using temp directory: {temp_dir}")

        for idx, (article_key, article) in enumerate(cleaned_articles.items(), 1):
            print(f"\n[{idx}/{len(cleaned_articles)}]", end=" ")

            try:
                markdown_article = convert_article_to_markdown(article_key, article, temp_dir)
                markdown_articles[article_key] = markdown_article

                print(f"  ✓ Converted to markdown ({len(markdown_article['content']['markdown'])} chars)")

            except Exception as e:
                print(f"  ✗ ERROR: {e}")
                import traceback
                traceback.print_exc()
                continue

    print("\n" + "=" * 80)
    print(f"Writing markdown articles to {output_path}...")

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(markdown_articles, f, ensure_ascii=False, indent=2)

    print(f"✓ Successfully converted {len(markdown_articles)} articles to markdown")

    # Summary statistics
    total_chars = sum(len(a['content']['markdown']) for a in markdown_articles.values())
    total_images = sum(a['metadata']['image_count'] for a in markdown_articles.values())

    print("\n" + "=" * 80)
    print("SUMMARY:")
    print("=" * 80)
    print(f"Total articles converted: {len(markdown_articles)}")
    print(f"Total screenshots parsed with LlamaParse: {total_images}")
    print(f"Total markdown characters: {total_chars:,}")
    print(f"Average article size: {total_chars // len(markdown_articles):,} chars")

    # Show sample
    print("\n" + "=" * 80)
    print("SAMPLE MARKDOWN (first 1500 chars):")
    print("=" * 80)
    sample_article = list(markdown_articles.values())[0]
    print(sample_article['content']['markdown'][:1500])
    print("...")


if __name__ == "__main__":
    main()
