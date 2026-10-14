# Tavi: yakın çevre için dil (taslak v0.1)

"Tavi" çalışma adıdır ve "bizim çevre" demektir. Değiştirmek serbest.

Tasarım hedefleri:
1. **Öğrenmesi kolay.** Çekim yok, cinsiyet yok, düzensiz fiil yok. Bir akşamda 100 kelime ve 8 kural öğrenilir.
2. **Yazması kolay.** Ekstra harf yok (Türkçe klavyede `ş` yeter). Her harf tek bir ses.
3. **Okunması kolay.** Hece yapısı sade, sonraki aşamada sesli uygulama yazmak için uygun.
4. **Bir kişilik.** Cümle sonu parçacıkları (`zo`, `yo`) dile "sadece bizim" tadı verir.

> Güvenlik notu: bu bir *dil*, şifreleme değil. Sözlüğü ve kuralları bilen biri okur. Aidiyet ve eğlence için ideal, gerçek gizlilik için yetersiz.

---

## 1. Sesler ve yazım

**Ünlüler (5):** `a e i o u`. Her biri her zaman aynı okunur. Uzun/kısa ayrımı yok.

**Ünsüzler (17):** `p b t d k g m n s z ş h v l r y f`
`ş` = Türkçedeki ş. `y` = Türkçedeki y. `g` her zaman sert g. Çift ünsüz yok.

**Hece:** `(Ü)Ü(n)`, yani isteğe bağlı ünsüz, ünlü, isteğe bağlı `n`. Kelime sonunda herhangi bir ünsüz olabilir (`kam`, `moş`). Ünsüz kümesi yok (`st`, `rk` olmaz).

**Vurgu:** her zaman ilk hecede: **KA**-si, **BE**-lu.

Yazım Türkçe okuyana doğal gelir: `ni lov su` okunduğu gibi yazılıdır.

## 2. Dilbilgisi: 8 kural

1. **Sıra: Özne – Fiil – Nesne** (Türkçenin tersine Fiil ortada).
   `ni ten ha` = Ben onu görüyorum / gördüm.

2. **Fiil çekimlenmez.** Zaman fiilden önce gelen küçük kelimeyle gösterilir:
   - hiçbiri = genel/şimdi: `ni moş`
   - `ta` = geçmiş: `ni ta moş` (yedim)
   - `ne` = gelecek: `ni ne moş` (yiyeceğim)
   - `sa` = şu an oluyor: `ni sa moş` (yiyorum)

3. **Olumsuzluk:** `ko` fiilden/sıfattan önce. `ni ko moş` = yemiyorum. `ha ko haru` = o mutlu değil.

4. **Soru:** cümlenin sonuna `re`. `su sa moş re?` = Yiyor musun?
   Soru sözcükleri yerinde kalır: `su kam to kuri re?` = Nereye gidiyorsun?

5. **Çoğul:** isme `-li`. `iyo` → `iyoli` (arkadaşlar). Sayıdan sonra çoğul eki **yok**: `duve iyo` (iki arkadaş).
   Zamirlerde de aynı: `ni`→`nili` (biz), `su`→`suli` (siz), `ha`→`hali` (onlar).
   `nasu` özel bir zamir: **"sen ve ben"** (kapsayıcı biz).

6. **Sıfat isimden sonra gelir:** `kasi mora` = büyük ev. Sıfatı yüklem yapmak için özneyi ayrı yaz: `ha mora` = o büyük (zamirden sonra sıfat yüklemdir). İsimlerle "olmak" için `ye` kullan: `ha ye iyo` = o bir arkadaş.
   - Aitlik: sahip olunan + `de` + sahip: `kasi de ni` = benim evim.

7. **Edatlar isimden önce:** `to` (-e), `ka` (-de), `ma` (ile), `pe` (için).
   `ni kam to ayu` = suya gidiyorum.

