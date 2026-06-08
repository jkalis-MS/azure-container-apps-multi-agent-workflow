"""LangGraph research agent -- searches priority Azure sources and fetches actual content."""

from __future__ import annotations

import json
import os
from typing import Any, TypedDict

from langgraph.graph import StateGraph, END
from langchain_openai import AzureChatOpenAI
from opentelemetry import context as context_api, trace
from opentelemetry.trace import SpanKind, StatusCode, set_span_in_context
from tools.search_learn import search_learn
from tools.search_github import search_github_repos
from tools.search_azure_sources import search_azure_blogs, search_tech_community, search_azure_updates
from tools.fetch_content import fetch_multiple
from tools.extract_links import extract_links_from_fetched
from tools.sandbox_fetch import fetch_with_sandboxes

_tracer = trace.get_tracer("research-agent")

# Live status for polling by Dev UI (updated during research execution)
_live_status: dict[str, Any] = {"phase": "idle", "sandbox_statuses": [], "egress_violations": []}


class ResearchState(TypedDict):
    topic: str
    preferences: dict
    docs: list[dict]           # Microsoft Learn results
    repos: list[dict]          # GitHub Azure-Samples results
    blogs: list[dict]          # Azure Blog + Tech Community
    updates: list[dict]        # Azure Updates / roadmap
    ranked_urls: list[str]     # LLM-prioritized URLs for fetching
    audience: str              # Target audience extracted by LLM
    fetched_content: list[dict] # Actual page content from top URLs
    linked_sources: list[dict] # Depth-1 extracted links from fetched pages
    sandbox_results: list[dict] # Per-sandbox execution metadata
    egress_violations: list[dict] # Blocked requests (hallucination demo)
    brief: dict | None
    iteration: int


def _get_llm() -> AzureChatOpenAI:
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    return AzureChatOpenAI(
        azure_deployment=deployment,
        model=deployment,
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
    )


async def plan_research(state: ResearchState) -> ResearchState:
    """Initialize research iteration."""
    with _tracer.start_as_current_span(
        "execute_tool plan_research",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "plan_research",
            "gen_ai.tool.type": "function",
        },
    ):
        state["iteration"] = state.get("iteration", 0) + 1
    return state


async def search_docs(state: ResearchState) -> ResearchState:
    """Search Microsoft Learn for documentation."""
    with _tracer.start_as_current_span(
        "execute_tool search_docs",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "search_docs",
            "gen_ai.tool.type": "function",
            "gen_ai.tool.description": "Search Microsoft Learn documentation",
        },
    ):
        state["docs"] = await search_learn(state["topic"], top=40)
    return state


async def search_code(state: ResearchState) -> ResearchState:
    """Search GitHub Azure-Samples and Azure org."""
    with _tracer.start_as_current_span(
        "execute_tool search_code",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "search_code",
            "gen_ai.tool.type": "function",
            "gen_ai.tool.description": "Search GitHub Azure-Samples and Azure org",
        },
    ):
        state["repos"] = await search_github_repos(state["topic"], top=25)
    return state


async def search_blogs(state: ResearchState) -> ResearchState:
    """Search Azure Blog, Tech Community, and Azure Updates."""
    with _tracer.start_as_current_span(
        "execute_tool search_blogs",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "search_blogs",
            "gen_ai.tool.type": "function",
            "gen_ai.tool.description": "Search Azure Blog, Tech Community, and Azure Updates",
        },
    ):
        azure_blog = await search_azure_blogs(state["topic"], top=50)
        tech_comm = await search_tech_community(state["topic"], top=50)
        updates = await search_azure_updates(state["topic"], top=15)
        state["blogs"] = azure_blog + tech_comm
        state["updates"] = updates
    return state


