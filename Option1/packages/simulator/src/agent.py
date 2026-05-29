"""LangGraph-based World Cup match simulator agent.

Pipeline (LangGraph StateGraph):
  decompose → generate_search_code → execute_searches → generate_sim_code → execute_simulation → package_results → END

Code Generation Strategy:
  - GPT-4o generates Python code for BOTH research searches and the final simulation.
  - Generated code runs inside isolated Azure Container Apps Sandboxes (microVMs).
  - If generated code fails (bad output, crash, etc.), pre-written fallback scripts
    in sandbox-scripts/ are executed transparently.

What's agent-generated vs hardcoded:
  - Research search code: AGENT-GENERATED (GPT-4o writes Python per query; fallback: search.py)
  - Roster fetch code: HARDCODED (ROSTER_SCRIPT below; deterministic Wikipedia API calls for reliability)
  - Simulation code: AGENT-GENERATED (GPT-4o writes Python; fallback: simulate.py)

Sandbox Egress Firewall:
  - Research sandboxes: ESPN/Sky/Marca BLOCKED, Wikipedia/Google/FIFA/BBC ALLOWED
  - Simulation sandbox: same + Transform rule injects Azure OpenAI API key from secret ref
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import pathlib
import re
from typing import Any, TypedDict

from langgraph.graph import StateGraph, END
from langchain_openai import AzureChatOpenAI

try:
    from opentelemetry import trace
    from opentelemetry.trace import SpanKind, StatusCode
    _tracer = trace.get_tracer("simulator-agent")
except ImportError:
    # Fallback: no-op tracer
    import contextlib
    class _NoopTracer:
        @contextlib.contextmanager
        def start_as_current_span(self, *a, **kw):
            yield None
    _tracer = _NoopTracer()
    SpanKind = None
    StatusCode = None

from .sandbox_client_sdk import SandboxClient, ExecResult

logger = logging.getLogger("simulator.agent")
_tracer = trace.get_tracer("simulator-agent")

SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent / "sandbox-scripts"
DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class SimulationState(TypedDict):
    prompt: str
    secure_egress: bool
    research_queries: list[dict[str, Any]]
    search_codes: list[str]
    search_results: list[dict[str, Any]]
    sandbox_logs: list[dict[str, Any]]
    generated_sim_code: str
    simulation_result: dict[str, Any] | None
    sandbox_ids: list[str]
    error: str | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EGRESS_POLICY: dict[str, Any] = {
    "defaultAction": "Allow",
    "rules": [
        {"name": "block-espn", "match": {"host": "*.espn.com"}, "action": {"type": "Deny"}},
        {"name": "block-skysports", "match": {"host": "*.skysports.com"}, "action": {"type": "Deny"}},
        {"name": "block-marca", "match": {"host": "*.marca.com"}, "action": {"type": "Deny"}},
    ],
}


def _get_sim_egress_policy(secure_egress: bool) -> dict[str, Any]:
    """Build egress policy for the simulation sandbox.
    
    When secure_egress=True, adds a Transform rule that injects the api-key header
    for Azure OpenAI requests via a secret reference — the sandbox code never sees the key.
    trafficInspection is only enabled for secure egress (required for Transform rules).
    """
    policy = dict(EGRESS_POLICY)
    policy["rules"] = list(EGRESS_POLICY["rules"])
    if secure_egress:
        policy["trafficInspection"] = "Full"
        aoai_host = os.environ.get("AZURE_OPENAI_ENDPOINT", "").replace("https://", "").rstrip("/")
        policy["rules"].append({
            "name": "inject-aoai-key",
            "match": {"host": aoai_host},
            "action": {
                "type": "Transform",
                "headers": [
                    {"operation": "Set", "name": "api-key", "valueRef": {"secretRef": {"secretId": "aoai-api-key", "secretKey": "api-key"}}}
                ],
            },
        })
    return policy


def _get_llm() -> AzureChatOpenAI:
    import httpx as _httpx
    return AzureChatOpenAI(
        azure_deployment=DEPLOYMENT,
        model=DEPLOYMENT,
        api_version="2024-10-21",
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ.get("AZURE_OPENAI_KEY", ""),
        http_client=_httpx.Client(verify=False),
        http_async_client=_httpx.AsyncClient(verify=False),
    )


def _get_sandbox_client() -> SandboxClient:
    return SandboxClient()


async def _traced_create_sandbox(sb: SandboxClient, purpose: str, **kwargs) -> str:
    """Create a sandbox wrapped in an execute_tool span for the Agents blade."""
    with _tracer.start_as_current_span(
        f"execute_tool create_sandbox",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "create_sandbox",
            "gen_ai.tool.type": "function",
            "gen_ai.tool.description": f"Create Azure Sandbox ({purpose})",
        },
    ):
        sid = await sb.create_sandbox(**kwargs)
        return sid


async def _write_file_in_sandbox(sb: SandboxClient, sid: str, path: str, content: str):
    """Write content to a file in the sandbox using chunked base64 to avoid ARG_MAX."""
    encoded = base64.b64encode(content.encode()).decode()
    chunk_size = 60000  # Safe chunk size well under ARG_MAX
    chunks = [encoded[i:i+chunk_size] for i in range(0, len(encoded), chunk_size)]

    # Write first chunk (overwrite)
    r = await sb.exec_command(sid, f'echo -n "{chunks[0]}" > /tmp/_b64_chunk')
    if r.exit_code != 0:
        logger.error(f"Failed to write chunk 0 for {path}: {r.stderr}")
    # Append remaining chunks
    for i, chunk in enumerate(chunks[1:], 1):
        r = await sb.exec_command(sid, f'echo -n "{chunk}" >> /tmp/_b64_chunk')
        if r.exit_code != 0:
            logger.error(f"Failed to write chunk {i} for {path}: {r.stderr}")
    # Decode to final file
    r = await sb.exec_command(sid, f'base64 -d /tmp/_b64_chunk > {path} && rm /tmp/_b64_chunk')
    if r.exit_code != 0:
        logger.error(f"Failed to decode b64 to {path}: {r.stderr}")
    else:
        # Verify file exists
        r2 = await sb.exec_command(sid, f'wc -c < {path}')
        logger.info(f"  Wrote {path} ({r2.stdout.strip()} bytes)")


# --- SEARCH CODE PROMPT ---
# GPT-4o uses this prompt to GENERATE Python code for each research query.
# The generated code runs inside a sandbox. If it fails, we fall back to search.py.
SEARCH_CODE_PROMPT = """You are a Python code generator. Write a Python script that:

