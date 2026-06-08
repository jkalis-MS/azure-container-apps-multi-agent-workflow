"""Tool: Extract trusted Azure-ecosystem links from fetched HTML pages.

Performs a depth-1 crawl of already-fetched content to discover additional
high-quality sources (docs, product pages, samples) that blog authors and 
doc writers linked to editorially.
"""

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

# Domains we trust — editorially curated links on these are high signal
TRUSTED_DOMAINS = (
    "learn.microsoft.com",
    "github.com",
    "azure.microsoft.com",
    "techcommunity.microsoft.com",
    "devblogs.microsoft.com",
    "developer.microsoft.com",
    "code.visualstudio.com",
    "marketplace.visualstudio.com",
)

# URL patterns to skip — low-value or navigational
_SKIP_PATTERNS = re.compile(
    r"(#|/feed|/rss|/login|/signup|/search|/profile|/settings"
    r"|/tag/|/category/|/page/\d|/archive"
    r"|\.pdf$|\.zip$|\.png$|\.jpg$|\.gif$|\.svg$)",
    re.IGNORECASE,
)


def _is_trusted(url: str) -> bool:
    """Check if a URL belongs to a trusted Azure-ecosystem domain."""
    try:
        host = urlparse(url).hostname or ""
        return any(host == d or host.endswith("." + d) for d in TRUSTED_DOMAINS)
    except Exception:
        return False


def _normalise(url: str) -> str:
    """Strip fragments and trailing slashes for dedup."""
    parsed = urlparse(url)
    clean = parsed._replace(fragment="").geturl()
    return clean.rstrip("/")


def extract_trusted_links(html: str, base_url: str) -> list[dict]:
    """Extract trusted Azure-ecosystem links from an HTML page.

    Returns a list of {"url": ..., "text": ...} for each unique trusted link
    found in the content area of the page.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Focus on content area — skip nav, footer, header, sidebar
    for tag in soup(["nav", "footer", "header", "aside", "script", "style"]):
        tag.decompose()

    # Prefer main content container
    content = (
        soup.find("main")
        or soup.find("article")
        or soup.find(attrs={"role": "main"})
        or soup.body
        or soup
    )

    seen: set[str] = set()
    links: list[dict] = []

    for a_tag in content.find_all("a", href=True):
        href = a_tag["href"]

        # Resolve relative URLs
        url = urljoin(base_url, href)

        # Normalise for dedup
        norm = _normalise(url)

        # Filter
        if norm in seen:
            continue
        if _SKIP_PATTERNS.search(url):
            continue
        if not _is_trusted(url):
            continue
        # Skip self-links
        if _normalise(base_url) == norm:
            continue

        seen.add(norm)
        link_text = a_tag.get_text(strip=True)[:120] or ""
        links.append({"url": url, "text": link_text})

    return links


def extract_links_from_fetched(
    fetched_content: list[dict],
    existing_urls: set[str],
) -> list[dict]:
    """Extract trusted links from a batch of already-fetched pages.

    Args:
        fetched_content: List of dicts from fetch_multiple(), each having
            "url" and optionally "html" (raw HTML) or "content" (text).
        existing_urls: Set of URLs already discovered — used for dedup.

    Returns:
        Deduplicated list of {"url": ..., "text": ..., "found_on": ...}
        sorted by most linked first.
    """
    # Track how many pages link to each URL (popularity signal)
    url_info: dict[str, dict] = {}  # url -> {text, found_on, count}

    normalised_existing = {_normalise(u) for u in existing_urls}

    for page in fetched_content:
        page_url = page.get("url", "")
        html = page.get("html", "")
        if not html:
            continue

        discovered = extract_trusted_links(html, page_url)
        for link in discovered:
            norm = _normalise(link["url"])
            if norm in normalised_existing:
                continue
            if norm in url_info:
                url_info[norm]["count"] += 1
            else:
                url_info[norm] = {
                    "url": link["url"],
                    "text": link["text"],
                    "found_on": page_url,
                    "count": 1,
                }

    # Sort by link count (most referenced first), cap at 30
    results = sorted(url_info.values(), key=lambda x: x["count"], reverse=True)
    return [{"url": r["url"], "text": r["text"], "found_on": r["found_on"]} for r in results[:30]]