async def rank_sources(state: ResearchState) -> ResearchState:
    """Use LLM to rank all discovered sources by relevance to the query intent."""
    span = _tracer.start_span(
        "execute_tool rank_sources",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "rank_sources",
            "gen_ai.tool.type": "function",
            "gen_ai.tool.description": "Use LLM to rank sources by relevance",
        },
    )
    ctx = set_span_in_context(span)
    token = context_api.attach(ctx)
    # Collect all sources into a flat list with index
    all_sources = []
    for item in state.get("docs", []):
        all_sources.append({"url": item.get("url", ""), "title": item.get("title", ""), "description": (item.get("description") or "")[:150], "type": "documentation"})
    for item in state.get("blogs", []):
        all_sources.append({"url": item.get("url", ""), "title": item.get("title", ""), "description": (item.get("description") or "")[:150], "type": "blog"})
    for item in state.get("repos", []):
        all_sources.append({"url": item.get("url", ""), "title": item.get("name", item.get("title", "")), "description": (item.get("description") or "")[:150], "type": "code_sample"})
    for item in state.get("updates", []):
        all_sources.append({"url": item.get("url", ""), "title": item.get("title", ""), "description": (item.get("description") or "")[:150], "type": "update"})

    # Remove entries without URLs
    all_sources = [s for s in all_sources if s["url"]]

    if not all_sources:
        state["ranked_urls"] = []
        return state

    # Build a numbered list for the LLM
    source_list = "\n".join(
        f"{i+1}. [{s['type']}] {s['title']} — {s['description']}\n   URL: {s['url']}"
        for i, s in enumerate(all_sources)
    )

    try:
        llm = _get_llm()
        prompt = f"""You are a research assistant. Given the user's query and a list of discovered sources,
do two things:
1. Extract the target audience EXACTLY as stated in the query. The query typically follows the
   pattern "<topic> for <audience>". Extract the audience portion verbatim (e.g. "developers",
   "architects", "DevOps engineers"). Do NOT generalize or rephrase — use the exact words from
   the query. If no audience is explicitly stated, use "technical professionals".
2. Rank the sources by relevance to the query intent and target audience.

Return ONLY a JSON object with two fields:
- "audience": the exact audience words from the query (do NOT generalize)
- "ranking": an array of source numbers (1-based) ordered from most to least relevant. Include ALL source numbers.

User query: "{state['topic']}"

Return ONLY valid JSON, e.g. {{"audience": "developers", "ranking": [3, 1, 7, 2, ...]}}. No other text.

Discovered sources:
{source_list}"""

        response = await llm.ainvoke(prompt)
        text = response.content.strip()

        # Parse the JSON array of indices
        # Handle potential markdown code blocks
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        result = json.loads(text)

        # Handle both formats: JSON object with audience+ranking, or plain array
        if isinstance(result, dict):
            state["audience"] = result.get("audience", "technical professionals")
            ranked_indices = result.get("ranking", [])
        else:
            ranked_indices = result
            state["audience"] = "technical professionals"

        # Convert 1-based indices to 0-based, filter valid
        ranked_urls = []
        for idx in ranked_indices:
            i = idx - 1
            if 0 <= i < len(all_sources):
                ranked_urls.append(all_sources[i]["url"])

        state["ranked_urls"] = ranked_urls
        print(f"[Research] LLM ranked {len(ranked_urls)} sources for audience '{state['audience']}': {state['topic']}")

    except Exception as e:
        print(f"[Research] LLM ranking failed ({e}), using default order")
        # Fallback: just use all URLs in discovery order
        state["ranked_urls"] = [s["url"] for s in all_sources]
        # Extract audience from topic (e.g. "Azure for beginners" → "beginners")
        topic_lower = state["topic"].lower()
        if " for " in topic_lower:
            state["audience"] = state["topic"].split(" for ", 1)[1].strip()
        else:
            state["audience"] = "technical professionals"
        span.set_status(StatusCode.ERROR, str(e))
    finally:
        context_api.detach(token)
        span.end()

    return state


