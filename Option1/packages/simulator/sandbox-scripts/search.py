"""
search.py — FALLBACK script that runs INSIDE a sandbox.

This script is NOT agent-generated. It is a pre-written fallback that runs when
GPT-4o-generated search code fails (bad output, crash, etc.). The agent.py
pipeline transparently switches to this script without the user knowing.

Usage: python search.py "search query here"

Search strategy:
  1. Wikipedia API (primary — very reliable from cloud IPs)
  2. Wikipedia article fetch (for squad/roster queries)
  3. Google search scraping (backup)

Also probes sports websites (ESPN, Sky, Marca, FIFA, BBC) to demonstrate
the sandbox egress firewall — some are blocked (403), some allowed.

Prints results as JSON to stdout.
"""

import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse
import re
import html as html_mod


# Sports sites to probe — some allowed by egress policy, others blocked.
SPORTS_PROBES = [
    {"host": "www.espn.com",       "url": "https://www.espn.com/soccer/",                "label": "ESPN"},
    {"host": "www.skysports.com",  "url": "https://www.skysports.com/football",          "label": "Sky Sports"},
    {"host": "www.marca.com",      "url": "https://www.marca.com/en/football.html",      "label": "Marca"},
    {"host": "www.fifa.com",       "url": "https://www.fifa.com/fifaplus/en/tournaments", "label": "FIFA"},
    {"host": "www.bbc.co.uk",      "url": "https://www.bbc.co.uk/sport/football",        "label": "BBC Sport"},
]


def wikipedia_search(query: str) -> list[dict]:
    """Search using Wikipedia API — very reliable from any environment."""
    encoded = urllib.parse.quote(query)
    url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={encoded}&format=json&srlimit=5&srprop=snippet"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "WorldCupSimulator/1.0 (demo; contact@example.com)")

    results = []
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        for item in data.get("query", {}).get("search", []):
            title = item.get("title", "")
            snippet = html_mod.unescape(re.sub(r'<[^>]+>', '', item.get("snippet", "")))
            wiki_url = "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")
            results.append({"name": title, "url": wiki_url, "snippet": snippet})
    except Exception as exc:
        results.append({"error": f"Wikipedia search failed: {exc}"})

    return results


def wikipedia_article(title: str) -> str:
    """Fetch full Wikipedia article text for detailed squad/roster info."""
    encoded = urllib.parse.quote(title)
    url = f"https://en.wikipedia.org/w/api.php?action=query&titles={encoded}&prop=extracts&exintro=false&explaintext=true&format=json"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "WorldCupSimulator/1.0 (demo; contact@example.com)")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            return page.get("extract", "")[:5000]
    except Exception:
        pass
    return ""


def google_search(query: str) -> list[dict]:
    """Backup: scrape Google search results."""
    encoded = urllib.parse.quote(query)
    url = f"https://www.google.com/search?q={encoded}&num=5"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

    results = []
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            page = resp.read().decode("utf-8", errors="replace")

        # Extract title+link pairs from Google results
        links = re.findall(r'<a[^>]+href="(/url\?q=([^&"]+)[^"]*)"[^>]*>(.*?)</a>', page, re.DOTALL)
        for _, raw_url, title_html in links[:5]:
            clean_url = urllib.parse.unquote(raw_url)
            if clean_url.startswith("http") and "google.com" not in clean_url:
                title = html_mod.unescape(re.sub(r'<[^>]+>', '', title_html)).strip()
                if title:
                    results.append({"name": title, "url": clean_url, "snippet": ""})
    except Exception as exc:
        results.append({"error": f"Google search failed: {exc}"})

    return results


def web_search(query: str) -> list[dict]:
    """Multi-engine search: Wikipedia first, Google as backup."""
    results = wikipedia_search(query)

    # If query mentions a national team, fetch the article for roster details
    query_lower = query.lower()
    if "squad" in query_lower or "roster" in query_lower or "team" in query_lower:
        if "mexico" in query_lower:
            article_text = wikipedia_article("Mexico national football team")
            if article_text:
                results.append({"name": "Mexico national football team (full article)", "url": "https://en.wikipedia.org/wiki/Mexico_national_football_team", "snippet": article_text[:2000]})
        if "czech" in query_lower:
            article_text = wikipedia_article("Czech Republic national football team")
            if article_text:
                results.append({"name": "Czech Republic national football team (full article)", "url": "https://en.wikipedia.org/wiki/Czech_Republic_national_football_team", "snippet": article_text[:2000]})

    # Try Google as backup if Wikipedia gave few results
    if len([r for r in results if "error" not in r]) < 3:
        google_results = google_search(query)
        results.extend(google_results)

    return results


def probe_sports_sites() -> list[dict]:
    """Try fetching sports websites. Returns egress probe results."""
    results = []
    for site in SPORTS_PROBES:
        try:
            req = urllib.request.Request(site["url"])
            req.add_header("User-Agent", "Mozilla/5.0")
            with urllib.request.urlopen(req, timeout=8) as resp:
                results.append({
                    "host": site["host"],
                    "label": site["label"],
                    "blocked": False,
                    "status": resp.status,
                })
        except urllib.error.HTTPError as exc:
            is_blocked = exc.code == 403
            results.append({
                "host": site["host"],
                "label": site["label"],
                "blocked": is_blocked,
                "status": exc.code,
                "error": f"HTTP {exc.code} Forbidden" if is_blocked else f"HTTP {exc.code}",
            })
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            reason = str(exc.reason) if hasattr(exc, "reason") else str(exc)
            results.append({
                "host": site["host"],
                "label": site["label"],
                "blocked": True,
                "error": reason,
            })
        except Exception as exc:
            results.append({
                "host": site["host"],
                "label": site["label"],
                "blocked": True,
                "error": str(exc),
            })
    return results


def main():
    query = sys.argv[1] if len(sys.argv) > 1 else input().strip()

    # Run web search first (the actual research)
    results = web_search(query)

    # Then probe sports sites for egress demo
    egress_probes = []
    if os.environ.get("PROBE_SPORTS") == "1":
        egress_probes = probe_sports_sites()

    output = {
        "query": query,
        "results": results,
    }
    if egress_probes:
        output["egress_probes"] = egress_probes

    try:
        with open("/tmp/results.json", "w") as f:
            json.dump(output, f, indent=2)
    except OSError:
        pass

    print(json.dumps(output))


if __name__ == "__main__":
    main()
