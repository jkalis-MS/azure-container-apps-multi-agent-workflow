"""Tool: Search Microsoft Learn documentation via the Learn API with sub-query expansion."""

import asyncio
import os
import httpx

LEARN_SEARCH_URL = os.getenv("LEARN_SEARCH_URL", "https://learn.microsoft.com/api/search")


def _build_queries(topic: str) -> list[str]:
    """Build a set of search queries from the topic for broader coverage.

    Returns the original topic plus 2-3 word sub-phrases that capture
    important aspects of the query.
    """
    queries = [topic]
    words = topic.split()
    if len(words) <= 2:
        return queries

    # Generate overlapping bigrams: "dynamic sessions", "sessions AI", etc.
    for i in range(len(words) - 1):
        bigram = f"{words[i]} {words[i + 1]}"
        if bigram.lower() != topic.lower():
            queries.append(bigram)

    # Add "Azure <keyword>" for each meaningful keyword
    for w in words:
        if len(w) >= 2 and w.lower() not in {"the", "and", "for", "with", "from", "about", "azure"}:
            q = f"Azure {w}"
            if q.lower() != topic.lower():
                queries.append(q)

    return queries


async def _search_once(client: httpx.AsyncClient, query: str, top: int) -> list[dict]:
    """Execute a single Learn search API call."""
    try:
        resp = await client.get(
            LEARN_SEARCH_URL,
            params={"search": query, "locale": "en-us", "$top": top},
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("results", [])[:top]:
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "description": item.get("description", ""),
                "relevance": "primary",
            })
        return results
    except httpx.HTTPError:
        return []


async def search_learn(topic: str, top: int = 5) -> list[dict]:
    """Search Microsoft Learn using the topic plus expanded sub-queries."""
    queries = _build_queries(topic)
    per_query = max(top // len(queries) + 5, 10)

    async with httpx.AsyncClient(timeout=15.0) as client:
        tasks = [_search_once(client, q, per_query) for q in queries]
        all_results = await asyncio.gather(*tasks)

    # Merge and deduplicate by URL, preserving discovery order
    seen: set[str] = set()
    merged: list[dict] = []
    for batch in all_results:
        for item in batch:
            url = item.get("url", "")
            if url and url not in seen:
                seen.add(url)
                merged.append(item)

    if not merged:
        return [{"title": f"Search for: {topic}", "url": LEARN_SEARCH_URL, "description": "Search returned no results", "relevance": "fallback"}]

    return merged[:top]
