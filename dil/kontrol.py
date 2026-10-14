#!/usr/bin/env python3
"""gizli/sozluk.csv dosyasını doğrular.

Kontroller: hece yapısı, türün son harfiyle uyumu, yinelenen kelime/anlam,
kelimeler arası en az 2 harf fark, sert ses oranı.
Kullanım: python3 dil/uret.py && python3 dil/kontrol.py
"""
import csv
import re
import sys
from collections import Counter
from pathlib import Path

C = "pbtdkgmnszşhvlryf"
SYL = rf"[{C}]?[aeiou]n?"
WORD = re.compile(rf"^(?:{SYL})*(?:{SYL}|[{C}]?[aeiou][{C}])$")
ENDING = {"noun": "aeu", "adj": "i", "adv": "o", "verb": "n"}
HARD = set("ptkbdg")

rows = list(csv.DictReader(open(Path(__file__).parent / "gizli" / "sozluk.csv", encoding="utf-8")))
errors, warns = [], []
words = {}
for r in rows:
    w, pos = r["word"], r["pos"]
    core = w.strip("-")
    if pos in ("suffix", "prefix"):
        continue
    if not WORD.match(core):
        errors.append(f"hece yapısı: {w}")
    if r["cat"] != "islev" and core[-1] not in ENDING.get(pos, ""):
        errors.append(f"tür/son harf uyumsuz: {w} ({pos})")
    if w in words:
        errors.append(f"yinelenen kelime: {w}")
    words[w] = r

en = Counter((r["en"], r["pos"]) for r in rows)
for (e, p), n in en.items():
    if n > 1:
        warns.append(f"aynı anlam birden çok kez: {e} ({p}) x{n}")

# En az 2 harf fark: tüm tek-düzenleme komşuluklarına bak
alpha = "abdefhilmnorstuvyzşkgpf"
def nb(w):
    s = set()
    for i in range(len(w)):
        s.add(w[:i] + w[i + 1:])
        for ch in alpha:
            s.add(w[:i] + ch + w[i + 1:])
    for i in range(len(w) + 1):
        for ch in alpha:
            s.add(w[:i] + ch + w[i:])
    s.discard(w)
    return s
for w in words:
    if len(w) > 3 and (nb(w) & set(words)):
        hit = sorted(nb(w) & set(words))
        warns.append(f"çok benzer: {w} ~ {', '.join(hit)}")

body = "".join(w for w, r in words.items() if r["cat"] != "islev")
hard = sum(ch in HARD for ch in body) / len(body)
print(f"{len(words)} kelime; üretilen kısımda sert ünsüz oranı: {hard:.1%}")
print("türler:", dict(Counter(r["pos"] for r in words.values())))
for e in errors:
    print("HATA:", e)
for w in warns[:20]:
    print("uyarı:", w)
print(f"{len(errors)} hata, {len(warns)} uyarı")
sys.exit(1 if errors else 0)
