# Statrys video analizi — görev tanımı

Statrys YouTube kanalındaki (Hong Kong merkezli bir fintech; son aylarda Asya/Çin ekonomisi üzerine viral, tık odaklı başlıklı videolar) videoların İngilizce otomatik altyazı transkriptlerini analiz ediyorsun. Kullanıcı Türk bir girişimci: her video için **Türkçe** özet, **transkripte dayalı** grafik verisi, şema ve iş fikirleri istiyor. Kullanıcı şüpheci, doğruluğa önem veriyor.

Transkriptler: `research/statrys/yt/txt/<tarih>_<videoId>.txt` (otomatik altyazı: noktalama yok, sayılar ve özel isimler yanlış yazılmış olabilir).
Meta veriler: `research/statrys/meta/index.json`.

Her video için **`research/statrys/analysis/<videoId>.json`** dosyası yaz (UTF-8, geçerli JSON — yazdıktan sonra `python3 -c "import json;json.load(open(PATH))"` ile doğrula). Metinler Türkçe olsun; şirket/ülke adları özgün haliyle.

```json
{
  "id": "videoId",
  "title_tr": "Başlığın sade, abartısız Türkçe karşılığı (emoji/clickbait yok)",
  "category": "Şu listeden biri: Çin ekonomisi | Güneydoğu Asya | Doğu Asya (Japonya/Kore) | Küresel finans & bankacılık | Şirket vakası | Teknoloji & YZ | Jeopolitik & ticaret | Şirket kurma & vergi (Statrys hizmet videoları)",
  "region": ["Çin", "..."],
  "summary_tr": "3-5 cümlelik özet: videonun ana tezi ve dayanakları.",
  "key_points": ["4-6 madde, her biri tek cümle"],
  "numbers": [{"label": "kısa etiket", "value": 123.4, "unit": "milyar $ | % | kişi | ...", "context": "videoda bu sayı ne için söylendi"}],
  "chart": {
    "type": "bar | hbar | pie | line",
    "title": "Grafik başlığı",
    "unit": "birim",
    "source_note": "Videoda belirtilen rakamlar; bağımsız doğrulanmadı",
    "data": [{"label": "...", "value": 0}]
  },
  "diagram": {
    "type": "flow | cycle",
    "title": "Şema başlığı (ör. 'Sorunun neden-sonuç zinciri' veya 'İş modelinin işleyişi')",
    "steps": [{"label": "kısa düğüm adı (≤5 kelime)", "detail": "1 cümle açıklama"}]
  },
  "business_ideas": [{
    "name": "Fikrin adı",
    "insight": "Videodaki hangi gözlemden çıkıyor (1 cümle)",
    "problem": "...",
    "solution": "...",
    "target": "Hedef müşteri",
    "revenue_model": "Gelir modeli",
    "first_step": "İlk 30 günde somut olarak yapılacak doğrulama adımı",
    "capital": "Düşük | Orta | Yüksek",
    "difficulty": 1,
    "turkey_angle": "Türkiye'den yürütülebilirlik / Türkiye'ye uyarlama notu",
    "risks": "Ana risk(ler)",
    "originality": 3,
    "problem_solving": 4,
    "score_note": "Tek cümle: puanın gerekçesi; piyasada benzeri var mı, sorun ne kadar gerçek",
    "similar": [{"id": "benzer fikrin bulunduğu videoId", "name": "benzer fikrin adı (analysis/ içindeki yazımıyla)"}]
  }],
  "skeptic_notes": ["Videodaki şüpheli, abartılı, tek kaynaklı veya doğrulanması gereken iddialar; altyazı kaynaklı olası sayı hataları"]
}
```

