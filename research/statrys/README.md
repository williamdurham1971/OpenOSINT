# Statrys Fikir Defteri

[@Statrys](https://www.youtube.com/@Statrys) YouTube kanalındaki videoların İngilizce altyazılarından Türkçe özet, transkripte dayalı grafik, şema ve iş fikri üreten küçük bir hat.

- **Yayımlanan sayfa:** https://claude.ai/artifact/22mbhumKX9398w8ixKPHaL (özel; sahibi paylaşmadıkça yalnızca sahibi açabilir)
- **Kapsam:** 10.11.2025'ten itibaren yüklenen videolar. Yeni videolar eklenir, eskiler silinmez.
- **Transkriptler depoya konmaz.** Telifli içerik oldukları için `yt/` klasörü `.gitignore` ile dışarıda bırakılmıştır. Depoda yalnızca meta veri (`meta/index.json`) ve özgün Türkçe analizler (`analysis/*.json`) bulunur.

## Dosyalar

| Dosya | Görev |
|---|---|
| `update.py` | Kanalda yeni video var mı bakar, varsa altyazısını indirip `yt/txt/` altına düz metin yazar, `meta/index.json`'ı ve izlenme sayılarını günceller. Analiz bekleyen ID'leri JSON olarak basar. |
| `BRIEF.md` | Her video için yazılacak `analysis/<id>.json` dosyasının şeması ve kuralları (sayılar yalnızca transkriptten, telif, etik sınırlar). |
| `validate.py` | `analysis/*.json` dosyalarını şemaya göre doğrular. |
| `ground.py` | Analizlerdeki sayıların transkriptte geçip geçmediğini kaba biçimde denetler (yalnızca indirilmiş transkripti olan videolar için). |
| `ctx.py` | `python3 ctx.py <videoId> "<regex>"` ile bir sayının transkriptteki bağlamını gösterir. |
| `build_data.py` | `meta/index.json` + `analysis/` → `site/data.json`. |
| `site/index.html` | Yayımlanan sayfa; `data.json`'ı yanından okur. |

## Güncelleme adımları (zamanlanmış görev bunları izler)

Tüm komutlar `research/statrys/` içinden çalıştırılır.

1. `pip install -q yt-dlp` (gerekirse).
2. `python3 update.py` → çıktıdaki `pending_analysis` listesine bak.
   - Liste boşsa ve `new` da boşsa: değişiklik yok, dur. Sayfayı yalnızca izlenme sayısı için yeniden yayımlama.
   - `no_captions` içindeki videolar altyazısız; analiz edilmez, kullanıcıya bildirilir.
3. `pending_analysis` içindeki her video için `BRIEF.md`'yi harfiyen uygulayarak `analysis/<id>.json` yaz.
4. `python3 validate.py analysis` → "all valid" olmalı.
5. `python3 ground.py <yeni ID'ler>` → bulunamayan her değeri `ctx.py` ile kontrol et; transkriptte karşılığı yoksa değeri sil ya da düzelt.
6. `python3 build_data.py meta/index.json analysis site/data.json`
7. Sayfayı aynı adrese yeniden yayımla: Artifact aracıyla önce `action: "read"` + yukarıdaki `url`, sonra `site/index.html` dosyasını aynı `url` ile ve `files: {"data.json": "research/statrys/site/data.json"}` ile yayımla. `icon` verme.
8. `meta/`, `analysis/`, `site/` değişikliklerini commit'le ve çalışma dalına push'la.
