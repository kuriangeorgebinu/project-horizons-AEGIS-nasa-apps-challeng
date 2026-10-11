"""
AEGIS RAG Query Module
NASA Space Apps Challenge 2026

Simple interface for querying the ChromaDB knowledge base.
"""

import re
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.utils import embedding_functions

# Configuration
CHROMA_DIR = Path(__file__).parent / "chroma_db"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"


class AEGISKnowledgeBase:
    """Interface to the AEGIS ChromaDB knowledge base."""
    
    def __init__(self, chroma_dir: Optional[Path] = None):
        self.chroma_dir = chroma_dir or CHROMA_DIR
        self._client = None
        self._collections = None
        self._embedding_fn = None
    
    def _setup(self):
        """Lazy initialization of ChromaDB client."""
        if self._client is None:
            self._client = chromadb.PersistentClient(path=str(self.chroma_dir))
            self._embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=EMBEDDING_MODEL
            )
            self._collections = {
                "countermeasures": self._client.get_collection(
                    name="countermeasures",
                    embedding_function=self._embedding_fn
                ),
                "risks": self._client.get_collection(
                    name="risks", 
                    embedding_function=self._embedding_fn
                ),
                "citations": self._client.get_collection(
                    name="citations",
                    embedding_function=self._embedding_fn
                ),
                "protocols": self._client.get_collection(
                    name="protocols",
                    embedding_function=self._embedding_fn
                )
            }
    
    def query(
        self, 
        query: str, 
        collections: Optional[list[str]] = None,
        n_results: int = 5
    ) -> dict:
        """
        Query the knowledge base.
        
        Args:
            query: Natural language search query
            collections: List of collections to search (default: all)
            n_results: Number of results per collection
            
        Returns:
            Dict with results from each collection
        """
        self._setup()
        
        collections_to_search = collections or list(self._collections.keys())
        results = {}
        
        for name in collections_to_search:
            if name not in self._collections:
                continue
            
            collection = self._collections[name]
            try:
                result = collection.query(
                    query_texts=[query],
                    n_results=n_results,
                    include=["documents", "metadatas", "distances"]
                )
                results[name] = {
                    "documents": result['documents'][0] if result['documents'] else [],
                    "metadatas": result['metadatas'][0] if result['metadatas'] else [],
                    "distances": result['distances'][0] if result['distances'] else []
                }
            except Exception as e:
                print(f"Error querying {name}: {e}")
                results[name] = {"documents": [], "metadatas": [], "distances": []}
        
        return results
    
    def query_countermeasures(self, condition: str, n_results: int = 5) -> list[dict]:
        """
        Query specifically for countermeasures related to a condition.
        
        Args:
            condition: Health condition or symptom
            n_results: Number of results
            
        Returns:
            List of countermeasure documents with metadata
        """
        results = self.query(
            f"countermeasures treatment for {condition}",
            collections=["countermeasures", "protocols"],
            n_results=n_results
        )
        
        combined = []
        for collection_name, data in results.items():
            for i, doc in enumerate(data["documents"]):
                combined.append({
                    "text": doc,
                    "metadata": data["metadatas"][i] if i < len(data["metadatas"]) else {},
                    "distance": data["distances"][i] if i < len(data["distances"]) else 1.0,
                    "source_collection": collection_name
                })
        
        # Sort by distance (lower = more relevant)
        combined.sort(key=lambda x: x["distance"])
        return combined[:n_results]
    
    def retrieve_evidence(self, query: str, n_results: int = 4, per_collection: int = 2) -> list[dict]:
        """
        RAG grounding for recommendations: semantic search over the countermeasures, NASA HRR risks,
        citations (and protocols, when populated) collections. Each hit carries a readable source label
        and, where one exists, a link. Best matches first, at most `per_collection` from each collection
        so the evidence isn't all from one place.
        """
        self._setup()
        hits = []
        for name in ("countermeasures", "risks", "citations", "protocols"):
            col = self._collections.get(name)
            try:
                count = col.count() if col is not None else 0
                if count == 0:
                    continue
                r = col.query(query_texts=[query], n_results=min(per_collection, count),
                              include=["documents", "metadatas", "distances"])
            except Exception as e:
                print(f"retrieve_evidence: error querying {name}: {e}")
                continue
            for doc, meta, dist in zip(r["documents"][0], r["metadatas"][0], r["distances"][0]):
                label, url = _source_label(name, doc, meta or {})
                hits.append({"text": doc, "source": label, "url": url,
                             "collection": name, "distance": round(float(dist), 3)})
        hits.sort(key=lambda h: h["distance"])
        # The same report can be indexed twice (e.g. a citation and a countermeasure note with one NTRS id):
        # keep one hit per link, preferring the content-rich countermeasure entry
        seen, deduped = {}, []
        for h in hits:
            key = h["url"] or h["source"]
            if key in seen:
                if h["collection"] == "countermeasures" and deduped[seen[key]]["collection"] != "countermeasures":
                    deduped[seen[key]] = h
                continue
            seen[key] = len(deduped)
            deduped.append(h)
        hits = deduped
        top = hits[:n_results]
        # Citations are titles only; make sure the best content-rich countermeasure document is included
        if top and not any(h["collection"] == "countermeasures" for h in top):
            best_cm = next((h for h in hits if h["collection"] == "countermeasures"), None)
            if best_cm:
                top[-1] = best_cm
                top.sort(key=lambda h: h["distance"])
        return top

    def query_risks(self, query: str, n_results: int = 5) -> list[dict]:
        """
        Query for spaceflight health risks.
        
        Args:
            query: Risk-related query
            n_results: Number of results
            
        Returns:
            List of risk documents with metadata
        """
        results = self.query(
            query,
            collections=["risks", "countermeasures"],
            n_results=n_results
        )
        
        combined = []
        for collection_name, data in results.items():
            for i, doc in enumerate(data["documents"]):
                combined.append({
                    "text": doc,
                    "metadata": data["metadatas"][i] if i < len(data["metadatas"]) else {},
                    "distance": data["distances"][i] if i < len(data["distances"]) else 1.0,
                    "source_collection": collection_name
                })
        
        combined.sort(key=lambda x: x["distance"])
        return combined[:n_results]
    
    def get_context_for_llm(
        self, 
        query: str, 
        max_tokens: int = 2000,
        n_results: int = 5
    ) -> str:
        """
        Get formatted context string for LLM augmentation.
        
        Args:
            query: User's question
            max_tokens: Approximate token limit (chars / 4)
            n_results: Results per collection
            
        Returns:
            Formatted context string for LLM prompt
        """
        results = self.query(query, n_results=n_results)
        
        context_parts = ["## Relevant Knowledge Base Context\n"]
        char_count = 0
        max_chars = max_tokens * 4  # Rough char to token ratio
        
        for collection_name, data in results.items():
            if not data["documents"]:
                continue
            
            section = f"\n### {collection_name.title()}\n"
            for i, doc in enumerate(data["documents"], 1):
                entry = f"{i}. {doc}\n"
                if char_count + len(section) + len(entry) > max_chars:
                    break
                section += entry
                char_count += len(entry)
            
            context_parts.append(section)
            
            if char_count > max_chars:
                break
        
        return "".join(context_parts)
    
    def get_stats(self) -> dict:
        """Get statistics about the knowledge base."""
        self._setup()
        
        stats = {}
        for name, collection in self._collections.items():
            stats[name] = collection.count()
        
        stats["total"] = sum(stats.values())
        return stats


