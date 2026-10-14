# Tavi: yakın çevre için dil (taslak v0.2)

"Tavi" çalışma adıdır ("bizim çevre"). Hedefler: **sır tutmak**, **yumuşak ses**, **~2000 kelime**.

> **Gizlilik modeli.** Bu repo herkese açık olduğu için burada yalnızca *kurallar* ve *anlam listesi* var. Kelimelerin kendisi (`sozluk.csv`) ve onları üreten gizli tohum **`dil/gizli/`** klasöründedir ve `.gitignore` ile git'e girmez. Gizli klasörü açık bir yere koymayın. Not: bir dil, şifreleme değildir. Sözlüğü ele geçiren biri her şeyi okur, konuşulanı dinleyen biri ise yeterince örnekle yapıyı çözebilir. Gerçek gizlilik için bu bir *ek katman*dır, tek katman değil.

> **Eski sürüm uyarısı.** v0.1 (ilk commit) sözlüğü ve örnekleri git geçmişinde hâlâ görünür. Onlar artık **kullanımdan kalktı**, v0.2 ile hiçbir içerik paylaşmaz (işlev sözcükleri hariç). Geçmişi tamamen silmek istersen repoyu gizli yapmak veya yeni bir gizli repo açmak gerekir.

## Dosyalar

| Dosya | Açık/gizli | Ne işe yarar |
|---|---|---|
| `kaynak/*.txt` | açık | 1960+ anlam (Türkçe\|İngilizce), kategorilere ayrılmış |
| `islev.csv` | açık | Sabit işlev sözcükleri (zamirler, parçacıklar, sayılar...) |
| `uret.py` | açık | Anlam listesinden, gizli tohumla kelime üretir |
| `kontrol.py` | açık | Üretilen sözlüğü doğrular |
| `gizli/` | **gizli** | tohum, `sozluk.csv`, okunabilir sözlükler, örnek cümleler |

Çalıştırma: `python3 dil/uret.py && python3 dil/kontrol.py`
`gizli/sozluk.csv` aynı zamanda kilit dosyasıdır: kaybedersen kelimeler yeniden rastgele atanır. **`gizli/` klasörünü yedekle.**

## 1. Sesler (yumuşak)

- Ünlüler: `a e i o u`. Ünsüzler: `l m n r v y s ş f h z` (yaygın) ve `d b t k` (nadir, ~%3).
- Hece: `(Ü)Ü(n)`. Sert patlamalılar (`p g` neredeyse hiç) ve ünsüz kümeleri yok.
- Vurgu ilk hecede.
- Yazım fonetik: bir harf bir ses. `ş` = Türkçe ş.

## 2. Kelime biçimi

**Son harf kelimenin türünü verir**, böylece kelimeyi görünce ne olduğunu bilirsin:

| Tür | Son harf | Örnek biçim |
|---|---|---|
| isim | `a` `e` `u` | `….a` |
| fiil | `-n` | `….an` |
| sıfat | `i` | `….i` |
| zarf, bağlaç, edat | `o` | `….o` |

**İlk hece kategoriyi verir** (hayvanlar hep aynı iki heceden birinle başlar, yiyecekler başka bir üçüyle, vb.). Önek tablosu `gizli/` içinde. Bu ezberi kolaylaştırır.

## 3. Dilbilgisi (8 kural, v0.1 ile aynı)

1. **Sıra: Özne–Fiil–Nesne.**
2. **Fiil çekimlenmez.** Zaman: hiçbiri = genel; `ta` geçmiş; `ne` gelecek; `sa` şu an olan.
3. **Olumsuz:** `ko` fiilden/sıfattan önce.
4. **Soru:** cümle sonu `re`. Soru sözcükleri yerinde kalır (`kuno` ne, `kuse` kim, `kuri` nerede, `kuta` ne zaman, `kubo` neden, `kuhin` nasıl). `ku-` ile başlayan başka kelime üretilmez.
5. **Çoğul:** isme `-li` (sayıdan sonra yok). Zamirler: `ni nili`, `su suli`, `ha hali`. `nasu` = sen ve ben.
6. **Sıfat isimden sonra.** İsimlerle "olmak" için `ye`. Aitlik: `sahip olunan de sahip`.
7. **Edatlar isimden önce:** `to` -e, `ka` -de, `ma` ile, `pe` için.
8. **Cümle sonu parçacıkları:** `zo` sadece aramızda, `yo` emir, `re` soru; cümle sonunda en fazla biri.

## 4. Türetme (v0.2'de biçime uyacak şekilde değişti)

| Ek | İş | Kural | Sonuç türü |
|---|---|---|---|
| `-a` | fiilden isim | fiil + `a` (`…an` → `…ana`) | isim ✓ |
| `-ru` | yapan kişi | fiil kökü (son `n` düşer) + `ru` | isim ✓ |
| `-le` | küçültme | isim + `le` | isim ✓ |
| `-mu` | büyütme | isim + `mu` | isim ✓ |
| `ge-` | zıt anlam | `ge` + sıfat/fiil | aynı tür |
| `-o` | sıfattan zarf | son `i` yerine `o` | zarf ✓ |

Ekli kelimenin son harfi hâlâ türü gösterir.

## 5. Sayılar ve işlev sözcükleri

`islev.csv` dosyasında. Sayılar: 0 `zira`, 1 `ika`, 2 `duve`, 3 `teri`, 4 `boti`, 5 `pema`, 6 `şeko`, 7 `sebi`, 8 `ahu`, 9 `zeno`, 10 `desa`. 11 = `desa ika`, 20 = `duve desa`.

## 6. Sıradaki adımlar

1. Sizin kişisel kelimeleriniz (isimler, ortak şakalar, yer adları): `kaynak/` altına ekleyin, yeniden üretin. `uret.py` mevcut `gizli/sozluk.csv`'yi kilit olarak okur: var olan kelimeler **değişmez**, sadece yeni girişler için kelime üretilir.
2. Telaffuz uygulaması: yazım fonetik olduğundan kural tablosu yeter.
3. Alıştırma modülü: flashcard + sözlük arama. Veri `gizli/sozluk.csv`.