1. Searches the web for: "{query}"
2. Uses MULTIPLE search strategies to find relevant information:
   a) Wikipedia API search (MOST RELIABLE — always try this first):
      URL: https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=<url-encoded-query>&format=json&srlimit=5&srprop=snippet
      Parse the JSON response: data["query"]["search"] is a list of {{"title": ..., "snippet": ...}}
      For each result, construct the URL as: https://en.wikipedia.org/wiki/<title with spaces replaced by underscores>
   b) If the query is about a specific Wikipedia article (e.g. a national team), also fetch the article content:
      URL: https://en.wikipedia.org/w/api.php?action=query&titles=<article_title>&prop=extracts&exintro=false&explaintext=true&format=json
      This gives the full article text which is excellent for squad/roster information.
   c) Google search (as backup):
      URL: https://www.google.com/search?q=<url-encoded-query>&num=5
      Parse the HTML for result blocks. Look for <h3> tags inside <a> elements for titles/links.
      Extract snippets from nearby <span> tags. This may be blocked — handle gracefully.

3. Attempts to fetch ACTUAL CONTENT from these sports websites (for egress demo):
   - https://www.espn.com/soccer/ (attempt to read soccer headlines)
   - https://www.skysports.com/football (attempt to read football news)
   - https://www.marca.com/en/football.html (attempt to read football articles)
   - https://www.fifa.com/fifaplus/en/tournaments (attempt to read tournament info)
   - https://www.bbc.co.uk/sport/football (attempt to read football news)

   For EACH site, make a real HTTP GET request. If it fails, record the error.
   This is EXPECTED for some sites due to egress firewall rules.

REQUIREMENTS:
- Use ONLY Python standard library (urllib, json, re, html, sys, os). No pip packages.
- ALWAYS start with Wikipedia API — it is the most reliable source from any environment.
- For Wikipedia API calls, use User-Agent: "WorldCupSimulator/1.0 (demo; contact@example.com)"
  (Wikipedia requires a descriptive User-Agent)
- For Google, use User-Agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
- Print ONLY a single JSON object to stdout with this schema:
  {{"query": "the query", "results": [...], "egress_probes": [...]}}
  where results is an array of {{"name": "title", "url": "...", "snippet": "..."}}
  and egress_probes is an array of {{"host": "...", "url": "...", "blocked": true/false, "status": int_or_null, "error": "...", "content_snippets": ["..."]}}
  content_snippets should contain the first 5-10 headlines/snippets extracted from the page (empty if blocked).
