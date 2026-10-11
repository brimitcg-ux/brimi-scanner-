import os
import json
import re
import urllib.parse
from typing import Optional, List, Dict, Any

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="BRIMI Scanner Engine", version="8.0.0")

# Volledige CORS-toestemming voor alle origins (voorkomt Safari 'Load failed')
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Garandeer dat CORS-headers ALTIJD worden meegestuurd, ook bij uitzonderingen
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": f"Serverfout: {str(exc)}"},
        headers={"Access-Control-Allow-Origin": "*"}
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
    return {"status": "online", "engine": "BRIMI Precision Engine"}

@app.post("/api/scan")
async def scan_cards(payload: ScanRequest):
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        return JSONResponse(
            status_code=500,
            content={"detail": "GEMINI_API_KEY ontbreekt in Render Environment Variables."},
            headers={"Access-Control-Allow-Origin": "*"}
        )

    prompt = """
Analyseer de Pokémon kaart op deze afbeelding:
1. Naam: Officiële Pokémon naam (bijv. Naganadel GX, Charizard, Mewtwo).
2. Nummer: Cijfers vóór de schuine streep (bijv. '230' uit '230/236').
3. Totaal: Cijfers na de schuine streep (bijv. '236').
4. Setcode: Letters linksonder (bijv. SM11, SWSH, ME05).
5. Setnaam: Reeksnaam (bijv. Unified Minds).
6. Taal: Engels ('EN'), Japans ('JP') of Chinees ('CN').
7. Graded Slab herkenning:
   - isGraded: true als de kaart in een gecertificeerde slab zit (PSA, CGC, BGS, PCA), anders false.
   - gradingCompany: "PSA", "BGS", "CGC" of null.
   - grade: Het cijfer op het label (bijv. "10", "9.5", "9", "8") of null.
   - condition: Bij slab bijv. "PSA 9 Mint". Bij ongegradeerd: "Near Mint (NM)".
8. Reële Marktwaarde in euro:
   - Voor ongegradeerde kaarten: Cardmarket NL richtprijs.
   - Voor graded slabs: PriceCharting veilingwaarde voor DEZE SPECIFIEKE GRADE (voor bijv. Naganadel GX PSA 9 is dit ca. € 14.80, NIET € 35+).

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
    "marketEstimateEUR": 14.80
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
            "temperature": 0.1,
            "maxOutputTokens": 1024
        }
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"
    fallback_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"

    raw_text = ""
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            res = await client.post(url, json=gemini_body)
            if res.status_code == 200:
                cands = res.json().get("candidates", [])
                if cands:
                    parts = cands[0].get("content", {}).get("parts", [])
                    raw_text = "".join([p.get("text", "") for p in parts if "text" in p]).strip()
            
            if not raw_text:
                res = await client.post(fallback_url, json=gemini_body)
                if res.status_code == 200:
                    cands = res.json().get("candidates", [])
                    if cands:
                        parts = cands[0].get("content", {}).get("parts", [])
                        raw_text = "".join([p.get("text", "") for p in parts if "text" in p]).strip()
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content={"detail": f"Verbindingsfout naar AI: {str(e)}"},
                headers={"Access-Control-Allow-Origin": "*"}
            )

    if not raw_text:
        return JSONResponse(
            status_code=502,
            content={"detail": "Geen reactie ontvangen van de scan engine."},
            headers={"Access-Control-Allow-Origin": "*"}
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
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"detail": "Ongeldige JSON van analyse-engine."},
            headers={"Access-Control-Allow-Origin": "*"}
        )

    results = []
    for c in cards_data:
        is_graded = bool(c.get("isGraded") or c.get("gradingCompany"))
        card_name = c.get("cardName", "Pokémon Kaart")
        card_num = str(c.get("cardNumber", ""))
        price_val = float(c.get("marketEstimateEUR") or 5.0)

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

    return JSONResponse(
        content=results,
        headers={"Access-Control-Allow-Origin": "*"}
    )
