import os
import json
import re
import urllib.parse
import asyncio
from typing import Optional, List, Dict, Any

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="BRIMI Fast Precision Engine", version="12.3.0")

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

SET_CODE_MAP: Dict[str, Dict[str, str]] = {
    # 30th Anniversary & Special Expansions
    "30C":  {"name": "30th Celebration", "tcgdex": "30c"},
    "30TH": {"name": "30th Celebration", "tcgdex": "30c"},
    "CEL":  {"name": "Celebrations", "tcgdex": "cel25"},
    "CEL25":{"name": "Celebrations", "tcgdex": "cel25"},

    # Scarlet & Violet Series
    "JTG": {"name": "Journey Together", "tcgdex": "sv09"},
    "PRE": {"name": "Prismatic Evolutions", "tcgdex": "sv08.5"},
    "SSP": {"name": "Surging Sparks", "tcgdex": "sv08"},
    "SCR": {"name": "Stellar Crown", "tcgdex": "sv07"},
    "SFA": {"name": "Shrouded Fable", "tcgdex": "sv06.5"},
    "TWM": {"name": "Twilight Masquerade", "tcgdex": "sv06"},
    "TEF": {"name": "Temporal Forces", "tcgdex": "sv05"},
    "PAF": {"name": "Paldean Fates", "tcgdex": "sv04.5"},
    "PAR": {"name": "Paradox Rift", "tcgdex": "sv04"},
    "MEW": {"name": "151", "tcgdex": "sv03.5"},
    "151": {"name": "151", "tcgdex": "sv03.5"},
    "OBF": {"name": "Obsidian Flames", "tcgdex": "sv03"},
    "PAL": {"name": "Paldea Evolved", "tcgdex": "sv02"},
    "SVI": {"name": "Scarlet & Violet Base", "tcgdex": "sv01"},

    # Sword & Shield Series
    "CRZ": {"name": "Crown Zenith", "tcgdex": "swsh12.5"},
    "CZ":  {"name": "Crown Zenith", "tcgdex": "swsh12.5"},
    "SIT": {"name": "Silver Tempest", "tcgdex": "swsh12"},
    "ST":  {"name": "Silver Tempest", "tcgdex": "swsh12"},
    "LOR": {"name": "Lost Origin", "tcgdex": "swsh11"},
    "LO":  {"name": "Lost Origin", "tcgdex": "swsh11"},
    "PGO": {"name": "Pokémon GO", "tcgdex": "pgo"},
    "ASR": {"name": "Astral Radiance", "tcgdex": "swsh10"},
    "BRS": {"name": "Brilliant Stars", "tcgdex": "swsh09"},
    "FST": {"name": "Fusion Strike", "tcgdex": "swsh08"},
    "EVS": {"name": "Evolving Skies", "tcgdex": "swsh07"},
    "CRE": {"name": "Chilling Reign", "tcgdex": "swsh06"},
    "BST": {"name": "Battle Styles", "tcgdex": "swsh05"},
    "SHF": {"name": "Shining Fates", "tcgdex": "swsh04.5"},
    "VIV": {"name": "Vivid Voltage", "tcgdex": "swsh04"},
    "CPA": {"name": "Champion's Path", "tcgdex": "swsh03.5"},
    "DAA": {"name": "Darkness Ablaze", "tcgdex": "swsh03"},
    "RCL": {"name": "Rebel Clash", "tcgdex": "swsh02"},
    "SSH": {"name": "Sword & Shield Base", "tcgdex": "swsh01"},

    # Sun & Moon Series
    "CEC": {"name": "Cosmic Eclipse", "tcgdex": "sm12"},
    "HIF": {"name": "Hidden Fates", "tcgdex": "sm115"},
    "UNM": {"name": "Unified Minds", "tcgdex": "sm11"},
    "UNB": {"name": "Unbroken Bonds", "tcgdex": "sm10"},
    "DET": {"name": "Detective Pikachu", "tcgdex": "det1"},
    "TEU": {"name": "Team Up", "tcgdex": "sm09"},
    "LOT": {"name": "Lost Thunder", "tcgdex": "sm08"},
    "DRM": {"name": "Dragon Majesty", "tcgdex": "sm75"},
    "CES": {"name": "Celestial Storm", "tcgdex": "sm07"},
    "FLI": {"name": "Forbidden Light", "tcgdex": "sm06"},
    "UPR": {"name": "Ultra Prism", "tcgdex": "sm05"},
    "CIN": {"name": "Crimson Invasion", "tcgdex": "sm04"},
    "SLG": {"name": "Shining Legends", "tcgdex": "sm35"},
    "BUS": {"name": "Burning Shadows", "tcgdex": "sm03"},
    "GRI": {"name": "Guardians Rising", "tcgdex": "sm02"},
    "SUM": {"name": "Sun & Moon Base", "tcgdex": "sm01"},

    # XY Series
    "EVO": {"name": "Evolutions", "tcgdex": "xy12"},
    "STS": {"name": "Steam Siege", "tcgdex": "xy11"},
    "FCO": {"name": "Fates Collide", "tcgdex": "xy10"},
    "GEN": {"name": "Generations", "tcgdex": "g1"},
    "BKP": {"name": "BREAKpoint", "tcgdex": "xy09"},
    "BKT": {"name": "BREAKthrough", "tcgdex": "xy08"},
    "AOR": {"name": "Ancient Origins", "tcgdex": "xy07"},
    "ROS": {"name": "Roaring Skies", "tcgdex": "xy06"},
    "PRC": {"name": "Primal Clash", "tcgdex": "xy05"},
    "PHF": {"name": "Phantom Forces", "tcgdex": "xy04"},
    "FFI": {"name": "Furious Fists", "tcgdex": "xy03"},
    "FLF": {"name": "Flashfire", "tcgdex": "xy02"},
    "XY":  {"name": "XY Base", "tcgdex": "xy01"}
}

