#!/usr/bin/env python3
"""
Clean and structure scraped help articles JSON for RAG ingestion.
Retains important content, filters out noise, adds comprehensive metadata.
INCLUDES IMAGE POSITION TRACKING for contextual understanding.
"""
import json
import re
from typing import Dict, List, Any, Tuple
from datetime import datetime
from html.parser import HTMLParser

# ===== CONFIG =====
INPUT_JSON = "data/articles/scraped_help_articles.json"
OUTPUT_JSON = "data/articles/cleaned_help_articles.json"

# Image filtering - exclude these patterns from content images
EXCLUDE_IMAGE_PATTERNS = [
    r"freshworks.*logo",
    r"no-results\.png",
    r"fav_icon",
    r"apple-touch-icon",
    r"navbar",
    r"icon-",
    r"<your-company>.*logo",
    r"brand",
]

# Whole-block chrome patterns. If a block's text matches any of these
# (case-insensitive, anchored at start where appropriate), the entire block is
# dropped rather than just having pieces stripped. NOISE_PATTERNS below only
# works on concatenated text — it can't span multiple per-block snippets like
# "Norwegian support" / "Telephone: ..." / "Mail: info@<your-company>.com" which
# arrive as separate paragraph blocks.
CHROME_BLOCK_PATTERNS = [
    r'^was this article helpful',
    r'^thank you for your feedback',
    r"^that[''`]s great",
    r"^sorry[!]? we couldn[''`]?t be helpful",
    r'^print$',
    r'^feedback sent',
    r'^need more information difficult to understand',
    r'^select at least one of the reasons',
    r'^please give your comments',
    r'^captcha verification',
    r'^your e-mail address',
    r'^let us know how can we improve',
    r'^norwegian support$',
    r'^swedish support$',
    r'^telephone:\s*\+',
    r'^mail:\s*info@<your-company>\.com',
    r'^opening hours',
    r'^this knowledgebase is licensed',
    r'^accept all(\s+cookies)?$',
    r'^view cookies$',
    r'^cookie preferences manager$',
    r'^strictly necessary cookies',
    r'^we use cookies',
    r'^you can learn more about',
    r'^because we respect your right',
    r'^these cookies are necessary',
    r'^articles in this folder',
    r'^you may like to read',
    r'^your privacy(\s+strictly necessary cookies)?$',
    r'^home\s+knowledge base',
    r'^skip to main content',
    r'^all articles\s+recent searches',
    r'^no recent searches',
    r'^popular articles',
    r'^articles view all',
    r'^topics view all',
    r'^tickets view all',
    r'^login\s+sign up',
    r'^modified on\s',
    # JS artifacts
    r"^document\.queryselectorall",
    r"^const attachment_error_image",
    r"^\[\s*[''`]click[''`]",
    r"^0 of 0\b",
    r"^footer\s+a\s+\{",
    r"^[\w\.]+\.<your-company>\.com\s+\d",  # cookie table rows like "_fw_crm_v <your-company>.your help center provider.com 1 year"
]
_CHROME_RE = re.compile('|'.join(f'({p})' for p in CHROME_BLOCK_PATTERNS),
                         flags=re.IGNORECASE)

def is_chrome_block(text: str) -> bool:
    """True if a block's text is page chrome (cookie banner, support footer,
    JS artifact, knowledge-base nav) and should be dropped entirely."""
    t = text.strip()
    if not t:
        return True
    return bool(_CHROME_RE.match(t))

