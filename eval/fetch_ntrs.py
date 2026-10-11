"""
Fetch the real NTRS record (title + abstract) for every NASA report cited in data/AEGIS_SOURCES.md.
Writes data/real/ntrs_abstracts.json: {id: {cited_title, ntrs_title, abstract, found, title_match}}.
Used to (1) check that our citations exist and match their titles, (2) give the RAG index real text.
"""
import json, re, time, difflib
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent.parent
src = (ROOT / "data" / "AEGIS_SOURCES.md").read_text(encoding="utf-8")
out_path = ROOT / "data" / "real" / "ntrs_abstracts.json"
out = json.loads(out_path.read_text()) if out_path.exists() else {}

items = []
for line in src.splitlines():
    m = re.match(r"^\s*\d+\.\s*(.*)$", line)
    if not m:
        continue
    text = m.group(1)
    idm = re.search(r"\b((?:19|20)\d{9})\b", text)
    if idm:
        title = re.split(r"\s+-\s+", text, maxsplit=1)
        items.append((idm.group(1), title[1].strip() if len(title) > 1 else text))

print(f"{len(items)} NTRS ids in AEGIS_SOURCES.md")
client = httpx.Client(timeout=30, headers={"Accept": "application/json"})
for i, (nid, cited) in enumerate(items, 1):
    if nid in out and out[nid].get("found") is not None:
        continue
    rec = {"cited_title": cited, "found": False, "ntrs_title": None, "abstract": None, "title_match": 0.0}
    try:
        r = client.get(f"https://ntrs.nasa.gov/api/citations/{nid}")
        if r.status_code == 200:
            d = r.json()
            rec.update(found=True, ntrs_title=d.get("title"), abstract=(d.get("abstract") or "").strip(),
                       published=(d.get("publications") or [{}])[0].get("publicationDate") if d.get("publications") else None)
            rec["title_match"] = round(difflib.SequenceMatcher(None, cited.lower(), (d.get("title") or "").lower()).ratio(), 2)
        else:
            rec["http"] = r.status_code
    except Exception as e:
        rec["error"] = str(e)[:200]
    out[nid] = rec
    print(f"[{i}/{len(items)}] {nid} found={rec['found']} match={rec['title_match']} abstract={len(rec['abstract'] or '')}ch")
    time.sleep(0.3)
    if i % 10 == 0:
        out_path.write_text(json.dumps(out, indent=1))
out_path.write_text(json.dumps(out, indent=1))
found = [v for v in out.values() if v["found"]]
print(f"done: {len(found)}/{len(out)} found, {sum(1 for v in found if v['abstract'])} with abstracts")
