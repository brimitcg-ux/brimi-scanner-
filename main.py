import os
import re
import json
import base64
import httpx
import asyncio
import logging
from urllib.parse import quote_plus
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("brimi-backend")

app = FastAPI(title="BRIMI Precision Scanner API", version="2.6.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ScanRequest(BaseModel):
    image_base64: str
    mime_type: Optional[str] = "image/jpeg"

class CardResult(BaseModel):
    cardName: str
    cardNumber: str
    setTotal: Optional[str] = None
    setCode: Optional[str] = None
    setName: str
    language: str
    languageCode: str
    rarity: str
    condition: str
    isGraded: bool = False
    gradingCompany: Optional[str] = None
    grade: Optional[str] = None
    certNumber: Optional[str] = None
    certUrl: Optional[str] = None
    currentPrice: float
    basePriceNM: float
    estimatedPriceEUR: float
    lowPriceEUR: Optional[float] = None
    avg30EUR: Optional[float] = None
    directMarketUrl: str
    priceSource: str

def get_api_keys() -> List[str]:
    raw_keys = os.getenv("GEMINI_API_KEY", "")
    keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
    return keys

CANDIDATE_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.5-flash"
]

SYSTEM_PROMPT = """
Je bent de geavanceerde visuele inspectie-engine van BRIMI Scanner voor Pokémon TCG.
Analyseer de afbeelding nauwkeurig. De foto kan één losse kaart, een stapel kaarten of een graded slab (PSA, CGC, BGS, PCA) bevatten.

Geef ALTIJD en UITSLUITEND een geldige JSON-array terug met voor elke herkende kaart een object:
[
  {
    "cardName": "Officiële Engelse naam van de Pokémon / Trainer (bijv. Alolan Exeggutor of Naganadel GX)",
    "cardNumber": "Nummer van de kaart, bijv. 230 of 097 of 205",
    "setTotal": "Totaal aantal kaarten in set indien leesbaar, bijv. 236 of 198 of null",
    "setCode": "Setcode linksonder indien zichtbaar, bijv. SVI, CEL, UNM, PAL, PR-SW",
    "setName": "Officiële setnaam, bijv. Unified Minds, Celebrations, Scarlet & Violet",
    "language": "Engels, Japans, Chinees, Duits, Frans, etc.",
    "languageCode": "EN, JP, CN, DE, FR",
    "rarity": "Common, Uncommon, Rare, Ultra Rare, Secret Rare, Illustration Rare",
    "isGraded": false,
    "gradingCompany": null,
    "grade": null,
    "certNumber": null
  }
]
"""

USD_TO_EUR_RATE = 1.08

PC_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9"
}

def extract_pricecharting_grade_price(html: str, grade: Optional[str]) -> Optional[float]:
    """Haalt de exacte veilingprijs in USD uit de PriceCharting producttabel voor een specifieke grade."""
    grade_str = str(grade or "9").strip().lower()
    
    target_ids = []
    if "10" in grade_str:
        target_ids = ["grade-10-price", "graded_price", "manual_only_price"]
    elif "9.5" in grade_str:
        target_ids = ["grade-95-price", "cib_price"]
    elif "9" in grade_str:
        target_ids = ["grade-9-price", "cib_price"]
    elif "8" in grade_str:
        target_ids = ["grade-8-price"]
    elif "7" in grade_str:
        target_ids = ["grade-7-price"]
    else:
        target_ids = ["grade-9-price", "cib_price", "graded_price"]

    # Poging 1: Zoeken naar specifieke TD element-IDs in PriceCharting HTML
    for tid in target_ids:
        pattern = rf'id=["\']{tid}["\'][^>]*>([\s\S]*?)</td>'
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            cell_content = match.group(1)
            p_match = re.search(r'\$\s*([\d,]+(?:\.\d{2})?)', cell_content)
            if p_match:
                try:
                    val = float(p_match.group(1).replace(",", ""))
                    if val > 0:
                        return val
                except ValueError:
                    pass

    # Poging 2: Tabelrijen matchen (bijv. "Grade 9" gevolgd door dollarteken)
    label_pattern = ""
    if "10" in grade_str:
        label_pattern = r'(?:Grade\s*10|PSA\s*10|Gem\s*Mint\s*10)'
    elif "9.5" in grade_str:
        label_pattern = r'(?:Grade\s*9\.5|Gem\s*Mint\s*9\.5)'
    elif "9" in grade_str:
        label_pattern = r'(?:Grade\s*9|Mint\s*9)'
    elif "8" in grade_str:
        label_pattern = r'(?:Grade\s*8)'
    elif "7" in grade_str:
        label_pattern = r'(?:Grade\s*7)'

    if label_pattern:
        row_pattern = rf'{label_pattern}[\s\S]*?\$([\d,]+(?:\.\d{2})?)'
        match = re.search(row_pattern, html, re.IGNORECASE)
        if match:
            try:
                val = float(match.group(1).replace(",", ""))
                if val > 0:
                    return val
            except ValueError:
                pass

    return None

