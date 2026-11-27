"""
On-device speech-to-text for the dashboard mic (offline Whisper).

The dashboard's voice used to be output-only: the agents speak (Polly or the
browser's voices) and the crew types. Voice input was removed because the
browser's speech recognition sends audio to the cloud — wrong for a crew
minutes from Earth. This service brings it back without the cloud: the
browser records the mic, the audio is posted to /api/transcribe, and a local
Whisper model (faster-whisper, the same family Handy uses) transcribes it on
this machine. Nothing leaves the machine.

Settings (environment):
  AEGIS_STT          on (default) | off — switch the mic off without removing the model
  AEGIS_STT_MODEL    Whisper model: tiny | base (default) | small — larger is more
                     accurate but slower and heavier. The first transcription
                     downloads the model (~75 MB tiny, ~150 MB base, ~500 MB small)
                     into the Hugging Face cache, so the first use is slow.

The faster-whisper import is lazy: if the package isn't installed the backend
still starts and /api/transcribe answers 503 with a clear message instead of
crashing the whole app at import time.

Version notes: faster-whisper 1.2.1 calls av.open(metadata_errors=...), which
av 15+ removed, and av 14.4 ships no cp312 wheel — so requirements.txt pins
av==13.1.0. If either pin is bumped, re-check that combo first.
"""
from __future__ import annotations

import os
import tempfile
import threading

MAX_AUDIO_BYTES = 10 * 1024 * 1024   # ~a minute of Opus/WebM is ~1 MB; 10 MB is generous
_MODELS = ("tiny", "base", "small", "medium", "large-v3")

try:
    from faster_whisper import WhisperModel
except Exception:  # pragma: no cover - faster-whisper not installed
    WhisperModel = None  # type: ignore

_model = None
_lock = threading.Lock()


def enabled() -> bool:
    return (os.getenv("AEGIS_STT") or "on").strip().lower() not in ("0", "false", "off", "no")


def model_name() -> str:
    name = (os.getenv("AEGIS_STT_MODEL") or "base").strip().lower()
    return name if name in _MODELS else "base"


def status() -> dict:
    """What the dashboard (and /api/stt/status) needs: on/off, model, loaded?"""
    if not enabled():
        return {"enabled": False, "model": model_name(), "loaded": False,
                "reason": "AEGIS_STT=off"}
    if WhisperModel is None:
        return {"enabled": True, "model": model_name(), "loaded": False,
                "reason": "faster-whisper is not installed (pip install -r requirements.txt)"}
    return {"enabled": True, "model": model_name(), "loaded": _model is not None}


def _load_model():
    """Load (and on first use, download) the Whisper model once, thread-safely."""
    global _model
    if _model is not None:
        return _model
    if WhisperModel is None:
        raise RuntimeError("faster-whisper is not installed (pip install -r requirements.txt)")
    with _lock:
        if _model is None:
            _model = WhisperModel(model_name(), device="cpu", compute_type="int8")
    return _model


def transcribe_bytes(data: bytes, filename: str = "audio.webm") -> dict:
    """Transcribe one recorded clip. Raises ValueError for bad input, RuntimeError if STT can't run."""
    if not enabled():
        raise RuntimeError("Speech-to-text is switched off (AEGIS_STT=off)")
    if not data:
        raise ValueError("No audio received")
    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError(f"Audio is too long — keep it under about a minute ({MAX_AUDIO_BYTES // 1024 // 1024} MB max)")
    suffix = os.path.splitext(filename or "")[1] or ".webm"
    if len(suffix) > 10 or not suffix.startswith("."):
        suffix = ".webm"
    model = _load_model()
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(data)
        tmp.close()
        try:
            segments, info = model.transcribe(tmp.name, language="en",
                                              beam_size=1, vad_filter=True)
            text = "".join(seg.text for seg in segments).strip()
        except Exception as e:
            raise ValueError(f"Could not understand that audio ({e})")
        return {"text": text, "language": info.language,
                "duration": round(info.duration, 1), "model": model_name()}
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
