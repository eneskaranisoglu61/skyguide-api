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

 Kurulum:  pip install fastapi uvicorn requests
 Calistir: uvicorn skyguide_api:app --host 0.0.0.0 --port 8000
============================================================
"""

from fastapi import FastAPI, HTTPException
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
import math

app = FastAPI(title="Sky Guide API", version="2.0")

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 10  # saniye


# ============================================================
#  YARDIMCI: Open-Meteo'dan veri cek
# ============================================================
def hava_verisi_cek(lat: float, lon: float) -> dict:
    """Open-Meteo'dan gozlem icin gerekli tum degiskenleri ceker."""
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
    r = requests.get(OPEN_METEO, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("current", {})


# ============================================================
#  AY EVRESI HESABI (ay parlakligi gozlem icin onemli)
# ============================================================
def ay_evresi(tarih: datetime) -> dict:
    """
    Basit ay evresi hesabi. Ayin aydinlatma yuzdesini doner.
    Dolunay gokyuzunu aydinlatir -> gozlem icin kotu.
    Yeni ay -> en karanlik gokyuzu -> gozlem icin iyi.
    """
    # Bilinen bir yeni ay tarihi (referans): 2000-01-06
    bilinen_yeniay = datetime(2000, 1, 6, 18, 14, tzinfo=ZoneInfo("UTC"))
    sinodik = 29.530588853  # bir ay dongusu (gun)

    simdi_utc = tarih.astimezone(ZoneInfo("UTC"))
    gecen_gun = (simdi_utc - bilinen_yeniay).total_seconds() / 86400.0
    evre = (gecen_gun % sinodik) / sinodik  # 0=yeni ay, 0.5=dolunay

    # Aydinlatma yuzdesi (0=karanlik, 100=dolunay)
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
    Gercek isik kirliligi haritasi (VIIRS) ucretli/agir oldugu icin,
    burada konum bazli kaba bir tahmin yapiyoruz. Buyuk sehir
    merkezlerine yakinlik arttikca isik kirliligi artar.
    Gercek projede VIIRS verisi entegre edilebilir.
    Donen deger: 0 (karanlik) - 100 (cok kirli) arasi tahmin.
    """
    # Turkiye'nin birkac buyuk sehrinin koordinati (genisletilebilir)
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
        # Mesafe arttikca etki azalir (ust uste binebilir, max alinir)
        etki = siddet * math.exp(-mesafe * 3.5)
        en_yakin_etki = max(en_yakin_etki, etki)

    return round(min(en_yakin_etki, 100), 1)


# ============================================================
#  GOZLEM UYGUNLUK SKORU
# ============================================================
def gozlem_skoru_hesapla(hava: dict, isik: float, ay: dict) -> dict:
    """
    0-100 arasi gozlem uygunluk skoru hesaplar.
    Her faktor agirlikli olarak skora katkida bulunur.
    En kritik faktor BULUT ORTUSU'dur.
    """
    bulut = hava.get("cloud_cover", 100)          # %
    bulut_yuksek = hava.get("cloud_cover_high", 0) # ince yuksek bulut
    nem = hava.get("relative_humidity_2m", 100)    # %
    ruzgar = hava.get("wind_speed_10m", 0)         # km/s
    gorus = hava.get("visibility", 0)              # metre
    sicaklik = hava.get("temperature_2m", 0)
    ciy_noktasi = hava.get("dew_point_2m", sicaklik)

    # --- 1) Bulut ortusu (en onemli, agirlik %40) ---
    # %0 bulut = 40 puan, %100 bulut = 0 puan
    bulut_puan = (100 - bulut) / 100 * 40

    # --- 2) Isik kirliligi (agirlik %20) ---
    # Dusuk kirlilik = yuksek puan
    isik_puan = (100 - isik) / 100 * 20

    # --- 3) Ay aydinlatmasi (agirlik %15) ---
    # Yeni ay (karanlik) = iyi, dolunay = kotu
    ay_puan = (100 - ay["aydinlatma_yuzde"]) / 100 * 15

    # --- 4) Nem (agirlik %10) ---
    # Yuksek nem optik bozulma + ciy/sis riski
    nem_puan = (100 - nem) / 100 * 10

    # --- 5) Gorus mesafesi (agirlik %10) ---
    # 24000 m ve uzeri mukemmel
    gorus_puan = min(gorus / 24000, 1.0) * 10

    # --- 6) Ruzgar (agirlik %5) ---
    # Hafif ruzgar iyi (turbulans az), cok kuvvetli ruzgar teleskobu titretir
    if ruzgar <= 15:
        ruzgar_puan = 5
    elif ruzgar <= 30:
        ruzgar_puan = 3
    else:
        ruzgar_puan = 1

    toplam = bulut_puan + isik_puan + ay_puan + nem_puan + gorus_puan + ruzgar_puan
    toplam = round(max(0, min(toplam, 100)), 1)

    # --- Durum siniflandirmasi ---
    if toplam >= 70:
        durum = "Uygun"
    elif toplam >= 40:
        durum = "Orta"
    else:
        durum = "Uygun Degil"

    # --- Sis/ciy riski (sicaklik ciy noktasina yakinsa) ---
    fark = sicaklik - ciy_noktasi
    if fark <= 1.5:
        sis_riski = "Yuksek"
    elif fark <= 4:
        sis_riski = "Orta"
    else:
        sis_riski = "Dusuk"

    # --- Uyarilar (kullaniciya anlamli geri bildirim) ---
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
        "message": "Sky Guide API v2 calisiyor",
        "kullanim": "/data?lat=40.313&lon=36.553",
    }


# ============================================================
#  ENDPOINT: VERI (Deneyap kart bunu cagiriyor)
# ============================================================
@app.get("/data")
def get_data(lat: float, lon: float):
    """
    Deneyap kart icin SADELESTIRILMIS cikti.
    Kart ekraninda gostermek icin gerekli alanlari doner.
    """
    try:
        hava = hava_verisi_cek(lat, lon)
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Hava verisi alinamadi: {e}")

    simdi = datetime.now(ZoneInfo("Europe/Istanbul"))
    ay = ay_evresi(simdi)
    isik = isik_kirliligi_tahmin(lat, lon)
    skor_bilgi = gozlem_skoru_hesapla(hava, isik, ay)

    # Deneyap kartin kolay parse edebilecegi DUZ yapida cevap
    return {
        "time": simdi.strftime("%H:%M:%S"),
        "temperature": hava.get("temperature_2m", -1),
        "pressure": hava.get("surface_pressure", -1),
        "humidity": hava.get("relative_humidity_2m", -1),
        "cloud_cover": hava.get("cloud_cover", -1),
        "wind_speed": hava.get("wind_speed_10m", -1),
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
    """
    Tum cikarimlari iceren DETAYLI cevap. Web arayuzu veya
    detayli analiz icin. Deneyap kart /data'yi kullanir.
    """
    try:
        hava = hava_verisi_cek(lat, lon)
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Hava verisi alinamadi: {e}")

    simdi = datetime.now(ZoneInfo("Europe/Istanbul"))
    ay = ay_evresi(simdi)
    isik = isik_kirliligi_tahmin(lat, lon)
    skor_bilgi = gozlem_skoru_hesapla(hava, isik, ay)

    return {
        "zaman": simdi.strftime("%Y-%m-%d %H:%M:%S"),
        "konum": {"lat": lat, "lon": lon},
        "hava": {
            "sicaklik": hava.get("temperature_2m"),
            "nem": hava.get("relative_humidity_2m"),
            "ciy_noktasi": hava.get("dew_point_2m"),
            "basinc": hava.get("surface_pressure"),
            "bulut_toplam": hava.get("cloud_cover"),
            "bulut_alcak": hava.get("cloud_cover_low"),
            "bulut_orta": hava.get("cloud_cover_mid"),
            "bulut_yuksek": hava.get("cloud_cover_high"),
            "ruzgar_hizi": hava.get("wind_speed_10m"),
            "gorus_mesafesi_m": hava.get("visibility"),
        },
        "ay": ay,
        "isik_kirliligi": isik,
        "gozlem": skor_bilgi,
    }
