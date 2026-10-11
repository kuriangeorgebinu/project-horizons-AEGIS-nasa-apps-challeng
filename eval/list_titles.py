import json
from pathlib import Path
d = json.loads((Path(__file__).resolve().parent.parent / "data" / "real" / "ntrs_abstracts.json").read_text())
for k, v in sorted(d.items(), key=lambda kv: kv[1]["title_match"]):
    print(f"{v['title_match']:.2f} {k} | CITED: {v['cited_title'][:70]} | NTRS: {(v['ntrs_title'] or '')[:95]}")