async def fetch_content_sandboxed(state: ResearchState) -> ResearchState:
    """Fetch content via ACA Sandboxes with isolated egress policies."""
    global _live_status
    _live_status = {"phase": "creating_sandboxes", "sandbox_statuses": [], "egress_violations": []}
    print("[Status] Phase: creating_sandboxes")
    with _tracer.start_as_current_span(
        "execute_tool fetch_content_sandboxed",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "fetch_content_sandboxed",
            "gen_ai.tool.type": "function",
            "gen_ai.tool.description": "Fetch content via ACA Sandboxes with egress isolation",
        },
    ):
        urls = state.get("ranked_urls", [])
        # De-duplicate while preserving ranked order
        seen = set()
        unique_urls = []
        for url in urls:
            if url not in seen:
                seen.add(url)
                unique_urls.append(url)

        # Ensure domain diversity: guarantee minimum URLs from each domain group
        # so we always get sandboxes for learn.microsoft.com, github.com, and azure blogs
        from tools.sandbox_fetch import group_urls_by_domain
        domain_groups = group_urls_by_domain(unique_urls)
        if len(domain_groups) > 1:
            min_per_domain = max(3, 25 // len(domain_groups))
            diverse_urls = []
            remaining = []
            for domain, group in domain_groups.items():
                diverse_urls.extend(group[:min_per_domain])
                remaining.extend(group[min_per_domain:])
            # Fill up to 25 with remaining in ranked order
            for url in unique_urls:
                if url not in diverse_urls and len(diverse_urls) < 25:
                    diverse_urls.append(url)
            unique_urls = diverse_urls[:25]
        else:
            unique_urls = unique_urls[:25]

        try:
            async def _on_sandbox_status(statuses: list[dict]) -> None:
                global _live_status
                _live_status = {
                    "phase": "creating_sandboxes",
                    "sandbox_statuses": statuses,
                    "egress_violations": _live_status.get("egress_violations", []),
                }

            fetched, violations, sandbox_statuses = await fetch_with_sandboxes(
                urls=unique_urls,
                topic=state["topic"],
                status_callback=_on_sandbox_status,
            )
            state["fetched_content"] = fetched
            state["egress_violations"] = violations
            state["sandbox_results"] = sandbox_statuses
            # Update live status immediately (before synthesize LLM call)
            _live_status = {
                "phase": "sandboxes_complete",
                "sandbox_statuses": sandbox_statuses,
                "egress_violations": violations,
            }
            print(f"[Status] Phase: sandboxes_complete ({len(sandbox_statuses)} sandboxes)")
            if violations:
                print(f"[Sandbox] {len(violations)} egress violation(s) detected (hallucination blocked)")
            print(f"[Sandbox] Fetched content from {len(fetched)} URLs via {len(sandbox_statuses)} sandbox(es)")
        except Exception as e:
            print(f"[Sandbox] Sandbox fetch failed ({e}), falling back to direct fetch")
            # Fallback to direct fetch
            state["fetched_content"] = await fetch_multiple(unique_urls)
            state["egress_violations"] = []
            state["sandbox_results"] = [{"domain": "fallback", "status": "fallback", "error": str(e), "urls_count": 0, "egress_blocked": False, "hallucination_target": False}]

        # Depth-1 link extraction (same as direct path)
        existing_urls = set(unique_urls)
        state["linked_sources"] = extract_links_from_fetched(
            state["fetched_content"], existing_urls,
        )
        linked_count = len(state["linked_sources"])
        if linked_count:
            print(f"[Research] Extracted {linked_count} additional trusted links from fetched pages")
            new_urls = [ls["url"] for ls in state["linked_sources"]]
            new_content = await fetch_multiple(new_urls[:20])
            state["fetched_content"].extend(new_content)

    return state


async def fetch_source_content(state: ResearchState) -> ResearchState:
    """Fetch actual content from the LLM-ranked URLs."""
    with _tracer.start_as_current_span(
        "execute_tool fetch_content",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "fetch_content",
            "gen_ai.tool.type": "function",
            "gen_ai.tool.description": "Fetch content from top-ranked source URLs",
        },
    ):
        urls = state.get("ranked_urls", [])

        # De-duplicate while preserving ranked order
        seen = set()
        unique_urls = []
        for url in urls:
            if url not in seen:
                seen.add(url)
                unique_urls.append(url)

        # Fetch top 25 most relevant sources
        state["fetched_content"] = await fetch_multiple(unique_urls[:25])

        # Depth-1 link extraction from fetched pages (#1 blog/TC, #2 Learn, #3 GitHub)
        existing_urls = set(unique_urls)
        state["linked_sources"] = extract_links_from_fetched(
            state["fetched_content"], existing_urls,
        )
        linked_count = len(state["linked_sources"])
        if linked_count:
            print(f"[Research] Extracted {linked_count} additional trusted links from fetched pages")

            # Fetch content from the newly discovered links
            new_urls = [ls["url"] for ls in state["linked_sources"]]
            new_content = await fetch_multiple(new_urls[:20])  # cap at 20 extra fetches
            state["fetched_content"].extend(new_content)
    return state


