import os
import json
import re
import urllib.parse
from typing import Optional, List, Dict, Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="BRIMI Scanner Engine", version="8.1.0")

# CORS middleware configuratie
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Garandeer dat preflights (OPTIONS) en fouten ALTIJD correct beantwoord worden met CORS headers
@app.middleware("http")
async def cors_handler_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
                "Access-Control-Allow-Headers": "*",
            },
        )
    try:
        response = await call_next(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"detail": f"Interne serverfout: {str(exc)}"},
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

    # Sanitize base64 payload
    clean_b64 = payload.image_base64
    if "," in clean_b64:
        clean_b64 = clean_b64.split(",", 1)[1]
    clean_b64 = clean_b64.replace("\n", "").replace("\r", "").strip()

    prompt = """
Analyseer de Pokémon kaart op deze afbeelding:
1. Naam: Officiële Pokémon naam (bijv. Palafin, Naganadel GX, Charizard, Mewtwo).
2. Nummer: Cijfers vóór de schuine streep (bijv. '062' of '230').
3. Totaal: Cijfers na de schuine streep (bijv. '091' of '236').
4. Setcode: Letters/code linksonder (bijv. PAF, SV06, SM11).
5. Setnaam: Reeksnaam (bijv. Paldean Fates, Twilight Masquerade, Unified Minds).
6. Taal: Engels ('EN'), Japans ('JP') of Chinees ('CN').
7. Graded Slab herkenning:
   - isGraded: true als de kaart in een gecertificeerde slab zit (PSA, CGC, BGS), anders false.
   - gradingCompany: "PSA", "BGS", "CGC" of null.
   - grade: Cijfer op het label (bijv. "10", "9.5", "9") of null.
   - condition: Bij slab bijv. "PSA 9 Mint". Bij ongegradeerd: "Near Mint (NM)".
8. Reële Marktwaarde in euro:
   - Voor ongegradeerde kaarten: Cardmarket NL richtprijs (Near Mint).
   - Voor graded slabs: PriceCharting veilingwaarde voor DEZE SPECIFIEKE GRADE (voor bijv. Naganadel GX PSA 9 ca. € 14.80, PSA 10 ca. € 115).

Output UITSLUITEND een geldige JSON array:
[
  {
    "cardName": "Palafin",
    "cardNumber": "062",
    "setTotal": "091",
    "setCode": "PAF",
    "setName": "Paldean Fates",
    "language": "Engels",
    "languageCode": "EN",
    "rarity": "Illustration Rare",
    "isGraded": false,
    "gradingCompany": null,
    "grade": null,
    "condition": "Near Mint (NM)",
    "marketEstimateEUR": 4.50
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

    # Modellen proberen met automatische fallback
    models_to_try = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
    raw_text = ""
    last_error = ""

    async with httpx.AsyncClient(timeout=25.0) as client:
        for model_name in models_to_try:
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
                    last_error = f"{model_name} HTTP {res.status_code}"
            except Exception as e:
                last_error = str(e)

    if not raw_text:
        return JSONResponse(
            status_code=502,
            content={"detail": f"Geen analyse ontvangen van de engine ({last_error})."},
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
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"detail": f"Analyse-fout bij verwerken data: {str(e)}"},
            headers={"Access-Control-Allow-Origin": "*"}
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

    return JSONResponse(
        content=results,
        headers={"Access-Control-Allow-Origin": "*"}
    )
