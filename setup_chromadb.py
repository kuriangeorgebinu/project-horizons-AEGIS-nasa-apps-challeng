"""
AEGIS Knowledge Base - ChromaDB Setup and Indexing
NASA Space Apps Challenge 2026

This script indexes all NASA space health data into ChromaDB for RAG retrieval.
"""

import json
import os
import re
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions

# Configuration
DATA_DIR = Path(__file__).parent / "data"
CHROMA_DIR = Path(__file__).parent / "chroma_db"

# Use sentence-transformers for embeddings (runs locally, no API key needed)
EMBEDDING_MODEL = "all-MiniLM-L6-v2"


def setup_chroma():
    """Initialize ChromaDB with persistent storage."""
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    
    # Use sentence-transformers embedding function
    embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL
    )
    
    # Create collections for different document types
    collections = {
        "countermeasures": client.get_or_create_collection(
            name="countermeasures",
            embedding_function=embedding_fn,
            metadata={"description": "Space health countermeasures and interventions"}
        ),
        "risks": client.get_or_create_collection(
            name="risks",
            embedding_function=embedding_fn,
            metadata={"description": "NASA HRP spaceflight health risks"}
        ),
        "citations": client.get_or_create_collection(
            name="citations",
            embedding_function=embedding_fn,
            metadata={"description": "NASA research citations and sources"}
        ),
        "protocols": client.get_or_create_collection(
            name="protocols",
            embedding_function=embedding_fn,
            metadata={"description": "Medical protocols and procedures"}
        )
    }
    
    return client, collections


