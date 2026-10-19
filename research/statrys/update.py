"""Find new @Statrys uploads, download their English captions and refresh view counts.

Run from research/statrys/. Writes yt/raw + yt/txt (gitignored, transcripts are not committed)
and updates meta/index.json. Prints a JSON summary; "pending_analysis" lists the video IDs
that still need an analysis/<id>.json written per BRIEF.md.
"""
import datetime, json, pathlib, re, subprocess, sys

CHANNEL = "https://www.youtube.com/@Statrys/videos"
ROOT = pathlib.Path(__file__).resolve().parent
META = ROOT / "meta" / "index.json"
RAW, TXT = ROOT / "yt" / "raw", ROOT / "yt" / "txt"
RETRY_DAYS = 3  # captions can appear a while after upload; retry before giving up


def ytdlp(*args):
    return subprocess.run(["yt-dlp", *args], capture_output=True, text=True).stdout


def vtt_to_text(path):
    seen, lines = set(), []
    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith(("WEBVTT", "Kind:", "Language:")) or "-->" in line or line.isdigit():
            continue
        c = re.sub(r"<[^>]*>", "", line).replace("&amp;", "&").replace("&gt;", ">").replace("&lt;", "<").replace("&nbsp;", " ").strip()
        if c and c not in seen:
            seen.add(c)
            lines.append(c)
    return " ".join(lines)


def fetch(vid):
    RAW.mkdir(parents=True, exist_ok=True)
    TXT.mkdir(parents=True, exist_ok=True)
    ytdlp("--write-auto-sub", "--write-sub", "--sub-langs", "en.*,en", "--sub-format", "vtt",
          "--write-info-json", "--skip-download", "-o", str(RAW / "%(upload_date)s_%(id)s"),
          f"https://www.youtube.com/watch?v={vid}")
    infos = list(RAW.glob(f"*_{vid}.info.json"))
    if not infos:
        return None
    meta = json.loads(infos[0].read_text())
    stem = infos[0].name[:-len(".info.json")]
    # prefer the original-language auto track, then plain "en"
    vtts = sorted(RAW.glob(stem + ".*.vtt"), key=lambda p: (".en-orig" not in p.name, ".en." not in p.name))
    text = vtt_to_text(vtts[0]) if vtts else ""
    if text:
        (TXT / f"{stem}.txt").write_text(text)
    return {"id": vid, "date": meta.get("upload_date"), "title": meta.get("title"),
            "duration": meta.get("duration"), "views": meta.get("view_count"),
            "words": len(text.split()), "sub_file": vtts[0].name if vtts else None}


def main():
    index = json.loads(META.read_text())
    known = {v["id"]: v for v in index}
    listing = [l.split("|") for l in ytdlp("--flat-playlist", "--playlist-end", "40", "--print",
                                           "%(id)s|%(view_count)s", CHANNEL).split() if "|" in l]
    if not listing:
        sys.exit("Kanal listesi alınamadı (ağ/yt-dlp sorunu).")
    new, waiting = [], []
    for vid, views in listing:  # newest first; everything above the first known ID is new
        if vid in known:
            break
        entry = fetch(vid)
        if not entry or not entry["date"]:
            waiting.append(vid)
            continue
        age = (datetime.date.today() - datetime.datetime.strptime(entry["date"], "%Y%m%d").date()).days
        if not entry["words"] and age < RETRY_DAYS:
            waiting.append(vid)  # captions not generated yet; try again on the next run
            continue
        index.append(entry)
        new.append(vid)
    for vid, views in listing:
        if vid in known and views.isdigit():
            known[vid]["views"] = max(int(views), known[vid].get("views") or 0)
    index.sort(key=lambda v: v["date"])
    META.write_text(json.dumps(index, ensure_ascii=False, indent=1))
    pending = [v["id"] for v in index if v["words"] and not (ROOT / "analysis" / f"{v['id']}.json").exists()]
    no_captions = [v["id"] for v in index if not v["words"]]
    print(json.dumps({"new": new, "waiting_for_captions": waiting, "pending_analysis": pending,
                      "no_captions": no_captions}, ensure_ascii=False))


if __name__ == "__main__":
    main()
