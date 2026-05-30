"""Audio processing utilities: interleaving, format conversion, and blob upload."""

from __future__ import annotations

import io
import os
from dataclasses import dataclass

from pydub import AudioSegment as PydubSegment


@dataclass
class AudioSegment:
    """A single TTS audio segment with metadata."""
    speaker: str
    audio_bytes: bytes
    duration_ms: float


def interleave_audio(
    segments: list[AudioSegment],
    turn_gap_ms: int = 150,
    section_gap_ms: int = 400,
) -> tuple[bytes, list[dict], float]:
    """Interleave audio segments into a single WAV with chapter markers.

    Inserts short silence between turns and longer silence every ~8 turns
    to create natural section breaks.

    Returns:
        (wav_bytes, chapter_markers, duration_seconds) where chapter_markers is a list of
        {"text": str, "start_seconds": float, "end_seconds": float}.
    """
    if not segments:
        empty = PydubSegment.silent(duration=100, frame_rate=24000)
        buf = io.BytesIO()
        empty.export(buf, format="wav")
        return buf.getvalue(), [], 0.1

    combined = PydubSegment.empty()
    chapter_markers: list[dict] = []
    section_start_ms = 0.0
    section_index = 1

    for i, seg in enumerate(segments):
        # Add gap before this segment
        if i > 0:
            if i % 8 == 0:
                gap = PydubSegment.silent(duration=section_gap_ms, frame_rate=24000)
                # Close the previous section chapter
                chapter_markers.append({
                    "text": f"Section {section_index}",
                    "start_seconds": section_start_ms / 1000.0,
                    "end_seconds": len(combined) / 1000.0,
                })
                section_index += 1
                section_start_ms = len(combined) + section_gap_ms
            else:
                gap = PydubSegment.silent(duration=turn_gap_ms, frame_rate=24000)
            combined += gap

        # Decode and append the WAV audio
        audio = PydubSegment.from_wav(io.BytesIO(seg.audio_bytes))
        combined += audio

    # Final section chapter
    chapter_markers.append({
        "text": f"Section {section_index}",
        "start_seconds": section_start_ms / 1000.0,
        "end_seconds": len(combined) / 1000.0,
    })

    buf = io.BytesIO()
    combined.export(buf, format="wav")
    duration_seconds = len(combined) / 1000.0
    return buf.getvalue(), chapter_markers, duration_seconds


def convert_to_mp3(wav_bytes: bytes) -> bytes:
    """Convert WAV audio bytes to MP3 format. Falls back to WAV if ffmpeg is missing."""
    audio = PydubSegment.from_wav(io.BytesIO(wav_bytes))
    buf = io.BytesIO()
    try:
        audio.export(buf, format="mp3")
    except FileNotFoundError:
        # ffmpeg not installed — return WAV as-is (works for playback, just larger)
        import logging
        logging.getLogger(__name__).warning("ffmpeg not found, skipping MP3 conversion (serving WAV)")
        return wav_bytes
    return buf.getvalue()


async def upload_audio_to_blob(
    audio_bytes: bytes,
    task_id: str,
    fmt: str = "mp3",
) -> str:
    """Upload audio bytes to Azure Blob Storage and return a SAS URL.

    Uses AZURE_STORAGE_CONNECTION_STRING from environment. Creates the
    'podcasts' container if it does not already exist. Returns a time-limited
    SAS URL (24h) since anonymous blob access is disabled by policy.
    """
    import asyncio
    from datetime import datetime, timedelta, timezone
    from azure.storage.blob import (
        BlobServiceClient,
        ContentSettings,
        generate_blob_sas,
        BlobSasPermissions,
    )

    def _sync_upload() -> str:
        connection_string = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
        blob_service = BlobServiceClient.from_connection_string(connection_string)

        container_name = "podcasts"
        container_client = blob_service.get_container_client(container_name)

        # Create container if it doesn't exist (private access)
        try:
            container_client.get_container_properties()
        except Exception:
            container_client.create_container()

        blob_name = f"{task_id}.{fmt}"
        mime = "audio/mpeg" if fmt == "mp3" else f"audio/{fmt}"
        content_settings = ContentSettings(content_type=mime)

        blob_client = container_client.get_blob_client(blob_name)
        blob_client.upload_blob(
            audio_bytes,
            overwrite=True,
            content_settings=content_settings,
        )

        # Generate SAS token for 24h read access
        account_name = blob_service.account_name
        account_key = blob_service.credential.account_key
        sas_token = generate_blob_sas(
            account_name=account_name,
            container_name=container_name,
            blob_name=blob_name,
            account_key=account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(timezone.utc) + timedelta(hours=24),
        )

        return f"{blob_client.url}?{sas_token}"

    return await asyncio.to_thread(_sync_upload)
