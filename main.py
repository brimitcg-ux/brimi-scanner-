import os
import re
import json
import base64
import httpx
import asyncio
import logging
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("brimi-backend")

app = FastAPI(title="BRIMI Precision Scanner API", version="2.5.1")

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
    "cardName": "Engelse of officiële naam van de Pokémon / Trainer",
    "cardNumber": "Nummer van de kaart, bijv. 205 of 097",
    "setTotal": "Totaal aantal kaarten in set indien leesbaar, bijv. 198 of null",
    "setCode": "Setcode linksonder indien zichtbaar, bijv. SVI, PAL, OBF, MEW, PR-SV",
    "setName": "Officiële setnaam, bijv. Scarlet & Violet, 151, Paldea Evolved",
    "language": "Engels, Japans, Chinees, Duits, Frans, etc.",
    "languageCode": "EN, JP, CN, DE, FR",
    "rarity": "Common, Uncommon, Rare, Double Rare, Illustration Rare, Special Illustration Rare, Ultra Rare, Hyper Rare",
    "isGraded": false,
    "gradingCompany": null,
    "grade": null,
    "certNumber": null,
    "estimatedPriceEUR": 5.0
  }
]
"""

async def fetch_tcgdex_data(client: httpx.AsyncClient, card_name: str, card_number: str, set_code: Optional[str]) -> Optional[Dict[str, Any]]:
    clean_num = str(card_number).strip().lstrip("0")
    if not clean_num:
        clean_num = str(card_number).strip()
    
    headers = {"Accept": "application/json"}
    
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
                resp = await client.get(url, headers=headers, timeout=3.5)
                if resp.status_code == 200:
                    data = resp.json()
                    cm = data.get("cardmarket", {})
                    prices = cm.get("prices", {})
                    return {
                        "trend": prices.get("trend"),
                        "avg30": prices.get("avg30"),
                        "low": prices.get("low"),
                        "official_set": data.get("set", {}).get("name"),
                        "cm_url": cm.get("url")
                    }
            except Exception:
                pass

    try:
        clean_name = re.sub(r'[^a-zA-Z0-9\s]', '', card_name).strip()
        search_url = f"https://api.tcgdex.net/v2/en/cards?name={httpx.URL(clean_name)}"
        resp = await client.get(search_url, headers=headers, timeout=3.5)
        if resp.status_code == 200:
            results = resp.json()
            if isinstance(results, list):
                for item in results:
                    item_local_id = str(item.get("localId", "")).strip().lstrip("0")
                    if item_local_id == clean_num:
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
    except Exception:
        pass

    return None

def generate_market_url(card_name: str, card_number: str, is_graded: bool, grading_company: Optional[str], grade: Optional[str]) -> str:
    clean_name = card_name.replace(" ", "+")
    if is_graded:
        return f"https://www.pricecharting.com/search-products?q={clean_name}+{card_number}&type=prices"
    else:
        return f"https://www.cardmarket.com/en/Pokemon/Products/Search?searchString={clean_name}+{card_number}&sellerCountry=17&minCondition=2"

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
                            last_error_msg = f"Google Safety Filter ({block_reason})"
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
    return {"status": "online", "app": "BRIMI Precision Scanner", "version": "2.5.1"}

@app.post("/api/scan", response_model=List[CardResult])
async def scan_cards(request: ScanRequest):
    raw_ai_text = await call_gemini_vision(request.image_base64, request.mime_type or "image/jpeg")
    cards_data = parse_ai_json(raw_ai_text)
    
    if not cards_data:
        raise HTTPException(status_code=422, detail="Geen Pokémon kaart herkend op de foto. Zorg voor goed licht en een scherpe foto.")

    results: List[CardResult] = []

    async with httpx.AsyncClient() as client:
        tcgdex_tasks = [
            fetch_tcgdex_data(
                client,
                c.get("cardName", ""),
                str(c.get("cardNumber", "")),
                c.get("setCode")
            )
            for c in cards_data
        ]
        tcgdex_results = await asyncio.gather(*tcgdex_tasks, return_exceptions=True)

    for i, c in enumerate(cards_data):
        tcgdex_data = tcgdex_results[i] if (i < len(tcgdex_results) and isinstance(tcgdex_results[i], dict)) else None
        
        is_graded = bool(c.get("isGraded", False))
        ai_price = float(c.get("estimatedPriceEUR", 2.5) or 2.5)
        resolved_set_name = c.get("setName", "Onbekende Set")
        low_price_eur = None
        avg30_eur = None

        if is_graded:
            grade_val = str(c.get("grade", "9")).strip()
            multiplier = 4.5 if "10" in grade_val else (2.0 if "9" in grade_val else 1.3)
            final_price = round(ai_price * multiplier, 2)
            price_source = f"PriceCharting Slab ({c.get('gradingCompany', 'Slab')} {grade_val})"
        else:
            if tcgdex_data and (tcgdex_data.get("trend") or tcgdex_data.get("avg30")):
                trend_val = tcgdex_data.get("trend")
                avg_val = tcgdex_data.get("avg30")
                final_price = float(trend_val or avg_val)
                avg30_eur = float(avg_val) if avg_val else None
                low_price_eur = float(tcgdex_data.get("low")) if tcgdex_data.get("low") else None
                price_source = "Cardmarket NL Koers"
                if tcgdex_data.get("official_set"):
                    resolved_set_name = tcgdex_data["official_set"]
            elif tcgdex_data and tcgdex_data.get("low"):
                final_price = float(tcgdex_data["low"])
                low_price_eur = float(tcgdex_data["low"])
                price_source = "Cardmarket Bodem"
                if tcgdex_data.get("official_set"):
                    resolved_set_name = tcgdex_data["official_set"]
            else:
                final_price = ai_price
                price_source = "Cardmarket NL Richtprijs"

        final_price = round(final_price, 2)

        cert_num = c.get("certNumber")
        cert_url = None
        if cert_num and is_graded:
            company = str(c.get("gradingCompany", "")).upper()
            if "PSA" in company:
                cert_url = f"https://www.psacard.com/cert/{cert_num}"
            elif "CGC" in company:
                cert_url = f"https://www.cgccards.com/certlookup/{cert_num}"

        market_url = generate_market_url(
            c.get("cardName", ""),
            str(c.get("cardNumber", "")),
            is_graded,
            c.get("gradingCompany"),
            c.get("grade")
        )

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
