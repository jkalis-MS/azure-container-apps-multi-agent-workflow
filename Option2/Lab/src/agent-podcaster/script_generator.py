"""LLM-powered conversation script generator for podcast episodes.

Uses the GitHub Copilot SDK with Azure OpenAI BYOK (Bring Your Own Key)
to generate natural two-person podcast conversations from research materials.
"""

from __future__ import annotations

import json
import os
from typing import Any

from copilot import CopilotClient, define_tool
from copilot.session import PermissionHandler
from json_repair import repair_json
from pydantic import BaseModel, Field
from tools.fetch_content import fetch_page_content


def _get_azure_provider() -> dict:
    """Build the Azure OpenAI BYOK provider config from environment."""
    return {
        "type": "azure",
        "base_url": os.environ["AZURE_OPENAI_ENDPOINT"],
        "api_key": os.environ["AZURE_OPENAI_API_KEY"],
        "azure": {
            "api_version": os.getenv("AZURE_OPENAI_SDK_API_VERSION", "2024-12-01-preview"),
        },
    }


def _get_model() -> str:
    """Get the Azure OpenAI deployment name."""
    return os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")


# ---------------------------------------------------------------------------
# Feature #1: Source Enrichment Tool
# ---------------------------------------------------------------------------

class FetchUrlParams(BaseModel):
    url: str = Field(description="The URL to fetch and extract readable text from")


@define_tool(description="Fetch a web page and extract its readable text content")
async def fetch_url_tool(params: FetchUrlParams) -> dict:
    """SDK tool: lets the LLM fetch additional source content during generation."""
    result = await fetch_page_content(params.url)
    return {"url": result["url"], "content": result.get("content", "")[:3000]}


# ---------------------------------------------------------------------------
# Feature #3: Script Quality Critique Prompt
# ---------------------------------------------------------------------------

_CRITIQUE_PROMPT = """Now review the podcast script you just generated. Rate it 1-10 on these criteria:
- Natural conversation flow
- Source references (does the guest cite actual sources?)
- Topic coverage (are key points addressed?)
- Listener engagement

Return ONLY a JSON object: {"score": <int 1-10>, "feedback": "<brief issues to fix>"}
If the script is good (score >= 7), set feedback to empty string."""


# ---------------------------------------------------------------------------
# Feature #2: Dynamic Pronunciation Generation
# ---------------------------------------------------------------------------

_PRONUNCIATION_SYSTEM_PROMPT = """You are a pronunciation expert for text-to-speech systems.
Given text, identify technical terms, acronyms, or brand names that a TTS engine
might mispronounce. Return ONLY a JSON object mapping each term to its phonetic
respelling (e.g. {"kubectl": "kube-control", "WASI": "wah-zee"}).
Only include terms that genuinely need help. Return {} if none are needed."""


def _build_system_prompt(target_words: int) -> str:
    return f"""You are a podcast script writer. You write natural, engaging conversation scripts
for a two-person tech podcast.

SPEAKERS:
- "host": The curious interviewer. Asks insightful questions, bridges topics for the audience,
  and summarises key points. Friendly and enthusiastic.
- "guest": The expert. Provides detailed answers, references specific sources and data,
  and explains complex concepts clearly. Authoritative but approachable.

RULES:
1. The conversation must feel natural — use filler words occasionally ("So,", "Right,",
   "That's a great point,"), but keep them minimal.
2. The guest MUST reference actual sources by name or URL when stating facts.
3. Aim for approximately {target_words} total words (~{target_words // 150} minutes at 150 wpm).
4. Start with the host introducing the topic.
5. End with the host summarising key takeaways.
6. Vary turn lengths — some short back-and-forth, some longer explanations.
7. Return ONLY a JSON object with a single key "conversation" whose value is an array of objects,
   each with "speaker" (either "host" or "guest") and "text" (the dialogue line).

Example format:
{{"conversation": [{{"speaker": "host", "text": "Welcome to..."}}, {{"speaker": "guest", "text": "Thanks for having me..."}}]}}"""