async def synthesize(state: ResearchState) -> ResearchState:
    """Package all research into a structured brief with actual content."""
    global _live_status
    _live_status["phase"] = "synthesizing"
    print("[Status] Phase: synthesizing (generating brief via LLM)")
    span = _tracer.start_span(
        "execute_tool synthesize",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "synthesize",
            "gen_ai.tool.type": "function",
            "gen_ai.tool.description": "Synthesize research into structured brief via LLM",
        },
    )
    ctx = set_span_in_context(span)
    token = context_api.attach(ctx)
    # Build source list with content
    sources = []
    content_map = {fc["url"]: fc for fc in state.get("fetched_content", []) if fc.get("content")}

    for item in state.get("docs", []):
        entry = {**item, "type": "documentation"}
        if item.get("url") in content_map:
            entry["content"] = content_map[item["url"]]["content"]
        sources.append(entry)

    for item in state.get("blogs", []):
        entry = {**item, "type": "blog"}
        if item.get("url") in content_map:
            entry["content"] = content_map[item["url"]]["content"]
        sources.append(entry)

    for item in state.get("repos", []):
        entry = {**item, "type": "code_sample"}
        if item.get("url") in content_map:
            entry["content"] = content_map[item["url"]]["content"]
        sources.append(entry)

    for item in state.get("updates", []):
        entry = {**item, "type": "update"}
        if item.get("url") in content_map:
            entry["content"] = content_map[item["url"]]["content"]
        sources.append(entry)

    for item in state.get("linked_sources", []):
        url = item.get("url", "")
        if url in content_map:
            sources.append({
                "title": item.get("text", "") or url,
                "url": url,
                "content": content_map[url]["content"],
                "type": "linked_reference",
                "found_on": item.get("found_on", ""),
            })

    # Only pass sources that have fetched content to the creator agent (cap at 25)
    sources_with_content = [s for s in sources if s.get("content")][:25]

    audience = state.get("audience", "technical professionals")

    brief = {
        "topic": state["topic"],
        "audience": audience,
        "sources": sources_with_content,
        "source_counts": {
            "documentation": len([s for s in sources if s["type"] == "documentation"]),
            "blog": len([s for s in sources if s["type"] == "blog"]),
            "code_sample": len([s for s in sources if s["type"] == "code_sample"]),
            "update": len([s for s in sources if s["type"] == "update"]),
            "linked_reference": len([s for s in sources if s["type"] == "linked_reference"]),
        },
        "sources_with_content": len(sources_with_content),
        "total_sources": len(sources),
        "egress_violations": state.get("egress_violations", []),
        "sandbox_mode": state.get("preferences", {}).get("use_sandboxes", False),
        "sandbox_results": state.get("sandbox_results", []),
    }

    # Build category breakdown for the debrief
    cat_parts = []
    sc = brief["source_counts"]
    if sc["documentation"]: cat_parts.append(f"{sc['documentation']} Microsoft Learn docs")
    if sc["blog"]: cat_parts.append(f"{sc['blog']} Azure Blog & Tech Community posts")
    if sc["code_sample"]: cat_parts.append(f"{sc['code_sample']} GitHub repos")
    if sc["update"]: cat_parts.append(f"{sc['update']} Azure Updates")
    if sc.get("linked_reference"): cat_parts.append(f"{sc['linked_reference']} linked references")
    categories_text = ", ".join(cat_parts) if cat_parts else "various Azure sources"
    audience = state.get('audience', 'technical professionals')

    # LLM-generated unified debrief
    try:
        llm = _get_llm()
        prompt = f"""Write a single concise debrief sentence (max 2 lines) for a research brief panel.

Facts to incorporate naturally into ONE sentence:
- Topic: "{state['topic']}"
- Target audience: {audience}
- Total sources discovered: {len(sources)}
- Sources analyzed and ranked by an LLM for relevance
- Top {len(sources_with_content)} most relevant sources were fetched with full content
- Source breakdown: {categories_text}
- These sources are passed to a content creator agent

Write ONLY the debrief sentence. No intro, no quotes, no bullet points. Keep it factual and concise."""

        response = await llm.ainvoke(prompt)
        brief["summary"] = response.content.strip().strip('"')
    except Exception:
        # Avoid redundant phrasing like '"Azure for beginners" for beginners'
        topic = state['topic']
        topic_lower = topic.lower()
        audience_lower = audience.lower()
        if f"for {audience_lower}" in topic_lower:
            relevance_phrase = f'relevant to "{topic}"'
        else:
            relevance_phrase = f'relevant to "{topic}" for {audience}'
        brief["summary"] = (
            f"An LLM analyzed {len(sources)} discovered sources ({categories_text}) "
            f"and ranked them by {relevance_phrase}; "
            f"the top {len(sources_with_content)} most relevant were fetched with full content "
            f"and passed to the content creator agent."
        )

    state["brief"] = brief
    context_api.detach(token)
    span.end()
    return state


