"""Singleton model loader for XTTS-v2 text-to-speech."""

import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_model = None
_voices: dict[str, str] = {}
_lock = threading.Lock()

MODEL_PATH = os.getenv("MODEL_PATH", "/app/models/xtts-v2")
VOICE_SAMPLES_DIR = os.getenv("VOICE_SAMPLES_DIR", "./voice_samples")


def _log_gpu_info() -> None:
    """Log GPU memory info if CUDA is available."""
    try:
        import torch

        if torch.cuda.is_available():
            device = torch.cuda.current_device()
            name = torch.cuda.get_device_name(device)
            mem_allocated = torch.cuda.memory_allocated(device) / (1024**3)
            mem_total = torch.cuda.get_device_properties(device).total_memory / (1024**3)
            logger.info(
                "GPU: %s | Memory: %.2f / %.2f GB", name, mem_allocated, mem_total
            )
        else:
            logger.warning("CUDA not available — running on CPU")
    except ImportError:
        logger.warning("torch not installed — cannot report GPU info")


def load_model():
    """Load XTTS-v2 from local path. Returns the TTS model object."""
    global _model

    from TTS.api import TTS

    logger.info("Loading XTTS-v2 model from %s …", MODEL_PATH)
    start = time.time()

    _log_gpu_info()

    try:
        import torch
        use_gpu = torch.cuda.is_available()
    except ImportError:
        use_gpu = False

    if not use_gpu:
        logger.warning("CUDA not available — loading model on CPU (inference will be slow)")

    if os.path.isdir(MODEL_PATH):
        _model = TTS(model_path=MODEL_PATH, config_path=os.path.join(MODEL_PATH, "config.json"), gpu=use_gpu)
    else:
        logger.info("Local model path not found, falling back to hub model name")
        _model = TTS(model_name="tts_models/multilingual/multi-dataset/xtts_v2", gpu=use_gpu)

    elapsed = time.time() - start
    logger.info("Model loaded in %.1f s", elapsed)
    _log_gpu_info()

    return _model


def get_model():
    """Return the cached model, loading on first call. Thread-safe."""
    global _model
    with _lock:
        if _model is None:
            load_model()
    return _model


def load_voice_samples(voice_dir: str | None = None) -> dict[str, str]:
    """Load all .wav files from the voice samples directory.

    Returns a dict mapping voice name (stem) to file path.
    """
    global _voices
    directory = Path(voice_dir or VOICE_SAMPLES_DIR)

    if not directory.is_dir():
        logger.warning("Voice samples directory not found: %s", directory)
        return _voices

    with _lock:
        for wav_file in sorted(directory.glob("*.wav")):
            _voices[wav_file.stem] = str(wav_file.resolve())
            logger.info("Loaded voice sample: %s → %s", wav_file.stem, wav_file)

    logger.info("Total voice samples loaded: %d", len(_voices))
    return _voices


def get_voice_path(name: str) -> str:
    """Return the path to a voice sample by name.

    Raises ValueError if the voice is not found.
    """
    if name in _voices:
        return _voices[name]

    # Also accept a raw file path that exists on disk
    if os.path.isfile(name):
        return name

    available = ", ".join(sorted(_voices.keys())) or "(none)"
    raise ValueError(f"Voice '{name}' not found. Available voices: {available}")


def get_loaded_voices() -> list[str]:
    """Return a sorted list of loaded voice names."""
    return sorted(_voices.keys())
