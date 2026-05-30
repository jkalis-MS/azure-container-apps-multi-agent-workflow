"""Tool: Search Azure blogs, Tech Community, Azure Updates, and product pages."""

import asyncio
import httpx
import xml.etree.ElementTree as ET
from html import unescape
import re

# Priority Azure RSS feeds — multiple feeds per source for broader coverage
AZURE_BLOG_FEEDS = [
    "https://azure.microsoft.com/blog/feed/",
]
TECH_COMMUNITY_FEEDS = [
    "https://techcommunity.microsoft.com/t5/azure/ct-p/Azure/rss/board?board.id=AzureBlog",
    "https://techcommunity.microsoft.com/t5/apps-on-azure-blog/bg-p/AppsonAzureBlog/rss",
    "https://techcommunity.microsoft.com/t5/azure-for-operators-blog/bg-p/AzureforOperatorsBlog/rss",
]
AZURE_UPDATES_FEED = "https://azure.microsoft.com/updates/feed/"

# Common English stopwords that pollute keyword matching
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "are", "was",
    "has", "have", "not", "but", "how", "what", "why", "when", "who",
    "can", "will", "its", "our", "your", "about", "into", "using",
    "use", "new", "all", "get", "also", "been", "more", "most",
})


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = unescape(clean)
    return re.sub(r"\s+", " ", clean).strip()


def _extract_keywords(topic: str) -> list[str]:
    """Extract meaningful keywords from a topic string."""
    return [
        kw.strip().lower()
        for kw in topic.split()
        if len(kw.strip()) >= 2 and kw.strip().lower() not in _STOPWORDS
    ]


def _match_topic(text: str, keywords: list[str]) -> bool:
    """Check if text contains any of the keywords (case-insensitive)."""
    lower = text.lower()
    return any(kw in lower for kw in keywords)


async def _fetch_rss(url: str, keywords: list[str], source_name: str, top: int = 5) -> list[dict]:
    """Fetch and parse RSS feed, filtering by topic relevance."""
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)

            # Handle both RSS 2.0 and Atom feeds
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            items = root.findall(".//item") or root.findall(".//atom:entry", ns)

            results = []
            for item in items:
                title = item.findtext("title") or item.findtext("atom:title", "", ns)
                link = item.findtext("link") or ""
                if not link:
                    link_el = item.find("atom:link", ns)
                    link = link_el.get("href", "") if link_el is not None else ""
                desc = item.findtext("description") or item.findtext("atom:summary", "", ns) or ""
                content = item.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") or desc

                searchable = f"{title} {_strip_html(desc)} {_strip_html(content)}"
                if _match_topic(searchable, keywords):
                    results.append({
                        "title": _strip_html(title),
                        "url": link.strip(),
                        "description": _strip_html(desc)[:300],
                        "source": source_name,
                    })
                if len(results) >= top:
                    break

            return results
        except Exception:
            return []


async def _fetch_multiple_feeds(
    feeds: list[str], topic: str, source_name: str, top: int,
) -> list[dict]:
    """Fetch multiple RSS feeds concurrently and merge results."""
    keywords = _extract_keywords(topic)
    if not keywords:
        return []

    per_feed = max(top, 20)  # fetch generously per feed, trim after merge
    tasks = [_fetch_rss(url, keywords, source_name, per_feed) for url in feeds]
    feed_results = await asyncio.gather(*tasks)

    # Merge and deduplicate by URL
    seen: set[str] = set()
    merged: list[dict] = []
    for batch in feed_results:
        for item in batch:
            url = item.get("url", "")
            if url and url not in seen:
                seen.add(url)
                merged.append(item)

    return merged[:top]


async def search_azure_blogs(topic: str, top: int = 3) -> list[dict]:
    """Search the main Azure Blog RSS feeds."""
    return await _fetch_multiple_feeds(AZURE_BLOG_FEEDS, topic, "Azure Blog", top)


async def search_tech_community(topic: str, top: int = 3) -> list[dict]:
    """Search Azure Tech Community blog posts across multiple boards."""
    return await _fetch_multiple_feeds(TECH_COMMUNITY_FEEDS, topic, "Tech Community", top)


async def search_azure_updates(topic: str, top: int = 3) -> list[dict]:
    """Search Azure Updates / Roadmap feed."""
    keywords = _extract_keywords(topic)
    if not keywords:
        return []
    return await _fetch_rss(AZURE_UPDATES_FEED, keywords, "Azure Updates", top)
