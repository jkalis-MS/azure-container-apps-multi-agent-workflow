"""Tests for the Copilot SDK-powered podcast script generator."""

import json
import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from script_generator import generate_script, _validate_script, _generate_fallback_script, _parse_llm_response, _critique_and_refine, generate_dynamic_pronunciations

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_SOURCES = [
    {"type": "documentation", "title": "Azure Container Apps overview", "url": "https://learn.microsoft.com/aca", "content": "ACA enables serverless containers."},
    {"type": "blog", "title": "What's new in ACA", "url": "https://techcommunity.microsoft.com/aca", "content": "GPU workload profiles announced."},
    {"type": "code_sample", "title": "azure-samples/container-apps-ai", "url": "https://github.com/samples/aca-ai", "description": "Sample AI workloads on ACA."},
]

VALID_LLM_CONVERSATION = {
    "conversation": [
        {"speaker": "host", "text": "Welcome to the show! Today we're discussing Azure Container Apps, a serverless container platform."},
        {"speaker": "guest", "text": "Thanks for having me. Azure Container Apps overview on Microsoft Learn shows how it enables microservices."},
        {"speaker": "host", "text": "So what makes it different from plain Kubernetes?"},
        {"speaker": "guest", "text": "Great question. According to the Azure Container Apps overview, it abstracts away cluster management entirely."},
        {"speaker": "host", "text": "That sounds powerful. What about GPU support?"},
        {"speaker": "guest", "text": "The What's new in ACA blog announced GPU workload profiles and serverless GPU support."},
        {"speaker": "host", "text": "And there are code samples available too?"},
        {"speaker": "guest", "text": "Absolutely. The azure-samples/container-apps-ai repo has great starter code."},
        {"speaker": "host", "text": "Wonderful. Let's wrap up — key takeaways?"},
        {"speaker": "guest", "text": "Serverless containers, built-in scaling, GPU support, and a growing ecosystem of samples."},
        {"speaker": "host", "text": "Thanks for joining us! Until next time, keep building."},
    ]
}


def _make_llm_response(content: str) -> MagicMock:
    """Create a mock LLM response object with .content attribute."""
    resp = MagicMock()
    resp.content = content
    return resp


def _mock_copilot_session(response_content):
    """Create mock CopilotClient + session that returns given content.

    response_content can be a single str (every send_and_wait returns it)
    or a list[str] (successive calls return successive items).
    """
    if isinstance(response_content, list):
        events = []
        for content in response_content:
            ev = MagicMock()
            ev.data.content = content
            events.append(ev)
        send_and_wait = AsyncMock(side_effect=events)
    else:
        mock_event = MagicMock()
        mock_event.data.content = response_content
        send_and_wait = AsyncMock(return_value=mock_event)

    mock_session = AsyncMock()
    mock_session.send_and_wait = send_and_wait
    mock_session.disconnect = AsyncMock()

    mock_client = MagicMock()
    mock_client.start = AsyncMock()
    mock_client.stop = AsyncMock()
    mock_client.create_session = AsyncMock(return_value=mock_session)

    return mock_client


class _patch_copilot:
    """Context manager that mocks CopilotClient and required env vars.

    response_content can be a str (same response for every call)
    or a list[str] (successive calls return successive responses).
    """

    _ENV = {
        "AZURE_OPENAI_ENDPOINT": "https://test.openai.azure.com",
        "AZURE_OPENAI_API_KEY": "test-key",
        "AZURE_OPENAI_DEPLOYMENT": "gpt-4o",
    }

    def __init__(self, response_content):
        self._mock_client = _mock_copilot_session(response_content)
        self._session = None  # populated on enter
        self._client_patch = patch("script_generator.CopilotClient", return_value=self._mock_client)
        self._env_patch = patch.dict("os.environ", self._ENV)

    def __enter__(self):
        self._env_patch.__enter__()
        mock_cls = self._client_patch.__enter__()
        # Expose the mock session for assertions
        self._session = self._mock_client.create_session.return_value \
            if not isinstance(self._mock_client.create_session, AsyncMock) \
            else None
        return mock_cls

    def __exit__(self, *args):
        self._client_patch.__exit__(*args)
        self._env_patch.__exit__(*args)

    @property
    def mock_client(self):
        return self._mock_client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestScriptHasBothSpeakers:
    """Generated script must contain turns from both host and guest."""

    @pytest.mark.asyncio
    async def test_script_has_both_speakers(self):
        with _patch_copilot(json.dumps(VALID_LLM_CONVERSATION)):
            script = await generate_script("Azure Container Apps", "Serverless containers on Azure", SAMPLE_SOURCES)

        speakers = {turn["speaker"] for turn in script}
        assert "host" in speakers
        assert "guest" in speakers


