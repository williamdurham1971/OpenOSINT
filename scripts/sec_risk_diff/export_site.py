"""Turn dataset/*.jsonl into the files the website reads.

  site/data/companies.json          summary of every processed company
  site/data/changes/<TICKER>.json   full change text per company
  site/db/companies/<TICKER>.json   one database document per company (live view)
  site/db/meta.json                 run progress document
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from build_dataset import DEFAULT_TICKERS

HERE = Path(__file__).parent
DS = HERE / "dataset"
SITE = HERE / "site"


def _jsonl(name: str) -> list[dict]:
    path = DS / name
    return [json.loads(x) for x in path.read_text().splitlines() if x] if path.exists() else []


def main() -> None:
    companies = {c["ticker"]: c for c in _jsonl("companies.jsonl")}
    pairs_by_t: dict[str, list[dict]] = defaultdict(list)
    for p in _jsonl("pairs.jsonl"):
        pairs_by_t[p["ticker"]].append(p)
    changes_by_pair: dict[str, list[dict]] = defaultdict(list)
    for c in _jsonl("changes.jsonl"):
        changes_by_pair[c["pair_id"]].append(c)

    (SITE / "data" / "changes").mkdir(parents=True, exist_ok=True)
    (SITE / "db" / "companies").mkdir(parents=True, exist_ok=True)

    summary = []
    for t, comp in companies.items():
        pairs = []
        for p in pairs_by_t.get(t, []):
            nf, of = p["new_filing"], p["old_filing"]
            pairs.append({
                "pair_id": p["pair_id"], "status": p["status"],
                "fy_new": nf["report_date"][:4], "fy_old": of["report_date"][:4],
                "filed_new": nf["filing_date"], "filed_old": of["filing_date"],
                "url_new": nf["url"], "url_old": of["url"],
                "stats": p.get("stats"), "paragraphs_new": p.get("new_paragraphs"),
                "paragraphs_old": p.get("old_paragraphs"), "words_new": p.get("new_words"),
                "words_old": p.get("old_words"), "changes": p.get("changes", 0),
            })
        pairs.sort(key=lambda x: x["fy_new"], reverse=True)
        doc = {"ticker": t, "name": comp.get("name", t), "cik": comp.get("cik"),
               "status": comp.get("status"), "errors": comp.get("errors", [])[:5],
               "pairs": pairs}
        summary.append(doc)
        (SITE / "db" / "companies" / f"{t}.json").write_text(json.dumps(doc, ensure_ascii=False))
        detail = {"ticker": t, "pairs": [
            {**{k: v for k, v in pr.items()}, "items": [
                {"id": c["change_id"], "kind": c["kind"], "section": c["section"],
                 "old": c["old"], "new": c["new"], "similarity": c["similarity"]}
                for c in changes_by_pair.get(pr["pair_id"], [])]}
            for pr in pairs]}
        (SITE / "data" / "changes" / f"{t}.json").write_text(json.dumps(detail, ensure_ascii=False))

    order = {t: i for i, t in enumerate(DEFAULT_TICKERS)}
    summary.sort(key=lambda d: order.get(d["ticker"], 999))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = {"planned": len(DEFAULT_TICKERS), "processed": len(summary),
            "pairs": sum(len(d["pairs"]) for d in summary),
            "changes": sum(len(v) for v in changes_by_pair.values()),
            "scored": 0, "updated": now}
    (SITE / "data" / "companies.json").write_text(
        json.dumps({"meta": meta, "companies": summary}, ensure_ascii=False))
    (SITE / "db" / "meta.json").write_text(json.dumps(meta))
    print(meta)


if __name__ == "__main__":
    main()