8. **Cümle sonu parçacıkları:**
   - `zo` = "bu sadece aramızda" (fısıltı/sır tonu). `ni lov ha zo.`
   - `yo` = emir. `vem yo!` = gel!
   - `re` = soru (yukarıda).
   Cümle sonunda en fazla biri durur.

### Türetme (yeni kelimeyi ücretsiz üretme)

| Ek | Anlam | Örnek |
|---|---|---|
| `-a` | fiilden isim | `moş` yemek → `moşa` yemek (yiyecek); `lov` sevmek → `lova` aşk; `kon` konuşmak → `kona` söz (bu zaten sözlükte) |
| `-ru` | yapan kişi | `kon` → `konru` konuşan; `mako` → `makoru` işçi |
| `-ki` | küçültme | `kasi` → `kasiki` kulübe; `iyo` → `iyoki` ufaklık, tatlı dost |
| `-mo` | büyütme | `ayu` → `ayumo` nehir/büyük su |
| `ge-` | zıt anlam | `sufa` → `gesufa` kötü; `vol` → `gevol` istememek, nefret etmek; `nuvi` → `genuvi` eski gibi (ama `reba` zaten var, kısa yol seçimi sizde) |

Bunlar *önerilen* kalıplar. Çevre içinde alışkanlık oluştukça "resmî" kelimeler olur.

---

## 3. Örnek cümleler

| Tavi | Kelime kelime | Türkçe |
|---|---|---|
| `Ni lov su.` | ben sevmek sen | Seni seviyorum. |
| `Su sa moş re?` | sen -iyor yemek ? | Yiyor musun? |
| `Ni ta ten ti kasi.` | ben geçmiş görmek bu ev | Bu evi gördüm. |
| `Nasu ne kam to ayu zo.` | biz(sen+ben) gelecek gitmek -e su (sır) | Sen ve ben suya gideriz, aramızda kalsın. |
| `Iyoli de ni ko vem.` | arkadaş-lar -in ben değil gelmek | Arkadaşlarım gelmiyor. |
| `Su vol kuno re?` | sen istemek ne ? | Ne istiyorsun? |
| `Ha ye iyo sufa de ni.` | o olmak arkadaş iyi -in ben | O benim iyi arkadaşım. |
| `Ni yoku. Ni vol sin.` | ben yorgun. ben istemek uyumak. | Yorgunum. Uyumak istiyorum. |
| `Dama, tavi.` | teşekkürler, çevre | Sağ ol, ekibim. |
| `Vem yo, ni sa ruş su!` | gel!, ben -iyor beklemek sen | Gel, seni bekliyorum! |

## 4. Sayılar

0 `zira` · 1 `ika` · 2 `duve` · 3 `teri` · 4 `boti` · 5 `pema` · 6 `şeko` · 7 `sebi` · 8 `ahu` · 9 `zeno` · 10 `desa`
11–19: `desa` + sayı → `desa ika` (11). Yirmi: `duve desa`. 21: `duve desa ika`.

## 5. Dosyalar

- `sozluk.csv`: tüm sözlük (kelime, tür, Türkçe, İngilizce). Sonraki aşamada uygulama doğrudan bundan beslenir.
- `kontrol.py`: sözlük doğrulama betiği. Hece kuralına uymayan, yinelenen ve konuşmada karışabilecek kadar benzer kelimeleri bulur. `python3 dil/kontrol.py`

## 6. Sıradaki adımlar (öneri)

1. Çevrenle ortak kararlar: ad, ses tonu, hangi konuşma konuları (sözlük genişlemesi).
2. Sözlüğü ~300 kelimeye çıkar. Önce sizin ortak sohbet konularından (şakalar, ortak anılar, yer adları).
3. Telaffuz uygulaması: yazım zaten fonetik olduğundan kelimeden sese çevirmek kolay, basit bir kural tablosu yeter. Kayıtlı ses de eklenebilir.
4. Alıştırma modülü (flashcard, "cümle çevir").