async def fetch_pricecharting_data(client: httpx.AsyncClient, card_name: str, card_number: str, grade: Optional[str]) -> Optional[Dict[str, Any]]:
    """Doorzoekt PriceCharting en haalt de officiële koers op voor de graded slab."""
    clean_name = re.sub(r'[^a-zA-Z0-9\s]', '', card_name).strip()
    clean_num = str(card_number).strip().lstrip("0") or str(card_number).strip()
    search_q = f"{clean_name} {clean_num}"
    search_url = f"https://www.pricecharting.com/search-products?q={quote_plus(search_q)}&type=prices"

    try:
        resp = await client.get(search_url, headers=PC_HEADERS, timeout=5.0, follow_redirects=True)
        if resp.status_code != 200:
            return None

        final_url = str(resp.url)
        html_content = resp.text

        # Als PriceCharting direct doorverwijst naar de productpagina (/game/...)
        if "/game/" in final_url:
            usd_price = extract_pricecharting_grade_price(html_content, grade)
            if usd_price and usd_price > 0:
                eur_price = round(usd_price / USD_TO_EUR_RATE, 2)
                return {
                    "price_eur": eur_price,
                    "price_usd": usd_price,
                    "product_url": final_url
                }

        # Anders: zoek de eerste productlink in de zoekresultaten
        product_links = re.findall(r'href=["\'](/game/pokemon-[^"\']+)["\']', html_content)
        if product_links:
            # Neem de best passende link
            target_slug = product_links[0]
            for pl in product_links:
                if clean_num in pl:
                    target_slug = pl
                    break

            full_product_url = f"https://www.pricecharting.com{target_slug}"
            prod_resp = await client.get(full_product_url, headers=PC_HEADERS, timeout=4.5)
            if prod_resp.status_code == 200:
                usd_price = extract_pricecharting_grade_price(prod_resp.text, grade)
                if usd_price and usd_price > 0:
                    eur_price = round(usd_price / USD_TO_EUR_RATE, 2)
                    return {
                        "price_eur": eur_price,
                        "price_usd": usd_price,
                        "product_url": full_product_url
                    }
    except Exception as ex:
        logger.warning(f"PriceCharting lookup mislukt voor {card_name} #{card_number}: {ex}")

    return None