def should_continue(state: ResearchState) -> str:
    """After blogs search, always proceed to fetch content."""
    return "fetch_content"


def _route_fetch(state: ResearchState) -> str:
    """Route to sandbox or direct fetch based on preferences."""
    use_sandboxes = state.get("preferences", {}).get("use_sandboxes", True)
    if use_sandboxes:
        return "fetch_content_sandboxed"
    return "fetch_content"


def build_graph() -> StateGraph:
    """Build the LangGraph research state machine."""
    graph = StateGraph(ResearchState)

    graph.add_node("plan_research", plan_research)
    graph.add_node("search_docs", search_docs)
    graph.add_node("search_code", search_code)
    graph.add_node("search_blogs", search_blogs)
    graph.add_node("rank_sources", rank_sources)
    graph.add_node("fetch_content", fetch_source_content)
    graph.add_node("fetch_content_sandboxed", fetch_content_sandboxed)
    graph.add_node("synthesize", synthesize)

    graph.set_entry_point("plan_research")
    graph.add_edge("plan_research", "search_docs")
    graph.add_edge("search_docs", "search_code")
    graph.add_edge("search_code", "search_blogs")
    graph.add_edge("search_blogs", "rank_sources")
    graph.add_conditional_edges("rank_sources", _route_fetch, {
        "fetch_content_sandboxed": "fetch_content_sandboxed",
        "fetch_content": "fetch_content",
    })
    graph.add_edge("fetch_content", "synthesize")
    graph.add_edge("fetch_content_sandboxed", "synthesize")
    graph.add_edge("synthesize", END)

    return graph


_graph = build_graph().compile()


async def run_research(topic: str, preferences: dict | None = None) -> dict:
    """Execute the research graph and return the brief."""
    with _tracer.start_as_current_span(
        "invoke_agent research-agent",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": "research-agent",
            "gen_ai.agent.id": "research-agent",
            "gen_ai.provider.name": "azure.ai.openai",
        },
    ) as agent_span:
        initial_state: ResearchState = {
            "topic": topic,
            "preferences": preferences or {},
            "docs": [],
            "repos": [],
            "blogs": [],
            "updates": [],
            "ranked_urls": [],
            "audience": "",
            "fetched_content": [],
            "linked_sources": [],
            "sandbox_results": [],
            "egress_violations": [],
            "brief": None,
            "iteration": 0,
        }

        try:
            result = await _graph.ainvoke(initial_state)
            return result.get("brief", {})
        except Exception as e:
            agent_span.set_status(StatusCode.ERROR, str(e))
            raise
        finally:
            _live_status["phase"] = "idle"