class TestScriptStartsWithHost:
    """The first turn should always be from the host."""

    @pytest.mark.asyncio
    async def test_script_starts_with_host(self):
        with _patch_copilot(json.dumps(VALID_LLM_CONVERSATION)):
            script = await generate_script("Azure Container Apps", "Summary", SAMPLE_SOURCES)
        assert script[0]["speaker"] == "host"


class TestScriptReferencesSources:
    """At least one source title should appear in the transcript."""

    @pytest.mark.asyncio
    async def test_script_references_sources(self):
        with _patch_copilot(json.dumps(VALID_LLM_CONVERSATION)):
            script = await generate_script("Azure Container Apps", "Summary", SAMPLE_SOURCES)

        full_text = " ".join(turn["text"] for turn in script)
        source_titles = [s["title"] for s in SAMPLE_SOURCES]
        assert any(title in full_text for title in source_titles), (
            f"No source title found in transcript. Titles: {source_titles}"
        )


class TestTargetWordCountRespected:
    """Total word count should be within ±30% of the target (default 1050)."""

    @pytest.mark.asyncio
    async def test_target_word_count_respected(self):
        # Build a response with ~1050 words
        filler = "This is a really interesting point and I think our listeners will appreciate it. "
        turns = []
        word_count = 0
        speaker_toggle = ["host", "guest"]
        idx = 0
        while word_count < 1050:
            line = filler * 3  # ~24 words per repetition
            turns.append({"speaker": speaker_toggle[idx % 2], "text": line.strip()})
            word_count += len(line.split())
            idx += 1

        conversation = {"conversation": turns}
        with _patch_copilot(json.dumps(conversation)):
            script = await generate_script("Test Topic", "Summary", SAMPLE_SOURCES, target_words=1050)

        total_words = sum(len(turn["text"].split()) for turn in script)
        assert 1050 * 0.7 <= total_words <= 1050 * 1.3, (
            f"Word count {total_words} outside ±30% of 1050"
        )


class TestFallbackOnLLMFailure:
    """When the Copilot SDK raises an exception, generate_script should return a valid fallback script."""

    @pytest.mark.asyncio
    async def test_fallback_on_llm_failure(self):
        mock_client = MagicMock()
        mock_client.start = AsyncMock(side_effect=RuntimeError("Copilot CLI unavailable"))
        mock_client.stop = AsyncMock()

        with patch("script_generator.CopilotClient", return_value=mock_client):
            script = await generate_script("Azure Container Apps", "Summary", SAMPLE_SOURCES)

        # Fallback must still have correct structure
        assert len(script) >= 5
        assert script[0]["speaker"] == "host"
        speakers = {turn["speaker"] for turn in script}
        assert "host" in speakers and "guest" in speakers
        # Every turn must have non-empty text
        for turn in script:
            assert turn.get("text", "").strip()


class TestMalformedJsonRepaired:
    """When the LLM returns slightly broken JSON, json_repair should fix it."""

    @pytest.mark.asyncio
    async def test_malformed_json_repaired(self):
        # Missing closing brace + trailing comma — common LLM mistake
        broken_json = (
            '{"conversation": ['
            '{"speaker": "host", "text": "Welcome to the show about testing!"},'
            '{"speaker": "guest", "text": "Thanks for having me on."},'
            '{"speaker": "host", "text": "Tell us about it."},'
            '{"speaker": "guest", "text": "Sure, testing is important."},'
            '{"speaker": "host", "text": "Great insights, thanks!"},'
            ']}'
        )
        with _patch_copilot(broken_json):
            script = await generate_script("Testing", "Summary about testing", SAMPLE_SOURCES)

        assert len(script) >= 5
        assert script[0]["speaker"] == "host"


class TestValidateScript:
    """Unit tests for the _validate_script helper."""

    def test_valid_script_passes(self):
        assert _validate_script(VALID_LLM_CONVERSATION["conversation"]) is True

    def test_too_few_turns_fails(self):
        short = [{"speaker": "host", "text": "Hi"}, {"speaker": "guest", "text": "Hello"}]
        assert _validate_script(short) is False

    def test_missing_speaker_fails(self):
        only_host = [{"speaker": "host", "text": f"Line {i}"} for i in range(6)]
        assert _validate_script(only_host) is False

    def test_not_starting_with_host_fails(self):
        bad_start = list(VALID_LLM_CONVERSATION["conversation"])
        bad_start[0] = {"speaker": "guest", "text": "I go first"}
        assert _validate_script(bad_start) is False


