"""
simulate.py — FALLBACK script that runs INSIDE a computation sandbox.

This script is NOT agent-generated. It is a pre-written fallback that runs when
GPT-4o-generated simulation code fails. The agent.py pipeline transparently
switches to this script without the user knowing.

Expects data to be pre-written to files (by agent.py's _write_file_in_sandbox):
  /tmp/research.json  — JSON array of research results from search sandboxes
  /tmp/user_prompt.txt — the original user prompt

Calls Azure OpenAI (GPT-4o) to synthesize research into a structured match simulation.
Also writes the full HTTP request to /tmp/openai_request.txt for demo purposes
(shows API key visibility in secure vs non-secure egress modes).

Prints the simulation JSON to stdout.
"""

import json
import os
import sys

# These are injected by the orchestrator before execution
try:
    research_data = json.loads(RESEARCH_JSON)  # noqa: F821
except Exception:
    research_data = []

try:
    user_prompt = USER_PROMPT  # noqa: F821
except Exception:
    user_prompt = "Simulate a World Cup match between Mexico and Czechia"


def call_openai(research: list, prompt: str) -> dict:
    """Call Azure OpenAI to produce the match simulation."""
    import urllib.request
    import urllib.error
    import random

    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    api_key = os.environ.get("AZURE_OPENAI_KEY", "")
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")

    url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version=2024-10-21"

    # Build a detailed summary of research findings for the model
    research_summary = ""
    for i, r in enumerate(research_data):
        query = r.get("query", "unknown query")
        results = r.get("results", [])
        research_summary += f"\n--- Research Query {i+1}: \"{query}\" ---\n"
        for item in results:
            if "error" in item:
                research_summary += f"  [Error: {item['error']}]\n"
            else:
                research_summary += f"  • {item.get('name', 'untitled')}\n"
                research_summary += f"    {item.get('snippet', 'no snippet')}\n"

    system_prompt = f"""You are a World Cup match simulator. You MUST base your simulation on the
ACTUAL research data provided below. Do NOT use your general knowledge about players who may have
been popular in the past — use ONLY the information from the research snippets to determine:
- Current team rosters and predicted lineups
- Recent form and results
- Tactical approaches
- Key players who are CURRENTLY active and available

CRITICAL RULES:
1. Only include players mentioned in the research data or who are confirmed active in 2026
2. Do NOT include retired players or players confirmed as unavailable
3. Vary the score — it could be anything from 0-0 to 4-3. Use the research to inform likelihood.
4. The simulation should feel unique and unpredictable. Seed: {random.randint(1, 99999)}
5. Include a "reasoning" field explaining HOW you used the research to arrive at this result

Return ONLY valid JSON with this exact schema:
{{
  "homeTeam": "Mexico",
  "awayTeam": "Czechia",
  "homeScore": <int>,
  "awayScore": <int>,
  "goals": [
    {{ "minute": <int>, "scorer": "<name>", "team": "<team>", "description": "<how the goal was scored>" }}
  ],
  "keyEvents": [
    {{ "minute": <int>, "event": "<description of event>" }}
  ],
  "summary": "<2-3 paragraph match summary>",
  "reasoning": "<1-2 paragraphs explaining how you used the research data to inform lineup choices, tactics, and the predicted result>"
}}

Include 0-5 goals and 5-10 key events (cards, substitutions, near-misses, VAR reviews, etc.).
Either team can win, or it can be a draw. Be creative and realistic."""

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                f"User request: {prompt}\n\n"
                f"=== RESEARCH DATA (use this as your primary source) ===\n{research_summary}"
            ),
        },
    ]

    body = json.dumps({
        "messages": messages,
        "temperature": 0.95,
        "response_format": {"type": "json_object"},
    }).encode()

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("api-key", api_key)

    # Log the full outgoing request to /tmp/openai_request.txt for demo inspection
    try:
        with open("/tmp/openai_request.txt", "w") as f:
            f.write(f"POST {url}\n\n")
            f.write("=== HEADERS ===\n")
            f.write(f"Content-Type: application/json\n")
            f.write(f"api-key: {api_key}\n\n")
            f.write("=== BODY (first 2000 chars) ===\n")
            f.write(body.decode()[:2000] + "\n")
    except OSError:
        pass

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw_body = resp.read().decode()
            if not raw_body.strip():
                raise RuntimeError("OpenAI API returned empty response body")
            data = json.loads(raw_body)
            content = data["choices"][0]["message"]["content"]
            return json.loads(content)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        print(f"HTTP Error {e.code}: {error_body[:500]}", file=sys.stderr)
        raise RuntimeError(f"OpenAI API returned {e.code}: {error_body[:200]}")
    except urllib.error.URLError as e:
        print(f"URL Error: {e.reason}", file=sys.stderr)
        raise RuntimeError(f"Network error calling OpenAI: {e.reason}")
    except json.JSONDecodeError as e:
        print(f"JSON decode error: {e}", file=sys.stderr)
        raise RuntimeError(f"Failed to parse OpenAI response as JSON: {e}")


def main():
    print("Running simulation with research data...", file=sys.stderr)
    simulation = call_openai(research_data, user_prompt)

    # Write to /tmp for persistence
    try:
        with open("/tmp/simulation.json", "w") as f:
            json.dump(simulation, f, indent=2)
    except OSError:
        pass

    # Print to stdout for collection by orchestrator
    print(json.dumps(simulation))


if __name__ == "__main__":
    main()
