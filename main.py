import os
import re
import json
import asyncio
import urllib.parse
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(
    title="BRIMI Turbo Scanner Engine",
    description="Pokémon kaartdetectie met Cardmarket NL voor singles en live PriceCharting voor graded slabs.",
    version="6.8.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

PRICE_CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_TTL = timedelta(hours=4)

MODELS_CACHE: List[str] = []
MODELS_CACHE_TIME: Optional[datetime] = None

class ScanRequest(BaseModel):
    image_base64: str
    mime_type: Optional[str] = "image/jpeg"

def clean_card_number(raw_num: str) -> str:
    """Haalt het basisnummer op (bijv. '230' uit '230/236')."""
    if not raw_num:
        return ""
    main_num = str(raw_num).split("/")[0].strip()
    return re.sub(r"^0+", "", main_num) or "0"

def get_base_cardmarket_url(card_name: str, card_number: str) -> str:
    """Cardmarket NL link gefilterd op Nederlandse verkopers (sellerCountry=17) en Near Mint (minCondition=2)."""
    clean_num = clean_card_number(card_number)
    query = f"{card_name} {clean_num}".strip()
    return f"https://www.cardmarket.com/en/Pokemon/Products/Search?searchString={urllib.parse.quote_plus(query)}&sellerCountry=17&minCondition=2"

def get_pricecharting_search_url(card_name: str, card_number: str) -> str:
    """PriceCharting link met koershistorie."""
    clean_num = clean_card_number(card_number)
    query = f"pokemon {card_name} {clean_num}".strip()
    return f"https://www.pricecharting.com/search-products?q={urllib.parse.quote_plus(query)}&type=prices"

def get_ebay_sold_url(card_name: str, card_number: str, slab_info: Optional[str] = None) -> str:
    """eBay Sold link voor slabs of Aziatische kaarten."""
    query = f"pokemon {card_name} {card_number}"
    if slab_info:
        query = f"{slab_info} {query}"
    return f"https://www.ebay.com/sch/i.html?_nkw={urllib.parse.quote_plus(query.strip())}&LH_Sold=1&LH_Complete=1"

async def fetch_live_pricecharting_price(card_name: str, card_number: str, grade_str: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Haalt realtime de exacte veilingprijzen (Grade 8, Grade 9, PSA 10, etc.) van PriceCharting op."""
    clean_num = clean_card_number(card_number)
    query = f"pokemon {card_name} {clean_num}".strip()
    search_url = f"https://www.pricecharting.com/search-products?q={urllib.parse.quote_plus(query)}&type=prices"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,nl;q=0.8"
    }

    try:
        async with httpx.AsyncClient(timeout=4.5, follow_redirects=True, headers=headers) as client:
            resp = await client.get(search_url)
            if resp.status_code != 200:
                return None

            target_html = resp.text
            final_url = str(resp.url)

            # Indien search overview, open het eerste relevante product
            if "search-products" in final_url:
                soup = BeautifulSoup(target_html, "html.parser")
                link = soup.select_one("td.title a") or soup.select_one("a[href*='/game/pokemon-']")
                if link and link.get("href"):
                    product_href = link["href"]
                    final_url = urllib.parse.urljoin("https://www.pricecharting.com", product_href)
                    p_resp = await client.get(final_url)
                    if p_resp.status_code == 200:
                        target_html = p_resp.text

            soup = BeautifulSoup(target_html, "html.parser")
            g_raw = (grade_str or "9").lower().replace("psa", "").replace("cgc", "").replace("bgs", "").strip()

            def to_float(txt: str) -> Optional[float]:
                if not txt: return None
                nums = re.findall(r"[\d\.]+", txt.replace(",", ""))
                return float(nums[0]) if nums else None

            # 1. Check specifieke PriceCharting table cell IDs
            grade_id_map = {
                "10": ["manual_only_price", "psa10_price", "bgs10_price"],
                "9.5": ["box_only_price", "grade95_price"],
                "9": ["grade9_price"],
                "8": ["grade8_price"],
                "7": ["grade7_price", "cib_price"],
                "ungraded": ["ungraded_price", "loose_price"]
            }

            key = "9"
            if "10" in g_raw: key = "10"
            elif "9.5" in g_raw: key = "9.5"
            elif "8" in g_raw: key = "8"
            elif "7" in g_raw: key = "7"

            found_usd = None
            for cell_id in grade_id_map.get(key, []):
                el = soup.find(id=cell_id)
                if el:
                    val = to_float(el.get_text())
                    if val and val > 0:
                        found_usd = val
                        break

            # 2. Regex fallback over tekst als cell ID afwijkt
            if not found_usd:
                patterns = [
                    rf"(?:Grade\s*{re.escape(key)}|PSA\s*{re.escape(key)})\s*[:\s]*\$([0-9\.,]+)",
                    rf"\$([0-9\.,]+)\s*(?:Grade\s*{re.escape(key)}|PSA\s*{re.escape(key)})"
                ]
                for p in patterns:
                    m = re.search(p, target_html, re.I)
                    if m:
                        val = to_float(m.group(1))
                        if val and val > 0:
                            found_usd = val
                            break

            if found_usd and found_usd > 0:
                # Koersconversie USD naar EUR (ca. 0.92)
                eur = round(found_usd * 0.92, 2)
                return {
                    "price_eur": eur,
                    "price_usd": found_usd,
                    "url": final_url,
                    "grade_label": f"Grade {key}"
                }
    except Exception:
        pass

    return None

async def get_working_gemini_models(api_key: str) -> List[str]:
    """Haalt dynamisch beschikbare modellen op."""
    global MODELS_CACHE, MODELS_CACHE_TIME
    now = datetime.now()
    if MODELS_CACHE and MODELS_CACHE_TIME and (now - MODELS_CACHE_TIME < timedelta(hours=1)):
        return MODELS_CACHE

    discovered: List[str] = []
    try:
        async with httpx.AsyncClient(timeout=3.5) as client:
            res = await client.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}")
            if res.status_code == 200:
                data = res.json()
                for m in data.get("models", []):
                    methods = m.get("supportedGenerationMethods", [])
                    name = m.get("name", "")
                    if "generateContent" in methods and "flash" in name.lower():
                        discovered.append(name)
    except Exception:
        pass

    fallback_priority = [
        "models/gemini-2.0-flash",
        "models/gemini-2.0-flash-lite",
        "models/gemini-1.5-flash-latest",
        "models/gemini-1.5-flash"
    ]
    
    combined = []
    for m in discovered + fallback_priority:
        if m not in combined:
            combined.append(m)

    MODELS_CACHE = combined
    MODELS_CACHE_TIME = now
    return combined

async def resolve_card_price(
    card_name: str, 
    card_number: str, 
    language_code: str, 
    ai_estimate: float,
    is_graded: bool = False,
    grading_company: Optional[str] = None,
    grade: Optional[str] = None
) -> Dict[str, Any]:
    """Prijsresolutie: Live PriceCharting voor slabs, eBay voor Chinees, Cardmarket NL voor raw singles."""
    clean_num = clean_card_number(card_number)
    slab_id = f"{grading_company}_{grade}".lower() if is_graded else "raw"
    cache_key = f"{card_name.lower()}_{clean_num}_{language_code}_{slab_id}".strip()
    now = datetime.now()

    if cache_key in PRICE_CACHE and (now - PRICE_CACHE[cache_key]["time"] < CACHE_TTL):
        return PRICE_CACHE[cache_key]["data"]

    # 1. Gecertificeerde Graded Slabs: LIVE PriceCharting scraper
    if is_graded:
        pc_live = await fetch_live_pricecharting_price(card_name, card_number, grade)
        default_pc_url = get_pricecharting_search_url(card_name, card_number)
        label = f"{grading_company or 'Slab'} {grade or ''}".strip()

        if pc_live and pc_live.get("price_eur"):
            real_price = pc_live["price_eur"]
            data = {
                "price_eur": real_price,
                "url": pc_live.get("url") or default_pc_url,
                "source": f"PriceCharting Live (${pc_live.get('price_usd'):.2f})",
                "is_graded": True,
                "is_chinese": False
            }
        else:
            data = {
                "price_eur": round(max(ai_estimate, 12.0), 2),
                "url": default_pc_url,
                "source": f"PriceCharting ({label})",
                "is_graded": True,
                "is_chinese": False
            }

        PRICE_CACHE[cache_key] = {"time": now, "data": data}
        return data

    # 2. Chinese kaarten
    is_chinese = (language_code.upper() in ["CN", "ZH", "CHINESE"]) or ("chinees" in language_code.lower())
    if is_chinese:
        ebay_url = get_ebay_sold_url(card_name, card_number)
        data = {
            "price_eur": round(max(ai_estimate, 4.0), 2),
            "url": ebay_url,
            "source": "eBay Sold (Chinees)",
            "is_graded": False,
            "is_chinese": True
        }
        PRICE_CACHE[cache_key] = {"time": now, "data": data}
        return data

    # 3. Reguliere Singles (Cardmarket NL via TCGdex)
    cm_url = get_base_cardmarket_url(card_name, card_number)
    resolved_price = round(max(ai_estimate, 1.50), 2)
    source = "Cardmarket NL Richtprijs"

    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            search_url = f"https://api.tcgdex.net/v2/en/cards?name={urllib.parse.quote_plus(card_name)}"
            resp = await client.get(search_url)
            if resp.status_code == 200:
                card_list = resp.json()
                matched_id = None
                for item in card_list:
                    if clean_card_number(str(item.get("localId", ""))) == clean_num:
                        matched_id = item.get("id")
                        break
                if matched_id:
                    detail_resp = await client.get(f"https://api.tcgdex.net/v2/en/cards/{matched_id}")
                    if detail_resp.status_code == 200:
                        cm_pricing = detail_resp.json().get("pricing", {}).get("cardmarket", {})
                        trend = float(cm_pricing.get("trend") or cm_pricing.get("trend-holo") or 0.0)
                        if trend > 0.0:
                            resolved_price = round(trend * 1.10, 2)
                            source = "Cardmarket API"
    except Exception:
        pass

    data = {
        "price_eur": resolved_price,
        "url": cm_url,
        "source": source,
        "is_graded": False,
        "is_chinese": False
    }
    PRICE_CACHE[cache_key] = {"time": now, "data": data}
    return data

@app.get("/")
def ping():
    return {"status": "online", "engine": "BRIMI Turbo Scanner", "version": "6.8.0"}

@app.post("/api/scan")
async def scan_cards(payload: ScanRequest):
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY ontbreekt op Render Environment Variables.")

    prompt = """
Analyseer deze Pokémon kaartfoto direct:
1. Naam: officiële Pokémon naam (bijv. Naganadel GX, Dewgong, Misty's Vitality).
2. Nummer: cijfers vóór de streep (bijv. '230' uit '230/236').
3. Totaal: cijfers na de streep (bijv. '236').
4. Setcode: letters linksonder (bijv. SM11, PBL, PFL, ME05).
5. Setnaam: herken de reeks (bijv. Unified Minds, Pitch Black).
6. Taal: Chinees indien vereenvoudigde karakters ('CN'), Japans ('JP') of Engels ('EN').
7. Graded Slab: Check of de kaart in een gecertificeerde slab zit (bijv. PSA, BGS, CGC).
   - isGraded: true/false
   - gradingCompany: "PSA", "BGS", "CGC" of null
   - grade: bijv. "10", "9.5", "9", "8" of null
8. Conditie: Indien graded geef de volledige omschrijving (bijv. "PSA 9 Mint"). Indien raw, kies uit: "Near Mint (NM)", "Light Played (LP)", "Good (GD)".
9. Reële marktwaarde in euro:
   - Indien RAW: schat de Nederlandse Cardmarket marktwaarde.
   - Indien GRADED SLAB: schat de werkelijke PriceCharting veilingwaarde specifiek voor deze grade.

JSON Formaat:
[
  {
    "cardName": "Naam",
    "cardNumber": "230",
    "setTotal": "236",
    "setCode": "SM11",
    "setName": "Unified Minds",
    "language": "Engels",
    "languageCode": "EN",
    "rarity": "Secret Rare",
    "isGraded": true,
    "gradingCompany": "PSA",
    "grade": "9",
    "condition": "PSA 9 Mint",
    "marketEstimateEUR": 15.00
  }
]
"""

    gemini_body = {
        "contents": [{
            "role": "user",
            "parts": [
                {"text": prompt},
                {
                    "inlineData": {
                        "mimeType": payload.mime_type or "image/jpeg",
                        "data": payload.image_base64
                    }
                }
            ]
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.05,
            "maxOutputTokens": 1024
        }
    }

    candidate_models = await get_working_gemini_models(key)
    raw_text = ""
    error_log = []

    async with httpx.AsyncClient(timeout=16.0) as client:
        for model in candidate_models[:4]:
            model_path = model if model.startswith("models/") else f"models/{model}"
            url = f"https://generativelanguage.googleapis.com/v1beta/{model_path}:generateContent?key={key}"
            
            try:
                res = await client.post(url, json=gemini_body)
                if res.status_code == 200:
                    cands = res.json().get("candidates", [])
                    if cands:
                        parts = cands[0].get("content", {}).get("parts", [])
                        raw_text = "".join([p.get("text", "") for p in parts if "text" in p]).strip()
                        if raw_text:
                            break
                elif res.status_code in [404, 503, 429]:
                    error_log.append(f"{model_path} ({res.status_code})")
                    continue
                else:
                    error_log.append(f"{model_path} ({res.status_code})")
            except Exception as e:
                error_log.append(f"{model_path} ({type(e).__name__})")
                continue

    if not raw_text:
        detail_msg = f"Geen respons van scanner engine. Pogingen: {', '.join(error_log[-2:])}" if error_log else "Scanner time-out."
        raise HTTPException(status_code=502, detail=detail_msg)

    cleaned = raw_text.strip()
    if cleaned.startswith("```json"): cleaned = cleaned[7:]
    if cleaned.startswith("```"): cleaned = cleaned[3:]
    if cleaned.endswith("```"): cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    try:
        cards_data = json.loads(cleaned)
        if not isinstance(cards_data, list):
            cards_data = [cards_data]
    except Exception:
        raise HTTPException(status_code=500, detail="Ongeldig antwoord van scanner engine.")

    async def enrich_card(c: dict):
        base_estimate = float(c.get("marketEstimateEUR") or 3.00)
        is_graded = bool(c.get("isGraded") or c.get("gradingCompany"))
        price_info = await resolve_card_price(
            card_name=c.get("cardName", "Pokémon Kaart"),
            card_number=str(c.get("cardNumber", "")),
            language_code=c.get("languageCode", "EN"),
            ai_estimate=base_estimate,
            is_graded=is_graded,
            grading_company=c.get("gradingCompany"),
            grade=c.get("grade")
        )

        final_price = price_info["price_eur"]

        return {
            **c,
            "basePriceNM": final_price,
            "estimatedPriceEUR": final_price,
            "directMarketUrl": price_info["url"],
            "priceSource": price_info["source"],
            "isChinese": price_info.get("is_chinese", False),
            "isGraded": is_graded
        }

    enriched = await asyncio.gather(*[enrich_card(c) for c in cards_data])
    return list(enriched)