def _build_user_prompt(topic: str, summary: str, sources: list[dict]) -> str:
    # Group sources by type
    grouped: dict[str, list[dict]] = {}
    for src in sources:
        src_type = src.get("type", "other")
        grouped.setdefault(src_type, []).append(src)

    source_sections = []
    for src_type, items in grouped.items():
        lines = [f"\n## {src_type.replace('_', ' ').title()} Sources"]
        for item in items[:10]:  # cap per category
            title = item.get("title", item.get("name", "Untitled"))
            url = item.get("url", "")
            snippet = item.get("content", item.get("description", ""))[:500]
            lines.append(f"- **{title}**\n  URL: {url}\n  {snippet}")
        source_sections.append("\n".join(lines))

    sources_text = "\n".join(source_sections) if source_sections else "No detailed sources available."

    return f"""Create a podcast conversation about the following topic.

TOPIC: {topic}

RESEARCH SUMMARY:
{summary}

SOURCE MATERIALS:
{sources_text}

Remember: Return ONLY a JSON object with key "conversation" containing the dialogue array."""


async def generate_script(
    topic: str,
    summary: str,
    sources: list[dict],
    target_words: int = 1050,
) -> list[dict]:
    """Generate a conversational podcast script from research materials.

    Uses the Copilot SDK with Azure OpenAI BYOK to produce the conversation.
    Returns a list of {"speaker": "host"|"guest", "text": "..."} dicts.
    """
    system_prompt = _build_system_prompt(target_words)
    user_prompt = _build_user_prompt(topic, summary, sources)

    try:
        client = CopilotClient()
        await client.start()

        try:
            session = await client.create_session(
                model=_get_model(),
                provider=_get_azure_provider(),
                system_message={"content": system_prompt},
                infinite_sessions={"enabled": False},
                tools=[fetch_url_tool],  # Feature #1: source enrichment tool
                on_permission_request=PermissionHandler.approve_all,
            )

            # Turn 1: generate the script
            event = await session.send_and_wait(
                user_prompt,
                timeout=120,
            )

            conversation = _parse_llm_response(event)

            # Feature #3+#4: quality critique + multi-turn refinement
            if _validate_script(conversation):
                conversation = await _critique_and_refine(
                    session, conversation, topic, sources,
                )

            await session.disconnect()
        finally:
            await client.stop()

        # Final structural validation
        if not _validate_script(conversation):
            print("[ScriptGenerator] Validation failed, using fallback script")
            return _generate_fallback_script(topic, sources)

        return conversation

    except Exception as e:
        print(f"[ScriptGenerator] Copilot SDK script generation failed: {e}")
        return _generate_fallback_script(topic, sources)


def _parse_llm_response(event) -> list[dict]:
    """Extract conversation list from a Copilot SDK response event."""
    if not event or not event.data.content:
        raise ValueError("No response received from Copilot SDK session")

    raw = event.data.content.strip()

    # Strip markdown fences if present
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    repaired = repair_json(raw, return_objects=False)
    data = json.loads(repaired)

    if isinstance(data, dict):
        return data.get("conversation", [])
    elif isinstance(data, list):
        return data
    else:
        raise ValueError(f"Unexpected response type: {type(data)}")


async def _critique_and_refine(
    session,
    conversation: list[dict],
    topic: str,
    sources: list[dict],
) -> list[dict]:
    """Feature #3+#4: ask the same session for a quality critique,
    then request a revision if the score is below threshold."""
    try:
        # Turn 2: self-critique (same session = multi-turn)
        critique_event = await session.send_and_wait(
            _CRITIQUE_PROMPT,
            timeout=60,
        )
        if not critique_event or not critique_event.data.content:
            return conversation  # keep original on failure

        raw = critique_event.data.content.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        repaired = repair_json(raw, return_objects=False)
        critique = json.loads(repaired)
        score = critique.get("score", 10)
        feedback = critique.get("feedback", "")

        if score >= 7 or not feedback:
            return conversation  # good enough

        # Turn 3: ask for revision based on feedback (multi-turn refinement)
        refine_prompt = (
            f"The script scored {score}/10. Issues: {feedback}\n\n"
            "Please revise the script to address these issues. "
            "Return ONLY the revised JSON object with key 'conversation'."
        )
        refine_event = await session.send_and_wait(
            refine_prompt,
            timeout=120,
        )

        refined = _parse_llm_response(refine_event)
        if _validate_script(refined):
            return refined

    except Exception as e:
        print(f"[ScriptGenerator] Critique/refine failed (keeping original): {e}")

    return conversation