def load_knowledge_base(filepath: Path) -> dict:
    """Load the main AEGIS knowledge base JSON."""
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_hrr_risks(filepath: Path) -> dict:
    """Load NASA HRR risks JSON."""
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def parse_citations(filepath: Path) -> list[dict]:
    """Parse AEGIS_SOURCES.md into individual citations."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    citations = []
    # Split by citation entries (numbered lines starting with digits)
    lines = content.split('\n')
    current_citation = []
    current_category = "general"
    
    for line in lines:
        # Check for category headers
        if line.startswith('## '):
            current_category = line.replace('## ', '').strip().lower()
            continue
        
        # Check for numbered citations
        if re.match(r'^\d+\.', line.strip()):
            if current_citation:
                citations.append({
                    'text': ' '.join(current_citation),
                    'category': current_category
                })
            current_citation = [line.strip()]
        elif line.strip() and current_citation:
            current_citation.append(line.strip())
    
    # Don't forget the last citation
    if current_citation:
        citations.append({
            'text': ' '.join(current_citation),
            'category': current_category
        })
    
    return citations


def index_countermeasures(collection, knowledge_base: dict):
    """Index countermeasures from the knowledge base."""
    print("Indexing countermeasures...")
    
    documents = []
    metadatas = []
    ids = []
    
    # Index spaceflight risks (which contain countermeasures)
    for risk in knowledge_base.get('spaceflight_risks', []):
        risk_id = risk.get('id', 'UNKNOWN')
        
        # Create a rich document combining risk info and countermeasures
        doc_parts = [
            f"Risk: {risk.get('name', 'Unknown')}",
            f"Category: {risk.get('category', 'Unknown')}",
            f"Severity: {risk.get('severity', 'Unknown')}",
        ]
        
        if 'symptoms' in risk:
            doc_parts.append(f"Symptoms: {', '.join(risk['symptoms'])}")
        
        if 'effects' in risk:
            doc_parts.append(f"Effects: {', '.join(risk['effects'])}")
        
        if 'countermeasures' in risk:
            doc_parts.append(f"Countermeasures: {', '.join(risk['countermeasures'])}")
        
        if 'monitoring' in risk:
            doc_parts.append(f"Monitoring: {', '.join(risk['monitoring'])}")
        
        doc_text = ". ".join(doc_parts)
        
        documents.append(doc_text)
        metadatas.append({
            "risk_id": risk_id,
            "category": risk.get('category', 'unknown'),
            "severity": risk.get('severity', 'unknown'),
            "source": risk.get('source', 'AEGIS Knowledge Base'),
            "type": "risk_countermeasure"
        })
        ids.append(f"cm_{risk_id}")
    
    # Index countermeasure protocols
    for protocol in knowledge_base.get('countermeasure_protocols', []):
        protocol_id = protocol.get('id', 'UNKNOWN')
        
        doc_parts = [
            f"Protocol: {protocol.get('name', 'Unknown')}",
            f"Category: {protocol.get('category', 'Unknown')}",
        ]
        
        if 'description' in protocol:
            doc_parts.append(f"Description: {protocol['description']}")
        
        if 'indications' in protocol:
            doc_parts.append(f"Indications: {', '.join(protocol['indications'])}")
        
        if 'procedure' in protocol:
            doc_parts.append(f"Procedure: {protocol['procedure']}")
        
        doc_text = ". ".join(doc_parts)
        
        documents.append(doc_text)
        metadatas.append({
            "protocol_id": protocol_id,
            "category": protocol.get('category', 'unknown'),
            "source": "AEGIS Knowledge Base",
            "type": "protocol"
        })
        ids.append(f"proto_{protocol_id}")
    
    if documents:
        collection.add(documents=documents, metadatas=metadatas, ids=ids)
        print(f"  Indexed {len(documents)} countermeasure documents")
    
    return len(documents)


def index_hrr_risks(collection, hrr_data: dict):
    """Index NASA HRR official risks."""
    print("Indexing NASA HRR risks...")
    
    documents = []
    metadatas = []
    ids = []
    
    for risk in hrr_data.get('risks', []):
        risk_id = risk.get('id')
        
        doc_parts = [
            f"NASA Official Risk: {risk.get('name', 'Unknown')}",
            f"Category: {risk.get('category', 'Unknown')}",
            f"Status: {risk.get('status', 'Unknown')}",
        ]
        
        if 'effects' in risk:
            doc_parts.append(f"Effects: {', '.join(risk['effects'])}")
        
        doc_text = ". ".join(doc_parts)
        
        documents.append(doc_text)
        metadatas.append({
            "hrr_id": str(risk_id),
            "category": risk.get('category', 'unknown'),
            "status": risk.get('status', 'unknown'),
            "source": "NASA Human Research Roadmap",
            "url": f"https://humanresearchroadmap.nasa.gov/Risks/?i={risk_id}",
            "type": "hrr_risk"
        })
        ids.append(f"hrr_{risk_id}")
    
    if documents:
        collection.add(documents=documents, metadatas=metadatas, ids=ids)
        print(f"  Indexed {len(documents)} HRR risk documents")
    
    return len(documents)


def index_citations(collection, citations: list[dict]):
    """Index research citations."""
    print("Indexing citations...")
    
    documents = []
    metadatas = []
    ids = []
    
    # Real abstracts from the NASA Technical Reports Server (eval/fetch_ntrs.py), so a citation carries the
    # paper's actual text instead of only its title
    abstracts = {}
    abs_path = DATA_DIR / "real" / "ntrs_abstracts.json"
    if abs_path.exists():
        abstracts = json.loads(abs_path.read_text(encoding="utf-8"))

    for i, citation in enumerate(citations):
        text = citation['text']
        meta = {"category": citation['category'], "source": "AEGIS_SOURCES.md", "type": "citation"}
        idm = re.search(r"\b((?:19|20)\d{9})\b", text)
        rec = abstracts.get(idm.group(1)) if idm else None
        if rec and rec.get("abstract"):
            text = f"{text}. NTRS title: {rec.get('ntrs_title')}. Abstract: {rec['abstract'][:2000]}"
            meta["has_abstract"] = True
        documents.append(text)
        metadatas.append(meta)
        ids.append(f"cite_{i:04d}")
    
    if documents:
        collection.add(documents=documents, metadatas=metadatas, ids=ids)
        print(f"  Indexed {len(documents)} citation documents")
    
    return len(documents)


def index_medications(collection, knowledge_base: dict):
    """Index medication information."""
    print("Indexing medications...")
    
    documents = []
    metadatas = []
    ids = []
    
    for med in knowledge_base.get('medications', []):
        med_id = med.get('id', 'UNKNOWN')
        
        doc_parts = [
            f"Medication: {med.get('name', 'Unknown')}",
            f"Class: {med.get('class', 'Unknown')}",
        ]
        
        if 'indications' in med:
            doc_parts.append(f"Indications: {', '.join(med['indications'])}")
        
        if 'dosage' in med:
            doc_parts.append(f"Dosage: {med['dosage']}")
        
        if 'contraindications' in med:
            doc_parts.append(f"Contraindications: {', '.join(med['contraindications'])}")
        
        if 'side_effects' in med:
            doc_parts.append(f"Side effects: {', '.join(med['side_effects'])}")
        
        doc_text = ". ".join(doc_parts)
        
        documents.append(doc_text)
        metadatas.append({
            "medication_id": med_id,
            "class": med.get('class', 'unknown'),
            "source": "AEGIS Knowledge Base",
            "type": "medication"
        })
        ids.append(f"med_{med_id}")
    
    if documents:
        collection.add(documents=documents, metadatas=metadatas, ids=ids)
        print(f"  Indexed {len(documents)} medication documents")
    
    return len(documents)


def query_knowledge_base(collections: dict, query: str, n_results: int = 5) -> dict:
    """
    Query the knowledge base across all collections.
    
    Args:
        collections: Dict of ChromaDB collections
        query: Search query
        n_results: Number of results per collection
    
    Returns:
        Dict with results from each collection
    """
    results = {}
    
    for name, collection in collections.items():
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


def main():
    """Main indexing function."""
    print("=" * 60)
    print("AEGIS Knowledge Base - ChromaDB Indexing")
    print("=" * 60)
    
    # Setup ChromaDB
    print("\n1. Setting up ChromaDB...")
    client, collections = setup_chroma()
    print(f"   Database location: {CHROMA_DIR}")
    
    # Load data files
    print("\n2. Loading data files...")
    
    kb_path = DATA_DIR / "aegis_knowledge_base.json"
    hrr_path = DATA_DIR / "nasa_hrr_risks.json"
    citations_path = DATA_DIR / "AEGIS_SOURCES.md"
    
    knowledge_base = load_knowledge_base(kb_path)
    print(f"   Loaded: {kb_path.name}")
    
    hrr_risks = load_hrr_risks(hrr_path)
    print(f"   Loaded: {hrr_path.name}")
    
    citations = parse_citations(citations_path)
    print(f"   Loaded: {citations_path.name} ({len(citations)} citations)")
    
    # Index all data
    print("\n3. Indexing documents...")
    total_docs = 0
    
    total_docs += index_countermeasures(collections['countermeasures'], knowledge_base)
    total_docs += index_hrr_risks(collections['risks'], hrr_risks)
    total_docs += index_citations(collections['citations'], citations)
    total_docs += index_medications(collections['protocols'], knowledge_base)
    
    print(f"\n4. Indexing complete!")
    print(f"   Total documents indexed: {total_docs}")
    
    # Test query
    print("\n5. Testing query...")
    test_query = "bone loss countermeasures exercise"
    results = query_knowledge_base(collections, test_query, n_results=2)
    
    print(f"   Query: '{test_query}'")
    for collection_name, result in results.items():
        if result['documents']:
            print(f"\n   [{collection_name}]")
            for i, doc in enumerate(result['documents'][:2]):
                print(f"   {i+1}. {doc[:100]}...")
    
    print("\n" + "=" * 60)
    print("Setup complete! ChromaDB is ready for AEGIS RAG queries.")
    print("=" * 60)
    
    return client, collections


if __name__ == "__main__":
    main()
