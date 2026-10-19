"""Build the Item 1A change dataset for a list of companies.

For each ticker: fetch the last N+1 10-K filings, extract Item 1A from each,
and diff every consecutive pair. Results are appended as each company
finishes, so a long run can be watched (and resumed) while it works.

  export SEC_USER_AGENT="Name email@example.com"
  python build_dataset.py --years 3                 # default ticker list
  python build_dataset.py --tickers AAPL MSFT --years 2

Writes to dataset/:
  sections/<TICKER>_<filing date>.json   extracted Item 1A paragraphs
  companies.jsonl                        one line per company (status, counts)
  pairs.jsonl                            one line per year-over-year comparison
  changes.jsonl                          one line per changed paragraph

No model calls happen here; score_dataset.py adds the Claude assessment later.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

import sec_risk_diff as srd

# Large US companies across sectors (a convenience sample, not an index).
DEFAULT_TICKERS = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA", "JPM", "BAC", "WFC",
    "GS", "MS", "C", "V", "MA", "PYPL", "JNJ", "PFE", "MRK", "ABBV",
    "LLY", "UNH", "CVS", "XOM", "CVX", "COP", "KO", "PEP", "PG", "WMT",
    "COST", "HD", "MCD", "NKE", "DIS", "NFLX", "CMCSA", "T", "VZ", "INTC",
    "AMD", "CSCO", "ORCL", "IBM", "BA", "CAT", "GE", "F", "GM", "UPS",
]

OUT = Path(__file__).with_name("dataset")


def _append(name: str, row: dict) -> None:
    with (OUT / name).open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _done_tickers() -> set[str]:
    path = OUT / "companies.jsonl"
    if not path.exists():
        return set()
    return {json.loads(line)["ticker"] for line in path.read_text().splitlines() if line}


def process(ticker: str, years: int) -> None:
    cik, name = srd.ticker_to_cik(ticker)
    company = {"ticker": ticker, "name": name, "cik": cik, "status": "ok",
               "filings": 0, "extracted": 0, "pairs": 0, "errors": []}
    try:
        filings = srd.latest_10ks(cik, years + 1)
    except SystemExit as exc:  # fewer filings than asked for
        company.update(status="error", errors=[str(exc)])
        _append("companies.jsonl", company)
        return
    company["filings"] = len(filings)

    sections: dict[str, list[str] | None] = {}
    for f in filings:
        try:
            paras = srd.extract_item_1a(srd.html_to_paragraphs(srd.sec_get(f.url, cache=False)))
            sections[f.accession] = paras
            company["extracted"] += 1
            (OUT / "sections" / f"{ticker}_{f.filing_date}.json").write_text(
                json.dumps({"ticker": ticker, "filing": f.__dict__, "paragraphs": paras},
                           ensure_ascii=False), encoding="utf-8")
        except ValueError as exc:
            sections[f.accession] = None
            company["errors"].append(f"{f.filing_date}: {exc}")

    for new, old in zip(filings, filings[1:]):
        new_p, old_p = sections.get(new.accession), sections.get(old.accession)
        pair_id = f"{ticker}-{new.filing_date[:4]}"
        pair = {"pair_id": pair_id, "ticker": ticker, "name": name, "cik": cik,
                "new_filing": new.__dict__, "old_filing": old.__dict__}
        if new_p is None or old_p is None:
            pair.update(status="extraction_failed")
            _append("pairs.jsonl", pair)
            continue
        changes, stats = srd.diff_paragraphs(old_p, new_p)
        pair.update(status="ok", stats=stats,
                    old_paragraphs=len(old_p), new_paragraphs=len(new_p),
                    old_words=sum(len(p.split()) for p in old_p),
                    new_words=sum(len(p.split()) for p in new_p),
                    changes=len(changes))
        _append("pairs.jsonl", pair)
        company["pairs"] += 1
        for c in changes:
            _append("changes.jsonl", {
                "change_id": f"{pair_id}-{c.id}", "pair_id": pair_id, "ticker": ticker,
                "fiscal_year_new": new.report_date[:4], "fiscal_year_old": old.report_date[:4],
                "kind": c.kind, "section": c.section, "old": c.old, "new": c.new,
                "similarity": c.ratio,
            })

    if company["errors"]:
        company["status"] = "partial" if company["pairs"] else "error"
    _append("companies.jsonl", company)


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the Item 1A change dataset")
    ap.add_argument("--tickers", nargs="*", default=DEFAULT_TICKERS)
    ap.add_argument("--years", type=int, default=3, help="year-over-year comparisons per company")
    args = ap.parse_args()

    (OUT / "sections").mkdir(parents=True, exist_ok=True)
    done = _done_tickers()
    for n, t in enumerate(args.tickers, 1):
        if t in done:
            continue
        print(f"[{n}/{len(args.tickers)}] {t}", flush=True)
        try:
            process(t, args.years)
        except Exception as exc:  # keep going; record what broke
            traceback.print_exc()
            _append("companies.jsonl", {"ticker": t, "status": "error", "errors": [repr(exc)]})
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