async def generate_dynamic_pronunciations(text: str) -> dict[str, str]:
    """Feature #2: use a separate SDK session to discover additional
    technical terms that need TTS pronunciation help.

    Returns a dict of term -> phonetic respelling.
    """
    try:
        client = CopilotClient()
        await client.start()
        try:
            session = await client.create_session(
                model=_get_model(),
                provider=_get_azure_provider(),
                system_message={"content": _PRONUNCIATION_SYSTEM_PROMPT},
                on_permission_request=PermissionHandler.approve_all,
            )
            event = await session.send_and_wait(
                f"Identify terms needing pronunciation help:\n\n{text[:4000]}",
                timeout=30,
            )
            await session.disconnect()
        finally:
            await client.stop()

        if not event or not event.data.content:
            return {}

        raw = event.data.content.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        repaired = repair_json(raw, return_objects=False)
        data = json.loads(repaired)
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
        return {}
    except Exception as e:
        print(f"[ScriptGenerator] Dynamic pronunciation generation failed: {e}")
        return {}


def _validate_script(conversation: list[dict]) -> bool:
    """Validate that a script meets minimum quality requirements."""
    if not isinstance(conversation, list) or len(conversation) < 5:
        return False

    speakers = {turn.get("speaker") for turn in conversation}
    if not {"host", "guest"}.issubset(speakers):
        return False

    if conversation[0].get("speaker") != "host":
        return False

    # Every turn must have non-empty text
    for turn in conversation:
        if not turn.get("text", "").strip():
            return False

    return True


def _generate_fallback_script(topic: str, sources: list[dict]) -> list[dict]:
    """Generate a basic template script when LLM generation fails."""
    source_mentions = []
    for src in sources[:3]:
        title = src.get("title", src.get("name", "a recent source"))
        source_mentions.append(title)

    sources_text = ", ".join(source_mentions) if source_mentions else "several recent sources"

    return [
        {
            "speaker": "host",
            "text": f"Welcome back to the show! Today we're diving into {topic}. "
                    f"This is a topic that's been getting a lot of attention lately.",
        },
        {
            "speaker": "guest",
            "text": f"Thanks for having me. Yeah, {topic} is really exciting right now. "
                    f"I've been looking at {sources_text} to prepare for today.",
        },
        {
            "speaker": "host",
            "text": "So let's start from the basics. What should our listeners know about this topic?",
        },
        {
            "speaker": "guest",
            "text": f"The key thing to understand about {topic} is that it represents a significant "
                    f"shift in how we think about this space. The sources I mentioned earlier go "
                    f"into great detail about the architectural decisions and best practices.",
        },
        {
            "speaker": "host",
            "text": "That's really insightful. And what about practical applications?",
        },
        {
            "speaker": "guest",
            "text": "Practically speaking, teams are already adopting these patterns. "
                    "The documentation and code samples available make it quite approachable "
                    "for developers who want to get started.",
        },
        {
            "speaker": "host",
            "text": "Fantastic. Any final thoughts for our listeners?",
        },
        {
            "speaker": "guest",
            "text": f"I'd say the best next step is to check out the official documentation "
                    f"and try it hands-on. {topic} is one of those things that clicks once "
                    f"you start building with it.",
        },
        {
            "speaker": "host",
            "text": "Great advice. Thanks so much for joining us today, and thanks to all "
                    "our listeners for tuning in. Until next time!",
        },
    ]
