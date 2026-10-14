#!/usr/bin/env python3
"""Tavi sözlük üreticisi.

kaynak/*.txt dosyalarındaki anlam listesinden, gizli tohumla kelimeler üretir.
Çıktı dil/gizli/ altına yazılır ve git'e GİRMEZ (.gitignore).

Kelime biçimi:
  * İlk hece = anlam kategorisi (kaynak dosyadaki [kategori|tür|ön,ekler])
  * Son harf = tür:  isim a/e/u,  fiil -n,  sıfat -i,  zarf/işlev -o
  * Ses: yumuşak. Sert patlamalılar (p t k b d g) seyrek.
  * Her iki kelime arasında en az 2 harf fark var.

Kullanım: python3 dil/uret.py
"""
import csv
import random
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).parent
GIZLI = ROOT / "gizli"
GIZLI.mkdir(exist_ok=True)

seed_file = GIZLI / "tohum.txt"
if not seed_file.exists():
    seed_file.write_text(secrets.token_hex(16) + "\n")
rng = random.Random(seed_file.read_text().strip())

ONSET = {"l": 6, "n": 6, "m": 6, "r": 5, "v": 4, "y": 4, "s": 4, "ş": 3, "f": 3,
         "h": 3, "z": 3, "d": 1, "b": 1, "t": 1, "k": 1}
VOWEL = {"a": 4, "e": 3, "i": 3, "o": 2, "u": 2}
CODA_OK_BEFORE = set("sşfhzvdbtk")  # ortada n koda yalnızca bunlardan önce

BLACK_SUB = ["sik", "amk", "bok", "got", "kaka", "fuk", "fak", "sex", "seks", "kok",
             "dik", "kont", "piç", "pic"]
BLACK_WORD = set("""lale nane nine nene mama yaya dede sene sala sula lola leyla mine
mina yine yeni sana mene sunu hani haha hehe hihi vahe nasıl ila lani sevi seli
sema sema remi emre selim zeynep leyla hilal melis mert ali ayşe hazal""".split())
RESERVED_START = ("ku",)

def pick(d):
    return rng.choices(list(d), weights=list(d.values()))[0]

def make(prefixes, pos):
    """Önek hecesi + 0-2 orta hece + türü belirleyen son hece."""
    n_syl = rng.choices([2, 3, 4], weights=[40, 50, 10])[0]
    w = rng.choice(prefixes)
    force_onset = None  # önceki hece 'n' ile bittiyse sonraki onset kısıtlı
    for i in range(1, n_syl):
        last = i == n_syl - 1
        c = force_onset or pick(ONSET)
        force_onset = None
        if last:
            v = {"n": rng.choice("aeu"), "a": "i", "x": "o", "v": pick(VOWEL)}[pos]
            syl = c + v + ("n" if pos == "v" else "")
        else:
            syl = c + pick(VOWEL)
            if rng.random() < 0.08:
                syl += "n"
                force_onset = rng.choice([x for x in ONSET if x in CODA_OK_BEFORE])
        if w.endswith(syl):  # arka arkaya aynı hece (lala) yok
            return None
        w += syl
    return w


def neighbors(w):
    alpha = "abdefghilmnoprstuvyzşk"
    out = set()
    for i in range(len(w)):
        out.add(w[:i] + w[i + 1:])
        for ch in alpha:
            out.add(w[:i] + ch + w[i + 1:])
    for i in range(len(w) + 1):
        for ch in alpha:
            out.add(w[:i] + ch + w[i:])
    out.discard(w)
    return out

def parse_sources():
    entries = []
    for f in sorted((ROOT / "kaynak").glob("*.txt")):
        cat = pos = prefs = None
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("["):
                cat, pos, p = line.strip("[]").split("|")
                prefs = p.split(",")
                continue
            tr, en = line.split("|")
            entries.append((cat, pos, prefs, tr.strip(), en.strip()))
    return entries

def main():
    fixed = list(csv.DictReader(open(ROOT / "islev.csv", encoding="utf-8")))
    used = {r["word"].strip("-") for r in fixed if r["pos"] not in ("suffix", "prefix")}
    entries = parse_sources()
    # Kilit: önceden üretilmiş kelimeler aynen korunur, sadece yeni girişler üretilir.
    posname = {"n": "noun", "v": "verb", "a": "adj", "x": "adv"}
    locked = {}
    old = GIZLI / "sozluk.csv"
    if old.exists():
        for r in csv.DictReader(open(old, encoding="utf-8")):
            if r["cat"] != "islev":
                locked[(r["cat"], r["pos"], r["tr"], r["en"])] = r["word"]
    used |= set(locked.values())
    out, fails = [], 0
    for cat, pos, prefs, tr, en in entries:
        key = (cat, posname[pos], tr, en)
        if key in locked:
            out.append((locked[key], pos, cat, tr, en))
            continue
        for _ in range(5000):
            w = make(prefs, pos)
            if not w or len(w) < 4 or len(w) > 9:
                continue
            if w in used or w in BLACK_WORD or w.startswith(RESERVED_START):
                continue
            if any(b in w for b in BLACK_SUB):
                continue
            if neighbors(w) & used:
                continue
            used.add(w)
            out.append((w, pos, cat, tr, en))
            break
        else:
            fails += 1
            print("ÜRETİLEMEDİ:", tr, file=sys.stderr)
    with open(GIZLI / "sozluk.csv", "w", encoding="utf-8", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["word", "pos", "tr", "en", "cat"])
        for r in fixed:
            wr.writerow([r["word"], r["pos"], r["tr"], r["en"], "islev"])
        for w, pos, cat, tr, en in out:
            wr.writerow([w, posname[pos], tr, en, cat])
    print(f"{len(fixed)} işlev + {len(out)} üretilen = {len(fixed) + len(out)} giriş; başarısız: {fails}")

main()
