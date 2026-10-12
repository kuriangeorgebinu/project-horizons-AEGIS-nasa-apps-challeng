"""List overclaims in the design documents (for the Step 2 honesty pass)."""
import pathlib, re, html
root = pathlib.Path(__file__).resolve().parent.parent
files = ["artifacts/aegis-solution-brief.html", "AEGIS-Technical-Architecture.html", "research_report.html",
         "AEGIS-Development-Plan.html", "AEGIS-Test-Plan.html", "improvements_addressed.html", "README.md"]
pat = re.compile(r"days to weeks|days-to-weeks|weeks in advance|RadLab|EWMA|z-score|\bHRV\b|DSCOVR|TP53|trained on|"
                 r"Five agents|five agents|30-second|6 live APIs|six live|\bCRP\b|\bPVT\b|four independent|"
                 r"0\.5 mSv|1\.0 mSv|>\s*0\.5|>\s*1\.0|10\.5|Best Use of Science|10-second|machine learning|"
                 r"real-time biomarker|genomic|72 hours|48 hours", re.I)
for f in files:
    p = root / f
    if not p.exists():
        print("MISSING", f); continue
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    hits = [(i + 1, l) for i, l in enumerate(lines) if pat.search(l)]
    print(f"\n=== {f}: {len(hits)} lines")
    for i, l in hits:
        t = html.unescape(re.sub(r"<[^>]+>", " ", l))
        t = " ".join(t.split())
        for m in pat.finditer(t):
            s = max(0, m.start() - 70)
            print(f"  {i}: ...{t[s:m.end() + 70]}...")
