"""Podcaster agent — orchestrates script generation, TTS, and audio delivery."""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any

from opentelemetry import context as context_api, trace
from opentelemetry.trace import SpanKind, StatusCode, set_span_in_context
from tools.fetch_content import fetch_multiple
from tools.pronunciations import apply_pronunciations
from script_generator import generate_script, generate_dynamic_pronunciations
from tts_client import TTSClient
from audio_utils import AudioSegment, interleave_audio, convert_to_mp3, upload_audio_to_blob

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer("podcaster-agent")

# In-memory task store (fine for single-instance lab demo)
_tasks: dict[str, dict] = {}


def get_task(task_id: str) -> dict | None:
    return _tasks.get(task_id)


async def generate_podcast(task_id: str, research_brief: dict) -> dict:
    """Full podcast pipeline: script → TTS → audio → blob upload.

    Updates _tasks[task_id] with progress and final result.
    Returns the result dict.
    """
    span = _tracer.start_span(
        "invoke_agent podcaster-agent",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": "podcaster-agent",
        },
    )
    ctx = set_span_in_context(span)
    token = context_api.attach(ctx)

    _tasks[task_id] = {**_tasks.get(task_id, {}), "status": "in_progress", "progress": "starting", "result": None}

    try:
        topic = research_brief.get("topic", "Unknown Topic")
        summary = research_brief.get("summary", "")
        sources = research_brief.get("sources", [])

        # Step 1: Fire TTS warmup (overlaps with LLM call)
        tts = TTSClient()
        _tasks[task_id]["progress"] = "Warming_up_TTS"
        warmup_task = asyncio.create_task(tts.warmup())

        # Step 2: Optionally re-fetch top sources for richer content
        _tasks[task_id]["progress"] = "Enriching_sources"
        with _tracer.start_as_current_span(
            "execute_tool enrich_sources",
            kind=SpanKind.INTERNAL,
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "enrich_sources",
                "gen_ai.tool.type": "function",
            },
        ):
            source_urls = [s.get("url") for s in sources if s.get("url")][:5]
            if source_urls:
                try:
                    enriched = await fetch_multiple(source_urls)
                    url_to_content = {item["url"]: item["content"] for item in enriched if item.get("content")}
                    for src in sources:
                        if src.get("url") in url_to_content:
                            src["content"] = url_to_content[src["url"]]
                except Exception as e:
                    logger.warning("Source enrichment failed (using original content): %s", e)

        # Step 3: Generate conversation script via LLM
        _tasks[task_id]["progress"] = "Generating_script"
        with _tracer.start_as_current_span(
            "execute_tool generate_script",
            kind=SpanKind.INTERNAL,
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "generate_script",
                "gen_ai.tool.type": "function",
                "gen_ai.tool.description": "Generate podcast conversation script via LLM",
            },
        ):
            script = await generate_script(topic, summary, sources)

        # Step 4: Apply pronunciation fixes for TTS (keep originals for transcript)
        tts_script = [
            {**turn, "text": apply_pronunciations(turn["text"])}
            for turn in script
        ]

        # Step 4b: Enhance with dynamic pronunciations via SDK (Feature #2)
        with _tracer.start_as_current_span(
            "execute_tool dynamic_pronunciations",
            kind=SpanKind.INTERNAL,
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "dynamic_pronunciations",
                "gen_ai.tool.type": "function",
            },
        ):
            try:
                full_text = " ".join(t["text"] for t in script)
                extra_prons = await generate_dynamic_pronunciations(full_text)
                if extra_prons:
                    import re
                    for turn in tts_script:
                        text = turn["text"]
                        for term, pron in extra_prons.items():
                            pattern = rf'\b{re.escape(term)}\b'
                            text = re.sub(pattern, pron, text, flags=re.IGNORECASE)
                        turn["text"] = text
                    logger.info("Applied %d dynamic pronunciations", len(extra_prons))
            except Exception as e:
                logger.warning("Dynamic pronunciation enhancement failed: %s", e)

        # Step 5: Wait for TTS warmup to complete
        await warmup_task

        # Step 6: Synthesize audio for each turn sequentially
        _tasks[task_id]["progress"] = "Synthesizing_audio"
        with _tracer.start_as_current_span(
            "execute_tool synthesize_audio",
            kind=SpanKind.INTERNAL,
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "synthesize_audio",
                "gen_ai.tool.type": "function",
                "gen_ai.tool.description": "Synthesize speech audio for all script turns via TTS",
            },
        ):
            segments: list[AudioSegment] = []
            for i, turn in enumerate(tts_script):
                _tasks[task_id]["progress"] = f"Synthesizing_turn_{i+1}_of_{len(tts_script)}"
                audio_bytes = await tts.synthesize(turn["text"], turn["speaker"])
                segments.append(AudioSegment(
                    speaker=turn["speaker"],
                    audio_bytes=audio_bytes,
                    duration_ms=0.0,  # Computed during interleaving
                ))

        # Step 7: Interleave + convert to MP3
        _tasks[task_id]["progress"] = "Assembling_audio"
        with _tracer.start_as_current_span(
            "execute_tool assemble_audio",
            kind=SpanKind.INTERNAL,
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "assemble_audio",
                "gen_ai.tool.type": "function",
                "gen_ai.tool.description": "Interleave audio segments and convert to MP3",
            },
        ):
            wav_bytes, chapters, duration_seconds = interleave_audio(segments)
            mp3_bytes = convert_to_mp3(wav_bytes)

        # Step 8: Upload to Azure Blob Storage
        _tasks[task_id]["progress"] = "Uploading_audio"
        with _tracer.start_as_current_span(
            "execute_tool upload_audio",
            kind=SpanKind.INTERNAL,
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "upload_audio",
                "gen_ai.tool.type": "function",
                "gen_ai.tool.description": "Upload podcast MP3 to Azure Blob Storage",
            },
        ):
            try:
                audio_url = await upload_audio_to_blob(mp3_bytes, task_id, fmt="mp3")
            except Exception as e:
                logger.warning("Blob upload failed, serving audio as data URI: %s", e)
                import base64
                mime = "audio/mpeg" if mp3_bytes[:3] != b"RIF" else "audio/wav"
                audio_url = f"data:{mime};base64,{base64.b64encode(mp3_bytes).decode()}"

        await tts.close()

        # Build result
        result = {
            "podcast": {
                "topic": topic,
                "audio_url": audio_url,
                "duration_seconds": duration_seconds,
                "turn_count": len(script),
                "chapters": chapters,
                "transcript": script,
            }
        }

        _tasks[task_id] = {"status": "completed", "progress": "done", "result": result, "request_id": _tasks.get(task_id, {}).get("request_id")}
        span.set_status(StatusCode.OK)
        return result

    except Exception as e:
        logger.exception("Podcast generation failed")
        _tasks[task_id] = {"status": "failed", "progress": "error", "error": str(e), "result": None, "request_id": _tasks.get(task_id, {}).get("request_id")}
        span.set_status(StatusCode.ERROR, str(e))
        raise
    finally:
        context_api.detach(token)
        span.end()
