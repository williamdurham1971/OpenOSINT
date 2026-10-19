"""Merge yt index.json + analysis/*.json into site/data.json."""
import json, sys, pathlib, datetime

index_path, analysis_dir, out_path = map(pathlib.Path, sys.argv[1:4])
index = [v for v in json.loads(index_path.read_text()) if v.get("date")]
videos, missing = [], []
for v in index:
    a = analysis_dir / f"{v['id']}.json"
    if not a.exists():
        missing.append(v["id"]); continue
    d = json.loads(a.read_text())
    d.update({k: v[k] for k in ("id", "date", "title", "duration", "views")})
    d["url"] = f"https://www.youtube.com/watch?v={v['id']}"
    videos.append(d)
videos.sort(key=lambda x: x["date"], reverse=True)
out = {"generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
       "channel": "https://www.youtube.com/@Statrys", "videos": videos}
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
print(f"{len(videos)} videos written, missing analysis: {missing}")