# Text noise patterns to remove (appears in all articles)
NOISE_PATTERNS = [
    # Navigation chrome
    r"Home Knowledge base.*?<YOUR_PRODUCT> \.\.\.",
    r"Knowledge base <YOUR_COMPANY> One <YOUR_PRODUCT>",
    r"All Articles Recent Searches Clear all",
    r"No recent searches",
    r"Popular Articles",
    r"Articles View all",
    r"Topics View all",
    r"Tickets View all",
    r"Sorry! nothing found for",

    # Footer/metadata noise
    r"Skip to main content",
    r"Click here for more information about the cookies",
    r"Cookie Preferences Manager",
    r"Accept All Cookies",
    r"Accept All",
    r"View Cookies",
    r"CAPTCHA verification is required",
    r"Was this article helpful\?",
    r"Thank you for your feedback",
    r"Sorry! We couldn't be helpful",
    r"Print",
    r"Modified on.*?PM",
    r"Norwegian support.*?Mail: info@<your-company>\.com",
    r"Swedish support.*?Mail: info@<your-company>\.com",
    r"Telephone:.*?info@<your-company>\.com",
    r"Login\s+Sign up",
    r"Home\s+Knowledge base\s+Submit a ticket",

    # Cookie table (appears in many articles)
    r"Strictly Necessary Cookies.*?wf_filter\s+<your-company>\.your help center provider\.com\s+Session.*?Denotes which filter is applied for the tickets list\.",
    r"Contains the locale code for the user.*?Denotes which filter is applied",
    r"helpdesk_session.*?wf_filter.*?Session",

    # Image noise
    r"\[IMAGE:.*?views count\]",
    r"\[IMAGE:.*?logo\]",
    r"\[IMAGE:.*?banner\]",

    # JavaScript/technical artifacts
    r"document\.querySelectorAll.*?};",
    r"const attachment_error_image.*?};",
]

# ===== HELPER FUNCTIONS =====

def clean_title(title: str) -> str:
    """Extract clean article title from full title string."""
    title = re.sub(r'\s*:\s*<YOUR_COMPANY>\s*-\s*Customer Support Portal\s*$', '', title)
    title = re.sub(r'^<YOUR_COMPANY> One\s*-\s*Recruitment\s*-\s*', '', title)
    return title.strip()

def clean_text(text: str) -> str:
    """Remove noise patterns from text content."""
    cleaned = text
    for pattern in NOISE_PATTERNS:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE | re.DOTALL)

    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    cleaned = re.sub(r' {2,}', ' ', cleaned)
    return cleaned.strip()

def is_content_image(img: Dict[str, Any]) -> bool:
    """Determine if image is actual content (not logo/UI element).

    Filters chrome icons, SVGs (always icons by convention), and your help center provider
    tracking endpoints (URLs ending in /hit, /track, /pixel) so they never
    reach LlamaParse — saves cost and avoids NO_CONTENT_HERE responses on
    non-image URLs.
    """
    src = img.get('src', '').lower()
    alt = img.get('alt', '').lower()

    # SVGs: always icons, never content screenshots in this corpus.
    if src.endswith('.svg') or src.endswith('.svgz'):
        return False

    # Tracking / non-image endpoints (your help center provider hit-trackers etc.)
    parsed_path = src.split('?', 1)[0].rstrip('/')
    if parsed_path.endswith(('/hit', '/track', '/pixel', '/beacon', '/impression')):
        return False
    # No file extension and no recognizable image suffix anywhere → likely tracking
    if '.' not in parsed_path.rsplit('/', 1)[-1] and any(
            ep in parsed_path for ep in ('/hit', '/track', '/pixel')):
        return False

    for pattern in EXCLUDE_IMAGE_PATTERNS:
        if re.search(pattern, src, re.IGNORECASE) or re.search(pattern, alt, re.IGNORECASE):
            return False

    try:
        width = int(img.get('width', 0))
        height = int(img.get('height', 0))
        if width and height and (width < 50 or height < 50):
            return False
    except (ValueError, TypeError):
        pass

    ui_keywords = ['logo', 'icon', 'banner', 'menu', 'button', 'navigation']
    if any(keyword in alt for keyword in ui_keywords):
        return False

    return True

