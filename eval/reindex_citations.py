"""Rebuild only the 'citations' collection so it includes the real NTRS abstracts (stop the backend first)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import setup_chromadb as s

client, cols = s.setup_chroma()
before = cols["citations"].count()
client.delete_collection("citations")
client, cols = s.setup_chroma()
n = s.index_citations(cols["citations"], s.parse_citations(s.DATA_DIR / "AEGIS_SOURCES.md"))
with_abs = cols["citations"].get(where={"has_abstract": True})
print(f"citations: {before} -> {n}; with real abstracts: {len(with_abs['ids'])}")
