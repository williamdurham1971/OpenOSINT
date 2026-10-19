"""Validate analysis/*.json against the schema in BRIEF.md. Prints problems; exit 1 if any."""
import json, sys, pathlib

CATS = {"Çin ekonomisi", "Güneydoğu Asya", "Doğu Asya (Japonya/Kore)", "Küresel finans & bankacılık",
        "Şirket vakası", "Teknoloji & YZ", "Jeopolitik & ticaret", "Şirket kurma & vergi (Statrys hizmet videoları)"}
IDEA_KEYS = {"name", "insight", "problem", "solution", "target", "revenue_model", "first_step",
             "capital", "difficulty", "turkey_angle", "risks"}
problems = []
for p in sorted(pathlib.Path(sys.argv[1]).glob("*.json")):
    def bad(msg): problems.append(f"{p.stem}: {msg}")
    try:
        d = json.loads(p.read_text())
    except Exception as e:
        bad(f"invalid JSON ({e})"); continue
    if d.get("id") != p.stem: bad(f"id mismatch {d.get('id')!r}")
    if d.get("category") not in CATS: bad(f"category {d.get('category')!r}")
    for k in ("title_tr", "summary_tr"):
        if not d.get(k): bad(f"missing {k}")
    if not 3 <= len(d.get("key_points") or []) <= 7: bad("key_points count")
    ch = d.get("chart")
    if ch is not None:
        vals = [x.get("value") for x in ch.get("data", [])]
        if ch.get("type") not in ("bar", "hbar", "pie", "line"): bad(f"chart type {ch.get('type')!r}")
        if len(vals) < 3: bad("chart has <3 values")
        if not all(isinstance(v, (int, float)) for v in vals): bad("chart non-numeric value")
    dg = d.get("diagram") or {}
    if dg.get("type") not in ("flow", "cycle") or not 3 <= len(dg.get("steps") or []) <= 8: bad("diagram shape")
    ideas = d.get("business_ideas") or []
    if not 2 <= len(ideas) <= 3: bad(f"{len(ideas)} ideas")
    for i in ideas:
        miss = IDEA_KEYS - set(i)
        if miss: bad(f"idea {i.get('name')!r} missing {sorted(miss)}")
        if i.get("capital") not in ("Düşük", "Orta", "Yüksek"): bad(f"capital {i.get('capital')!r}")
        if not isinstance(i.get("difficulty"), int) or not 1 <= i["difficulty"] <= 5: bad(f"difficulty {i.get('difficulty')!r}")
        for k in ("originality", "problem_solving"):
            if not isinstance(i.get(k), int) or not 1 <= i[k] <= 5: bad(f"idea {i.get('name')!r}: {k} {i.get(k)!r}")
        if not (i.get("score_note") or "").strip(): bad(f"idea {i.get('name')!r}: missing score_note")
        for s in i.get("similar") or []:
            if not isinstance(s, dict) or not s.get("id") or not s.get("name"): bad(f"idea {i.get('name')!r}: bad similar entry {s!r}")
    for n in d.get("numbers") or []:
        if not isinstance(n.get("value"), (int, float)): bad(f"number non-numeric: {n.get('label')!r}={n.get('value')!r}")
print("\n".join(problems) or "all valid")
sys.exit(1 if problems else 0)