class ArticleContentParser(HTMLParser):
    """Parse HTML to extract structured content with image positions."""

    def __init__(self):
        super().__init__()
        self.content_blocks = []
        self.current_section = None
        self.text_buffer = []
        self.in_main = False
        self.content_started = False  # Only start capturing after first h1
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)

        # Track when we're in main content
        # Look for article tag, main content ID, or article-body class
        if (tag == 'article' or
            attrs_dict.get('id') == 'fw-main-content' or
            attrs_dict.get('class') in (['article-body'], 'article-body')):
            self.in_main = True

        if not self.in_main:
            return

        # Handle headers
        if tag in ['h1', 'h2', 'h3', 'h4']:
            # Start capturing content when we see the first h1
            if tag == 'h1' and not self.content_started:
                self.content_started = True

            if not self.content_started:
                return

            self._flush_text()
            self.current_tag = tag
            self.text_buffer = []

        # Handle images
        elif tag == 'img':
            if not self.content_started:
                return

            self._flush_text()
            src = attrs_dict.get('src', '')
            alt = attrs_dict.get('alt', '')

            # Filter content images
            if src and is_content_image(attrs_dict):
                self.content_blocks.append({
                    "type": "image",
                    "src": src,
                    "alt": alt,
                    "section": self.current_section,
                    "position": len(self.content_blocks)
                })

        # Handle paragraphs
        elif tag == 'p':
            if not self.content_started:
                return

            self._flush_text()
            self.current_tag = 'p'
            self.text_buffer = []

        # Handle lists
        elif tag in ['ul', 'ol']:
            if not self.content_started:
                return

            self._flush_text()
            self.current_tag = tag
            self.text_buffer = []

    def handle_endtag(self, tag):
        if tag in ['h1', 'h2', 'h3', 'h4', 'p', 'ul', 'ol']:
            self._flush_text()

        if tag == 'article':
            self.in_main = False

    def handle_data(self, data):
        # Only capture text after we've started the actual content
        if self.in_main and self.content_started and data.strip():
            self.text_buffer.append(data.strip())

    def _flush_text(self):
        """Flush accumulated text to content blocks."""
        if not self.text_buffer:
            return

        text = ' '.join(self.text_buffer).strip()
        if not text or len(text) < 3:
            self.text_buffer = []
            return

        tag = getattr(self, 'current_tag', 'p')

        if tag in ['h1', 'h2', 'h3', 'h4']:
            self.current_section = text
            self.content_blocks.append({
                "type": "header",
                "level": int(tag[1]),
                "text": text,
                "position": len(self.content_blocks)
            })
        else:
            self.content_blocks.append({
                "type": "paragraph",
                "text": text,
                "section": self.current_section,
                "position": len(self.content_blocks)
            })

        self.text_buffer = []

def parse_article_content(raw_html: str) -> Tuple[List[Dict[str, Any]], str]:
    """Parse HTML to get structured content blocks with image positions."""
    parser = ArticleContentParser()
    parser.feed(raw_html)

    # Build full text from blocks
    text_parts = []
    for block in parser.content_blocks:
        if block['type'] == 'header':
            text_parts.append(f"\n{'#' * block['level']} {block['text']}\n")
        elif block['type'] == 'paragraph':
            text_parts.append(block['text'])
        elif block['type'] == 'image':
            # Include image reference in text
            img_ref = f"\n[IMAGE: {block['alt'] or 'Screenshot'}]\n"
            text_parts.append(img_ref)

    full_text = '\n\n'.join(text_parts)
    return parser.content_blocks, full_text