def clean_card_number(raw_num: str) -> str:
    if not raw_num:
        return ""
    main_num = str(raw_num).split("/")[0].strip()
    stripped = re.sub(r"^0+", "", main_num)
    return stripped or "0"

def get_base_cardmarket_url(card_name: str, card_number: str) -> str:
    clean_num = clean_card_number(card_number)
    query = f"{card_name} {clean_num}".strip()
    return f"https://www.cardmarket.com/en/Pokemon/Products/Search?searchString={urllib.parse.quote_plus(query)}&sellerCountry=17&minCondition=2"

def get_pricecharting_url(card_name: str, card_number: str, set_name: Optional[str] = None) -> str:
    clean_num = clean_card_number(card_number)
    terms = ["pokemon", card_name, clean_num]
    if set_name and set_name != "Onbekende Set":
        terms.append(set_name)
    query = " ".join(terms).strip()
    return f"https://www.pricecharting.com/search-products?q={urllib.parse.quote_plus(query)}&type=prices"

def get_cert_url(company: Optional[str], cert_num: Optional[str]) -> Optional[str]:
    if not cert_num or not company:
        return None
    clean_cert = re.sub(r"[^0-9]", "", str(cert_num))
    if not clean_cert:
        return None
    comp_upper = company.upper()
    if "PSA" in comp_upper:
        return f"https://www.psacard.com/cert/{clean_cert}"
    if "CGC" in comp_upper:
        return f"https://www.cgccards.com/certlookup/{clean_cert}/"
    if "BGS" in comp_upper or "BECKETT" in comp_upper:
        return f"https://www.beckett.com/grading/card-lookup?item_type=BGS&item_id={clean_cert}"
    return None

