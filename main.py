"""
============================================================
 SKY GUIDE API - Gelismis Gokyuzu Gozlem Karar Destek
============================================================
 Open-Meteo'dan gercek meteoroloji verisi ceker ve gokyuzu
 gozlemi icin anlamli cikarimlar uretir:
   - Gozlem uygunluk skoru (0-100)
   - Gozlem durumu (Uygun / Orta / Uygun Degil)
   - Bulut, nem, ruzgar, gorus mesafesi degerlendirmesi
   - Sis/ciy riski
   - Isik kirliligi tahmini (konuma gore)
   - Ay evresi ve ay parlakligi etkisi

 DOSYA ADI: main.py  (Render: uvicorn main:app --host 0.0.0.0 --port $PORT)
 Kurulum:  pip install fastapi uvicorn requests
============================================================
"""

from fastapi import FastAPI
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
import math
import time

app = FastAPI(title="Sky Guide API", version="2.2")

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 10  # saniye

# ============================================================
#  ONBELLEK (CACHE) - 429 Too Many Requests'i onler
#  Open-Meteo'dan cekilen veri belirli sure saklanir, her
#  kart istegi Open-Meteo'ya gitmez. Boylece limit asilmaz.
# ============================================================
_cache = {}                      # {(lat,lon): (zaman, veri)}
CACHE_SURE = 300                 # 5 dakika (saniye)

def _cache_anahtar(lat, lon):
    # Konumu yuvarla ki yakin istekler ayni cache'i kullansin
    return (round(lat, 2), round(lon, 2))



# ============================================================
#  YARDIMCI: sozlukten guvenli sayi oku
#  Alan yoksa veya None ise varsayilan doner (COKME YOK)
# ============================================================
def gnum(d: dict, anahtar, varsayilan):
    deger = d.get(anahtar)
    return deger if deger is not None else varsayilan


