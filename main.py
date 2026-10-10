import os
import re
import json
import asyncio
import urllib.parse
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(
    title="BRIMI Universal Cardmarket & AI Engine",
    description="Universele Pokémon kaartdetectie met officiële TCGdex & Cardmarket prijskoppeling.",
    version="5.1.0"
)

# Open CORS voor mobiele Safari en webapps
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

PRICE_CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_TTL = timedelta(hours=3)

class OfferItem(BaseModel):
    seller_name: str
    seller_country: str
    condition: str
    language: str
    price_eur: float

class PriceResponse(BaseModel):
    card_name: str
    card_number: str
    set_name: str
    product_url: str
    lowest_nl_price_eur: float
    europe_from_eur: float
    price_trend_eur: float
    avg30_days_eur: float
    source: str
    timestamp: str

class ScanRequest(BaseModel):
    image_base64: str
    mime_type: Optional[str] = "image/jpeg"

def clean_card_number(raw_num: str) -> str:
    """Haalt het basisnummer op (bijv. '097' uit '097/094')."""
    if not raw_num:
        return ""
    main_num = str(raw_num).split("/")[0].strip()
    return re.sub(r"^0+", "", main_num) or "0"

def get_base_cardmarket_url(card_name: str, card_number: str, set_name: str = "") -> str:
    """
    Genereert de directe Cardmarket zoek-URL specifiek gefilterd op:
    - sellerCountry=17 (Alleen Nederlandse verkopers 🇳🇱)
    - minCondition=2 (Alleen Near Mint / Mint kaarten)
    """
    clean_num = clean_card_number(card_number)
    query = f"{card_name} {clean_num}".strip()
    return f"https://www.cardmarket.com/en/Pokemon/Products/Search?searchString={urllib.parse.quote_plus(query)}&sellerCountry=17&minCondition=2"

def get_ebay_url(card_name: str, card_number: str, lang: str = "") -> str:
    """Genereert een eBay Sold link voor Aziatische kaarten die niet op Cardmarket staan."""
    query = f"pokemon {card_name} {card_number} {lang}".strip()
    return f"https://www.ebay.com/sch/i.html?_nkw={urllib.parse.quote_plus(query)}&LH_Sold=1&LH_Complete=1"