class TestFallbackScript:
    """Unit tests for the _generate_fallback_script helper."""

    def test_fallback_has_correct_structure(self):
        script = _generate_fallback_script("Test Topic", SAMPLE_SOURCES)
        assert len(script) >= 5
        assert script[0]["speaker"] == "host"
        speakers = {t["speaker"] for t in script}
        assert speakers == {"host", "guest"}

    def test_fallback_mentions_sources(self):
        script = _generate_fallback_script("Test Topic", SAMPLE_SOURCES)
        full_text = " ".join(t["text"] for t in script)
        # Fallback should mention at least one source title
        assert any(s["title"] in full_text for s in SAMPLE_SOURCES[:3])


# ---------------------------------------------------------------------------
# Feature #1: Source Enrichment via Tools
# ---------------------------------------------------------------------------

class TestToolsPassedToSession:
    """The fetch_url_tool should be passed to create_session."""

    @pytest.mark.asyncio
    async def test_tools_list_passed_to_create_session(self):
        patcher = _patch_copilot(json.dumps(VALID_LLM_CONVERSATION))
        with patcher:
            await generate_script("Test Topic", "Summary", SAMPLE_SOURCES)

        # create_session should have been called with tools keyword arg
        call_args = patcher.mock_client.create_session.call_args
        assert call_args.kwargs.get("tools") is not None
        assert len(call_args.kwargs["tools"]) >= 1


class TestFetchUrlToolDefined:
    """The fetch_url_tool should be a callable tool function."""

    def test_fetch_url_tool_is_callable(self):
        from script_generator import fetch_url_tool
        assert callable(fetch_url_tool)


# ---------------------------------------------------------------------------
# Feature #2: Dynamic Pronunciations via SDK
# ---------------------------------------------------------------------------

SAMPLE_PRONUNCIATION_RESPONSE = json.dumps({
    "Terraform": "terra-form",
    "Pulumi": "puh-loo-mee",
})


class TestDynamicPronunciations:
    """generate_dynamic_pronunciations should return a dict of term -> phonetic."""

    @pytest.mark.asyncio
    async def test_returns_pronunciation_dict(self):
        with _patch_copilot(SAMPLE_PRONUNCIATION_RESPONSE):
            result = await generate_dynamic_pronunciations(
                "We use Terraform and Pulumi for infrastructure"
            )

        assert isinstance(result, dict)
        assert result.get("Terraform") == "terra-form"
        assert result.get("Pulumi") == "puh-loo-mee"

    @pytest.mark.asyncio
    async def test_returns_empty_on_failure(self):
        mock_client = MagicMock()
        mock_client.start = AsyncMock(side_effect=RuntimeError("SDK unavailable"))
        mock_client.stop = AsyncMock()

        with patch("script_generator.CopilotClient", return_value=mock_client), \
             patch.dict("os.environ", _patch_copilot._ENV):
            result = await generate_dynamic_pronunciations("Some text")

        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_on_no_content(self):
        """When the LLM returns empty JSON, result should be empty dict."""
        with _patch_copilot("{}"):
            result = await generate_dynamic_pronunciations("Hello world")

        assert result == {}


# ---------------------------------------------------------------------------
# Feature #3: Script Quality Validation (critique parsing)
# ---------------------------------------------------------------------------

class TestParseLlmResponse:
    """Unit tests for the _parse_llm_response helper."""

    def test_parses_valid_json(self):
        event = MagicMock()
        event.data.content = json.dumps(VALID_LLM_CONVERSATION)
        result = _parse_llm_response(event)
        assert len(result) == len(VALID_LLM_CONVERSATION["conversation"])

    def test_parses_json_in_markdown_fence(self):
        content = '```json\n' + json.dumps(VALID_LLM_CONVERSATION) + '\n```'
        event = MagicMock()
        event.data.content = content
        result = _parse_llm_response(event)
        assert len(result) >= 5

    def test_raises_on_empty_content(self):
        event = MagicMock()
        event.data.content = ""
        with pytest.raises(ValueError):
            _parse_llm_response(event)

    def test_raises_on_none_event(self):
        with pytest.raises(ValueError):
            _parse_llm_response(None)


