"""
AEGIS neural voices (Amazon Polly).

Each agent speaks with its own Polly voice. The generative engine sounds the most human; if a voice or
region does not offer it, the neural engine is used instead. Audio is cached in memory and on disk, so a
sentence is only paid for once, and a character budget caps what a public visitor can spend.

Settings (environment):
  AEGIS_TTS          off (default) | polly
  AEGIS_TTS_REGION   AWS region for Polly (default: AWS_REGION or us-east-1)
  AEGIS_TTS_CACHE    folder for cached mp3s (default: ./tts_cache next to this file)
  AEGIS_TTS_DAILY_CHARS  new characters synthesised per day before falling back to browser voices (default 200000)
Credentials come from the normal AWS chain (env keys, profile or instance role).
If Polly is unavailable the endpoint answers 503 and the dashboard keeps using the browser's voices.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path

# agent -> (voice, language). Picked for character: a steady lead, a calm physicist, a gentle
# British clinician, an upbeat coach, a warm Irish counsellor and a soft, friendly companion.
VOICES = {
    "aegis":   "Ruth",
    "solaris": "Stephen",
    "gene":    "Amy",
    "pulse":   "Matthew",
    "mirror":  "Niamh",
    "echo":    "Danielle",
}
MAX_CHARS = 600                      # one request = one sentence or short reply

_lock = threading.Lock()
_mem: "OrderedDict[str, bytes]" = OrderedDict()
_MEM_MAX = 400
_client = None
_engine_for: dict[str, str] = {}     # voice -> engine that worked
_disabled_reason: str | None = None
_budget = {"day": "", "used": 0}


def _mode() -> str:
    return (os.getenv("AEGIS_TTS") or "off").strip().lower()   # opt-in: set AEGIS_TTS=polly to use it


def _cache_dir() -> Path:
    d = Path(os.getenv("AEGIS_TTS_CACHE") or (Path(__file__).parent / "tts_cache"))
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def _get_client():
    global _client, _disabled_reason
    if _client is not None:
        return _client
    try:
        import boto3  # noqa: WPS433 (optional dependency)
        region = os.getenv("AEGIS_TTS_REGION") or os.getenv("AWS_REGION") or "us-east-1"
        session = boto3.Session()
        if session.get_credentials() is None:
            _disabled_reason = "no AWS credentials for Polly"
            return None
        _client = session.client("polly", region_name=region)
        return _client
    except Exception as e:  # boto3 missing or no credentials configured
        _disabled_reason = f"Polly client unavailable: {type(e).__name__}"
        return None


def status() -> dict:
    enabled = _mode() == "polly" and _disabled_reason is None and _get_client() is not None
    return {"enabled": enabled, "provider": "Amazon Polly" if enabled else None,
            "voices": VOICES if enabled else {}, "reason": None if enabled else (_disabled_reason or "off")}


def _over_budget(n: int) -> bool:
    day = time.strftime("%Y-%m-%d")
    limit = int(os.getenv("AEGIS_TTS_DAILY_CHARS") or 200000)
    with _lock:
        if _budget["day"] != day:
            _budget.update(day=day, used=0)
        if _budget["used"] + n > limit:
            return True
        _budget["used"] += n
        return False


def synthesize(text: str, agent: str = "aegis") -> bytes:
    """Return mp3 bytes for `text` in `agent`'s voice. Raises RuntimeError when Polly can't be used."""
    global _disabled_reason
    if _mode() != "polly":
        raise RuntimeError("voice service off")
    text = " ".join(str(text or "").split())[:MAX_CHARS]
    if not text:
        raise ValueError("empty text")
    voice = VOICES.get((agent or "aegis").lower(), VOICES["aegis"])
    key = hashlib.sha256(f"{voice}|{text}".encode("utf-8")).hexdigest()

    with _lock:
        if key in _mem:
            _mem.move_to_end(key)
            return _mem[key]
    path = _cache_dir() / f"{key}.mp3"
    if path.exists():
        data = path.read_bytes()
        _remember(key, data)
        return data

    client = _get_client()
    if client is None:
        raise RuntimeError(_disabled_reason or "voice service unavailable")
    if _over_budget(len(text)):
        raise RuntimeError("daily voice budget used")

    engines = [_engine_for[voice]] if voice in _engine_for else ["generative", "neural"]
    last = None
    for engine in engines:
        try:
            r = client.synthesize_speech(Text=text, VoiceId=voice, Engine=engine, OutputFormat="mp3",
                                         SampleRate="24000")
            data = r["AudioStream"].read()
            _engine_for[voice] = engine
            break
        except Exception as e:  # unsupported engine for this voice/region, throttling, credentials…
            last = e
            name = type(e).__name__
            if name in ("NoCredentialsError", "PartialCredentialsError") or "AccessDenied" in str(e) \
                    or "UnrecognizedClient" in str(e) or "InvalidClientTokenId" in str(e):
                _disabled_reason = f"Polly credentials rejected: {name}"
                raise RuntimeError(_disabled_reason) from e
    else:
        raise RuntimeError(f"Polly failed: {type(last).__name__}") from last

    try:
        path.write_bytes(data)
    except OSError:
        pass
    _remember(key, data)
    return data


def _remember(key: str, data: bytes) -> None:
    with _lock:
        _mem[key] = data
        _mem.move_to_end(key)
        while len(_mem) > _MEM_MAX:
            _mem.popitem(last=False)