async def fetch_tcgdex_resilient(client: httpx.AsyncClient, card_name: str, card_number: str, set_code: Optional[str]) -> Optional[Dict[str, Any]]:
    """3-Traps vangnet voor TCGdex / Cardmarket NL data."""
    clean_num = str(card_number).strip().lstrip("0") or str(card_number).strip()
    headers = {"Accept": "application/json"}

    # Trap 1: Directe Set-Match indien setcode aanwezig is
    if set_code:
        clean_set = set_code.strip().lower()
        candidate_ids = [
            f"{clean_set}-{card_number.strip()}",
            f"{clean_set}-{clean_num}",
            f"{clean_set}-{card_number.strip().zfill(3)}"
        ]
        for cid in candidate_ids:
            try:
                url = f"https://api.tcgdex.net/v2/en/cards/{cid}"
                resp = await client.get(url, headers=headers, timeout=3.0)
                if resp.status_code == 200:
                    data = resp.json()
                    cm = data.get("cardmarket", {})
                    prices = cm.get("prices", {})
                    if prices.get("trend") or prices.get("avg30") or prices.get("low"):
                        return {
                            "trend": prices.get("trend"),
                            "avg30": prices.get("avg30"),
                            "low": prices.get("low"),
                            "official_set": data.get("set", {}).get("name"),
                            "cm_url": cm.get("url")
                        }
            except Exception:
                pass

    # Trap 2: Zoeken op naam en nummer filteren (vangt Celebrations, promo's en speciale sets)
    try:
        clean_name = re.sub(r'[^a-zA-Z0-9\s]', '', card_name).strip()
        search_terms = [clean_name]
        
        # Voeg ook hoofdnaam toe als fallback (bijv. "Exeggutor" ipv "Alolan Exeggutor")
        words = clean_name.split()
        if len(words) > 1 and words[-1].lower() not in ["gx", "v", "ex", "vmax", "vstar"]:
            search_terms.append(words[-1])
        elif len(words) > 2:
            search_terms.append(words[1])

        for st in search_terms:
            search_url = f"https://api.tcgdex.net/v2/en/cards?name={quote_plus(st)}"
            resp = await client.get(search_url, headers=headers, timeout=3.5)
            if resp.status_code == 200:
                results = resp.json()
                if isinstance(results, list):
                    for item in results:
                        item_local = str(item.get("localId", "")).strip().lstrip("0")
                        item_raw = str(item.get("localId", "")).strip()
                        if item_local == clean_num or item_raw == str(card_number).strip():
                            full_card_url = f"https://api.tcgdex.net/v2/en/cards/{item.get('id')}"
                            card_resp = await client.get(full_card_url, headers=headers, timeout=3.5)
                            if card_resp.status_code == 200:
                                card_data = card_resp.json()
                                cm = card_data.get("cardmarket", {})
                                prices = cm.get("prices", {})
                                return {
                                    "trend": prices.get("trend"),
                                    "avg30": prices.get("avg30"),
                                    "low": prices.get("low"),
                                    "official_set": card_data.get("set", {}).get("name"),
                                    "cm_url": cm.get("url")
                                }
    except Exception as ex:
        logger.warning(f"TCGdex fuzzy search mislukt voor {card_name}: {ex}")

    return None

def generate_market_url(card_name: str, card_number: str, is_graded: bool, grading_company: Optional[str], grade: Optional[str]) -> str:
    clean_name = quote_plus(card_name)
    clean_num = quote_plus(card_number)
    if is_graded:
        return f"https://www.pricecharting.com/search-products?q={clean_name}+{clean_num}&type=prices"
    else:
        return f"https://www.cardmarket.com/en/Pokemon/Products/Search?searchString={clean_name}+{clean_num}&sellerCountry=17&minCondition=2"

