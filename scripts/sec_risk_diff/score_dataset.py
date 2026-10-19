"""Add Claude's assessment to every change in dataset/changes.jsonl.

  export ANTHROPIC_API_KEY=...
  python score_dataset.py --max-usd 40            # stops before passing the budget
  python score_dataset.py --tickers AAPL --max-usd 2

Writes site/db/scores/<TICKER>.json ({"items": {change_id: assessment}}) and
appends spend to dataset/score_usage.jsonl. Companies already scored are
skipped, so the run can be stopped and resumed.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import sec_risk_diff as srd

HERE = Path(__file__).parent
SCORES = HERE / "site" / "db" / "scores"
USAGE = HERE / "dataset" / "score_usage.jsonl"


def spent_so_far() -> float:
    if not USAGE.exists():
        return 0.0
    return sum(json.loads(x)["cost_usd"] for x in USAGE.read_text().splitlines() if x)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--max-usd", type=float, required=True, help="hard budget across all runs")
    ap.add_argument("--model", default=srd.DEFAULT_MODEL)
    ap.add_argument("--lang", default="English")
    args = ap.parse_args()

    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for line in (HERE / "dataset" / "changes.jsonl").read_text().splitlines():
        if line:
            row = json.loads(line)
            by_ticker[row["ticker"]].append(row)

    SCORES.mkdir(parents=True, exist_ok=True)
    for ticker in args.tickers or list(by_ticker):
        out = SCORES / f"{ticker}.json"
        if out.exists():
            continue
        rows = by_ticker.get(ticker, [])
        if not rows:
            continue
        spent = spent_so_far()
        # Rough upper estimate for this company before spending: ~4 chars per token, output ~30% at 5x the input price.
        est = sum(len(r["old"]) + len(r["new"]) for r in rows) / 4 / 1e6 * 2.0 * 2.5
        if spent + est > args.max_usd:
            print(f"Stopping before {ticker}: spent ${spent:.2f}, next ~${est:.2f}, budget ${args.max_usd}")
            break
        changes = [srd.Change(r["change_id"], r["kind"], r["section"], r["old"], r["new"], r["similarity"])
                   for r in rows]
        usage = srd.assess_changes(changes, args.model, args.lang)
        items = {c.id: c.assessment for c in changes if c.assessment}
        out.write_text(json.dumps({"ticker": ticker, "model": args.model, "items": items}, ensure_ascii=False))
        with USAGE.open("a") as f:
            f.write(json.dumps({"ticker": ticker, **usage}) + "\n")
        print(f"{ticker}: {len(items)}/{len(changes)} scored, ${usage['cost_usd']} (total ${spent + usage['cost_usd']:.2f})"
              + (f", problems: {usage['problems'][:3]}" if usage["problems"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
