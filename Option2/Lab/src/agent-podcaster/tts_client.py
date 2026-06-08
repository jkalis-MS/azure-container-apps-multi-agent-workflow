"""TTS client — abstracts GPU XTTS-v2 server and Azure OpenAI TTS fallback."""
from __future__ import annotations

import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

# Voice mapping: logical name → XTTS-v2 speaker_wav name or Azure OpenAI voice
VOICE_MAP_XTTS = {
    "host": os.getenv("TTS_HOST_VOICE", "host-female"),
    "guest": os.getenv("TTS_GUEST_VOICE", "guest-male"),
}
VOICE_MAP_AZURE = {
    "host": "nova",
    "guest": "onyx",
}

TTS_SERVER_URL = os.getenv("TTS_SERVER_URL", "http://localhost:8004")
TTS_TIMEOUT_BUDGET = int(os.getenv("TTS_TIMEOUT_BUDGET_SECONDS", "300"))
CONTENT_FACTORY_MODE = os.getenv("CONTENT_FACTORY_MODE", "lab")


class TTSClient:
    """Unified TTS client supporting GPU XTTS-v2 and Azure OpenAI TTS."""

    def __init__(self):
        self._http = httpx.AsyncClient(timeout=60.0)
        self._budget_start: float | None = None
        self._budget_used: float = 0.0
        self._warmed_up = False

    async def warmup(self) -> None:
        """Pre-warm GPU TTS server (non-blocking best-effort)."""
        if CONTENT_FACTORY_MODE != "full" or self._warmed_up:
            return
        try:
            resp = await self._http.post(f"{TTS_SERVER_URL}/warmup", timeout=120.0)
            resp.raise_for_status()
            self._warmed_up = True
            logger.info("TTS server warmed up: %s", resp.json())
        except Exception as e:
            logger.warning("TTS warmup failed (will retry on first synth): %s", e)

    async def synthesize(self, text: str, speaker: str) -> bytes:
        """Synthesize speech for a single turn. Returns WAV bytes.

        In 'full' mode, uses GPU XTTS-v2 server with fallback to Azure OpenAI.
        In 'lab' mode, uses Azure OpenAI TTS API directly.
        """
        if self._budget_start is None:
            self._budget_start = time.monotonic()

        # Check budget
        elapsed = time.monotonic() - self._budget_start
        if elapsed > TTS_TIMEOUT_BUDGET:
            logger.warning("TTS budget exceeded (%.0fs), forcing Azure OpenAI fallback", elapsed)
            return await self._synthesize_azure_openai(text, speaker)

        if CONTENT_FACTORY_MODE == "full":
            try:
                return await self._synthesize_xtts(text, speaker)
            except Exception as e:
                logger.warning("XTTS synthesis failed, falling back to Azure OpenAI: %s", e)
                return await self._synthesize_azure_openai(text, speaker)
        else:
            return await self._synthesize_azure_openai(text, speaker)

    async def _synthesize_xtts(self, text: str, speaker: str, retries: int = 3) -> bytes:
        """Call GPU XTTS-v2 server with retry on 503 (cold start)."""
        voice_name = VOICE_MAP_XTTS.get(speaker, speaker)
        payload = {"text": text, "speaker_wav": voice_name, "language": "en"}

        for attempt in range(retries):
            try:
                resp = await self._http.post(
                    f"{TTS_SERVER_URL}/synthesize",
                    json=payload,
                    timeout=60.0,
                )
                if resp.status_code == 503 and attempt < retries - 1:
                    wait = 2 ** attempt * 5  # 5s, 10s, 20s
                    logger.info("TTS server cold (503), retry in %ds (attempt %d/%d)", wait, attempt + 1, retries)
                    import asyncio
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.content
            except httpx.TimeoutException:
                if attempt < retries - 1:
                    wait = 2 ** attempt * 5
                    logger.info("TTS timeout, retry in %ds", wait)
                    import asyncio
                    await asyncio.sleep(wait)
                    continue
                raise

        raise RuntimeError("XTTS synthesis failed after all retries")

    async def _synthesize_azure_openai(self, text: str, speaker: str) -> bytes:
        """Azure OpenAI TTS API (tts-1 or tts-1-hd)."""
        voice = VOICE_MAP_AZURE.get(speaker, "nova")
        endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
        api_key = os.environ["AZURE_OPENAI_API_KEY"]
        # TTS requires a preview API version that supports the audio/speech endpoint
        api_version = os.getenv("AZURE_OPENAI_TTS_API_VERSION", "2024-12-01-preview")

        url = f"{endpoint}/openai/deployments/tts-1/audio/speech?api-version={api_version}"
        resp = await self._http.post(
            url,
            headers={"api-key": api_key, "Content-Type": "application/json"},
            json={"model": "tts-1", "input": text, "voice": voice, "response_format": "wav"},
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.content

    async def close(self) -> None:
        await self._http.aclose()
