"""Tool: Search GitHub for relevant repositories, prioritizing Azure-Samples."""

import os
import httpx

GITHUB_API = "https://api.github.com"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")


async def search_github_repos(topic: str, top: int = 5) -> list[dict]:
    """Search GitHub for repositories, prioritizing Azure-Samples org."""
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    all_results = []
    seen: set[str] = set()

    async def _search(query: str, source: str, per_page: int) -> None:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{GITHUB_API}/search/repositories",
                    params={"q": query, "sort": "stars", "per_page": per_page},
                    headers=headers,
                )
                resp.raise_for_status()
                for repo in resp.json().get("items", []):
                    url = repo.get("html_url", "")
                    if url and url not in seen:
                        seen.add(url)
                        all_results.append({
                            "name": repo.get("full_name", ""),
                            "url": url,
                            "stars": repo.get("stargazers_count", 0),
                            "description": repo.get("description", ""),
                            "language": repo.get("language", ""),
                            "source": source,
                        })
        except httpx.HTTPError:
            pass

    # Priority order: Azure-Samples → Azure org → broader Azure-related repos
    await _search(f"{topic} org:Azure-Samples", "Azure-Samples", top)
    await _search(f"{topic} org:Azure", "Azure", 10)
    await _search(f"{topic} azure in:readme,description", "Community", 10)

    return all_results[:top]
