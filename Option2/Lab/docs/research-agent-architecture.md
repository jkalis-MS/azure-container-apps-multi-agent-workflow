# Research Agent Architecture

LangGraph agent that searches Azure sources, LLM-ranks results by audience intent, fetches top content, extracts depth-1 linked references, and returns a structured brief via A2A protocol.

## Pipeline

```
plan_research → search_docs → search_code → search_blogs → rank_sources → fetch_content → synthesize
```

Built as a `StateGraph` in [agent.py](../src/agent-research/agent.py):

```python
class ResearchState(TypedDict):
    topic: str
    preferences: dict
    docs: list[dict]            # Microsoft Learn results
    repos: list[dict]           # GitHub Azure-Samples results
    blogs: list[dict]           # Azure Blog + Tech Community
    updates: list[dict]         # Azure Updates
    ranked_urls: list[str]      # LLM-prioritized URLs
    audience: str               # Target audience extracted by LLM
    fetched_content: list[dict] # Actual page content from top URLs
    linked_sources: list[dict]  # Depth-1 extracted links from fetched pages
    brief: dict | None
    iteration: int
```

## Search (4 sources, ~180 slots)

| Step | Source | Tool | Cap | Strategy |
|------|--------|------|-----|----------|
| `search_docs` | Microsoft Learn API | `tools/search_learn.py` | 40 | Sub-query expansion (full topic + bigrams + "Azure \<keyword\>"), merged & deduped |
| `search_code` | GitHub (Azure-Samples → Azure → community) | `tools/search_github.py` | 25 | 3 queries: org:Azure-Samples, org:Azure, `azure in:readme,description` |
| `search_blogs` | Azure Blog RSS (1 feed) + Tech Community RSS (3 board feeds) | `tools/search_azure_sources.py` | 50+50 | Multi-feed merge with dedup; keyword filter uses `≥ 2` chars with stopword list so "AI"/"ML" are preserved |
| `search_blogs` | Azure Updates RSS | `tools/search_azure_sources.py` | 15 | Same keyword filter |

### Keyword filtering

RSS feeds are keyword-matched against the topic. Keywords are extracted with a `≥ 2` character minimum and stopword removal (drops "the", "for", "with", etc. but keeps "AI", "ML", "VM"):

```python
def _extract_keywords(topic: str) -> list[str]:
    return [
        kw.strip().lower()
        for kw in topic.split()
        if len(kw.strip()) >= 2 and kw.strip().lower() not in _STOPWORDS
    ]
```

### Learn sub-query expansion

A single long topic like "dynamic sessions for ai scientists" may return few results. `search_learn` generates multiple queries — the original topic, overlapping bigrams, and "Azure \<keyword\>" variants — then merges and deduplicates:

```python
queries = _build_queries(topic)   # → ["dynamic sessions for ai scientists",
                                  #    "dynamic sessions", "sessions ai", ...,
                                  #    "Azure dynamic", "Azure sessions", "Azure ai", ...]
# Each query searches with per_query cap, results merged by URL
```

## LLM Ranking (`rank_sources`)

All sources are flattened into a numbered list (title, 150-char description, type, URL) and sent to `AzureChatOpenAI`. The LLM extracts the audience verbatim from the query pattern `"<topic> for <audience>"` and returns a relevance-ordered ranking:

```python
# LLM returns JSON:  {"audience": "developers", "ranking": [42, 15, 88, 3, ...]}
result = json.loads(text)
state["audience"] = result.get("audience", "technical professionals")
ranked_urls = [all_sources[i-1]["url"] for i in result.get("ranking", []) if 0 <= i-1 < len(all_sources)]
```

**Fallback:** on LLM failure, uses discovery order and extracts audience from topic string via simple split on `" for "`.

## Content Fetching + Depth-1 Link Extraction

Top **25** ranked URLs are fetched concurrently (semaphore=5, cap=30 per `fetch_multiple` call). Each page is parsed with BeautifulSoup, targeting `<main>`, `<article>`, or `role="main"`, capped at **3000 chars**:

```python
main = (soup.find("main") or soup.find("article")
        or soup.find(attrs={"role": "main"})
        or soup.find(class_=re.compile(r"content|article|post", re.I))
        or soup.body)
text = main.get_text(separator="\n", strip=True)[:MAX_CONTENT_LENGTH]
```

### Depth-1 link extraction (`tools/extract_links.py`)

After fetching the top 25 pages, the raw HTML is scanned for outbound links to trusted Azure-ecosystem domains. This discovers additional high-quality sources — Learn "See Also" / "Next Steps" links, GitHub README cross-references, and blog editorial citations:

**Trusted domains:** `learn.microsoft.com`, `github.com`, `azure.microsoft.com`, `techcommunity.microsoft.com`, `devblogs.microsoft.com`, `developer.microsoft.com`, `code.visualstudio.com`, `marketplace.visualstudio.com`

```python
# Per-page: extract links from content area, filter to trusted domains, skip nav/footer
links = extract_trusted_links(html, base_url)

# Batch: merge across all fetched pages, dedup against existing URLs, sort by popularity
linked_sources = extract_links_from_fetched(fetched_content, existing_urls)  # cap: 30
```

Up to **20** of the discovered linked URLs are then fetched for content (same 3000-char cap), extending `fetched_content`.

### Pipeline flow

```
rank_sources output (all discovered URLs, ranked)
  └─ fetch top 25 → fetched_content (with HTML)
       └─ extract_links_from_fetched → up to 30 linked URLs
            └─ fetch top 20 of those → appended to fetched_content
```

## Synthesis

Packages sources-with-content into a brief (topic, audience, source counts by type). Sources are capped at **25** in the brief to keep the downstream content creator payload manageable. Source counts reflect all discovered sources (not just those in the brief). An LLM generates a one-sentence debrief summary. Fallback builds the summary string mechanically if the LLM call fails.

**Source types in the brief:** `documentation`, `blog`, `code_sample`, `update`, `linked_reference`

## Caps Summary

| Stage | Cap | Purpose |
|-------|-----|---------|
| Learn search | 40 | Per-query results, merged across sub-queries |
| GitHub search | 25 | Across 3 query tiers |
| Blog + TC RSS | 50 + 50 | Per-source across multi-feed merge |
| Azure Updates RSS | 15 | Single feed |
| Primary fetch | 25 | Top LLM-ranked URLs |
| Linked URL discovery | 30 | Deduped, sorted by link popularity |
| Secondary fetch | 20 | Additional linked pages |
| `fetch_multiple` internal | 30 | Max concurrent batch per call |
| Content per page | 3000 chars | BeautifulSoup extraction |
| Brief sources | 25 | Passed to content creator |

## A2A Exposure

Served via FastAPI in [main.py](../src/agent-research/main.py). Accepts JSON-RPC methods `tasks/send`, `SendMessage`, `message/send`. Agent card at `/.well-known/agent.json`. Optional auth via Bearer token or `X-API-Key` header (controlled by `A2A_AUTH_ENABLED` env var).

## Observability

Every pipeline step is wrapped in OpenTelemetry spans with `gen_ai.*` attributes. Auto-instrumented: FastAPI (inbound), httpx (outbound), OpenAI SDK (LLM calls). Traces and logs export via OTLP/gRPC to the configured collector.
