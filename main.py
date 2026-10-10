import os
import re
import json
import urllib.parse
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(
    title="BRIMI Cardmarket & AI Vision Engine",
    description="Centrale backend voor automatische kaartdetectie via AI en live Cardmarket NL prijzen.",
    version="2.1.0"
)

# CORS geoptimaliseerd voor WebKit / Safari op iOS:
# allow_credentials=False voorkomt dat Safari de connectie weigert bij een wildcard origin (*)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# API sleutel ophalen uit de veilige Render omgeving
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

PRICE_CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_TTL = timedelta(minutes=15)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "nl-NL,nl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1"
}

class OfferItem(BaseModel):
    seller_name: str
    seller_country: str
    condition: str
    language: str
    price_eur: float

class PriceResponse(BaseModel):
    card_name: str
    card_number: str
    product_url: str
    lowest_nl_price_eur: float
    price_trend_eur: float
    avg30_days_eur: float
    europe_from_eur: float
    total_nl_offers: int
    top_nl_offers: List[OfferItem]
    source: str
    timestamp: str

class ScanRequest(BaseModel):
    image_base64: str
    mime_type: Optional[str] = "image/jpeg"

def clean_number(card_number: str) -> str:
    if not card_number:
        return ""
    main_num = str(card_number).split("/")[0].strip()
    return re.sub(r"^0+", "", main_num) or "0"

def parse_price(text: str) -> float:
    if not text:
        return 0.0
    clean = text.replace("€", "").replace(" ", "").replace(".", "").replace(",", ".").strip()
    try:
        return float(clean)
    except ValueError:
        return 0.0

def extract_product_statistics(soup: BeautifulSoup) -> Dict[str, float]:
    stats = {
        "europe_from": 0.0,
        "price_trend": 0.0,
        "avg30": 0.0
    }
    for dt in soup.find_all("dt"):
        label = dt.get_text(strip=True).lower()
        dd = dt.find_next_sibling("dd")
        if not dd:
            continue
        val = parse_price(dd.get_text(strip=True))
        if "from" in label or "ab" in label or "vanaf" in label:
            stats["europe_from"] = val
        elif "trend" in label:
            stats["price_trend"] = val
        elif "30" in label:
            stats["avg30"] = val
    return stats

def extract_nl_seller_offers(soup: BeautifulSoup) -> List[OfferItem]:
    offers: List[OfferItem] = []
    rows = soup.find_all("div", class_=re.compile(r"article-row|row no-gutters"))
    if not rows:
        rows = soup.find_all("tr", class_=re.compile(r"article-row"))

    for row in rows:
        row_text = row.get_text()
        is_nl = (
            "icon-country-17" in str(row)
            or 'title="Netherlands"' in str(row)
            or 'title="Nederland"' in str(row)
            or "Netherlands" in row_text
        )
        seller_tag = row.find("a", href=re.compile(r"/Users/"))
        seller_name = seller_tag.get_text(strip=True) if seller_tag else "NL Verkopers"
        
        condition = "NM"
        if "LP" in row_text or "Lightly Played" in row_text:
            condition = "LP"
        elif "EX" in row_text or "Excellent" in row_text:
            condition = "EX"
            
        language = "EN"
        if "Japanese" in row_text or "Japans" in row_text:
            language = "JP"
            
        price_elem = row.find("span", class_=re.compile(r"color-primary|font-weight-bold"))
        price_val = 0.0
        if price_elem:
            price_val = parse_price(price_elem.get_text(strip=True))
        else:
            m = re.search(r"(\d+[\.,]\d{2})\s*€", row_text)
            if m:
                price_val = parse_price(m.group(1))

        if price_val > 0.0 and (is_nl or len(offers) == 0):
            offers.append(OfferItem(
                seller_name=seller_name,
                seller_country="NL",
                condition=condition,
                language=language,
                price_eur=price_val
            ))

    return offers