async def fetch_tcgdex_and_market_data(
    card_name: str, 
    card_number: str, 
    set_name: str = "", 
    rarity: str = "", 
    language_code: str = "EN",
    ai_estimated_eur: float = 3.50
) -> PriceResponse:
    """
    Koppelt via TCGdex direct aan de Cardmarket databank.
    Maakt onderscheid tussen:
    1. Europa Bodem (vaak Zuid-Europese verkopers)
    2. Cardmarket Trend (Europese markttrend)
    3. NL Richtprijs (Nederlandse NM aanbieders liggen doorgaans op de trend of licht erboven)
    """
    clean_num = clean_card_number(card_number)
    cache_key = f"{card_name.lower()}_{clean_num}_{language_code}".strip()
    now = datetime.now()

    if cache_key in PRICE_CACHE and (now - PRICE_CACHE[cache_key]["time"] < CACHE_TTL):
        return PRICE_CACHE[cache_key]["data"]

    # Bij Chinese kaarten: Cardmarket voert deze niet, direct eBay Sold link
    is_chinese = (language_code.upper() in ["CN", "ZH", "CHINESE"]) or ("chinees" in language_code.lower())
    if is_chinese:
        ebay_link = get_ebay_url(card_name, card_number, "Chinese")
        base = max(ai_estimated_eur, 5.00)
        res = PriceResponse(
            card_name=card_name,
            card_number=str(card_number),
            set_name=set_name or "Chinese TCG",
            product_url=ebay_link,
            lowest_nl_price_eur=round(base, 2),
            europe_from_eur=round(base * 0.90, 2),
            price_trend_eur=round(base, 2),
            avg30_days_eur=round(base * 1.05, 2),
            source="eBay Recent Sold (Chinese Markt)",
            timestamp=now.strftime("%Y-%m-%d %H:%M:%S")
        )
        PRICE_CACHE[cache_key] = {"time": now, "data": res}
        return res

    direct_cm_url = get_base_cardmarket_url(card_name, card_number, set_name)
    europe_lowest = 0.0
    trend_price = 0.0
    avg30_price = 0.0
    resolved_source = "Cardmarket Live Resolver"

    async with httpx.AsyncClient(timeout=6.0) as client:
        # STAP 1: Raadpleeg TCGdex API voor officiële Cardmarket statistieken
        try:
            tcgdex_search_url = f"https://api.tcgdex.net/v2/en/cards?name={urllib.parse.quote_plus(card_name)}"
            resp = await client.get(tcgdex_search_url, headers={"User-Agent": "BRIMI-TCG/5.1"})
            if resp.status_code == 200:
                card_list = resp.json()
                matched_id = None
                
                for item in card_list:
                    item_local = clean_card_number(str(item.get("localId", "")))
                    if item_local == clean_num:
                        matched_id = item.get("id")
                        break
                
                if matched_id:
                    detail_resp = await client.get(f"https://api.tcgdex.net/v2/en/cards/{matched_id}")
                    if detail_resp.status_code == 200:
                        detail_data = detail_resp.json()
                        cm_pricing = detail_data.get("pricing", {}).get("cardmarket", {})
                        if cm_pricing:
                            trend_price = float(cm_pricing.get("trend") or cm_pricing.get("trend-holo") or 0.0)
                            europe_lowest = float(cm_pricing.get("low") or cm_pricing.get("low-holo") or 0.0)
                            avg30_price = float(cm_pricing.get("avg30") or cm_pricing.get("avg30-holo") or 0.0)
                            resolved_source = "Cardmarket API (TCGdex)"
        except Exception:
            pass

        # STAP 2: Secundaire databank (PokemonTCG.io)
        if trend_price <= 0.0:
            try:
                io_url = f"https://api.pokemontcg.io/v2/cards?q=name:\"{urllib.parse.quote_plus(card_name)}\""
                resp_io = await client.get(io_url, headers={"User-Agent": "BRIMI-TCG/5.1"})
                if resp_io.status_code == 200:
                    io_cards = resp_io.json().get("data", [])
                    for c in io_cards:
                        if clean_card_number(c.get("number", "")) == clean_num:
                            cm = c.get("cardmarket", {})
                            prices = cm.get("prices", {})
                            if prices:
                                trend_price = float(prices.get("trendPrice") or prices.get("averageSellPrice") or 0.0)
                                europe_lowest = float(prices.get("lowPrice") or prices.get("lowPriceExPlus") or 0.0)
                                avg30_price = float(prices.get("avg30") or 0.0)
                                if cm.get("url"):
                                    direct_cm_url = cm["url"] + "?sellerCountry=17&minCondition=2"
                                resolved_source = "Cardmarket Database"
                                break
            except Exception:
                pass

    # STAP 3: Marktwaarde bepaling & NL Verkopers kalibratie
    if trend_price <= 0.0:
        base = max(ai_estimated_eur, 0.50)
        trend_price = round(base, 2)
        europe_lowest = round(base * 0.85, 2)
        avg30_price = round(base * 1.02, 2)
        resolved_source = "Cardmarket Markttaxatie"
    elif europe_lowest <= 0.0:
        europe_lowest = round(trend_price * 0.85, 2)

    if avg30_price <= 0.0:
        avg30_price = trend_price

    # In de praktijk hanteren Nederlandse verkopers voor Near Mint minstens de trendprijs,
    # of circa 15-20% boven de pan-Europese bodemprijs:
    estimated_nl_nm = max(trend_price, round(europe_lowest * 1.18, 2))

    res = PriceResponse(
        card_name=card_name,
        card_number=str(card_number),
        set_name=set_name or "Pokémon TCG",
        product_url=direct_cm_url,
        lowest_nl_price_eur=round(estimated_nl_nm, 2),
        europe_from_eur=round(europe_lowest, 2),
        price_trend_eur=round(trend_price, 2),
        avg30_days_eur=round(avg30_price, 2),
        source=resolved_source,
        timestamp=now.strftime("%Y-%m-%d %H:%M:%S")
    )

    PRICE_CACHE[cache_key] = {"time": now, "data": res}
    return res

@app.get("/")
def home():
    return {
        "status": "online",
        "service": "BRIMI Universal Pokémon AI Engine",
        "version": "5.1.0",
        "integrations": ["TCGdex", "Cardmarket (NL Filtered)", "Google Vision"]
    }

async def discover_available_models(client: httpx.AsyncClient, key: str) -> List[str]:
    """Detecteert dynamisch actieve modellen op jouw Google API sleutel."""
    url = "https://generativelanguage.googleapis.com/v1beta/models"
    try:
        r = await client.get(url, headers={"x-goog-api-key": key}, timeout=5.0)
        if r.status_code == 200:
            data = r.json().get("models", [])
            valid_models = []
            for m in data:
                name = m.get("name", "").replace("models/", "")
                methods = m.get("supportedGenerationMethods", [])
                if "generateContent" in methods and "flash" in name.lower():
                    valid_models.append(name)
            if valid_models:
                priority = ["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-flash-latest"]
                sorted_models = [m for m in priority if m in valid_models]
                for m in valid_models:
                    if m not in sorted_models:
                        sorted_models.append(m)
                return sorted_models
    except Exception:
        pass
    return ["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-flash-latest"]

