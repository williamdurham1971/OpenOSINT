# SEC 10-K risk faktörü farkı (Item 1A)

Bir şirketin son iki yıllık raporunda (10-K) "Risk Factors" bölümünün ne değiştiğini bulur.
Metin farkını kod hesaplar; Claude yalnızca değişen paragrafları yorumlar ve önem puanı verir.

OpenOSINT'ten bağımsız, tek dosyalık bir araçtır.

## Kurulum

```bash
cd scripts/sec_risk_diff
pip install -r requirements.txt
export SEC_USER_AGENT="Ad Soyad eposta@ornek.com"   # SEC bunu şart koşar, kendi bilginizi yazın
export ANTHROPIC_API_KEY=...                         # sohbete/koda yapıştırmayın
```

## Kullanım

```bash
python sec_risk_diff.py AAPL --no-llm   # önce sadece farkı görün (ücretsiz)
python sec_risk_diff.py AAPL            # sonra değişiklikleri Claude'a puanlatın
python sec_risk_diff.py AAPL --lang English --model claude-haiku-5-5
python sec_risk_diff.py --files eski.htm yeni.htm --no-llm   # yerel dosyalarla
python -m unittest test_sec_risk_diff.py                     # çevrimdışı testler
```

Çıktı: `AAPL_risk_diff.md` (rapor) ve `AAPL_risk_diff.json` (ham veri).

## Nasıl çalışır

1. Ticker'dan CIK bulunur, EDGAR'dan son iki 10-K indirilir (`cache/` klasöründe saklanır, saniyede en fazla 5 istek).
2. HTML düz paragraflara çevrilir; gizli XBRL verisi ve sayfa numaraları atılır.
3. Item 1A bölümü çıkarılır (içindekiler tablosundaki satır atlanır).
4. Paragraflar eşleştirilir: değişmedi / küçük (yalnızca sayı ve tarih) / değişti / yeni / kaldırıldı.
5. Yalnızca "değişti", "yeni" ve "kaldırıldı" olanlar modele gider. Model her biri için kategori, 1-5 önem puanı, özet ve metinden alıntı döndürür.
6. Alıntı gerçekten dosyada geçiyor mu, kod kontrol eder; geçmiyorsa raporda işaretlenir.

## Maliyet (tahmin, ölçülmedi)

Tipik bir Item 1A 15-25 bin token. Yıldan yıla değişen kısım çoğu zaman bunun küçük bir bölümü.
Sonnet 5.5 ile ($2 / $10 per 1M token, düşünme token'ları çıktıya dahil) şirket başına
kabaca $0,05-0,20 bekliyorum. Gerçek maliyet raporda `usage` satırında yazar.
`--no-llm` hiç API maliyeti çıkarmaz.

## Sınırlar

- **Gerçek EDGAR verisiyle denenmedi.** Testler sentetik HTML ile çalışıyor. İlk olarak 2-3 şirkette
  `--no-llm` çalıştırıp paragraf sayılarının mantıklı olduğunu kontrol edin. Bazı 10-K'lar
  bölümü farklı başlıkla yazar ya da başka belgeye atıf yapar; o zaman "Extraction failed" hatası alırsınız.
- Bölünen veya birleştirilen paragraflar "yeni + kaldırıldı" olarak görünebilir.
- Model değerlendirmeleri yanlış olabilir. Önem puanı bir yorumdur, ölçüm değil.
- Geriye dönük test tuzağı: model geçmiş olayları biliyor. Bu araçla "hangi değişiklik düşüşü
  öngördü" testi yaparsanız sonuç şişer. Gerçek sınama ileriye dönük takiptir.
- Yatırım tavsiyesi değildir. Çıktıyı başkalarına "al/sat" önerisi olarak sunmak düzenlemeye tabi olabilir.