# ============================================================
#  YARDIMCI: Open-Meteo'dan veri cek (HATA KORUMALI)
# ============================================================
def hava_verisi_cek(lat: float, lon: float) -> dict:
    """
    Open-Meteo'dan veri ceker AMA once onbellege bakar.
    - Onbellekte taze veri varsa (5 dk) onu doner, Open-Meteo'ya
      GITMEZ. Bu 429 Too Many Requests hatasini onler.
    - Istek basarisiz olursa (429 dahil) elindeki eski veriyi
      doner (varsa). Boylece kart hep veri gorur.
    """
    anahtar = _cache_anahtar(lat, lon)
    simdi = time.time()

    # 1) Onbellekte taze veri var mi?
    if anahtar in _cache:
        zaman, veri = _cache[anahtar]
        if simdi - zaman < CACHE_SURE:
            return veri   # taze, Open-Meteo'ya gitme

    params = {
        "latitude": lat,
        "longitude": lon,
        "current": ",".join([
            "temperature_2m",
            "relative_humidity_2m",
            "dew_point_2m",
            "surface_pressure",
            "cloud_cover",
            "cloud_cover_low",
            "cloud_cover_mid",
            "cloud_cover_high",
            "wind_speed_10m",
            "visibility",
            "weather_code",
        ]),
        "timezone": "Europe/Istanbul",
    }
    try:
        r = requests.get(OPEN_METEO, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        veri = data.get("current", {}) or {}
        _cache[anahtar] = (simdi, veri)   # onbellege kaydet
        return veri
    except Exception as e:
        print("Open-Meteo hatasi:", e)
        # Hata olsa bile (429 vs.) elimizde eski veri varsa onu don
        if anahtar in _cache:
            print("Onbellekteki eski veri donduruldu.")
            return _cache[anahtar][1]
        return {}


# ============================================================
#  AY EVRESI HESABI (ay parlakligi gozlem icin onemli)
# ============================================================
def ay_evresi(tarih: datetime) -> dict:
    """
    Basit ay evresi hesabi. Ayin aydinlatma yuzdesini doner.
    Dolunay gokyuzunu aydinlatir -> gozlem icin kotu.
    Yeni ay -> en karanlik gokyuzu -> gozlem icin iyi.
    """
    bilinen_yeniay = datetime(2000, 1, 6, 18, 14, tzinfo=ZoneInfo("UTC"))
    sinodik = 29.530588853  # bir ay dongusu (gun)

    simdi_utc = tarih.astimezone(ZoneInfo("UTC"))
    gecen_gun = (simdi_utc - bilinen_yeniay).total_seconds() / 86400.0
    evre = (gecen_gun % sinodik) / sinodik  # 0=yeni ay, 0.5=dolunay

    aydinlatma = round((1 - math.cos(2 * math.pi * evre)) / 2 * 100, 1)

    if evre < 0.03 or evre > 0.97:
        isim = "Yeni Ay"
    elif evre < 0.22:
        isim = "Hilal (buyuyen)"
    elif evre < 0.28:
        isim = "Ilk Dordun"
    elif evre < 0.47:
        isim = "Sisman Ay (buyuyen)"
    elif evre < 0.53:
        isim = "Dolunay"
    elif evre < 0.72:
        isim = "Sisman Ay (kuculen)"
    elif evre < 0.78:
        isim = "Son Dordun"
    else:
        isim = "Hilal (kuculen)"

    return {"evre_ismi": isim, "aydinlatma_yuzde": aydinlatma}


# ============================================================
#  ISIK KIRLILIGI TAHMINI
# ============================================================
def isik_kirliligi_tahmin(lat: float, lon: float) -> float:
    """
    Konum bazli kaba isik kirliligi tahmini. Buyuk sehir
    merkezlerine yakinlik arttikca isik kirliligi artar.
    Donen deger: 0 (karanlik) - 100 (cok kirli).
    """
    sehirler = [
        (41.0082, 28.9784, 95),  # Istanbul
        (39.9334, 32.8597, 85),  # Ankara
        (38.4237, 27.1428, 80),  # Izmir
        (40.1885, 29.0610, 70),  # Bursa
        (36.8969, 30.7133, 65),  # Antalya
        (37.0000, 35.3213, 65),  # Adana
        (40.3167, 36.5500, 45),  # Tokat
    ]

    en_yakin_etki = 15.0  # taban (kirsal arka plan isigi)
    for s_lat, s_lon, siddet in sehirler:
        mesafe = math.sqrt((lat - s_lat) ** 2 + (lon - s_lon) ** 2)
        etki = siddet * math.exp(-mesafe * 3.5)
        en_yakin_etki = max(en_yakin_etki, etki)

    return round(min(en_yakin_etki, 100), 1)


# ============================================================
#  GOZLEM UYGUNLUK SKORU
# ============================================================
def gozlem_skoru_hesapla(hava: dict, isik: float, ay: dict) -> dict:
    """
    0-100 arasi gozlem uygunluk skoru. Her faktor agirlikli.
    En kritik faktor BULUT ORTUSU'dur. Veri eksikse guvenli
    varsayilanlar kullanilir (cokme olmaz).
    """
    # Guvenli okuma: alan yoksa "gozlem icin kotu" varsayilan
    bulut       = gnum(hava, "cloud_cover", 100)
    nem         = gnum(hava, "relative_humidity_2m", 100)
    ruzgar      = gnum(hava, "wind_speed_10m", 0)
    gorus       = gnum(hava, "visibility", 0)
    sicaklik    = gnum(hava, "temperature_2m", 0)
    ciy_noktasi = gnum(hava, "dew_point_2m", sicaklik)

    # 1) Bulut ortusu (agirlik %40)
    bulut_puan = (100 - bulut) / 100 * 40
    # 2) Isik kirliligi (agirlik %20)
    isik_puan = (100 - isik) / 100 * 20
    # 3) Ay aydinlatmasi (agirlik %15)
    ay_puan = (100 - ay["aydinlatma_yuzde"]) / 100 * 15
    # 4) Nem (agirlik %10)
    nem_puan = (100 - nem) / 100 * 10
    # 5) Gorus mesafesi (agirlik %10)
    gorus_puan = min(gorus / 24000, 1.0) * 10 if gorus else 0
    # 6) Ruzgar (agirlik %5)
    if ruzgar <= 15:
        ruzgar_puan = 5
    elif ruzgar <= 30:
        ruzgar_puan = 3
    else:
        ruzgar_puan = 1

    toplam = bulut_puan + isik_puan + ay_puan + nem_puan + gorus_puan + ruzgar_puan
    toplam = round(max(0, min(toplam, 100)), 1)

    if toplam >= 70:
        durum = "Uygun"
    elif toplam >= 40:
        durum = "Orta"
    else:
        durum = "Uygun Degil"

    # Sis/ciy riski
    fark = sicaklik - ciy_noktasi
    if fark <= 1.5:
        sis_riski = "Yuksek"
    elif fark <= 4:
        sis_riski = "Orta"
    else:
        sis_riski = "Dusuk"

    # Uyarilar (anlamli geri bildirim)
    uyarilar = []
    if bulut > 60:
        uyarilar.append("Yogun bulut ortusu, gozlem zor.")
    elif bulut > 30:
        uyarilar.append("Kismi bulutluluk var.")
    if ay["aydinlatma_yuzde"] > 70:
        uyarilar.append("Ay cok parlak, sonuk cisimler zor gorunur.")
    if sis_riski == "Yuksek":
        uyarilar.append("Sis/ciy riski yuksek, lens buharlanabilir.")
    if isik > 70:
        uyarilar.append("Isik kirliligi yuksek, sehir disina cikin.")
    if ruzgar > 30:
        uyarilar.append("Kuvvetli ruzgar, teleskop titreyebilir.")
    if not uyarilar:
        uyarilar.append("Kosullar gozlem icin elverisli.")

    return {
        "skor": toplam,
        "durum": durum,
        "sis_ciy_riski": sis_riski,
        "uyarilar": uyarilar,
        "puan_dagilimi": {
            "bulut": round(bulut_puan, 1),
            "isik_kirliligi": round(isik_puan, 1),
            "ay": round(ay_puan, 1),
            "nem": round(nem_puan, 1),
            "gorus": round(gorus_puan, 1),
            "ruzgar": round(ruzgar_puan, 1),
        },
    }


# ============================================================
#  ENDPOINT: ANA SAYFA
# ============================================================
@app.get("/")
def home():
    return {
        "message": "Sky Guide API v2.2 calisiyor",
        "kullanim": "/data?lat=40.313&lon=36.553",
    }


# ============================================================
#  ENDPOINT: VERI (Deneyap kart bunu cagiriyor)
#  ASLA COKMEZ - veri gelmezse -1 doner ama 200 verir.
# ============================================================
@app.get("/data")
def get_data(lat: float, lon: float):
    hava = hava_verisi_cek(lat, lon)   # hata olsa bile bos dict doner
    simdi = datetime.now(ZoneInfo("Europe/Istanbul"))
    ay = ay_evresi(simdi)
    isik = isik_kirliligi_tahmin(lat, lon)
    skor_bilgi = gozlem_skoru_hesapla(hava, isik, ay)

    return {
        "time": simdi.strftime("%H:%M:%S"),
        "temperature": gnum(hava, "temperature_2m", -1),
        "pressure": gnum(hava, "surface_pressure", -1),
        "humidity": gnum(hava, "relative_humidity_2m", -1),
        "cloud_cover": gnum(hava, "cloud_cover", -1),
        "wind_speed": gnum(hava, "wind_speed_10m", -1),
        "light_pollution": isik,
        "moon_illumination": ay["aydinlatma_yuzde"],
        "score": skor_bilgi["skor"],
        "status": skor_bilgi["durum"],
        "fog_risk": skor_bilgi["sis_ciy_riski"],
    }


# ============================================================
#  ENDPOINT: DETAY (web/uygulama icin tam analiz)
# ============================================================
@app.get("/detail")
def get_detail(lat: float, lon: float):
    hava = hava_verisi_cek(lat, lon)
    simdi = datetime.now(ZoneInfo("Europe/Istanbul"))
    ay = ay_evresi(simdi)
    isik = isik_kirliligi_tahmin(lat, lon)
    skor_bilgi = gozlem_skoru_hesapla(hava, isik, ay)

    return {
        "zaman": simdi.strftime("%Y-%m-%d %H:%M:%S"),
        "konum": {"lat": lat, "lon": lon},
        "veri_alindi": bool(hava),   # False ise Open-Meteo'dan veri gelmedi
        "hava": {
            "sicaklik": gnum(hava, "temperature_2m", None),
            "nem": gnum(hava, "relative_humidity_2m", None),
            "ciy_noktasi": gnum(hava, "dew_point_2m", None),
            "basinc": gnum(hava, "surface_pressure", None),
            "bulut_toplam": gnum(hava, "cloud_cover", None),
            "bulut_alcak": gnum(hava, "cloud_cover_low", None),
            "bulut_orta": gnum(hava, "cloud_cover_mid", None),
            "bulut_yuksek": gnum(hava, "cloud_cover_high", None),
            "ruzgar_hizi": gnum(hava, "wind_speed_10m", None),
            "gorus_mesafesi_m": gnum(hava, "visibility", None),
        },
        "ay": ay,
        "isik_kirliligi": isik,
        "gozlem": skor_bilgi,
    }