Kurallar:
1. **Sayılar yalnızca transkriptten.** `numbers` ve `chart.data` içine sadece videoda gerçekten söylenen rakamları koy; uydurma, tahmin etme, dış kaynaktan ekleme. Bir sayı altyazı hatası gibi görünüyorsa (ör. "1,110" yerine "11 10") en makul okumayı kullan ve `skeptic_notes`'a yaz. Grafik için anlamlı, aynı birimde ve karşılaştırılabilir en az 3 değer bul (karşılaştırma, zaman serisi, pay dağılımı). Yeterli veri yoksa `"chart": null`. Pie grafiği yalnızca parçaların bir bütünü oluşturduğu durumda kullan.
2. **Şema** 4-7 adım olsun ve videonun anlattığı mekanizmayı (neden-sonuç zinciri, iş modeli, para akışı, süreç adımları) göstersin.
3. **İş fikirleri**: video başına 2-3 fikir; spesifik, uygulanabilir, videonun içgörüsüne bağlı olsun. Jenerik ("danışmanlık yap", "e-ticaret sitesi aç") olmasın. Videoda anlatılan dolandırıcılık, yasa dışı veya etik dışı yöntemleri taklit eden fikir önerme; bu tür videolarda karşı tarafı çözen (tespit, uyum, koruma) fikirler öner. Vergi videolarında yasal sınırları ve uyum gerekliliklerini `risks` içinde belirt.
4. **Telif:** Transkriptten uzun alıntı yapma; tümüyle kendi cümlelerinle özetle. Gerekirse en fazla birkaç kelimelik alıntı.
5. Statrys'in kendi hizmetlerini tanıtan videolarda bunu `skeptic_notes`'ta belirt (reklam içeriği).

## Puanlama cetveli (her fikir için zorunlu)

Puanı fikri yazdıktan sonra, eleştirel bir yatırımcı gözüyle ver. Cömert olma: 5'ler istisnadır, çoğu fikir 2-3 alır.

**`originality` — özgünlük (1-5)**
- 5: Piyasada bilinen bir karşılığı yok; videodaki içgörüden çıkan, belirli bir mekanizma + müşteri birleşimi.
- 4: Bilinen bir kategorinin alışılmadık bir nişe veya mekanizmaya uyarlanması; Türkiye'de örneği yok.
- 3: Var olan bir ürün kategorisinin yeni bir pazara/dile taşınması; farklılaşma yalnızca açı düzeyinde.
- 2: Yaygın bir hizmet türü (danışmanlık, eşleştirme, bülten, ithalat aracılığı, karşılaştırma sitesi) küçük bir dokunuşla.
- 1: Jenerik; zaten çok sayıda sağlayıcı var ("X için danışmanlık", "Y bülteni", "Z'nin Türkçesi").

**`problem_solving` — sorun çözme derecesi (1-5)**
- 5: Ölçülebilir ve sık yaşanan bir kaybı/riski doğrudan ortadan kaldırıyor; alıcı belli ve bütçesi var.
- 4: Gerçek ve sık bir sorun; çözüm somut fayda sağlıyor ama kısmi (karar desteği, maliyet azaltma) ya da alıcı dar.
- 3: Sorun gerçek ama çözüm bilgi/farkındalık düzeyinde veya dolaylı; etkisi ölçülemez.
- 2: Sorun varsayımsal ya da "iyi olurdu" düzeyinde; ödeyen belirsiz.
- 1: Sorun yok denecek kadar zayıf ya da çözüm sorunla bağlantısız.

**Çapa örnekler (mevcut dosyalardan):** "Banka profili kayma alarmı" 5/4 · "Kamu altyapı projeleri için uydu görüntüsüyle ilerleme doğrulama" 5/4 · "KOBİ'ler için ödeme talimatı doğrulama (IBAN-unvan eşleştirme) aracı" 3/5 · "Türkçe KOBİ müşteri destek ajanı kurulum paketi" 1/4 · "Yen bazlı Japonya seyahat ve alışveriş paketleri" 1/1.

**Benzerlik kuralı:** Yeni fikri yazmadan önce `analysis/*.json` içindeki mevcut fikir adlarına bak (`grep -h '"name"' analysis/*.json`). Aynı sorunu aynı mekanizmayla çözen fikir varsa `similar` alanına yaz ve özgünlüğü en fazla 3 ver; mümkünse aynı fikri tekrar üretmek yerine videonun farklı bir içgörüsünden yeni bir fikir çıkar.

Bittiğinde kısa bir rapor dön: işlenen video ID'leri, `chart: null` olanlar ve dikkat çeken sorunlar. JSON içeriğini rapora tekrar yazma.
