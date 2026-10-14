#!/usr/bin/env python3
"""Sözlüğü doğrular: hece yapısı, yinelenen kelime, benzer kelimeler.

Kullanım: python3 dil/kontrol.py
"""
import csv
import itertools
import re
import sys
from pathlib import Path

# Hece yapısı: (C)V(n). Kelime sonunda herhangi bir ünsüz de olabilir.
C = "pbtdkgmnszşhvlryf"
SYLLABLE = rf"[{C}]?[aeiou]n?"
WORD = re.compile(rf"^(?:{SYLLABLE})*(?:{SYLLABLE}|[{C}]?[aeiou][{C}])$")

rows = list(csv.DictReader(open(Path(__file__).with_name("sozluk.csv"), encoding="utf-8")))
errors = []

words = {}
for r in rows:
    w = r["word"]
    core = w.strip("-")
    if r["pos"] in ("suffix", "prefix"):
        continue
    if not WORD.match(core):
        errors.append(f"hece yapısına uymuyor: {w}")
    if w in words:
        errors.append(f"yinelenen kelime: {w}")
    words[w] = r

# Tek harf farkı olan çiftler (karışma riski) -> uyarı, hata değil
def one_off(a, b):
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    s, l = sorted((a, b), key=len)
    return any(l[:i] + l[i + 1:] == s for i in range(len(l)))

warns = []
for a, b in itertools.combinations(words, 2):
    # Kısa işlev kelimelerini atla (2 harf), uyarı gürültüsü olur
    if len(a) > 3 and len(b) > 3 and one_off(a, b):
        warns.append(f"{a} ~ {b}")

print(f"{len(words)} kelime, {len(rows) - len(words)} ek")
for e in errors:
    print("HATA:", e)
for w in warns:
    print("uyarı (çok benzer):", w)
sys.exit(1 if errors else 0)