def extract_images_with_context(content_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract images with surrounding context for better understanding."""
    images = []

    for i, block in enumerate(content_blocks):
        if block['type'] != 'image':
            continue

        # Get context before and after
        context_before = []
        context_after = []

        # Look back for context (up to 2 blocks)
        for j in range(max(0, i-2), i):
            if content_blocks[j]['type'] in ['paragraph', 'header']:
                context_before.append(content_blocks[j]['text'])

        # Look ahead for context (up to 2 blocks)
        for j in range(i+1, min(len(content_blocks), i+3)):
            if content_blocks[j]['type'] in ['paragraph', 'header']:
                context_after.append(content_blocks[j]['text'])

        images.append({
            "src": block['src'],
            "alt": block['alt'],
            "description": block['alt'] or "Screenshot",
            "position": block['position'],
            "section_header": block.get('section', ''),
            "context_before": ' '.join(context_before[-2:]),  # Last 2 items
            "context_after": ' '.join(context_after[:2]),     # First 2 items
        })

    return images

def extract_keywords(article: Dict[str, Any]) -> List[str]:
    """Extract keywords from article metadata."""
    keywords = []

    meta_keywords = article.get('metadata', {}).get('meta_keywords', '')
    if meta_keywords:
        keywords.extend([k.strip() for k in meta_keywords.split(',') if k.strip()])

    headers = article.get('text_content', {}).get('headers', {})
    for header_list in headers.values():
        keywords.extend(header_list)

    keywords = list(set(k for k in keywords if k and len(k) > 2))
    return keywords[:20]

def calculate_reading_time(text: str) -> int:
    """Estimate reading time in minutes (assumes 200 words/min)."""
    word_count = len(text.split())
    return max(1, round(word_count / 200))

def clean_article(url: str, article: Dict[str, Any]) -> Dict[str, Any]:
    """Transform raw scraped article into clean, structured format."""
    metadata = article.get('metadata', {})
    text_content = article.get('text_content', {})
    raw_html = article.get('raw_html', '')

    # Extract core data
    title = clean_title(metadata.get('title', ''))
    description = metadata.get('meta_description', '')
    url_canonical = metadata.get('url', url)

    # Parse HTML to get structured content with image positions
    content_blocks, structured_text = parse_article_content(raw_html)

    # Clean the text
    clean_text_content = clean_text(structured_text)

    # Strip chrome from blocks before they reach the markdown step. Two-pass:
    # 1) drop whole blocks whose text matches a CHROME_BLOCK_PATTERN (cookie
    #    banners, support-contact footers, JS artifacts, KB navigation)
    # 2) apply NOISE_PATTERNS substring-stripping to surviving paragraphs/headers
    cleaned_blocks = []
    for block in content_blocks:
        if block.get('type') in ('paragraph', 'header'):
            text = block.get('text', '')
            if is_chrome_block(text):
                continue
            cleaned_text = clean_text(text)
            if not cleaned_text.strip():
                continue
            if is_chrome_block(cleaned_text):  # may match after NOISE_PATTERNS strips bits
                continue
            block = {**block, 'text': cleaned_text}
        cleaned_blocks.append(block)
    # Re-number positions so they stay contiguous after dropping noise blocks
    for i, b in enumerate(cleaned_blocks):
        b['position'] = i
    content_blocks = cleaned_blocks

    # Extract images with context (uses the now-cleaned blocks)
    images_with_context = extract_images_with_context(content_blocks)

    # Extract headers for navigation
    headers = text_content.get('headers', {})

    # Calculate metrics
    word_count = len(clean_text_content.split())
    char_count = len(clean_text_content)

    # Build cleaned article
    cleaned = {
        "metadata": {
            # Source info
            "source": "helpcenter",
            "url": url_canonical,
            "title": title,
            "description": description,
            "keywords": extract_keywords(article),

            # Content metrics
            "word_count": word_count,
            "char_count": char_count,
            "reading_time_minutes": calculate_reading_time(clean_text_content),

            # Images with position and context
            "has_images": len(images_with_context) > 0,
            "image_count": len(images_with_context),
            "images": images_with_context,

            # Structure
            "headers": {
                "h1": headers.get('h1', []),
                "h2": headers.get('h2', []),
                "h3": headers.get('h3', []),
            },

            # Tables (if present)
            "has_tables": len(article.get('tables', [])) > 0,
            "tables": article.get('tables', []),

            # Timestamps
            "extracted_at": article.get('extraction_timestamp', ''),
            "cleaned_at": datetime.utcnow().isoformat() + 'Z',
        },

        # Main content - structured blocks preserve order
        "content": {
            "text": clean_text_content,
            "blocks": content_blocks,  # Sequential content including images
            "paragraphs": [clean_text(p) for p in text_content.get('paragraphs', []) if p.strip()],
            "lists": text_content.get('lists', {}),
        },

        # Raw data (for reference)
        "raw": {
            "links_count": len(article.get('links', [])),
            "forms_count": len(article.get('forms', [])),
        }
    }

    return cleaned

# ===== MAIN =====

def main():
    print(f"Loading articles from {INPUT_JSON}...")
    with open(INPUT_JSON, 'r', encoding='utf-8') as f:
        raw_articles = json.load(f)

    print(f"Found {len(raw_articles)} articles to clean")
    print("=" * 80)

    cleaned_articles = {}

    for idx, (url, article) in enumerate(raw_articles.items(), 1):
        title = clean_title(article.get('metadata', {}).get('title', 'Unknown'))
        print(f"\n[{idx}/{len(raw_articles)}] Processing: {title}")

        cleaned = clean_article(url, article)

        # Use title-based key for easier lookup
        article_key = title.lower().replace(' ', '_').replace('-', '_')
        cleaned_articles[article_key] = cleaned

        # Print stats
        print(f"  ✓ Word count: {cleaned['metadata']['word_count']}")
        print(f"  ✓ Images: {cleaned['metadata']['image_count']}")
        print(f"  ✓ Content blocks: {len(cleaned['content']['blocks'])}")
        print(f"  ✓ Headers: {len(cleaned['metadata']['headers']['h2'])} sections")
        print(f"  ✓ Reading time: {cleaned['metadata']['reading_time_minutes']} min")

        # Show sample image context
        if cleaned['metadata']['images']:
            img = cleaned['metadata']['images'][0]
            print(f"  ✓ First image in section: '{img['section_header']}'")

    print("\n" + "=" * 80)
    print(f"Writing cleaned articles to {OUTPUT_JSON}...")

    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(cleaned_articles, f, ensure_ascii=False, indent=2)

    print(f"✓ Successfully cleaned {len(cleaned_articles)} articles")

    # Print summary statistics
    total_words = sum(a['metadata']['word_count'] for a in cleaned_articles.values())
    total_images = sum(a['metadata']['image_count'] for a in cleaned_articles.values())
    total_blocks = sum(len(a['content']['blocks']) for a in cleaned_articles.values())
    avg_reading_time = sum(a['metadata']['reading_time_minutes'] for a in cleaned_articles.values()) / len(cleaned_articles)

    print("\n" + "=" * 80)
    print("SUMMARY STATISTICS:")
    print("=" * 80)
    print(f"Total articles: {len(cleaned_articles)}")
    print(f"Total words: {total_words:,}")
    print(f"Total content blocks: {total_blocks}")
    print(f"Total content images: {total_images}")
    print(f"Average reading time: {avg_reading_time:.1f} minutes")
    print(f"Average words per article: {total_words // len(cleaned_articles):,}")

    # List articles with most images
    articles_by_images = sorted(
        cleaned_articles.items(),
        key=lambda x: x[1]['metadata']['image_count'],
        reverse=True
    )

    print("\nArticles with most images:")
    for key, article in articles_by_images[:5]:
        print(f"  {article['metadata']['image_count']:2}x - {article['metadata']['title']}")

    # Show sample image with context
    print("\n" + "=" * 80)
    print("SAMPLE IMAGE WITH CONTEXT:")
    print("=" * 80)
    sample_article = articles_by_images[0][1]
    if sample_article['metadata']['images']:
        img = sample_article['metadata']['images'][0]
        print(f"Article: {sample_article['metadata']['title']}")
        print(f"Image alt: {img['alt']}")
        print(f"Section: {img['section_header']}")
        print(f"Position: Block #{img['position']}")
        print(f"Context before: {img['context_before'][:100]}...")
        print(f"Context after: {img['context_after'][:100]}...")

if __name__ == "__main__":
    main()