async def query_tcgdex(client: httpx.AsyncClient, set_code: str, card_name: str, card_number: str) -> Optional[Dict[str, Any]]:
    clean_num = clean_card_number(card_number)
    raw_num = str(card_number).split("/")[0].strip()
    matched_set = SET_CODE_MAP.get(set_code.upper().strip()) if set_code else None

    if matched_set and matched_set.get("tcgdex"):
        set_id = matched_set["tcgdex"]
        for try_num in [raw_num, clean_num]:
            url = f"https://api.tcgdex.net/v2/en/sets/{set_id}/{try_num}"
            try:
                res = await client.get(url, timeout=2.0)
                if res.status_code == 200:
                    detail = res.json()
                    cm = detail.get("pricing", {}).get("cardmarket", {})
                    if cm:
                        trend = cm.get("trend") or cm.get("trend-holo")
                        low = cm.get("low") or cm.get("low-holo")
                        avg30 = cm.get("avg30") or cm.get("avg30-holo")
                        if trend or low:
                            return {
                                "trend": float(trend) if trend else None,
                                "low": float(low) if low else None,
                                "avg30": float(avg30) if avg30 else None,
                                "official_name": detail.get("name"),
                                "official_set": matched_set["name"]
                            }
            except Exception:
                pass

    if card_name and clean_num:
        clean_name = re.sub(r"(?i)\b(gx|ex|v|vmax|vstar|lv\.x|prime)\b", "", card_name).strip()
        search_q = clean_name or card_name
        try:
            url = f"https://api.tcgdex.net/v2/en/cards?name={urllib.parse.quote_plus(search_q)}"
            res = await client.get(url, timeout=2.0)
            if res.status_code == 200:
                cands = res.json()
                if isinstance(cands, list):
                    for cand in cands:
                        cand_local = str(cand.get("localId", "")).strip()
                        if cand_local in [clean_num, raw_num] or clean_card_number(cand_local) == clean_num:
                            card_id = cand.get("id")
                            if card_id:
                                d_res = await client.get(f"https://api.tcgdex.net/v2/en/cards/{card_id}", timeout=2.0)
                                if d_res.status_code == 200:
                                    detail = d_res.json()
                                    cm = detail.get("pricing", {}).get("cardmarket", {})
                                    if cm:
                                        trend = cm.get("trend") or cm.get("trend-holo")
                                        low = cm.get("low") or cm.get("low-holo")
                                        avg30 = cm.get("avg30") or cm.get("avg30-holo")
                                        if trend or low:
                                            return {
                                                "trend": float(trend) if trend else None,
                                                "low": float(low) if low else None,
                                                "avg30": float(avg30) if avg30 else None,
                                                "official_name": detail.get("name"),
                                                "official_set": detail.get("set", {}).get("name")
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
        comp_m = re.search(r'"gradingCompany"\s*:\s*"([^"]+)"', b)
        grade_m = re.search(r'"grade"\s*:\s*"([^"]+)"', b)
        cert_m = re.search(r'"certNumber"\s*:\s*"([^"]+)"', b)

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
            obj["certNumber"] = cert_m.group(1) if cert_m else None
            obj["condition"] = f"{obj['gradingCompany']} {obj['grade']}" if obj["isGraded"] else "Near Mint (NM)"
            obj["marketEstimateEUR"] = float(price_m.group(1)) if price_m else 2.50
            card_objects.append(obj)

    return card_objects

@app.get("/")
def health_check():
    raw_keys = os.getenv("GEMINI_API_KEY", "").strip()
    keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
    return {
        "status": "online",
        "engine": "BRIMI Fast Precision Engine 12.3 (Header Auth + AQ/AIza Multi-Key)",
        "configured_keys_count": len(keys)
    }

@app.post("/api/scan")
async def scan_cards(payload: ScanRequest):
    raw_keys = os.getenv("GEMINI_API_KEY", "").strip()
    if not raw_keys:
        return JSONResponse(
            status_code=500,
            content={"detail": "GEMINI_API_KEY ontbreekt in Render Environment Variables."}
        )

    api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]

    clean_b64 = payload.image_base64
    if "," in clean_b64:
        clean_b64 = clean_b64.split(",", 1)[1]
    clean_b64 = clean_b64.replace("\n", "").replace("\r", "").strip()

    prompt = """
Je bent een officiële Pokémon TCG OCR-analist. Analyseer alle officiële Pokémon kaarten op de foto.

BELANGRIJKE INSTRUCTIE VOOR 30TH CELEBRATION (30C) & ZELDZAAMHEDEN:
- Kaarten met setcode "30C" of 30-jaar stempel zijn 100% OFFICIËLE kaarten uit de "30th Celebration" set. NOOIT "Custom" of "Fan Art"!
- ONDERSCHEID REGULIERE ILLUSTRATION RARES STRIKT VAN SPECIAL ILLUSTRATION RARES (SIR):
  * Illustration Rare (IR) zoals Alolan Exeggutor 129/128, Drifloon, Dewgong etc. hebben een reële Cardmarket marktwaarde van slechts € 2.00 tot € 3.50! Schat deze NOOIT op € 35!
  * Alleen echte top-chase Special Illustration Rares (SIR) zoals Pikachu ex 149/128 zijn circa € 65.00 tot € 75.00.
  * Reguliere ex / GX / V kaarten zijn circa € 1.00 tot € 2.00.
  * Holo / Reverse Holo / Commons zijn € 0.20 tot € 0.80.

Lees voor ELKE kaart nauwkeurig:
1. cardName: Officiële kaartnaam (bijv. "Dewgong", "Alolan Exeggutor", "Pikachu ex", "Naganadel-GX").
2. cardNumber: Nummer vóór de slash (bijv. "129", "149", "064").
3. setTotal: Nummer ná de slash (bijv. "128", "064").
4. setCode: Setcode linksonder (bijv. 30C, MEW, UNM, OBF, PAL, PAR, CRZ).
5. setName: Volledige naam van de set (bijv. "30th Celebration", "151", "Unified Minds").
6. language: "Engels", "Japans" of "Chinees".
7. languageCode: "EN", "JP" of "CN".
8. rarity: "Illustration Rare", "Special Illustration Rare", "Ultra Rare", "Double Rare" of "Common".
9. isGraded: true als in slab (PSA, CGC, BGS), anders false.
10. gradingCompany: "PSA", "CGC", "BGS" of null.
11. grade: Cijfer op het label (bijv. "9", "10") of null.
12. certNumber: Serienummer van het label of null.
13. marketEstimateEUR: Realistische conservatieve Cardmarket bodem/richtprijs in EUR (bijv. 2.20 voor Dewgong, 2.40 voor Alolan Exeggutor, 68.00 voor Pikachu ex SIR).

Retourneer ALLEEN een geldige JSON array.
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
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"}
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1,
            "maxOutputTokens": 4096
        }
    }

    raw_text = ""
    last_error = ""

    candidate_models = [
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash-lite",
        "gemini-2.5-flash"
    ]

    async with httpx.AsyncClient(timeout=30.0) as client:
        for key_idx, key in enumerate(api_keys):
            headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": key
            }
            key_dead = False

            for model_name in candidate_models:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
                try:
                    res = await client.post(url, json=gemini_body, headers=headers)
                    if res.status_code == 200:
                        data = res.json()
                        cands = data.get("candidates", [])
                        if cands:
                            parts = cands[0].get("content", {}).get("parts", [])
                            raw_text = "".join([p.get("text", "") for p in parts if "text" in p]).strip()
                            if raw_text:
                                break
                            else:
                                finish_r = cands[0].get("finishReason", "UNKNOWN")
                                last_error = f"{model_name}: Lege reactie ({finish_r})"
                        else:
                            feedback = data.get("promptFeedback", {})
                            block_r = feedback.get("blockReason", "Geen kandidaten")
                            last_error = f"{model_name}: Geblokkeerd ({block_r})"
                    elif res.status_code in (401, 403):
                        last_error = f"Sleutel {key_idx + 1} ongeldig of geen rechten (HTTP {res.status_code})"
                        key_dead = True
                        break
                    elif res.status_code == 429:
                        last_error = f"Sleutel {key_idx + 1} limiet bereikt (HTTP 429)"
                        key_dead = True
                        break
                    elif res.status_code == 503:
                        last_error = f"{model_name} tijdelijk overbelast (HTTP 503)"
                        continue
                    elif res.status_code == 404:
                        last_error = f"{model_name} niet meer beschikbaar (HTTP 404)"
                        continue
                    else:
                        err_msg = ""
                        try:
                            err_msg = res.json().get("error", {}).get("message", res.text[:120])
                        except Exception:
                            err_msg = res.text[:120]
                        last_error = f"{model_name} (HTTP {res.status_code}: {err_msg})"
                except Exception as e:
                    last_error = f"{model_name} ({type(e).__name__}: {str(e) or 'timeout'})"

            if raw_text:
                break
            if key_dead:
                continue

    if not raw_text:
        return JSONResponse(
            status_code=502,
            content={
                "detail": f"AI Analyse tijdelijk niet gelukt ({last_error}). Probeer het over een minuutje nogmaals."
            }
        )

    cards_data = robust_parse_cards(raw_text)
    if not cards_data:
        return JSONResponse(
            status_code=500,
            content={"detail": "Geen kaartgegevens kunnen structureren uit het antwoord."}
        )

    # Snelle bevraging van TCGdex
    async with httpx.AsyncClient() as dex_client:
        tasks = [
            query_tcgdex(dex_client, c.get("setCode", ""), c.get("cardName", ""), str(c.get("cardNumber", "")))
            for c in cards_data
        ]
        tcgdex_results = await asyncio.gather(*tasks, return_exceptions=True)

    results = []
    for idx, c in enumerate(cards_data):
        is_graded = bool(c.get("isGraded") or c.get("gradingCompany"))
        card_name = c.get("cardName", "Pokémon Kaart")
        card_num = str(c.get("cardNumber", ""))
        set_code = str(c.get("setCode", "")).upper().strip()
        cert_num = str(c.get("certNumber", "")).strip() if c.get("certNumber") else None

        resolved_set_name = c.get("setName", "Onbekende Set")
        if set_code in SET_CODE_MAP:
            resolved_set_name = SET_CODE_MAP[set_code]["name"]

        ai_price = float(c.get("marketEstimateEUR") or 2.50)
        tcgdex_data = tcgdex_results[idx] if (idx < len(tcgdex_results) and isinstance(tcgdex_results[idx], dict)) else None

        low_price_eur = None
        avg30_eur = None

        if is_graded:
            target_url = get_pricecharting_url(card_name, card_num, resolved_set_name)
            price_source = f"PriceCharting Veilingkoers ({c.get('gradingCompany', 'Slab')} {c.get('grade', '')})".strip()
            final_price = ai_price
        else:
            target_url = get_base_cardmarket_url(card_name, card_num)

            # PRIORITEIT 1: Officiële Cardmarket Bodem (Low) via TCGdex
            if tcgdex_data and tcgdex_data.get("low"):
                final_price = tcgdex_data["low"]
                low_price_eur = tcgdex_data["low"]
                avg30_eur = tcgdex_data.get("avg30")
                price_source = "Cardmarket Bodemprijs"
                if tcgdex_data.get("official_set"):
                    resolved_set_name = tcgdex_data["official_set"]
            # PRIORITEIT 2: Cardmarket Trend
            elif tcgdex_data and tcgdex_data.get("trend"):
                final_price = tcgdex_data["trend"]
                avg30_eur = tcgdex_data.get("avg30")
                price_source = "Cardmarket Trend (Geverifieerd)"
                if tcgdex_data.get("official_set"):
                    resolved_set_name = tcgdex_data["official_set"]
            # PRIORITEIT 3: Gekalibreerde Richtprijs
            else:
                final_price = ai_price
                price_source = "Cardmarket NL Richtprijs"

        cert_verification_url = get_cert_url(c.get("gradingCompany"), cert_num) if is_graded else None

        results.append({
            "cardName": card_name,
            "cardNumber": card_num,
            "setTotal": c.get("setTotal", ""),
            "setCode": set_code,
            "setName": resolved_set_name,
            "language": c.get("language", "Engels"),
            "languageCode": c.get("languageCode", "EN"),
            "rarity": c.get("rarity", ""),
            "isGraded": is_graded,
            "gradingCompany": c.get("gradingCompany"),
            "grade": c.get("grade"),
            "certNumber": cert_num,
            "certUrl": cert_verification_url,
            "condition": c.get("condition", "Near Mint (NM)"),
            "basePriceNM": round(final_price, 2),
            "currentPrice": round(final_price, 2),
            "lowPriceEUR": round(low_price_eur, 2) if low_price_eur else None,
            "avg30EUR": round(avg30_eur, 2) if avg30_eur else None,
            "directMarketUrl": target_url,
            "priceSource": price_source
        })

    return JSONResponse(content=results)
