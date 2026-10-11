import os
import json
import re
import urllib.parse
from typing import Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="BRIMI Scanner Engine", version="8.5.0")

# Volledige CORS-ondersteuning voor iOS Safari en web-apps
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": f"Interne serverfout: {str(exc)}"}
    )

class ScanRequest(BaseModel):
    image_base64: str
    mime_type: Optional[str] = "image/jpeg"

def clean_card_number(raw_num: str) -> str:
    if not raw_num:
        return ""
    main_num = str(raw_num).split("/")[0].strip()
    return re.sub(r"^0+", "", main_num) or "0"

def get_base_cardmarket_url(card_name: str, card_number: str) -> str:
    clean_num = clean_card_number(card_number)
    query = f"{card_name} {clean_num}".strip()
    return f"https://www.cardmarket.com/en/Pokemon/Products/Search?searchString={urllib.parse.quote_plus(query)}&sellerCountry=17&minCondition=2"

def get_pricecharting_url(card_name: str, card_number: str) -> str:
    clean_num = clean_card_number(card_number)
    query = f"pokemon {card_name} {clean_num}".strip()
    return f"https://www.pricecharting.com/search-products?q={urllib.parse.quote_plus(query)}&type=prices"

@app.get("/")
def health_check():
    key_configured = bool(os.getenv("GEMINI_API_KEY", "").strip())
    return {
        "status": "online",
        "engine": "BRIMI Precision Engine",
        "gemini_api_key_configured": key_configured
    }

@app.post("/api/scan")
async def scan_cards(payload: ScanRequest):
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        return JSONResponse(
            status_code=500,
            content={"detail": "GEMINI_API_KEY ontbreekt in Render Environment Variables."}
        )

    # Sanitizeer de base64 payload
    clean_b64 = payload.image_base64
    if "," in clean_b64:
        clean_b64 = clean_b64.split(",", 1)[1]
    clean_b64 = clean_b64.replace("\n", "").replace("\r", "").strip()

    prompt = """
Analyseer de Pokémon kaart op deze afbeelding zorgvuldig:
1. Naam: Officiële Pokémon kaartnaam (bijv. Naganadel GX, Palafin, Charizard ex).
2. Nummer: Cijfers vóór de schuine streep (bijv. '230' of '062').
3. Totaal: Cijfers na de schuine streep (bijv. '236' of '091').
4. Setcode: Letters/code linksonder (bijv. SM11, PAF, SV06).
5. Setnaam: Officiële set (bijv. Unified Minds, Paldean Fates).
6. Taal: Engels ('EN'), Japans ('JP') of Chinees ('CN').
7. Graded Slab herkenning:
   - isGraded: true als de kaart in een gecertificeerde slab zit (PSA, CGC, BGS), anders false.
   - gradingCompany: "PSA", "BGS", "CGC" of null.
   - grade: Cijfer op het label (bijv. "10", "9.5", "9") of null.
   - condition: Bij slab bijv. "PSA 9 Mint". Bij ongegradeerd: "Near Mint (NM)".
8. Reële Marktwaarde in euro:
   - Voor ongegradeerde kaarten: Cardmarket NL richtprijs (Near Mint).
   - Voor graded slabs: PriceCharting actuele veilingkoers voor DEZE SPECIFIEKE GRADE.
     Voorbeeld: Een PSA 9 van Naganadel GX #230 ligt historisch rond $15.85 (€ 14.60), NIET € 35. Een PSA 10 ligt rond $125 (€ 115). Geef een accuraat getal passend bij de specifieke grade!

Output UITSLUITEND een geldige JSON array:
[
  {
    "cardName": "Naganadel GX",
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
    "marketEstimateEUR": 14.60
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
                        "data": clean_b64
                    }
                }
            ]
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1,
            "maxOutputTokens": 1024
        }
    }

    raw_text = ""
    last_error = ""

    async with httpx.AsyncClient(timeout=35.0) as client:
        # Stap 1: Dynamisch actieve modellen ophalen die specifiek voor deze sleutel werken
        candidate_models = []
        try:
            m_res = await client.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}")
            if m_res.status_code == 200:
                m_data = m_res.json()
                for m in m_data.get("models", []):
                    methods = m.get("supportedGenerationMethods", [])
                    name = m.get("name", "")
                    if "generateContent" in methods and "flash" in name.lower():
                        clean_name = name.replace("models/", "")
                        candidate_models.append(clean_name)
        except Exception:
            pass

        # Stap 2: Voeg actuele standaard flash-modellen toe als fallback
        for fallback in ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-001", "gemini-flash-latest"]:
            if fallback not in candidate_models:
                candidate_models.append(fallback)

        # Stap 3: Analyse uitvoeren met het eerste beschikbare model
        for model_name in candidate_models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={key}"
            try:
                res = await client.post(url, json=gemini_body)
                if res.status_code == 200:
                    cands = res.json().get("candidates", [])
                    if cands:
                        parts = cands[0].get("content", {}).get("parts", [])
                        raw_text = "".join([p.get("text", "") for p in parts if "text" in p]).strip()
                        if raw_text:
                            break
                else:
                    last_error = f"{model_name} HTTP {res.status_code}: {res.text[:120]}"
            except Exception as e:
                last_error = str(e)

    if not raw_text:
        return JSONResponse(
            status_code=502,
            content={"detail": f"Analyse mislukt door AI provider ({last_error})."}
        )

    cleaned = raw_text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    try:
        cards_data = json.loads(cleaned)
        if not isinstance(cards_data, list):
            cards_data = [cards_data]
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"detail": f"Fout bij verwerken data: {str(e)}"}
        )

    results = []
    for c in cards_data:
        is_graded = bool(c.get("isGraded") or c.get("gradingCompany"))
        card_name = c.get("cardName", "Pokémon Kaart")
        card_num = str(c.get("cardNumber", ""))
        price_val = float(c.get("marketEstimateEUR") or 4.0)

        if is_graded:
            target_url = get_pricecharting_url(card_name, card_num)
            price_source = f"PriceCharting ({c.get('gradingCompany', 'Slab')} {c.get('grade', '')})".strip()
        else:
            target_url = get_base_cardmarket_url(card_name, card_num)
            price_source = "Cardmarket NL"

        results.append({
            "cardName": card_name,
            "cardNumber": card_num,
            "setTotal": c.get("setTotal", ""),
            "setCode": c.get("setCode", ""),
            "setName": c.get("setName", "Onbekende Set"),
            "language": c.get("language", "Engels"),
            "languageCode": c.get("languageCode", "EN"),
            "rarity": c.get("rarity", ""),
            "isGraded": is_graded,
            "gradingCompany": c.get("gradingCompany"),
            "grade": c.get("grade"),
            "condition": c.get("condition", "Near Mint (NM)"),
            "basePriceNM": price_val,
            "estimatedPriceEUR": price_val,
            "directMarketUrl": target_url,
            "priceSource": price_source
        })

    return JSONResponse(content=results)
