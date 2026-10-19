"""Heuristic: does each chart/number value appear in the transcript text? Flags possible fabrication."""
import json, pathlib, re, sys
txt = {p.name.split("_", 1)[1][:-4]: p.read_text().lower() for p in pathlib.Path("yt/txt").glob("*.txt")}
def forms(v):
    out = set()
    if float(v).is_integer():
        i = int(v); out |= {str(i), f"{i:,}"}
        if i >= 1000:  # spoken shortenings: 1600 -> "1.6 thousand"? keep leading digits
            s = str(i).rstrip("0"); out.add(s[:3])
    else:
        out |= {f"{v:g}", f"{v:.1f}", f"{v:.2f}".rstrip("0")}
        out.add(f"{v:g}".lstrip("0"))
    return {f for f in out if f}
only = set(sys.argv[1:])  # optional: limit to these video IDs
tot = miss = 0; report = []
for p in sorted(pathlib.Path("analysis").glob("*.json")):
    d = json.loads(p.read_text())
    if (only and d["id"] not in only) or d["id"] not in txt:
        continue  # transcripts are not committed; only freshly downloaded ones can be checked
    t = txt[d["id"]]
    vals = [(n["label"], n["value"]) for n in d.get("numbers") or []]
    vals += [(x["label"], x["value"]) for x in (d.get("chart") or {}).get("data", [])]
    for lab, v in vals:
        tot += 1
        if not any(re.search(r"(?<![\d.])" + re.escape(f) + r"(?![\d])", t) for f in forms(v)):
            miss += 1; report.append(f"{d['id']}: {lab} = {v}")
print(f"{tot} values checked, {miss} not found verbatim ({miss/max(tot,1):.0%})")
print("\n".join(report[:200]))