_NTRS_ID = re.compile(r"\b(?:19|20)\d{9}\b")


def _source_label(collection: str, doc: str, meta: dict) -> tuple[str, Optional[str]]:
    """Human-readable source label + link for a knowledge-base hit."""
    url = meta.get("url")
    source = str(meta.get("source", ""))
    if collection == "risks":
        m = re.match(r"NASA Official Risk:\s*([^.]+)", doc)
        label = "NASA HRR — " + (m.group(1).strip() if m else "Human Research Roadmap risk")
    elif collection == "citations":
        label = re.sub(r"^\d+\.\s*", "", doc.split(". NTRS title:")[0]).strip()[:140]
    else:
        m = re.match(r"(?:Risk|Protocol|Medication):\s*([^.]+)", doc)
        label = f"{source or 'AEGIS Knowledge Base'} — {m.group(1).strip() if m else collection}"
    if not url:
        mu = re.search(r"https?://[^\s)\]]+", doc)
        if mu:
            url = mu.group(0).rstrip(".,;")
    if not url:
        # NASA technical reports: the 11-digit NTRS id maps to ntrs.nasa.gov/citations/<id>
        haystack = f"{doc} {source}"
        m = _NTRS_ID.search(haystack)
        if m and re.search(r"NTRS|NASA/(?:TM|CR|TP|SP)", haystack):
            url = f"https://ntrs.nasa.gov/citations/{m.group(0)}"
    if not url:
        haystack = f"{doc} {source}"
        pmc = re.search(r"\bPMC\d{5,9}\b", haystack)
        doi = re.search(r"\b10\.\d{4,9}/[^\s\"<>)]+", haystack)
        if pmc:
            url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc.group(0)}/"
        elif doi:
            url = f"https://doi.org/{doi.group(0).rstrip('.,;')}"
    return label, url


# Singleton instance for easy access
_kb_instance = None

def get_knowledge_base() -> AEGISKnowledgeBase:
    """Get the shared knowledge base instance."""
    global _kb_instance
    if _kb_instance is None:
        _kb_instance = AEGISKnowledgeBase()
    return _kb_instance


# Simple query function
def query(query_text: str, n_results: int = 5) -> dict:
    """Quick query function."""
    return get_knowledge_base().query(query_text, n_results=n_results)


if __name__ == "__main__":
    # Demo usage
    kb = get_knowledge_base()
    
    print("AEGIS Knowledge Base Stats:")
    print(kb.get_stats())
    
    print("\n--- Query: 'radiation exposure protection' ---")
    results = kb.query_countermeasures("radiation exposure protection", n_results=3)
    for r in results:
        print(f"\n[{r['source_collection']}] (distance: {r['distance']:.3f})")
        print(f"  {r['text'][:200]}...")
    
    print("\n--- LLM Context for: 'astronaut has elevated heart rate' ---")
    context = kb.get_context_for_llm("astronaut has elevated heart rate", max_tokens=1000)
    print(context[:1000])