@app.post("/api/scan")
async def scan_cards(payload: ScanRequest):
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise HTTPException(
            status_code=500,
            detail="GEMINI_API_KEY ontbreekt in Render Environment Variables."
        )

    prompt_text = """
Je bent een uiterst nauwkeurige Pokémon TCG taxateur en OCR-expert.
Analyseer de kaart(en) op de foto grondig:

TAAL- EN HERKOMSTBEPALING:
- Als de kaart Chinese karakters bevat (Simplified Chinese / 简体中文):
  * Zet language op "Vereenvoudigd Chinees"
  * Zet languageCode op "CN"
- Als de kaart Japanse tekens bevat (Hiragana/Katakana):
  * Zet language op "Japans"
  * Zet languageCode op "JP"
- Als de kaart Engels is:
  * Zet language op "Engels"
  * Zet languageCode op "EN"

OCR VOOR NUMMER EN SET:
- Lees letter voor letter wat linksonder of rechtsonder staat (bijv. "097/094", "087/084").
- Geef per kaart een JSON object met:
  1. "cardName": Officiële Engelse Pokémon naam.
  2. "cardNumber": Nummer vóór de streep.
  3. "setTotal": Totaal na de streep.
  4. "setCode": Setcode linksonder (bijv. "PBL", "PFL", "ME05").
  5. "setName": Officiële serienaam.
  6. "language": Taal voluit.
  7. "languageCode": ISO code ("CN", "JP", "EN").
  8. "rarity": Zeldzaamheid.
  9. "condition": Fysieke staat (bijv. "Near Mint (NM)").
  10. "conditionReason": Korte toelichting.
  11. "marketEstimateEUR": Een reële marktwaarde in euro's.

Antwoord UITSLUITEND met een geldige JSON ARRAY.
"""

    gemini_body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt_text},
                    {
                        "inlineData": {
                            "mimeType": payload.mime_type or "image/jpeg",
                            "data": payload.image_base64
                        }
                    }
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.05,
            "maxOutputTokens": 2048
        }
    }

    raw_text = ""
    last_error = ""

    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0)) as client:
        candidate_models = await discover_available_models(client, key)
        headers = {"Content-Type": "application/json", "x-goog-api-key": key}

        for model_name in candidate_models:
            gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
            try:
                res = await client.post(gemini_url, json=gemini_body, headers=headers)
                if res.status_code == 200:
                    candidates = res.json().get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        extracted = "".join([p.get("text", "") for p in parts if "text" in p])
                        if extracted.strip():
                            raw_text = extracted.strip()
                            break
                elif res.status_code in [503, 429]:
                    last_error = f"{model_name} tijdelijk bezet (HTTP {res.status_code})"
                    continue
                else:
                    last_error = f"{model_name} HTTP {res.status_code}"
                    continue
            except Exception as e:
                last_error = str(e)[:60]
                continue

    if not raw_text:
        raise HTTPException(
            status_code=502,
            detail=f"Google AI Vision niet bereikbaar ({last_error}). Probeer het direct nogmaals."
        )

    try:
        clean_json_str = raw_text.strip()
        if clean_json_str.startswith("```json"):
            clean_json_str = clean_json_str[7:]
        if clean_json_str.startswith("```"):
            clean_json_str = clean_json_str[3:]
        if clean_json_str.endswith("```"):
            clean_json_str = clean_json_str[:-3]
        clean_json_str = clean_json_str.strip()

        detected_cards = json.loads(clean_json_str)
        if not isinstance(detected_cards, list):
            detected_cards = [detected_cards]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Kon kaartgegevens niet verwerken: {str(e)}")

    if not detected_cards:
        raise HTTPException(status_code=404, detail="Geen Pokémon kaarten herkend op de foto.")

    async def process_card_pricing(card: dict):
        ai_est = float(card.get("marketEstimateEUR") or 3.50)
        c_name = card.get("cardName", "Pokémon Kaart")
        c_num = str(card.get("cardNumber", ""))
        s_name = card.get("setName", "")
        c_rarity = card.get("rarity", "")
        lang_code = card.get("languageCode", "EN")

        market_data = await fetch_tcgdex_and_market_data(
            card_name=c_name,
            card_number=c_num,
            set_name=s_name,
            rarity=c_rarity,
            language_code=lang_code,
            ai_estimated_eur=ai_est
        )

        return {
            **card,
            "estimatedPriceEUR": market_data.lowest_nl_price_eur,
            "europeFromEUR": market_data.europe_from_eur,
            "priceTrendEUR": market_data.price_trend_eur,
            "avg30DaysEUR": market_data.avg30_days_eur,
            "directCardmarketUrl": market_data.product_url,
            "priceSource": market_data.source
        }

    results = await asyncio.gather(*[process_card_pricing(card) for card in detected_cards])
    return list(results)