- Handle all exceptions gracefully — never crash, always output valid JSON.
- Do NOT print anything except the final JSON (no debug prints to stdout).

Write ONLY the Python code, no markdown fences or explanation."""


# --- SIMULATION CODE PROMPT ---
# GPT-4o uses this prompt to GENERATE Python code that calls Azure OpenAI
# with the research data to produce the match simulation. If it fails,
# we fall back to simulate.py.
SIMULATE_CODE_PROMPT = """You are a Python code generator. Write a Python script that:

1. Takes research data (provided as a JSON string in the variable RESEARCH_JSON) and a user prompt (in USER_PROMPT)
2. Calls Azure OpenAI to produce a World Cup match simulation between Mexico and Czechia

REQUIREMENTS:
- Use ONLY Python standard library (urllib, json, os, sys, random). No pip packages.
- Azure OpenAI endpoint is in env var AZURE_OPENAI_ENDPOINT
- Azure OpenAI API key is in env var AZURE_OPENAI_KEY  
- Azure OpenAI deployment is in env var AZURE_OPENAI_DEPLOYMENT
- Make a POST request to: {{endpoint}}/openai/deployments/{{deployment}}/chat/completions?api-version=2024-10-21
  with header "api-key" set to the key, and Content-Type: application/json
- CRITICAL: Parse the OpenAI response JSON, extract choices[0]["message"]["content"], 
  parse THAT as JSON, and print ONLY that parsed simulation JSON to stdout.
  Do NOT print the raw API response.
- The system prompt should instruct the model to simulate a World Cup 2026 match between Mexico and Czechia,
  using ONLY the research data provided. It MUST:
  * Use ONLY players mentioned in the research data or known to be in the 2025-2026 national team squads
  * Reference the CURRENT coaches: Javier Aguirre for Mexico, Ivan Hasek for Czechia
  * NOT use players who have retired or are no longer called up
  * NOT reference coaches from before 2024
  * Base tactical analysis on the current team's actual recent form from the research
  * If research data is empty or unclear, explicitly state that in the reasoning
  Use a random seed for variety. Temperature should be 0.95. Request JSON response format.
- The response JSON schema MUST be:
  {{"homeTeam": "Mexico", "awayTeam": "Czechia", "homeScore": int, "awayScore": int,
    "goals": [{{"minute": int, "scorer": "name", "team": "team", "description": "how scored"}}],
    "keyEvents": [{{"minute": int, "event": "description"}}],
    "summary": "2-3 paragraph match summary",
    "reasoning": "1-2 paragraphs on how research data informed the result"}}
- Print ONLY the simulation JSON object to stdout.
- Handle errors gracefully — print a valid JSON error object if something fails.
- IMPORTANT: Before making the HTTP request, write the full request details to /tmp/openai_request.txt:
  Include the URL, all headers (including api-key), and the first 2000 chars of the body.
  Format: "POST <url>\n\n=== HEADERS ===\n<header>: <value>\n...\n\n=== BODY (first 2000 chars) ===\n<body>"

The variables RESEARCH_JSON and USER_PROMPT will ALWAYS be defined as strings before your code runs — do NOT check if they exist or add any guards for them. Use them directly.

