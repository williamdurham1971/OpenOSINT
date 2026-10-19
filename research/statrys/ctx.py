import sys, re, pathlib
vid, pat = sys.argv[1], sys.argv[2]
t = next(pathlib.Path("yt/txt").glob(f"*_{vid}.txt")).read_text()
for m in list(re.finditer(pat, t, re.I))[:3]:
    print(f"[{vid}] …{t[max(0,m.start()-110):m.end()+90]}…")