class TestCritiqueAndRefine:
    """Tests for the multi-turn critique and refinement flow."""

    @pytest.mark.asyncio
    async def test_keeps_original_when_score_high(self):
        """Score >= 7 means the original script is kept."""
        critique_response = json.dumps({"score": 9, "feedback": ""})

        mock_event = MagicMock()
        mock_event.data.content = critique_response
        mock_session = AsyncMock()
        mock_session.send_and_wait = AsyncMock(return_value=mock_event)

        original = VALID_LLM_CONVERSATION["conversation"]
        result = await _critique_and_refine(
            mock_session, original, "Test Topic", SAMPLE_SOURCES,
        )

        assert result == original
        # Only one send_and_wait call (critique), no refinement
        assert mock_session.send_and_wait.call_count == 1

    @pytest.mark.asyncio
    async def test_refines_when_score_low(self):
        """Score < 7 triggers a refinement turn."""
        critique_response = json.dumps({"score": 4, "feedback": "Needs more source references"})
        refined_response = json.dumps(VALID_LLM_CONVERSATION)

        critique_event = MagicMock()
        critique_event.data.content = critique_response
        refined_event = MagicMock()
        refined_event.data.content = refined_response

        mock_session = AsyncMock()
        mock_session.send_and_wait = AsyncMock(side_effect=[critique_event, refined_event])

        original = VALID_LLM_CONVERSATION["conversation"]
        result = await _critique_and_refine(
            mock_session, original, "Test Topic", SAMPLE_SOURCES,
        )

        # Two send_and_wait calls: critique + refinement
        assert mock_session.send_and_wait.call_count == 2
        # Result should be the refined script (which is the same valid script in this test)
        assert len(result) >= 5

    @pytest.mark.asyncio
    async def test_keeps_original_on_critique_failure(self):
        """If critique call raises, the original script is returned."""
        mock_session = AsyncMock()
        mock_session.send_and_wait = AsyncMock(side_effect=RuntimeError("timeout"))

        original = VALID_LLM_CONVERSATION["conversation"]
        result = await _critique_and_refine(
            mock_session, original, "Test Topic", SAMPLE_SOURCES,
        )

        assert result == original

    @pytest.mark.asyncio
    async def test_keeps_original_when_refined_invalid(self):
        """If refinement returns invalid script, original is kept."""
        critique_response = json.dumps({"score": 3, "feedback": "Too short"})
        # Refined response is structurally invalid (only 2 turns)
        bad_refined = json.dumps({"conversation": [
            {"speaker": "host", "text": "Hi"},
            {"speaker": "guest", "text": "Hello"},
        ]})

        critique_event = MagicMock()
        critique_event.data.content = critique_response
        bad_event = MagicMock()
        bad_event.data.content = bad_refined

        mock_session = AsyncMock()
        mock_session.send_and_wait = AsyncMock(side_effect=[critique_event, bad_event])

        original = VALID_LLM_CONVERSATION["conversation"]
        result = await _critique_and_refine(
            mock_session, original, "Test Topic", SAMPLE_SOURCES,
        )

        # Falls back to original because refined script fails validation
        assert result == original


# ---------------------------------------------------------------------------
# Feature #4: Multi-turn Refinement (end-to-end)
# ---------------------------------------------------------------------------

class TestMultiTurnScriptGeneration:
    """End-to-end tests for the multi-turn generate → critique → refine flow."""

    @pytest.mark.asyncio
    async def test_full_flow_with_high_score(self):
        """High critique score → no refinement, original returned."""
        responses = [
            json.dumps(VALID_LLM_CONVERSATION),     # Turn 1: script
            json.dumps({"score": 8, "feedback": ""}),  # Turn 2: critique
        ]
        with _patch_copilot(responses):
            script = await generate_script("ACA", "Summary", SAMPLE_SOURCES)

        assert len(script) >= 5
        assert script[0]["speaker"] == "host"

    @pytest.mark.asyncio
    async def test_full_flow_with_low_score_triggers_refinement(self):
        """Low critique score → refinement turn → refined script returned."""
        responses = [
            json.dumps(VALID_LLM_CONVERSATION),                 # Turn 1: script
            json.dumps({"score": 4, "feedback": "Too generic"}),  # Turn 2: critique
            json.dumps(VALID_LLM_CONVERSATION),                 # Turn 3: refined
        ]
        with _patch_copilot(responses):
            script = await generate_script("ACA", "Summary", SAMPLE_SOURCES)

        assert len(script) >= 5
        assert script[0]["speaker"] == "host"
