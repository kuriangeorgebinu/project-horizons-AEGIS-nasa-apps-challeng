"""Single source of truth for repo-root paths (new in the refactor).

Before the refactor these were scattered as ``Path(__file__).parent / ...``
in database.py, aegis_rag.py, aegis_tts.py and setup_chromadb.py, so moving
any of those files silently changed where the DB, the Chroma index and the
TTS cache landed. Every path here is anchored at the repo root, so a module
may move anywhere inside ``app/`` without changing where data lives.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CHROMA_DIR = Path(os.getenv("AEGIS_CHROMA_DIR") or (ROOT / "chroma_db"))
DB_PATH = Path(os.getenv("AEGIS_DB_PATH") or (ROOT / "aegis.db"))
TTS_CACHE_DIR = Path(os.getenv("AEGIS_TTS_CACHE") or (ROOT / "tts_cache"))