async def call_gemini_vision(image_base64: str, mime_type: str) -> str:
    api_keys = get_api_keys()
    if not api_keys:
        raise HTTPException(status_code=500, detail="Geen GEMINI_API_KEY ingesteld in Render.")

    clean_base64 = re.sub(r"^data:image\/[a-zA-Z]+;base64,", "", image_base64).strip()

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": SYSTEM_PROMPT},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": clean_base64
                        }
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 2048,
            "responseMimeType": "application/json"
        },
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ]
    }

    last_error_msg = ""

    async with httpx.AsyncClient(timeout=25.0) as client:
        for key_idx, key in enumerate(api_keys):
            headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": key
            }

            for model_name in CANDIDATE_MODELS:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
                try:
                    logger.info(f"Scan uitvoeren met sleutel {key_idx + 1} op model {model_name}...")
                    response = await client.post(url, headers=headers, json=payload)
                    
                    if response.status_code == 200:
                        data = response.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            content = candidates[0].get("content", {})
                            parts = content.get("parts", [])
                            if parts and "text" in parts[0]:
                                return parts[0]["text"]
                        
                        feedback = data.get("promptFeedback", {})
                        block_reason = feedback.get("blockReason")
                        if block_reason:
                            last_error_msg = f"Google Safety ({block_reason})"
                            continue
                            
                    elif response.status_code == 429:
                        last_error_msg = f"Sleutel {key_idx + 1} limiet bereikt (HTTP 429)"
                        logger.warning(last_error_msg)
                        break
                    elif response.status_code in [404, 503]:
                        last_error_msg = f"{model_name} ({response.status_code})"
                        continue
                    else:
                        last_error_msg = f"{model_name} (HTTP {response.status_code})"
                except Exception as ex:
                    last_error_msg = f"{model_name} Netwerkfout: {str(ex)}"

    raise HTTPException(status_code=502, detail=f"AI Analyse tijdelijk niet gelukt ({last_error_msg}). Probeer het over een minuutje nogmaals.")

def parse_ai_json(raw_text: str) -> List[Dict[str, Any]]:
    cleaned = raw_text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"```$", "", cleaned, flags=re.MULTILINE).strip()
    
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return parsed
        elif isinstance(parsed, dict):
            return [parsed]
    except Exception:
        pass

    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start != -1 and end != -1:
        try:
            parsed = json.loads(cleaned[start:end+1])
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass

    start_obj = cleaned.find("{")
    end_obj = cleaned.rfind("}")
    if start_obj != -1 and end_obj != -1:
        try:
            parsed = json.loads(cleaned[start_obj:end_obj+1])
            if isinstance(parsed, dict):
                return [parsed]
        except Exception:
            pass

    logger.error(f"JSON Parse fout. Ruwe tekst: {raw_text}")
    return []

@app.get("/")
def read_root():
    return {"status": "online", "app": "BRIMI Precision Scanner", "version": "2.6.0"}

