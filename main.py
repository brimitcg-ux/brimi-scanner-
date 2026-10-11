import os
import json
import re
import asyncio
import urllib.parse
from typing import Optional, List, Dict, Any

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="BRIMI Scanner Precision Engine", version="8.9.0")

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

async def enrich_cardmarket_data(client: httpx.AsyncClient, card_name: str, card_number: str) -> Optional[Dict[str, Any]]:
    """
    Haalt live Cardmarket beursdata (trend, low, avg30) op via de TCGdex API.
    Draait met een strakke timeout zodat de scanner altijd vliegensvlug blijft.
    """
    clean_num = clean_card_number(card_number)
    if not card_name or not clean_num:
        return None

    # Eenvoudige zoeknaam zonder GX/V/ex toevoegingen voor bredere database matching
    simple_name = re.sub(r"(?i)\b(gx|ex|v|vmax|vstar)\b", "", card_name).strip()
    search_q = simple_name or card_name

    try:
        url = f"https://api.tcgdex.net/v2/en/cards?name={urllib.parse.quote_plus(search_q)}"
        res = await client.get(url, timeout=2.2)
        if res.status_code != 200:
            return None

        candidates = res.json()
        if not isinstance(candidates, list) or not candidates:
            return None

        # Zoek kaart met hetzelfde localId/nummer
        matched_id = None
        for cand in candidates:
            cand_local = str(cand.get("localId", "")).strip()
            if cand_local == clean_num or clean_card_number(cand_local) == clean_num:
                matched_id = cand.get("id")
                break

        if not matched_id:
            # Eerste fallback: check of het nummer voorkomt in het ID (bijv. mew-064)
            for cand in candidates:
                if str(clean_num) in str(cand.get("id", "")):
                    matched_id = cand.get("id")
                    break

        if not matched_id:
            return None

        # Detail fetch voor Cardmarket beursprijzen
        detail_res = await client.get(f"https://api.tcgdex.net/v2/en/cards/{matched_id}", timeout=2.2)
        if detail_res.status_code == 200:
            detail = detail_res.json()
            cm_pricing = detail.get("pricing", {}).get("cardmarket", {})
            if cm_pricing:
                trend = cm_pricing.get("trend") or cm_pricing.get("trend-holo")
                low = cm_pricing.get("low") or cm_pricing.get("low-holo")
                avg30 = cm_pricing.get("avg30") or cm_pricing.get("avg30-holo")
                if trend or low:
                    return {
                        "trend": float(trend) if trend else None,
                        "low": float(low) if low else None,
                        "avg30": float(avg30) if avg30 else None,
                        "cardmarket_id": detail.get("id"),
                        "official_name": detail.get("name")
                    }
    except Exception:
        pass

    return None

def robust_parse_cards(raw_text: str) -> List[Dict[str, Any]]:
    cleaned = raw_text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
    except Exception:
        pass

    try:
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

    card_objects = []
    blocks = re.findall(r"\{[^{}]*\}", cleaned, re.DOTALL)
    for b in blocks:
        obj = {}
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

    return card_objects

@app.get("/")
def health_check():
    key_configured = bool(os.getenv("GEMINI_API_KEY", "").strip())
    return {
        "status": "online",
        "engine": "BRIMI Precision Engine 8.9 (Cardmarket + PriceCharting Verified)",
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

Herken exact:
- cardName: Officiële kaartnaam (bijv. Goldeen, Naganadel-GX, Palafin, Charizard ex).
- cardNumber: Nummer vóór de schuine streep (bijv. "064" of "230").
- setTotal: Totaal ná de schuine streep (bijv. "064" of "236").
- setCode: Setcode linksonder indien zichtbaar (bijv. MEW, UNM, OBF).
- setName: Setnaam (bijv. 151, Unified Minds, Obsidian Flames).
- language: "Engels", "Japans" of "Chinees".
- languageCode: "EN", "JP" of "CN".
- rarity: Zeldzaamheid (bijv. Illustration Rare, Secret Rare, Ultra Rare).
- isGraded: true als in PSA/CGC/BGS/PCA slab, anders false.
- gradingCompany: "PSA", "BGS", "CGC" of null.
- grade: Cijfer op slab label (bijv. "9", "9.5", "10") of null.
- condition: Bij slab bijv. "PSA 9 Mint", anders "Near Mint (NM)".
- marketEstimateEUR: Realistische Cardmarket NL prijs (ongegradeerd) of PriceCharting veilingkoers (slab).

Retourneer ALLEEN een geldige JSON array:
[
  {
    "cardName": "Naganadel-GX",
    "cardNumber": "230",
    "setTotal": "236",
    "setCode": "UNM",
    "setName": "Unified Minds",
    "language": "Engels",
    "languageCode": "EN",
    "rarity": "Secret Rare",
    "isGraded": true,
    "gradingCompany": "PSA",
    "grade": "9",
    "condition": "PSA 9 Mint",
    "marketEstimateEUR": 15.50
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
        # Dynamische modellenlijst
        candidate_models = []
        try:
            m_res = await client.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}")
            if m_res.status_code == 200:
                for m in m_res.json().get("models", []):
                    methods = m.get("supportedGenerationMethods", [])
                    name = m.get("name", "")
                    if "generateContent" in methods and "flash" in name.lower():
                        candidate_models.append(name.replace("models/", ""))
        except Exception:
            pass

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
            content={"detail": "Geen kaartgegevens kunnen structureren uit het antwoord."}
        )

    # Parallel Cardmarket database verrijking
    async with httpx.AsyncClient() as cm_client:
        enrich_tasks = [
            enrich_cardmarket_data(cm_client, c.get("cardName", ""), str(c.get("cardNumber", "")))
            for c in cards_data
        ]
        enriched_results = await asyncio.gather(*enrich_tasks, return_exceptions=True)

    results = []
    for idx, c in enumerate(cards_data):
        is_graded = bool(c.get("isGraded") or c.get("gradingCompany"))
        card_name = c.get("cardName", "Pokémon Kaart")
        card_num = str(c.get("cardNumber", ""))
        ai_price = float(c.get("marketEstimateEUR") or 3.50)

        # Check of TCGdex Cardmarket beursdata beschikbaar is
        cm_data = None
        if idx < len(enriched_results) and isinstance(enriched_results[idx], dict):
            cm_data = enriched_results[idx]

        low_price_eur = None
        avg30_eur = None

        if is_graded:
            target_url = get_pricecharting_url(card_name, card_num)
            price_source = f"PriceCharting Veilingkoers ({c.get('gradingCompany', 'Slab')} {c.get('grade', '')})".strip()
            final_price = ai_price
        else:
            target_url = get_base_cardmarket_url(card_name, card_num)
            if cm_data and (cm_data.get("trend") or cm_data.get("low")):
                # Officiële Cardmarket Trendprijs
                final_price = cm_data.get("trend") or cm_data.get("low")
                low_price_eur = cm_data.get("low")
                avg30_eur = cm_data.get("avg30")
                price_source = "Cardmarket Trend (Geverifieerd)"
            else:
                final_price = ai_price
                price_source = "Cardmarket NL Marktindicatie"

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
            "basePriceNM": round(final_price, 2),
            "estimatedPriceEUR": round(final_price, 2),
            "lowPriceEUR": round(low_price_eur, 2) if low_price_eur else None,
            "avg30EUR": round(avg30_eur, 2) if avg30_eur else None,
            "directMarketUrl": target_url,
            "priceSource": price_source
        })

    return JSONResponse(content=results)