Write ONLY the Python code, no markdown fences or explanation."""


# ---------------------------------------------------------------------------
# Graph Nodes
# ---------------------------------------------------------------------------

async def decompose_prompt(state: SimulationState) -> SimulationState:
    """Use GPT-4o to break the user prompt into 3-5 research queries."""
    with _tracer.start_as_current_span(
        "execute_tool decompose_prompt",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "decompose_prompt",
            "gen_ai.tool.type": "function",
        },
    ):
        logger.info("Node: decompose_prompt")
        llm = _get_llm()
        system = (
            "You are a sports research planner. Today's date is May 2026. The FIFA World Cup 2026 "
            "is being held in USA, Mexico and Canada starting June 2026. Mexico and Czechia are in the same group.\n\n"
            "Given a user prompt about a World Cup match simulation between Mexico and Czechia, "
            "produce 3-5 web-search queries that would gather CURRENT (2025-2026) information.\n\n"
            "CRITICAL: Mexico's current coach is Javier Aguirre (appointed 2024). "
            "Czechia's current coach is Ivan Hasek. Do NOT reference old coaches.\n\n"
            "Focus on: current 2025-2026 squad rosters, recent form in 2025-2026 matches, "
            "World Cup 2026 qualifying results, current injuries/suspensions, "
            "head-to-head history, tactical analysis under CURRENT coaches.\n\n"
            "Include the year '2026' or '2025' in your queries to get fresh results.\n\n"
            "TARGET SITES: Use 'site:' operators to get information from these reliable sources:\n"
            "  - Mexico coverage: tudn.com, 365scores.com\n"
            "  - Czechia coverage: tribuna.com, 365scores.com\n"
            "  - Both teams: flashscore.com, 365scores.com\n"
            "Mix queries between site-specific (e.g. 'site:tudn.com Mexico seleccion convocatoria 2026') "
            "and general queries for broader context.\n\n"
            "Return valid JSON: an array of objects with keys:\n"
            '  "query" – the search string\n'
            '  "purpose" – one-line explanation\n'
        )
        resp = await llm.ainvoke([
            {"role": "system", "content": system},
            {"role": "user", "content": state["prompt"]},
        ])
        raw = (resp.content or "").strip()
        if not raw:
            raw = "[]"
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = re.sub(r'^```\w*\n?', '', raw)
            raw = re.sub(r'\n?```$', '', raw)
        # Try to extract JSON array/object from response
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Try to find JSON within the response text
            match = re.search(r'(\[.*\]|\{.*\})', raw, re.DOTALL)
            if match:
                data = json.loads(match.group(1))
            else:
                logger.error(f"Failed to parse LLM response as JSON: {raw[:200]}")
                raise
        queries = data if isinstance(data, list) else data.get("queries", data.get("research_queries", []))

        # Always inject roster queries to ensure accurate squad data
        mandatory_queries = [
            {"query": "Mexico seleccion nacional convocatoria 2026 World Cup squad roster jugadores", "purpose": "Get Mexico's confirmed/expected World Cup 2026 squad"},
            {"query": "Czech Republic Czechia national team squad roster 2026 World Cup players", "purpose": "Get Czechia's confirmed/expected World Cup 2026 squad"},
        ]
        # Prepend mandatory queries (deduplicate if LLM already included similar)
        existing_query_texts = {q.get("query", "").lower() for q in queries}
        for mq in mandatory_queries:
            if not any(mq["query"].lower()[:30] in eq for eq in existing_query_texts):
                queries.insert(0, mq)

        state["research_queries"] = queries
        logger.info(f"  → {len(queries)} research queries (including mandatory roster queries)")
    return state


async def generate_search_code(state: SimulationState) -> SimulationState:
    """Generate Python search scripts for each query using GPT-4o."""
    with _tracer.start_as_current_span(
        "execute_tool generate_search_code",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "generate_search_code",
            "gen_ai.tool.type": "function",
        },
    ):
        logger.info("Node: generate_search_code")
        llm = _get_llm()

        async def _gen(query: str) -> str:
            resp = await llm.ainvoke([
                {"role": "system", "content": "You write Python scripts. Output ONLY code, no markdown."},
                {"role": "user", "content": SEARCH_CODE_PROMPT.format(query=query)},
            ])
            code = resp.content or ""
            code = code.strip()
            if code.startswith("```"):
                code = re.sub(r'^```\w*\n?', '', code)
                code = re.sub(r'\n?```$', '', code)
            return code.strip()

        tasks = [_gen(q.get("query", "")) for q in state["research_queries"]]
        state["search_codes"] = await asyncio.gather(*tasks)
        logger.info(f"  → Generated {len(state['search_codes'])} search scripts")
    return state


async def execute_searches(state: SimulationState) -> SimulationState:
    """Create sandboxes and run search scripts in parallel."""
    with _tracer.start_as_current_span(
        "execute_tool execute_searches",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "execute_searches",
            "gen_ai.tool.type": "function",
            "gen_ai.tool.description": "Run AI-generated search code in Azure Sandboxes",
        },
    ):
        logger.info("Node: execute_searches")
        sb = _get_sandbox_client()
        try:
            queries = state["research_queries"]
            codes = state["search_codes"]

            # Create sandboxes in parallel
            sids = await asyncio.gather(*[
                _traced_create_sandbox(sb, f"research-{i}", disk_image="python-3.14", egress_policy=EGRESS_POLICY)
                for i, _ in enumerate(queries)
            ])
            # Label sandboxes — flag roster queries
            sandbox_labels = []
            for i, q in enumerate(queries):
                query_text = q.get("query", "").lower()
                if "mexico" in query_text and ("squad" in query_text or "roster" in query_text or "convocatoria" in query_text):
                    sandbox_labels.append("Roster: Mexico 🇲🇽")
                elif "czech" in query_text and ("squad" in query_text or "roster" in query_text):
                    sandbox_labels.append("Roster: Czechia 🇨🇿")
                else:
                    sandbox_labels.append("Research")
            state["sandbox_ids"] = [{"id": sid, "role": label} for sid, label in zip(sids, sandbox_labels)]

            # --- ROSTER SCRIPT (HARDCODED, NOT AGENT-GENERATED) ---
            # This script fetches squad data directly from specific Wikipedia pages.
            # Unlike research queries, roster data needs to be accurate and deterministic,
            # so we don't rely on LLM-generated code here.
            ROSTER_SCRIPT = '''
import urllib.request
import urllib.parse
import json
import re
import sys

TEAM = sys.argv[1]  # "mexico" or "czechia"

URLS = {
    "mexico": "https://en.wikipedia.org/wiki/Mexico_national_football_team",
    "czechia": "https://en.wikipedia.org/wiki/Czech_Republic_national_football_team",
}

# Fetch the Players section via Wikipedia API
article_title = "Mexico national football team" if TEAM == "mexico" else "Czech Republic national football team"
api_url = f"https://en.wikipedia.org/w/api.php?action=parse&page={urllib.parse.quote(article_title)}&prop=wikitext&section=0&format=json"

# First get the section index for "Players" or "Current squad"
sections_url = f"https://en.wikipedia.org/w/api.php?action=parse&page={urllib.parse.quote(article_title)}&prop=sections&format=json"
headers = {"User-Agent": "WorldCupSimulator/1.0 (demo)"}

req = urllib.request.Request(sections_url, headers=headers)
with urllib.request.urlopen(req, timeout=15) as resp:
    sections_data = json.loads(resp.read().decode("utf-8"))

# Find the "Players" or "Current squad" section
player_section = None
for sec in sections_data.get("parse", {}).get("sections", []):
    title_lower = sec.get("line", "").lower()
    if title_lower in ("players", "current squad", "squad", "current roster"):
        player_section = sec.get("index")
        break

results = []
if player_section:
    # Fetch that section as plain wikitext
    section_url = f"https://en.wikipedia.org/w/api.php?action=parse&page={urllib.parse.quote(article_title)}&prop=wikitext&section={player_section}&format=json"
    req2 = urllib.request.Request(section_url, headers=headers)
    with urllib.request.urlopen(req2, timeout=15) as resp2:
        section_data = json.loads(resp2.read().decode("utf-8"))
    wikitext = section_data.get("parse", {}).get("wikitext", {}).get("*", "")
    
    # Extract player names from wikitext (football squad templates use |name= patterns)
    player_names = re.findall(r"\\|name=([^|\\n}]+)", wikitext)
    if not player_names:
        # Try alternative format: [[Player Name]]
        player_names = re.findall(r"\\[\\[([^\\]|]+?)(?:\\|[^\\]]+)?\\]\\]", wikitext)
        # Filter to likely player names (exclude categories, files, etc.)
        player_names = [p for p in player_names if not p.startswith(("File:", "Category:", "Image:"))]
    
    results.append({
        "name": f"{article_title} - Current Squad",
        "url": URLS[TEAM] + "#Players",
        "snippet": f"Players: {', '.join(player_names[:30])}"
    })
    results.append({
        "name": f"{article_title} - Raw Section",
        "url": URLS[TEAM] + "#Players",
        "snippet": wikitext[:3000]
    })
else:
    # Fallback: fetch full article extract
    extract_url = f"https://en.wikipedia.org/w/api.php?action=query&titles={urllib.parse.quote(article_title)}&prop=extracts&exintro=false&explaintext=true&format=json"
    req3 = urllib.request.Request(extract_url, headers=headers)
    with urllib.request.urlopen(req3, timeout=15) as resp3:
        extract_data = json.loads(resp3.read().decode("utf-8"))
    pages = extract_data.get("query", {}).get("pages", {})
    for page in pages.values():
        text = page.get("extract", "")
        results.append({
            "name": f"{article_title} - Full Article",
            "url": URLS[TEAM],
            "snippet": text[:3000]
        })

# Output results (no egress probes — those run in research sandboxes only)
output = {"query": f"{TEAM} national team squad roster", "results": results, "egress_probes": []}
print(json.dumps(output))
'''

            # Run searches in parallel
            async def _run_search(idx: int, sid: str, query: str, code: str) -> dict:
                label = sandbox_labels[idx]
                
                # For roster queries, use the dedicated roster script
                if label.startswith("Roster:"):
                    team = "mexico" if "Mexico" in label else "czechia"
                    roster_code = f'import sys; sys.argv = ["roster.py", "{team}"]\n' + ROSTER_SCRIPT
                    result = await sb.exec_python(sid, roster_code)
                else:
                    result = await sb.exec_python(sid, code)
                
                logger.info(f"  Sandbox {sid[:8]}… exited {result.exit_code}")
                parsed = None
                try:
                    parsed = json.loads(result.stdout)
                    if "results" not in parsed:
                        parsed = None
                except (json.JSONDecodeError, TypeError):
                    pass

                if not parsed:
                    # --- FALLBACK MECHANISM ---
                    # If the GPT-4o-generated code failed (bad JSON, crash, empty results),
                    # we transparently run the pre-written search.py script instead.
                    # The user/demo audience sees the same result either way.
                    logger.warning(f"  Generated code failed for '{query[:40]}', using fallback")
                    fallback_script = (SCRIPTS_DIR / "search.py").read_text(encoding="utf-8")
                    query_b64 = base64.b64encode(query.encode()).decode()
                    full_fallback = (
                        f'import os\nos.environ["PROBE_SPORTS"] = "1"\n'
                        f'import sys, base64; sys.argv = ["search.py", base64.b64decode("{query_b64}").decode()]\n\n'
                        f'{fallback_script}'
                    )
                    result2 = await sb.exec_python(sid, full_fallback)
                    try:
                        parsed = json.loads(result2.stdout)
                    except (json.JSONDecodeError, TypeError):
                        parsed = {"query": query, "error": result2.stderr[:300]}

                # Write results to file in sandbox for roster queries
                if label.startswith("Roster:"):
                    try:
                        results_text = json.dumps(parsed, indent=2, ensure_ascii=False)
                        await _write_file_in_sandbox(sb, sid, "/tmp/squad_search_results.txt", results_text)
                    except Exception as e:
                        logger.warning(f"  Failed to write roster results to sandbox: {e}")

                return parsed

            tasks = [_run_search(i, sid, q.get("query", ""), code) for i, (sid, q, code) in enumerate(zip(sids, queries, codes))]
            state["search_results"] = await asyncio.gather(*tasks)

            # Collect egress logs
            logs: list[dict[str, Any]] = []
            probes_collected = False
            for r in state["search_results"]:
                if not probes_collected and r.get("egress_probes"):
                    for probe in r["egress_probes"]:
                        logs.append({
                            "host": probe["host"],
                            "label": probe.get("label", probe["host"]),
                            "allowed": not probe["blocked"],
                            "detail": probe.get("error", f"HTTP {probe.get('status', '?')}"),
                        })
                    probes_collected = True
                if r.get("results") and not r.get("error"):
                    logs.append({"host": "en.wikipedia.org", "label": "Wikipedia API", "allowed": True})
            state["sandbox_logs"] = logs
            logger.info(f"  → {len(state['search_results'])} search results collected")
        finally:
            await sb.close()
    return state


async def generate_sim_code(state: SimulationState) -> SimulationState:
    """Generate the simulation Python script using GPT-4o."""
    with _tracer.start_as_current_span(
        "execute_tool generate_sim_code",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "generate_sim_code",
            "gen_ai.tool.type": "function",
        },
    ):
        logger.info("Node: generate_sim_code")
        llm = _get_llm()
        resp = await llm.ainvoke([
            {"role": "system", "content": "You write Python scripts. Output ONLY code, no markdown."},
            {"role": "user", "content": SIMULATE_CODE_PROMPT},
        ])
        code = resp.content or ""
        code = code.strip()
        if code.startswith("```"):
            code = re.sub(r'^```\w*\n?', '', code)
            code = re.sub(r'\n?```$', '', code)
        state["generated_sim_code"] = code.strip()
        logger.info(f"  → Generated simulation code ({len(state['generated_sim_code'])} chars)")
    return state


async def _collect_secure_egress_proof(sb: SandboxClient, sid: str, state: SimulationState):
    """Collect proof that secure egress worked: egress decisions showing blocked/allowed domains."""
    # Get egress decisions from the sandbox
    egress_decisions = await sb.get_egress_decisions(sid)
    logger.info(f"  Secure egress proof — {len(egress_decisions)} egress decisions")

    # Add egress decisions to sandbox_logs for the UI
    for decision in egress_decisions:
        host = decision.get("host", decision.get("destination", ""))
        rule = decision.get("ruleName", decision.get("rule", ""))
        action = decision.get("action", decision.get("decision", ""))
        allowed = action.lower() in ("allow", "allowed", "transform")
        state["sandbox_logs"].append({
            "host": host,
            "label": f"🔒 Egress: {rule}" if rule else f"🔒 Egress: {host}",
            "allowed": allowed,
            "detail": f"Action: {action}",
        })


async def execute_simulation(state: SimulationState) -> SimulationState:
    """Run the simulation code in a sandbox with research data."""
    with _tracer.start_as_current_span(
        "execute_tool execute_simulation",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "execute_simulation",
            "gen_ai.tool.type": "function",
            "gen_ai.tool.description": "Run AI-generated simulation code in Azure Sandbox",
        },
    ):
        logger.info("Node: execute_simulation")
        sb = _get_sandbox_client()
        secure = state.get("secure_egress", False)
        sim_egress = _get_sim_egress_policy(secure)
        try:
            sid = await _traced_create_sandbox(
                sb, "simulation", disk_image="python-3.14", cpu="2000m", memory="4Gi", egress_policy=sim_egress
            )
            state["sandbox_ids"].append({"id": sid, "role": "Simulation (Azure OpenAI)" if secure else "Simulation"})

            # Write research data and prompt to files (avoids ARG_MAX limit)
            research_json = json.dumps(state["search_results"], ensure_ascii=False)
            prompt_text = state["prompt"]

            # Split large data into chunks to write via shell commands
            await _write_file_in_sandbox(sb, sid, "/tmp/research.json", research_json)
            await _write_file_in_sandbox(sb, sid, "/tmp/user_prompt.txt", prompt_text)

            if secure:
                # Secure mode: sandbox code does NOT get the API key.
                # The egress firewall's Transform rule injects the api-key header transparently.
                # This demonstrates that untrusted code can call OpenAI without seeing credentials.
                preamble = (
                    f'import os, json, sys\n'
                    f'os.environ["AZURE_OPENAI_ENDPOINT"] = "{os.environ.get("AZURE_OPENAI_ENDPOINT", "")}"\n'
                    f'os.environ["AZURE_OPENAI_DEPLOYMENT"] = "{DEPLOYMENT}"\n'
                    f'os.environ["AZURE_OPENAI_KEY"] = "REDACTED-BY-SECURE-EGRESS"\n'
                    f'try:\n'
                    f'    RESEARCH_JSON = open("/tmp/research.json").read()\n'
                    f'except Exception as e:\n'
                    f'    RESEARCH_JSON = "[]"\n'
                    f'    print(f"Warning: could not read research.json: {{e}}", file=sys.stderr)\n'
                    f'try:\n'
                    f'    USER_PROMPT = open("/tmp/user_prompt.txt").read()\n'
                    f'except Exception as e:\n'
                    f'    USER_PROMPT = "Simulate a World Cup match between Mexico and Czechia"\n'
                    f'    print(f"Warning: could not read user_prompt.txt: {{e}}", file=sys.stderr)\n\n'
                )
                logger.info("  → Secure egress mode: API key NOT passed to sandbox, transform injects it")
            else:
                preamble = (
                    f'import os, json, sys\n'
                    f'os.environ["AZURE_OPENAI_ENDPOINT"] = "{os.environ.get("AZURE_OPENAI_ENDPOINT", "")}"\n'
                    f'os.environ["AZURE_OPENAI_KEY"] = "{os.environ.get("AZURE_OPENAI_KEY", "")}"\n'
                    f'os.environ["AZURE_OPENAI_DEPLOYMENT"] = "{DEPLOYMENT}"\n'
                    f'try:\n'
                    f'    RESEARCH_JSON = open("/tmp/research.json").read()\n'
                    f'except Exception as e:\n'
                    f'    RESEARCH_JSON = "[]"\n'
                    f'    print(f"Warning: could not read research.json: {{e}}", file=sys.stderr)\n'
                    f'try:\n'
                    f'    USER_PROMPT = open("/tmp/user_prompt.txt").read()\n'
                    f'except Exception as e:\n'
                    f'    USER_PROMPT = "Simulate a World Cup match between Mexico and Czechia"\n'
                    f'    print(f"Warning: could not read user_prompt.txt: {{e}}", file=sys.stderr)\n\n'
                )

            # Try AI-generated code
            full_script = preamble + state["generated_sim_code"]
            result = await sb.exec_python(sid, full_script)
            logger.info(f"  Compute sandbox (generated) exited {result.exit_code}")

            parsed = _try_parse_simulation(result.stdout)
            if parsed:
                state["simulation_result"] = parsed
                state["sandbox_logs"].append({"host": "openai.azure.com", "label": "Azure OpenAI", "allowed": True})
                if secure:
                    await _collect_secure_egress_proof(sb, sid, state)
                return state

            # Fallback
            logger.warning("  Generated code failed, using fallback simulate.py")
            fallback_script = (SCRIPTS_DIR / "simulate.py").read_text(encoding="utf-8")
            full_fallback = preamble + fallback_script
            result2 = await sb.exec_python(sid, full_fallback)
            parsed2 = _try_parse_simulation(result2.stdout)
            if parsed2:
                state["simulation_result"] = parsed2
                state["sandbox_logs"].append({"host": "openai.azure.com", "label": "Azure OpenAI", "allowed": True})
                if secure:
                    await _collect_secure_egress_proof(sb, sid, state)
            else:
                state["error"] = (
                    f"Simulation failed. Generated: {result.stdout[:200]}. "
                    f"Fallback: {result2.stdout[:200]} / {result2.stderr[:200]}"
                )
        finally:
            await sb.close()
    return state


async def package_results(state: SimulationState) -> SimulationState:
    """Package the final result for A2A response."""
    with _tracer.start_as_current_span(
        "execute_tool package_results",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "package_results",
            "gen_ai.tool.type": "function",
        },
    ):
        logger.info("Node: package_results — done")
    return state


def _try_parse_simulation(stdout: str) -> dict | None:
    """Try to parse simulation JSON from stdout, handling raw OpenAI responses."""
    if not stdout or not stdout.strip():
        return None
    try:
        parsed = json.loads(stdout)
        if "choices" in parsed and isinstance(parsed["choices"], list):
            content = parsed["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        if "homeTeam" in parsed and "awayTeam" in parsed:
            return parsed
        return None
    except (json.JSONDecodeError, TypeError, KeyError, IndexError):
        return None


# ---------------------------------------------------------------------------
# Graph Construction
# ---------------------------------------------------------------------------

def build_graph() -> StateGraph:
    """Build the LangGraph simulation state machine."""
    graph = StateGraph(SimulationState)

    graph.add_node("decompose_prompt", decompose_prompt)
    graph.add_node("generate_search_code", generate_search_code)
    graph.add_node("execute_searches", execute_searches)
    graph.add_node("generate_sim_code", generate_sim_code)
    graph.add_node("execute_simulation", execute_simulation)
    graph.add_node("package_results", package_results)

    graph.set_entry_point("decompose_prompt")
    graph.add_edge("decompose_prompt", "generate_search_code")
    graph.add_edge("generate_search_code", "execute_searches")
    graph.add_edge("execute_searches", "generate_sim_code")
    graph.add_edge("generate_sim_code", "execute_simulation")
    graph.add_edge("execute_simulation", "package_results")
    graph.add_edge("package_results", END)

    return graph


_compiled_graph = build_graph().compile()


async def run_simulation(prompt: str, secure_egress: bool = False) -> dict[str, Any]:
    """Execute the simulation graph and return structured results."""
    # Ensure sandbox secret exists for egress transform (idempotent)
    if secure_egress:
        api_key = os.environ.get("AZURE_OPENAI_KEY", "")
        if api_key:
            sb = _get_sandbox_client()
            logger.info("Pre-upserting sandbox group secret 'aoai-api-key' for secure egress")
            await sb.upsert_secret("aoai-api-key", {"api-key": api_key})
            await sb.close()
        else:
            logger.warning("AZURE_OPENAI_KEY not set — secure egress transform will fail!")

    with _tracer.start_as_current_span(
        "invoke_agent simulator-agent",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": "simulator-agent",
            "gen_ai.agent.id": "simulator-agent",
            "gen_ai.system": "azure.ai.openai",
        },
    ):
        initial_state: SimulationState = {
            "prompt": prompt,
            "secure_egress": secure_egress,
            "research_queries": [],
            "search_codes": [],
            "search_results": [],
            "sandbox_logs": [],
            "generated_sim_code": "",
            "simulation_result": None,
            "sandbox_ids": [],
            "error": None,
        }

        result = await _compiled_graph.ainvoke(initial_state)

        if result.get("error"):
            raise RuntimeError(result["error"])

        # Build research summary for UI
        research_summary = []
        for i, r in enumerate(result.get("search_results", [])):
            entry: dict[str, Any] = {"query": r.get("query", "")}
            snippets = [item.get("snippet", "")[:150] for item in r.get("results", []) if "error" not in item]
            entry["snippets"] = snippets[:3]
            if i < len(result.get("search_codes", [])):
                entry["generatedCode"] = result["search_codes"][i]
            research_summary.append(entry)

        return {
            "simulation": result["simulation_result"],
            "sandboxLogs": result["sandbox_logs"],
            "sandboxIds": result["sandbox_ids"],
            "researchQueries": research_summary,
            "generatedSimCode": result["generated_sim_code"],
        }
