"""Offline tests: synthetic 10-K HTML, no network and no API key needed.

Run:  python -m unittest test_sec_risk_diff.py
"""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import sec_risk_diff as srd

FILLER = (
    "Our business depends on the continued demand for our products in many markets, "
    "and adverse economic conditions could reduce that demand and harm our results. "
)


def make_10k(risks: list[str], year: int) -> bytes:
    body = "".join(f"<p><span>{r}</span></p>" for r in risks)
    return f"""<html><body>
<div style="display:none"><ix:header><ix:hidden>HIDDEN XBRL FACT</ix:hidden></ix:header></div>
<table><tr><td>Item 1A.</td><td>Risk Factors</td><td>12</td></tr>
<tr><td>Item 1B.</td><td>Unresolved Staff Comments</td><td>30</td></tr></table>
<p>Item 1. Business</p><p>We make widgets.</p>
<table><tr><td>Item 1A.</td><td>Risk Factors</td></tr></table>
<p>Macroeconomic Risks</p>
{body}
<p>12</p>
<p>Example Corp. | {year} Form 10-K | 13</p>
<table><tr><td>Item 1B.</td><td>Unresolved Staff Comments</td></tr></table>
<p>None.</p>
</body></html>""".encode()


OLD = [
    FILLER * 6,
    "We rely on a single supplier located in Taiwan for key components, and any "
    "disruption at that supplier could delay our shipments for several months. " * 4,
    "Changes in fiscal 2024 tax rules could affect our effective tax rate, which was 15% "
    "in the year ended December 31, 2024, and we continue to monitor developments. " * 4,
    "We face intense competition from larger companies with more resources, which may "
    "reduce our market share and pricing power over time. " * 4,
]
NEW = [
    FILLER * 6,                                              # unchanged
    "We rely on a single supplier located in Taiwan for key components, and any "
    "disruption at that supplier, including export restrictions imposed by the US "
    "government in 2025, has already delayed our shipments and could do so again. " * 4,
    "Changes in fiscal 2025 tax rules could affect our effective tax rate, which was 16% "
    "in the year ended December 31, 2025, and we continue to monitor developments. " * 4,
    "The SEC has opened an investigation into our revenue recognition practices, and an "
    "adverse outcome could result in penalties and a restatement of prior results. " * 4,
]


class ExtractionTest(unittest.TestCase):
    def test_skips_toc_and_hidden_xbrl(self):
        paras = srd.html_to_paragraphs(make_10k(OLD, 2024))
        self.assertNotIn("HIDDEN XBRL FACT", " ".join(paras))
        section = srd.extract_item_1a(paras)
        self.assertEqual(section[0], "Macroeconomic Risks")
        self.assertEqual(len(section), 1 + len(OLD))  # heading + risks, page junk dropped
        self.assertFalse(any("Form 10-K" in p for p in section))

    def test_missing_section_raises(self):
        with self.assertRaises(ValueError):
            srd.extract_item_1a(["Item 1. Business", "We make widgets."])


class DiffTest(unittest.TestCase):
    def setUp(self):
        old = srd.extract_item_1a(srd.html_to_paragraphs(make_10k(OLD, 2024)))
        new = srd.extract_item_1a(srd.html_to_paragraphs(make_10k(NEW, 2025)))
        self.changes, self.stats = srd.diff_paragraphs(old, new)

    def test_classification(self):
        kinds = sorted(c.kind for c in self.changes)
        self.assertEqual(kinds, ["modified", "new", "removed"])
        self.assertEqual(self.stats["minor"], 1)       # tax paragraph: only years/rates changed
        self.assertEqual(self.stats["unchanged"], 2)   # heading + filler

    def test_modified_is_the_supplier_paragraph(self):
        mod = next(c for c in self.changes if c.kind == "modified")
        self.assertIn("export restrictions", mod.new)
        self.assertEqual(mod.section, "Macroeconomic Risks")

    def test_report_renders_without_llm(self):
        report = srd.render_report("Example", None, self.stats, self.changes, None)
        self.assertIn("{+", report)
        self.assertIn("SEC has opened an investigation", report)


class MinorTest(unittest.TestCase):
    def test_numbers_only_is_minor(self):
        self.assertTrue(srd.is_minor("Revenue was $10 million in 2024.",
                                     "Revenue was $12 million in 2025."))

    def test_single_negation_is_not_minor(self):
        long = "Our supplier could meet the increased demand for components next year. " * 6
        self.assertFalse(srd.is_minor(long, long.replace("could meet", "could not meet", 1)))

    def test_new_clause_is_not_minor(self):
        self.assertFalse(srd.is_minor(
            "We may face litigation.",
            "We may face litigation, including a class action filed against us in March."))


class _FakeStream:
    def __init__(self, reply):
        self.reply = reply

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return types.SimpleNamespace(
            stop_reason="end_turn",
            content=[types.SimpleNamespace(type="thinking", thinking=""),
                     types.SimpleNamespace(type="text", text=self.reply)],
            usage=types.SimpleNamespace(input_tokens=1_000_000, output_tokens=100_000),
        )


class AssessTest(unittest.TestCase):
    """Exercises the API path with a stand-in client; no network or key needed."""

    def test_parses_reply_and_checks_quotes(self):
        changes = [srd.Change("c1", "new", "S", "", "The SEC has opened an investigation."),
                   srd.Change("c2", "removed", "S", "We face intense competition.", "")]
        reply = json.dumps({"items": [
            {"id": "c1", "category": "new_risk", "materiality": 5, "summary": "x",
             "quote": "SEC has opened an investigation"},
            {"id": "c2", "category": "removed_risk", "materiality": 2, "summary": "y",
             "quote": "a sentence that is not in the filing"},
        ]})
        captured = {}

        class FakeMessages:
            def stream(self, **kwargs):
                captured.update(kwargs)
                return _FakeStream("Here you go:\n" + reply)

        fake = types.ModuleType("anthropic")
        fake.Anthropic = lambda **kw: types.SimpleNamespace(messages=FakeMessages())
        with mock.patch.dict(sys.modules, {"anthropic": fake}):
            usage = srd.assess_changes(changes, "claude-sonnet-5-5", "Turkish")

        self.assertEqual(captured["model"], "claude-sonnet-5-5")
        self.assertIn("Turkish", captured["system"])
        self.assertTrue(changes[0].assessment["quote_verified"])
        self.assertFalse(changes[1].assessment["quote_verified"])
        self.assertEqual(usage["cost_usd"], 3.0)  # 1M in x $2 + 0.1M out x $10
        self.assertEqual(usage["problems"], [])


class CliTest(unittest.TestCase):
    def test_files_mode_no_llm(self):
        with tempfile.TemporaryDirectory() as d:
            old, new, out = Path(d, "old.htm"), Path(d, "new.htm"), Path(d, "r.md")
            old.write_bytes(make_10k(OLD, 2024))
            new.write_bytes(make_10k(NEW, 2025))
            self.assertEqual(srd.main(["--files", str(old), str(new), "--no-llm", "--out", str(out)]), 0)
            self.assertTrue(out.exists() and out.with_suffix(".json").exists())


if __name__ == "__main__":
    unittest.main()