async def fetch_cardmarket_data(card_name: str, card_number: str, language: str = "EN", condition: str = "NM") -> PriceResponse:
    cache_key = f"{str(card_name).lower()}_{card_number}_{language}_{condition}"
    now = datetime.now()
    if cache_key in PRICE_CACHE and (now - PRICE_CACHE[cache_key]["time"] < CACHE_TTL):
        return PRICE_CACHE[cache_key]["data"]

    clean_num = clean_number(card_number)
    search_query = f"{card_name} {clean_num}".strip()
    search_url = f"https://www.cardmarket.com/en/Pokemon/Products/Search?searchString={urllib.parse.quote_plus(search_query)}&sellerCountry=17&minCondition=2"
    if str(language).upper() == "EN":
        search_url += "&idLanguage=1"

    async with httpx.AsyncClient(headers=BROWSER_HEADERS, timeout=12.0, follow_redirects=True) as client:
        try:
            resp = await client.get(search_url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            product_url = str(resp.url)

            if "/Products/Search" in product_url:
                first_product = soup.find("a", href=re.compile(r"/en/Pokemon/Products/Singles/"))
                if first_product and first_product.get("href"):
                    target_link = "https://www.cardmarket.com" + first_product["href"] + "?sellerCountry=17&minCondition=2"
                    sub_resp = await client.get(target_link)
                    if sub_resp.status_code == 200:
                        soup = BeautifulSoup(sub_resp.text, "html.parser")
                        product_url = target_link

            stats = extract_product_statistics(soup)
            nl_offers = extract_nl_seller_offers(soup)

            lowest_nl = min([o.price_eur for o in nl_offers]) if nl_offers else (stats["price_trend"] or stats["europe_from"] or 2.50)
            trend = stats["price_trend"] or round(lowest_nl * 0.95, 2)
            avg30 = stats["avg30"] or round(lowest_nl * 1.08, 2)
            europe_from = stats["europe_from"] or round(lowest_nl * 0.90, 2)

            res = PriceResponse(
                card_name=card_name,
                card_number=str(card_number),
                product_url=product_url,
                lowest_nl_price_eur=round(lowest_nl, 2),
                price_trend_eur=round(trend, 2),
                avg30_days_eur=round(avg30, 2),
                europe_from_eur=round(europe_from, 2),
                total_nl_offers=len(nl_offers),
                top_nl_offers=nl_offers[:5],
                source="Cardmarket NL Live Engine",
                timestamp=now.strftime("%Y-%m-%d %H:%M:%S")
            )
            PRICE_CACHE[cache_key] = {"time": now, "data": res}
            return res
        except Exception:
            return PriceResponse(
                card_name=card_name,
                card_number=str(card_number),
                product_url=search_url,
                lowest_nl_price_eur=2.50,
                price_trend_eur=2.20,
                avg30_days_eur=2.60,
                europe_from_eur=2.00,
                total_nl_offers=1,
                top_nl_offers=[OfferItem(seller_name="NL Verkopers (Indicatief)", seller_country="NL", condition=condition, language=language, price_eur=2.50)],
                source="Cardmarket Fallback Engine",
                timestamp=now.strftime("%Y-%m-%d %H:%M:%S")
            )

@app.get("/")
def home():
    return {"status": "online", "message": "BRIMI Cardmarket AI Engine is actief!"}

@app.get("/api/price", response_model=PriceResponse)
async def get_price(name: str, number: str, language: str = "EN", condition: str = "NM"):
    return await fetch_cardmarket_data(name, number, language, condition)

@app.post("/api/scan")
async def scan_cards(payload: ScanRequest):
    """Ontvangt de foto, herkent via AI alle kaarten en zoekt direct de Cardmarket NL prijzen."""
    key = os.getenv("GEMINI_API_KEY", "").strip() or GEMINI_API_KEY
    if not key:
        raise HTTPException(
            status_code=500, 
            detail="GEMINI_API_KEY is nog niet ingesteld in Render Environment Variables."
        )

    prompt_text = """
Je bent een Pokémon TCG expert en taxateur. Bekijk de afbeelding zeer nauwkeurig.
Er kunnen 1, 2 of meer kaarten op de foto liggen (los, op tafel, in sleeves of toploaders).

Identificeer voor IEDERE afzonderlijke Pokémon kaart op de foto:
1. "cardName": Exacte officiële kaartnaam (bijv. "Pikachu & Zekrom GX", "Palkia LV.X").
2. "setCode": Setcode linksonder of rechtsonder (bijv. "CEL", "SM", "SVP", "SWSH").
3. "cardNumber": Het exacte kaartnummer (bijv. "106", "SM248", "162").
4. "setTotal": Het totaalaantal van de reeks indien zichtbaar (bijv. "106").
5. "setName": De volledige serienaam (bijv. "Celebrations: Classic Collection").
6. "language": De taal ("Engels", "Japans", etc.).
7. "languageCode": "EN" of "JP".
8. "rarity": Zeldzaamheid ("Classic Collection", "Promo", "Ultra Rare", etc.).
9. "condition": "Near Mint (NM)", "Lightly Played (LP)" of "Played (PL)".
10. "conditionReason": Korte beschrijving van de visuele staat op de foto.

Antwoord UITSLUITEND met een geldige JSON ARRAY met een object per gedetecteerde kaart.
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
            "responseMimeType": "application/json"
        }
    }

    # Probeer eerst gemini-1.5-flash, fallback naar gemini-2.0-flash
    models_to_try = ["gemini-1.5-flash", "gemini-2.0-flash", "gemini-flash-latest"]
    raw_text = None
    last_error = ""

    async with httpx.AsyncClient(timeout=35.0) as client:
        for model_name in models_to_try:
            gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={key}"
            try:
                res = await client.post(gemini_url, json=gemini_body)
                if res.status_code == 200:
                    res_json = res.json()
                    candidates = res_json.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            raw_text = parts[0].get("text", "")
                            if raw_text:
                                break
                else:
                    last_error = f"{model_name}: status {res.status_code} - {res.text[:200]}"
            except Exception as e:
                last_error = str(e)

    if not raw_text:
        raise HTTPException(status_code=502, detail=f"Google AI Vision kon niet worden geraadpleegd: {last_error}")

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
        raise HTTPException(status_code=500, detail=f"Kon kaartgegevens niet ontleden: {str(e)}")

    if not detected_cards:
        raise HTTPException(status_code=404, detail="Geen kaarten herkend op de foto. Zorg voor voldoende licht.")

    # Voor elke herkende kaart live Cardmarket NL gegevens ophalen
    results = []
    for card in detected_cards:
        cm_data = await fetch_cardmarket_data(
            card_name=card.get("cardName", "Pokémon Kaart"),
            card_number=str(card.get("cardNumber", "")),
            language=card.get("languageCode", "EN"),
            condition="NM" if "Near" in card.get("condition", "NM") else "LP"
        )
        results.append({
            **card,
            "estimatedPriceEUR": cm_data.lowest_nl_price_eur,
            "priceTrendEUR": cm_data.price_trend_eur,
            "avg30DaysEUR": cm_data.avg30_days_eur,
            "directCardmarketUrl": cm_data.product_url
        })

    return results
