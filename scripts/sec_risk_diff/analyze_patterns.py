"""Find themes and patterns in the Item 1A dataset, without any model calls.

For every extracted Risk Factors section, count how often each theme is
mentioned. Then:

  * per company and year: which themes appeared, disappeared or surged;
  * across companies and years: which themes many companies started (or
    stopped) writing about at the same time.

Those comparisons become rule-based signals ("attention", "positive" or
"info"). They say what changed in the filings, not why; news_signals.py adds
a Claude reading of related news on top.

Writes:
  dataset/themes.json          per-company theme counts and per-change theme tags
  site/db/patterns.json        cross-company theme matrix (one database document)
  site/db/signals/<id>.json    one database document per signal
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
DS = HERE / "dataset"
DB = HERE / "site" / "db"

# id: (label, pattern, severity when it newly appears)
# Patterns are matched case-insensitively unless written with (?-i:...).
THEMES: dict[str, tuple[str, str, str]] = {
    "tariffs": ("Tariffs and trade policy",
                r"\btariffs?\b|\btrade (?:wars?|polic(?:y|ies)|tensions?|restrictions?|barriers?|disputes?)\b"
                r"|\bimport dut(?:y|ies)\b|\bretaliatory\b|\bsection 232\b|\bsection 301\b", "medium"),
    "export_controls": ("Export controls and sanctions",
                        r"\bexport controls?\b|\bexport restrictions?\b|\bentity list\b|\bsanctions?\b", "medium"),
    "china": ("China and Taiwan exposure", r"\bchina\b|\bchinese\b|\bprc\b|\btaiwan\b|\bhong kong\b", "low"),
    "ai": ("Artificial intelligence",
           r"\bartificial intelligence\b|(?-i:\bAI\b)|\bgenerative\b|\bmachine learning\b|\blarge language models?\b", "low"),
    "cyber": ("Cybersecurity",
              r"\bcyber[- ]?(?:security|attacks?|threats?|incidents?|criminals?)\b|\bransomware\b"
              r"|\bdata breach(?:es)?\b|\bsecurity breach(?:es)?\b|\bmalware\b|\bhack(?:ing|ers?|ed)?\b", "low"),
    "geopolitics": ("War and geopolitical conflict",
                    r"\bukraine\b|\brussia(?:n)?\b|\bisrael\b|\bmiddle east\b|\bgaza\b|\bred sea\b|\bhamas\b"
                    r"|\biran\b|\barmed conflicts?\b|\bmilitary conflicts?\b|\bgeopolitical\b", "medium"),
    "rates_inflation": ("Inflation, rates and recession",
                        r"\binflation(?:ary)?\b|\binterest rates?\b|\brecessions?\b|\beconomic downturns?\b", "low"),
    "pandemic": ("Pandemic and public health",
                 r"\bcovid(?:-19)?\b|\bpandemics?\b|\bcoronavirus\b|\bpublic health (?:crises|crisis|emergenc(?:y|ies))\b", "medium"),
    "climate": ("Climate and sustainability",
                r"\bclimate(?: change)?\b|\bgreenhouse gas(?:es)?\b|\bextreme weather\b|\bnet[- ]zero\b"
                r"|(?-i:\bESG\b)|\bsustainability\b", "low"),
    "antitrust": ("Antitrust and competition law",
                  r"\bantitrust\b|\bcompetition (?:laws?|authorities|regulators?)\b|\bdigital markets act\b"
                  r"|(?-i:\bDMA\b)|\bmonopol(?:y|ies|ization|istic)\b", "medium"),
    "investigations": ("Investigations and litigation",
                       r"\binvestigations?\b|\bsubpoenas?\b|\bclass actions?\b|\blawsuits?\b|\bindictments?\b"
                       r"|\bconsent decrees?\b|\bcivil investigative demands?\b", "high"),
    "supply_chain": ("Supply chain and sourcing",
                     r"\bsupply chains?\b|\bsingle[- ]source(?:d)?\b|\bsole[- ]source(?:d)?\b|\bshortages?\b", "low"),
    "labor": ("Labor relations",
              r"\bwork stoppages?\b|\bstrikes\b|\blabor unions?\b|\bunionization\b|\bcollective bargaining\b"
              r"|\blabor (?:shortages?|disputes?)\b", "medium"),
    "tax": ("Tax law changes",
            r"\bglobal minimum tax\b|\bpillar two\b|(?-i:\bOECD\b)|\btax (?:legislation|reform)\b"
            r"|\bchanges in tax laws?\b|\beffective tax rates?\b", "low"),
    "us_policy": ("U.S. government and policy shifts",
                  r"\bexecutive orders?\b|\bgovernment shutdowns?\b|\bfederal (?:spending|funding|budget)\b"
                  r"|\bnew administration\b|\bpresidential administration\b|\bU\.S\. administration\b", "medium"),
    "privacy": ("Privacy and data protection",
                r"\bprivacy\b|(?-i:\bGDPR\b)|\bpersonal (?:data|information)\b|\bdata protection\b", "low"),
    "liquidity": ("Liquidity, credit and going concern",
                  r"\bgoing concern\b|\bsubstantial doubt\b|\bcovenants?\b|\bcredit ratings?\b|\bdowngrades?\b"
                  r"|\bliquidity\b", "high"),
    "immigration": ("Immigration and visas", r"\bimmigration\b|\bvisas?\b|(?-i:\bH-1B\b)", "medium"),
    "drug_pricing": ("Drug pricing", r"\bdrug pricing\b|\bprice negotiations?\b|\binflation reduction act\b"
                                     r"|\bmost[- ]favou?red[- ]nation\b|(?-i:\bMFN\b)", "medium"),
    "crypto": ("Digital assets", r"\bdigital assets?\b|\bcrypto(?:currenc(?:y|ies)|-assets?)?\b|\bstablecoins?\b"
                                 r"|\bblockchain\b", "low"),
}
COMPILED = {k: re.compile(p, re.IGNORECASE) for k, (_, p, _) in THEMES.items()}

EASING_THEMES = {"pandemic", "liquidity", "investigations", "geopolitics"}
MIN_COMPANIES = 12      # a year needs this many comparable companies for a market signal
RISE_SHARE = 0.20       # share of companies moving the same way to call it a pattern
MIN_MOVERS = 5


def count_themes(text: str) -> dict[str, int]:
    return {k: len(rx.findall(text)) for k, rx in COMPILED.items()}


def _jsonl(name: str) -> list[dict]:
    p = DS / name
    return [json.loads(x) for x in p.read_text().splitlines() if x] if p.exists() else []


def main() -> None:
    # --- per filing counts -------------------------------------------------
    counts: dict[tuple[str, str], dict] = {}          # (ticker, filing_date) -> info
    for f in sorted((DS / "sections").glob("*.json")):
        d = json.loads(f.read_text())
        text = "\n".join(d["paragraphs"])
        counts[(d["ticker"], d["filing"]["filing_date"])] = {
            "fy": d["filing"]["report_date"][:4], "words": len(text.split()), "themes": count_themes(text)}

    pairs = [p for p in _jsonl("pairs.jsonl") if p.get("status") == "ok"]
    changes = _jsonl("changes.jsonl")
    names = {c["ticker"]: c.get("name", c["ticker"]) for c in _jsonl("companies.jsonl")}

    # --- per change theme tags ---------------------------------------------
    change_tags: dict[str, dict] = {}
    by_pair_theme: dict[tuple[str, str], list[str]] = defaultdict(list)   # (pair, theme) -> change ids
    for c in changes:
        old_c, new_c = count_themes(c["old"]), count_themes(c["new"])
        added = [k for k in THEMES if new_c[k] > old_c[k]]
        removed = [k for k in THEMES if old_c[k] > new_c[k]]
        if added or removed:
            change_tags[c["change_id"]] = {"added": added, "removed": removed}
        for k in added + removed:
            by_pair_theme[(c["pair_id"], k)].append(c["change_id"])

    # --- per company year-over-year deltas -----------------------------------
    deltas = []   # one per (pair, theme)
    for p in pairs:
        new = counts.get((p["ticker"], p["new_filing"]["filing_date"]))
        old = counts.get((p["ticker"], p["old_filing"]["filing_date"]))
        if not new or not old:
            continue
        for k in THEMES:
            deltas.append({"pair_id": p["pair_id"], "ticker": p["ticker"], "fy": new["fy"], "fy_old": old["fy"],
                           "theme": k, "old": old["themes"][k], "new": new["themes"][k]})

    # --- cross-company matrix ------------------------------------------------
    matrix: dict[str, dict[str, dict]] = defaultdict(dict)
    by_fy_theme: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for d in deltas:
        by_fy_theme[(d["fy"], d["theme"])].append(d)
    years = sorted({d["fy"] for d in deltas})
    for (fy, k), rows in by_fy_theme.items():
        cell = {"n": len(rows), "started": [], "stopped": [], "up": [], "down": [],
                "mentions_old": sum(r["old"] for r in rows), "mentions_new": sum(r["new"] for r in rows),
                "companies_mentioning": sum(1 for r in rows if r["new"] > 0)}
        for r in rows:
            o, n = r["old"], r["new"]
            if o == 0 and n > 0:
                cell["started"].append(r["ticker"])
            elif o > 0 and n == 0:
                cell["stopped"].append(r["ticker"])
            elif n - o >= 3 and n >= 1.5 * o:
                cell["up"].append(r["ticker"])
            elif o - n >= 3 and n <= o / 1.5:
                cell["down"].append(r["ticker"])
        matrix[k][fy] = cell

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    signals: list[dict] = []

    # Market-level: many companies moving the same way in the same fiscal year.
    for k, by_year in matrix.items():
        label = THEMES[k][0]
        for fy, cell in by_year.items():
            n = cell["n"]
            inc = cell["started"] + cell["up"]
            dec = cell["stopped"] + cell["down"]
            if n < MIN_COMPANIES:
                continue
            need = max(MIN_MOVERS, RISE_SHARE * n)
            evidence = {"n": n, "started": cell["started"], "increased": cell["up"], "stopped": cell["stopped"],
                        "decreased": cell["down"], "mentions_old": cell["mentions_old"],
                        "mentions_new": cell["mentions_new"]}
            if len(inc) >= need and len(inc) >= 2 * max(len(dec), 1):
                signals.append({
                    "id": f"m-{k}-{fy}-rising", "scope": "market", "theme": k, "theme_label": label, "fy": fy,
                    "kind": "rising", "severity": "attention",
                    "title": f"{label}: {len(inc)} of {n} companies wrote more about it in FY{fy}",
                    "detail": (f"{len(cell['started'])} started mentioning it and {len(cell['up'])} expanded it, "
                               f"while {len(dec)} cut it back. Mentions across these filings went from "
                               f"{cell['mentions_old']} to {cell['mentions_new']}."),
                    "evidence": evidence, "score": len(inc) / n})
            elif len(dec) >= need and len(dec) >= 2 * max(len(inc), 1):
                signals.append({
                    "id": f"m-{k}-{fy}-fading", "scope": "market", "theme": k, "theme_label": label, "fy": fy,
                    "kind": "fading", "severity": "positive",
                    "title": f"{label}: {len(dec)} of {n} companies wrote less about it in FY{fy}",
                    "detail": (f"{len(cell['stopped'])} dropped it entirely and {len(cell['down'])} shortened it, "
                               f"while {len(inc)} expanded it. Mentions went from {cell['mentions_old']} to "
                               f"{cell['mentions_new']}. Fading risk language can mean the risk eased, or that "
                               f"companies folded it into other sections."),
                    "evidence": evidence, "score": len(dec) / n})

    # Company-level: the latest comparison for each company.
    latest_pair = {}
    for p in sorted(pairs, key=lambda p: p["new_filing"]["report_date"]):
        latest_pair[p["ticker"]] = p
    for t, p in latest_pair.items():
        rows = [d for d in deltas if d["pair_id"] == p["pair_id"]]
        found = []
        for d in rows:
            k, o, n = d["theme"], d["old"], d["new"]
            label, _, sev = THEMES[k]
            ids = by_pair_theme.get((p["pair_id"], k), [])[:6]
            base = {"scope": "company", "ticker": t, "name": names.get(t, t), "theme": k, "theme_label": label,
                    "fy": d["fy"], "fy_old": d["fy_old"], "pair_id": p["pair_id"],
                    "evidence": {"mentions_old": o, "mentions_new": n, "change_ids": ids}}
            if o == 0 and n >= 3:
                found.append({**base, "id": f"c-{t}-{d['fy']}-{k}-emerged", "kind": "emerged",
                              "severity": "attention" if sev in ("high", "medium") else "info",
                              "title": f"{t} added {label.lower()} to its risk factors in FY{d['fy']}",
                              "detail": f"Not mentioned in FY{d['fy_old']}; {n} mentions in FY{d['fy']}.",
                              "score": n * (3 if sev == "high" else 2 if sev == "medium" else 1)})
            elif o >= 3 and n == 0:
                found.append({**base, "id": f"c-{t}-{d['fy']}-{k}-dropped", "kind": "dropped",
                              # Removing a warning reads as good news only for risks that ease on their own;
                              # for policy, tax or tariff wording it is often rewording.
                              "severity": "positive" if k in EASING_THEMES else "info",
                              "title": f"{t} removed {label.lower()} from its risk factors in FY{d['fy']}",
                              "detail": (f"{o} mentions in FY{d['fy_old']}, none in FY{d['fy']}. This can mean the "
                                         f"risk eased, or that the text moved elsewhere in the filing."),
                              "score": o})
            elif n - o >= 6 and n >= 2 * o:
                found.append({**base, "id": f"c-{t}-{d['fy']}-{k}-surged", "kind": "surged",
                              "severity": "attention" if sev == "high" else "info",
                              "title": f"{t} doubled its discussion of {label.lower()} in FY{d['fy']}",
                              "detail": f"Mentions rose from {o} in FY{d['fy_old']} to {n} in FY{d['fy']}.",
                              "score": (n - o) * (2 if sev == "high" else 1)})
        st = p.get("stats") or {}
        total = (p.get("new_paragraphs") or 0) + st.get("removed", 0)
        changed = st.get("modified", 0) + st.get("new", 0) + st.get("removed", 0)
        structural = st.get("new", 0) + st.get("removed", 0)
        if total and structural / total >= 0.3:
            found.append({"id": f"c-{t}-{p['new_filing']['report_date'][:4]}-rewrite", "scope": "company",
                          "ticker": t, "name": names.get(t, t), "kind": "rewrite", "severity": "info",
                          "fy": p["new_filing"]["report_date"][:4], "pair_id": p["pair_id"],
                          "title": f"{t} rewrote {round(100 * changed / total)}% of its risk factors",
                          "detail": (f"{st.get('modified', 0)} paragraphs rewritten, {st.get('new', 0)} new, "
                                     f"{st.get('removed', 0)} removed. A large rewrite often follows a strategy "
                                     f"change, a deal, or a reorganised section; read the new paragraphs first."),
                          "evidence": {"changed": changed, "total": total}, "score": 5})
        order = {"attention": 0, "positive": 1, "info": 2}
        found.sort(key=lambda s: (order[s["severity"]], -s["score"]))
        signals.extend(found[:4])

    # Keep news checks from earlier runs (news_signals.py, or ones verified by hand).
    verified_path = DS / "news_verified.json"
    verified = json.loads(verified_path.read_text()) if verified_path.exists() else {}
    keep = ("news", "interpretation", "confidence", "pattern_note", "caution", "source", "verified_at", "model")
    for s in signals:
        s.update({"source": "rules", "news": [], "interpretation": None, "confidence": None,
                  "pattern_note": None, "caution": None, "updated": now})
        extra = verified.get(s["id"])
        if extra:
            s.update({k: v for k, v in extra.items() if k in keep})

    # --- write ---------------------------------------------------------------
    (DS / "themes.json").write_text(json.dumps({
        "labels": {k: v[0] for k, v in THEMES.items()},
        "filings": {f"{t}|{fd}": v for (t, fd), v in counts.items()},
        "change_tags": change_tags}, ensure_ascii=False))
    DB.mkdir(parents=True, exist_ok=True)
    (DB / "patterns.json").write_text(json.dumps({
        "themes": [{"id": k, "label": v[0]} for k, v in THEMES.items()], "years": years,
        "matrix": matrix, "rules": {"min_companies": MIN_COMPANIES, "share": RISE_SHARE, "min_movers": MIN_MOVERS},
        "updated": now}, ensure_ascii=False))
    sig_dir = DB / "signals"
    sig_dir.mkdir(exist_ok=True)
    for old in sig_dir.glob("*.json"):
        old.unlink()
    for s in signals:
        (sig_dir / f"{s['id']}.json").write_text(json.dumps(s, ensure_ascii=False))
    kinds = defaultdict(int)
    for s in signals:
        kinds[(s["scope"], s["severity"])] += 1
    print(f"{len(counts)} filings, {len(deltas)} theme comparisons, {len(signals)} signals: {dict(kinds)}")


if __name__ == "__main__":
    main()
