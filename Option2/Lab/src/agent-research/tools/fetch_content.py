"""Tool: Fetch actual content from URLs and extract readable text."""

import re
import httpx
from bs4 import BeautifulSoup

MAX_CONTENT_LENGTH = 3000  # chars per source to keep LLM context manageable


async def fetch_page_content(url: str) -> dict:
    """Fetch a URL and extract the main text content."""
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        try:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; AzureContentFactory/1.0)"
            })
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")

            # Remove non-content elements
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
                tag.decompose()

            # Try to find the main content area
            main = (
                soup.find("main")
                or soup.find("article")
                or soup.find(attrs={"role": "main"})
                or soup.find(class_=re.compile(r"content|article|post", re.I))
                or soup.body
            )

            if main is None:
                return {"url": url, "content": "", "html": resp.text, "error": "No content found"}

            text = main.get_text(separator="\n", strip=True)
            # Clean up excessive whitespace
            text = re.sub(r"\n{3,}", "\n\n", text)
            text = text[:MAX_CONTENT_LENGTH]

            return {"url": url, "content": text, "html": resp.text, "chars": len(text)}

        except Exception as e:
            return {"url": url, "content": "", "html": "", "error": str(e)}


async def fetch_multiple(urls: list[str], max_concurrent: int = 5) -> list[dict]:
    """Fetch content from multiple URLs concurrently."""
    import asyncio
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _fetch(url: str) -> dict:
        async with semaphore:
            return await fetch_page_content(url)

    tasks = [_fetch(url) for url in urls[:30]]  # cap at 30 URLs
    return await asyncio.gather(*tasks)
