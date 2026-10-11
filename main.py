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

app = FastAPI(title="BRIMI Scanner Engine", version="8.7.0")

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

def robust_parse_cards(raw_text: str) -> List[Dict[str, Any]]:
    """
    Onbreekbare JSON/Object parser:
    1. Probeert standaard json.loads
    2. Probeert JSON auto-repair (afgeknipte arrays/strings afronden)
    3. Fallback: Regex extractie per object blok
    """
    cleaned = raw_text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    # 1. Directe poging
    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
    except Exception:
        pass

    # 2. Reparatie poging voor afgeknipte array
    try:
        # Zoek eerste '[' en laatste '}'
        start_idx = cleaned.find("[")
        if start_idx != -1:
            sub = cleaned[start_idx:]
            last_brace = sub.rfind("}")
            if last_brace != -1:
                repaired = sub[:last_brace + 1].strip() + "]"
                data = json.loads(repaired)
                if isinstance(data, list):
                    return data
    except Exception:
        pass

    # 3. Fallback: Regex extractie van elk { ... } blok
    card_objects = []
    blocks = re.findall(r"\{[^{}]*\}", cleaned, re.DOTALL)
    for b in blocks:
        obj = {}
        # Zoek basis properties
        name_m = re.search(r'"cardName"\s*:\s*"([^"]+)"', b)
        num_m = re.search(r'"cardNumber"\s*:\s*"([^"]+)"', b)
        tot_m = re.search(r'"setTotal"\s*:\s*"([^"]+)"', b)
        code_m = re.search(r'"setCode"\s*:\s*"([^"]+)"', b)
        set_m = re.search(r'"setName"\s*:\s*"([^"]+)"', b)
        lang_m = re.search(r'"language"\s*:\s*"([^"]+)"', b)
        price_m = re.search(r'"marketEstimateEUR"\s*:\s*([0-9\.]+)', b)
        graded_m = re.search(r'"isGraded"\s*:\s*(true|false)', b, re.IGNORECASE)
        grade_m = re.search(r'"grade"\s*:\s*([0-9\.]+)', b)
        comp_m = re.search(r'"gradingCompany"\s*:\s*"([^"]+)"', b)

        if name_m:
            obj["cardName"] = name_m.group(1)
            obj["cardNumber"] = num_m.group(1) if num_m else ""
            obj["setTotal"] = tot_m.group(1) if tot_m else ""
            obj["setCode"] = code_m.group(1) if code_m else ""
            obj["setName"] = set_m.group(1) if set_m else "Pokémon Set"
            obj["language"] = lang_m.group(1) if lang_m else "Engels"
            obj["languageCode"] = "EN"
            obj["isGraded"] = True if (graded_m and graded_m.group(1).lower() == "true") else False
            obj["gradingCompany"] = comp_m.group(1) if comp_m else None
            obj["grade"] = grade_m.group(1) if grade_m else None
            obj["condition"] = f"{obj['gradingCompany']} {obj['grade']}" if obj["isGraded"] else "Near Mint (NM)"
            obj["marketEstimateEUR"] = float(price_m.group(1)) if price_m else 4.50
            card_objects.append(obj)

    if card_objects:
        return card_objects

    return []

@app.get("/")
def health_check():
    key_configured = bool(os.getenv("GEMINI_API_KEY", "").strip())
    return {
        "status": "online",
        "engine": "BRIMI Precision Engine 8.7",
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

    clean_b64 = payload.image_base64
    if "," in clean_b64:
        clean_b64 = clean_b64.split(",", 1)[1]
    clean_b64 = clean_b64.replace("\n", "").replace("\r", "").strip()

    prompt = """
Analyseer alle Pokémon kaarten op de afbeelding. Dit kan 1 enkele kaart zijn of meerdere tegelijk op tafel.
Scan elke kaart systematisch van links naar rechts en boven naar onder.

Geef per kaart:
- cardName: Officiële kaartnaam (bijv. Goldeen, Charizard ex, Vaporeon).
- cardNumber: Nummer vóór de schuine streep (bijv. "064").
- setTotal: Totaal ná de schuine streep (bijv. "064").
- setCode: Setcode linksonder (bijv. MEW, OBF, TEU).
- setName: Setnaam (bijv. 151, Obsidian Flames).
- language: "Engels", "Japans" of "Chinees".
- languageCode: "EN", "JP" of "CN".
- rarity: Zeldzaamheid (bijv. Illustration Rare, Secret Rare, Ultra Rare).
- isGraded: true als in PSA/CGC/BGS slab, anders false.
- gradingCompany: "PSA", "BGS", "CGC" of null.
- grade: Cijfer op slab label of null.
- condition: Bij slab bijv. "PSA 9 Mint", anders "Near Mint (NM)".
- marketEstimateEUR: Reële Cardmarket NL prijs (ongegradeerd) of PriceCharting veilingkoers (slab).

Retourneer ALLEEN een geldige JSON array:
[
  {
    "cardName": "Goldeen",
    "cardNumber": "064",
    "setTotal": "064",
    "setCode": "MEW",
    "setName": "151",
    "language": "Engels",
    "languageCode": "EN",
    "rarity": "Illustration Rare",
    "isGraded": false,
    "gradingCompany": null,
    "grade": null,
    "condition": "Near Mint (NM)",
    "marketEstimateEUR": 3.50
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
            "maxOutputTokens": 4096
        }
    }

    raw_text = ""
    last_error = ""

    async with httpx.AsyncClient(timeout=45.0) as client:
        # Dynamisch actieve modellen detecteren
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

        # Vaste fallback volgorde
        for fallback in ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest"]:
            if fallback not in candidate_models:
                candidate_models.append(fallback)

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

    cards_data = robust_parse_cards(raw_text)
    if not cards_data:
        return JSONResponse(
            status_code=500,
            content={"detail": "Geen kaartgegevens kunnen structureren uit het AI antwoord."}
        )

    results = []
    for c in cards_data:
        is_graded = bool(c.get("isGraded") or c.get("gradingCompany"))
        card_name = c.get("cardName", "Pokémon Kaart")
        card_num = str(c.get("cardNumber", ""))
        price_val = float(c.get("marketEstimateEUR") or 3.50)

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
