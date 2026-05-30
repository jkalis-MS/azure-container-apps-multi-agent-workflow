"""Tests for TTS Server — runs WITHOUT a GPU by mocking the model."""

import io
import struct
import pytest
from unittest.mock import MagicMock, patch

from httpx import ASGITransport, AsyncClient


def _make_dummy_wav(duration_samples: int = 100) -> bytes:
    """Create a minimal valid WAV file in memory."""
    num_channels = 1
    sample_rate = 22050
    bits_per_sample = 16
    data_size = duration_samples * num_channels * (bits_per_sample // 8)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM
        num_channels,
        sample_rate,
        sample_rate * num_channels * (bits_per_sample // 8),
        num_channels * (bits_per_sample // 8),
        bits_per_sample,
        b"data",
        data_size,
    )
    return header + b"\x00" * data_size


# ---- Fixtures -------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_model_cache():
    """Reset model_loader caches between tests."""
    import model_loader

    model_loader._model = None
    model_loader._voices = {}
    yield
    model_loader._model = None
    model_loader._voices = {}


@pytest.fixture()
def _mock_otel():
    """Stub out OpenTelemetry exports so tests don't need a collector."""
    with patch("main.BatchSpanProcessor"), \
         patch("main.OTLPSpanExporter"):
        yield


# ---- Tests ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_endpoint(_mock_otel):
    """GET /health returns 200 with a status field."""
    from main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert data["model"] == "xtts-v2"
    assert "gpu_available" in data
    assert "voices" in data


@pytest.mark.asyncio
async def test_warmup_endpoint(_mock_otel):
    """POST /warmup returns 200 with voices_loaded list."""
    from main import app

    mock_model = MagicMock()
    with patch("model_loader.load_model", return_value=mock_model), \
         patch("model_loader.load_voice_samples", return_value={"alice": "/v/alice.wav"}):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/warmup")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert isinstance(data["voices_loaded"], list)


@pytest.mark.asyncio
async def test_synthesize_returns_wav(_mock_otel, tmp_path):
    """POST /synthesize with valid payload returns audio/wav content type."""
    from main import app
    import model_loader

    wav_bytes = _make_dummy_wav()

    mock_model = MagicMock()
    # When tts_to_file is called, write dummy WAV bytes to the output path
    def fake_tts_to_file(text, file_path, speaker_wav, language, speed):
        with open(file_path, "wb") as f:
            f.write(wav_bytes)

    mock_model.tts_to_file.side_effect = fake_tts_to_file
    model_loader._model = mock_model
    model_loader._voices = {"alice": str(tmp_path / "alice.wav")}

    with patch("model_loader.get_voice_path", return_value=str(tmp_path / "alice.wav")):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/synthesize",
                json={
                    "text": "Hello world",
                    "speaker_wav": "alice",
                    "language": "en",
                    "speed": 1.0,
                },
            )

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    assert resp.content[:4] == b"RIFF"


@pytest.mark.asyncio
async def test_synthesize_rejects_missing_text(_mock_otel):
    """POST /synthesize with empty text returns 400."""
    from main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/synthesize",
            json={
                "text": "",
                "speaker_wav": "alice",
                "language": "en",
            },
        )

    assert resp.status_code == 400
    assert "non-empty" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_synthesize_rejects_unknown_voice(_mock_otel):
    """POST /synthesize with nonexistent speaker_wav returns 400."""
    from main import app
    import model_loader

    mock_model = MagicMock()
    model_loader._model = mock_model
    model_loader._voices = {}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/synthesize",
            json={
                "text": "Hello",
                "speaker_wav": "nonexistent_voice",
                "language": "en",
            },
        )

    assert resp.status_code == 400
    assert "not found" in resp.json()["detail"].lower()