@app.post("/api/scan", response_model=List[CardResult])
async def scan_cards(request: ScanRequest):
    raw_ai_text = await call_gemini_vision(request.image_base64, request.mime_type or "image/jpeg")
    cards_data = parse_ai_json(raw_ai_text)
    
    if not cards_data:
        raise HTTPException(status_code=422, detail="Geen Pokémon kaart herkend op de foto. Zorg voor goed licht en een scherpe foto.")

    results: List[CardResult] = []

    async with httpx.AsyncClient() as client:
        # Taken klaarzetten voor gelijktijdige afhandeling van TCGdex en PriceCharting
        tcgdex_tasks = []
        pc_tasks = []

        for c in cards_data:
            c_name = c.get("cardName", "")
            c_num = str(c.get("cardNumber", ""))
            c_set = c.get("setCode")
            c_grade = c.get("grade")
            is_gr = bool(c.get("isGraded", False))

            if is_gr:
                pc_tasks.append(fetch_pricecharting_data(client, c_name, c_num, c_grade))
                tcgdex_tasks.append(asyncio.sleep(0, result=None))
            else:
                tcgdex_tasks.append(fetch_tcgdex_resilient(client, c_name, c_num, c_set))
                pc_tasks.append(asyncio.sleep(0, result=None))

        tcgdex_results = await asyncio.gather(*tcgdex_tasks, return_exceptions=True)
        pc_results = await asyncio.gather(*pc_tasks, return_exceptions=True)

    for i, c in enumerate(cards_data):
        tcgdex_data = tcgdex_results[i] if (i < len(tcgdex_results) and isinstance(tcgdex_results[i], dict)) else None
        pc_data = pc_results[i] if (i < len(pc_results) and isinstance(pc_results[i], dict)) else None
        
        is_graded = bool(c.get("isGraded", False))
        resolved_set_name = c.get("setName", "Onbekende Set")
        low_price_eur = None
        avg30_eur = None
        final_price = 0.0
        price_source = ""
        market_url = ""

        # Pijplijn 1: Graded Slabs (Echte PriceCharting veilingdata)
        if is_graded:
            company = c.get("gradingCompany") or "Slab"
            grade_val = c.get("grade") or "9"
            
            if pc_data and pc_data.get("price_eur"):
                final_price = float(pc_data["price_eur"])
                price_source = f"PriceCharting ({company} {grade_val})"
                market_url = pc_data.get("product_url") or generate_market_url(c.get("cardName", ""), str(c.get("cardNumber", "")), True, company, grade_val)
            else:
                # Eerlijke fallback: GEEN verzonnen multiplier meer!
                final_price = 0.0
                price_source = f"PriceCharting (Handmatig Invoeren)"
                market_url = generate_market_url(c.get("cardName", ""), str(c.get("cardNumber", "")), True, company, grade_val)

        # Pijplijn 2: Singles (Cardmarket NL via TCGdex)
        else:
            if tcgdex_data and (tcgdex_data.get("trend") or tcgdex_data.get("avg30")):
                trend_val = tcgdex_data.get("trend")
                avg_val = tcgdex_data.get("avg30")
                final_price = float(trend_val or avg_val)
                avg30_eur = float(avg_val) if avg_val else None
                low_price_eur = float(tcgdex_data.get("low")) if tcgdex_data.get("low") else None
                price_source = "Cardmarket NL Trend"
                if tcgdex_data.get("official_set"):
                    resolved_set_name = tcgdex_data["official_set"]
                market_url = tcgdex_data.get("cm_url") or generate_market_url(c.get("cardName", ""), str(c.get("cardNumber", "")), False, None, None)
            elif tcgdex_data and tcgdex_data.get("low"):
                final_price = float(tcgdex_data["low"])
                low_price_eur = float(tcgdex_data["low"])
                price_source = "Cardmarket Bodem"
                if tcgdex_data.get("official_set"):
                    resolved_set_name = tcgdex_data["official_set"]
                market_url = tcgdex_data.get("cm_url") or generate_market_url(c.get("cardName", ""), str(c.get("cardNumber", "")), False, None, None)
            else:
                # Eerlijke fallback: GEEN willekeurige AI getallen meer!
                final_price = 0.0
                price_source = "Cardmarket NL (Handmatig Invoeren)"
                market_url = generate_market_url(c.get("cardName", ""), str(c.get("cardNumber", "")), False, None, None)

        final_price = round(final_price, 2)

        cert_num = c.get("certNumber")
        cert_url = None
        if cert_num and is_graded:
            comp_upper = str(c.get("gradingCompany", "")).upper()
            if "PSA" in comp_upper:
                cert_url = f"https://www.psacard.com/cert/{cert_num}"
            elif "CGC" in comp_upper:
                cert_url = f"https://www.cgccards.com/certlookup/{cert_num}"

        results.append(CardResult(
            cardName=c.get("cardName", "Pokémon Kaart"),
            cardNumber=str(c.get("cardNumber", "?")),
            setTotal=c.get("setTotal"),
            setCode=c.get("setCode"),
            setName=resolved_set_name,
            language=c.get("language", "Engels"),
            languageCode=c.get("languageCode", "EN"),
            rarity=c.get("rarity", "Normaal"),
            condition="Near Mint (NM)" if not is_graded else f"{c.get('gradingCompany', 'Slab')} {c.get('grade', '')}".strip(),
            isGraded=is_graded,
            gradingCompany=c.get("gradingCompany"),
            grade=c.get("grade"),
            certNumber=cert_num,
            certUrl=cert_url,
            currentPrice=final_price,
            basePriceNM=final_price,
            estimatedPriceEUR=final_price,
            lowPriceEUR=low_price_eur,
            avg30EUR=avg30_eur,
            directMarketUrl=market_url,
            priceSource=price_source
        ))

    return results
