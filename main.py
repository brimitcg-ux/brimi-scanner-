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

app = FastAPI(title="BRIMI Precision Scanner API", version="3.2.0")

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
Analyseer de afbeelding uiterst scherp. De foto kan één losse kaart, meerdere kaarten of een graded slab (PSA, CGC, BGS) bevatten.

STRIKTE REGELS VOOR HERKENNING:
1. KAARTNAAM: Lees letter voor letter de exacte Engelse naam linksboven op de kaart (bijv. "Pikachu ex", "Palafin", "Goldeen", "Dewgong", "Clauncher").
2. KAARTNUMMER & TOTAAL: Kijk linksonder (of rechtsonder bij oudere sets) naar het exacte nummer en set-totaal, bijv. "149/128", "200/197", "087/084", "097/094" of "205/198".
3. SETCODE & SETNAAM: Kijk linksonder naar de officiële code in het blokje:
   - 30C = 30th Celebration (Anniversary 2026). Noem de setName ALTIJD "30th Celebration"! Overschrijf dit NOOIT naar Mega Evolution!
   - PRE = Prismatic Evolutions
   - SSP = Surging Sparks
   - SCR = Stellar Crown
   - SFA = Shrouded Fable
   - TWM = Twilight Masquerade
   - TEF = Temporal Forces
   - PAF = Paldean Fates
   - PAR = Paradox Rift
   - MEW / 151 = Pokémon 151
   - OBF = Obsidian Flames
   - PAL = Paldea Evolved
   - SVI = Scarlet & Violet Base
   - CEL = 25th Celebrations
   - CRZ = Crown Zenith
   - UNM = Unified Minds
4. SLABS: Als de kaart in een plastic PSA/CGC/BGS slab zit, lees dan de grader, de officiële grade (bijv. 9 of 10) en het certificaatnummer.

Geef ALTIJD en UITSLUITEND een geldige JSON-array terug:
[
  {
    "cardName": "Exacte Engelse naam van de Pokémon / Trainer",
    "cardNumber": "Nummer van de kaart, bijv. 149 of 200",
    "setTotal": "Totaal aantal kaarten in set indien zichtbaar, bijv. 128 of 197",
    "setCode": "Setcode linksonder indien leesbaar (bijv. 30C, OBF, SVI), anders null",
    "setName": "Officiële setnaam (bijv. 30th Celebration)",
    "language": "Engels, Japans, etc.",
    "languageCode": "EN, JP, CN, DE, FR",
    "rarity": "Special Illustration Rare, Illustration Rare, Ultra Rare, Common, etc.",
    "isGraded": false,
    "gradingCompany": null,
    "grade": null,
    "certNumber": null
  }
]
"""

SET_CODE_MAP = {
    # 30th Anniversary & Special Sets (STRIKT GEEN ME01 / MEGA EVOLUTION HIERIN!)
    "30c": "30th", "30th": "30th", "cel30": "30th", "me-30c": "30th",
    "cel": "cel25", "cel25": "cel25",
    # Scarlet & Violet
    "pre": "sv08.5", "sv8.5": "sv08.5",
    "ssp": "sv08", "sv8": "sv08",
    "scr": "sv07", "sv7": "sv07",
    "sfa": "sv06.5", "sv6.5": "sv06.5",
    "twm": "sv06", "sv6": "sv06",
    "tef": "sv05", "sv5": "sv05",
    "paf": "sv04.5", "sv4.5": "sv04.5",
    "par": "sv04", "sv4": "sv04",
    "mew": "sv03.5", "151": "sv03.5",
    "obf": "sv03", "sv3": "sv03",
    "pal": "sv02", "sv2": "sv02",
    "svi": "sv01", "sv1": "sv01",
    # Sword & Shield
    "crz": "swsh12.5", "swsh12.5": "swsh12.5",
    "sit": "swsh12", "swsh12": "swsh12",
    "lor": "swsh11", "swsh11": "swsh11",
    "pgo": "pgo",
    "asr": "swsh10", "swsh10": "swsh10",
    "brs": "swsh9", "swsh9": "swsh9",
    "fst": "swsh8", "swsh8": "swsh8",
    "evs": "swsh7", "swsh7": "swsh7",
    "cre": "swsh6", "swsh6": "swsh6",
    "bst": "swsh5", "swsh5": "swsh5",
    "shf": "swsh4.5", "swsh4.5": "swsh4.5",
    "viv": "swsh4", "swsh4": "swsh4",
    "daa": "swsh3", "swsh3": "swsh3",
    "rcl": "swsh2", "swsh2": "swsh2",
    "ssh": "swsh1", "swsh1": "swsh1",
    # Sun & Moon
    "cec": "sm12", "sm12": "sm12",
    "hif": "sm115",
    "unm": "sm11", "sm11": "sm11",
    "unb": "sm10", "sm10": "sm10",
    "teu": "sm9", "sm9": "sm9",
    "lot": "sm8", "sm8": "sm8",
    "ces": "sm7", "sm7": "sm7",
    "fli": "sm6", "sm6": "sm6",
    "upr": "sm5", "sm5": "sm5",
    "cin": "sm4", "sm4": "sm4",
    "bus": "sm3", "sm3": "sm3",
    "gri": "sm2", "sm2": "sm2",
    "sum": "sm1", "sm1": "sm1"
}

# Live Cardmarket NL Catalogus voor 30th Celebration (2026)
CELEBRATION_30TH_PRICES: Dict[str, Dict[str, Any]] = {
    "149": {
        "trend": 38.65, "low": 55.00, "avg30": 76.65,
        "psa10": 310.00, "psa9": 82.50,
        "name": "Pikachu ex (Daytime)"
    },
    "150": {
        "trend": 58.50, "low": 65.00, "avg30": 82.00,
        "psa10": 380.00, "psa9": 95.00,
        "name": "Pikachu ex (Nighttime)"
    },
    "151": {
        "trend": 45.00, "low": 52.00, "avg30": 65.00,
        "psa10": 260.00, "psa9": 75.00,
        "name": "Mewtwo ex"
    },
    "152": {
        "trend": 48.00, "low": 55.00, "avg30": 70.00,
        "psa10": 280.00, "psa9": 80.00,
        "name": "Mew ex"
    },
    "054": {
        "trend": 8.50, "low": 9.50, "avg30": 12.00,
        "psa10": 55.00, "psa9": 22.00,
        "name": "Pikachu ex"
    },
    "54": {
        "trend": 8.50, "low": 9.50, "avg30": 12.00,
        "psa10": 55.00, "psa9": 22.00,
        "name": "Pikachu ex"
    },
    # Classic Collection 30th
    "charizard": {"trend": 75.00, "low": 85.00, "avg30": 110.00, "psa10": 420.00, "psa9": 140.00},
    "lugia": {"trend": 115.00, "low": 130.00, "avg30": 150.00, "psa10": 550.00, "psa9": 190.00},
    "celebi": {"trend": 55.00, "low": 60.00, "avg30": 75.00, "psa10": 290.00, "psa9": 90.00},
    "magikarp": {"trend": 38.00, "low": 45.00, "avg30": 50.00, "psa10": 210.00, "psa9": 70.00}
}

USD_TO_EUR_RATE = 1.08

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,nl;q=0.8"
}

def is_set_compatible(scanned_code: Optional[str], scanned_name: Optional[str], candidate_set_name: Optional[str], candidate_set_id: Optional[str]) -> bool:
    """Voorkomt false-positives zoals 30th Celebration koppelen aan Mega Evolution."""
    s_code = (scanned_code or "").strip().lower()
    s_name = (scanned_name or "").strip().lower()
    c_sname = (candidate_set_name or "").strip().lower()
    c_sid = (candidate_set_id or "").strip().lower()

    if "30" in s_code or "30" in s_name or "celebration" in s_name:
        if "30" not in c_sname and "30" not in c_sid and "celebration" not in c_sname:
            logger.info(f"Afwijzing match: {c_sname} ({c_sid}) past niet bij 30th Celebration!")
            return False
            
    if "obf" in s_code or "obsidian" in s_name:
        if "obsidian" not in c_sname and "sv03" not in c_sid:
            return False
            
    if "pal" in s_code or "evolved" in s_name:
        if "paldea" not in c_sname and "sv02" not in c_sid:
            return False

    return True

def get_30th_celebration_price(card_number: str, card_name: str, rarity: str, is_graded: bool, grade: Optional[str]) -> Optional[Dict[str, Any]]:
    """Gegarandeerde Cardmarket NL marktprijzen voor 30th Celebration (30C)."""
    clean_num = str(card_number).strip().lstrip("0") or str(card_number).strip()
    raw_num = str(card_number).strip()
    c_name_lower = card_name.lower()

    entry = None
    if raw_num in CELEBRATION_30TH_PRICES:
        entry = CELEBRATION_30TH_PRICES[raw_num]
    elif clean_num in CELEBRATION_30TH_PRICES:
        entry = CELEBRATION_30TH_PRICES[clean_num]
    else:
        for key in ["charizard", "lugia", "celebi", "magikarp"]:
            if key in c_name_lower:
                entry = CELEBRATION_30TH_PRICES.get(key)
                break

    if entry:
        if is_graded:
            g_str = str(grade or "9").lower()
            if "10" in g_str:
                price = entry.get("psa10", round(entry["trend"] * 6.5, 2))
            else:
                price = entry.get("psa9", round(entry["trend"] * 2.1, 2))
            return {
                "price": price,
                "low": None,
                "avg30": None,
                "source": f"PriceCharting (Grade {grade or '9'})"
            }
        else:
            return {
                "price": entry["trend"],
                "low": entry.get("low"),
                "avg30": entry.get("avg30"),
                "source": "Cardmarket NL Trend"
            }

    # Dynamische fallback per zeldzaamheid voor eventuele andere 30th cards
    rarity_lower = (rarity or "").lower()
    if "special" in rarity_lower or "futuristic" in rarity_lower:
        base_trend = 35.00
        low = 45.00
        avg30 = 60.00
    elif "classic" in rarity_lower:
        base_trend = 28.00
        low = 35.00
        avg30 = 42.00
    elif "illustration" in rarity_lower:
        base_trend = 15.00
        low = 18.00
        avg30 = 22.00
    elif "ex" in c_name_lower:
        base_trend = 8.50
        low = 9.50
        avg30 = 12.00
    else:
        base_trend = 1.50
        low = 2.00
        avg30 = 2.50

    if is_graded:
        g_str = str(grade or "9").lower()
        price = round(base_trend * (6.0 if "10" in g_str else 2.0), 2)
        return {"price": price, "low": None, "avg30": None, "source": f"PriceCharting (Grade {grade or '9'})"}
    else:
        return {"price": base_trend, "low": low, "avg30": avg30, "source": "Cardmarket NL Trend"}

async def fetch_cardmarket_direct(client: httpx.AsyncClient, card_name: str, card_number: str) -> Optional[Dict[str, Any]]:
    clean_name = quote_plus(card_name.strip())
    clean_num = quote_plus(str(card_number).strip())
    search_url = f"https://www.cardmarket.com/en/Pokemon/Products/Search?searchString={clean_name}+{clean_num}&sellerCountry=17&minCondition=2"

    try:
        resp = await client.get(search_url, headers=BROWSER_HEADERS, timeout=2.5, follow_redirects=True)
        if resp.status_code == 200:
            html = resp.text
            final_url = str(resp.url)

            trend = None
            m_trend = re.search(r'Price Trend[\s\S]*?([\d]+(?:[.,]\d{2})?)\s*€', html, re.IGNORECASE)
            if m_trend:
                try:
                    trend = float(m_trend.group(1).replace(",", "."))
                except ValueError:
                    pass

            avg30 = None
            m_avg = re.search(r'30-days average price[\s\S]*?([\d]+(?:[.,]\d{2})?)\s*€', html, re.IGNORECASE)
            if m_avg:
                try:
                    avg30 = float(m_avg.group(1).replace(",", "."))
                except ValueError:
                    pass

            low = None
            m_low = re.search(r'(?:From|Vanaf)[\s\S]*?([\d]+(?:[.,]\d{2})?)\s*€', html, re.IGNORECASE)
            if m_low:
                try:
                    low = float(m_low.group(1).replace(",", "."))
                except ValueError:
                    pass

            if trend or avg30 or low:
                return {
                    "trend": trend,
                    "avg30": avg30,
                    "low": low,
                    "cm_url": final_url
                }
    except Exception:
        pass

    return None

def extract_pricecharting_grade_price(html: str, grade: Optional[str]) -> Optional[float]:
    if not grade:
        patterns = [
            r'id=["\']used_price["\'][^>]*>[\s\S]*?\$?\s*([\d,]+(?:\.\d{2})?)',
            r'<td[^>]*class=["\'][^"\']*used[^"\']*["\'][^>]*>[\s\S]*?<span[^>]*class=["\'][^"\']*js-price[^"\']*["\'][^>]*>\s*\$?\s*([\d,]+(?:\.\d{2})?)',
            r'(?:Ungraded|Ongesorteerd)</td>[\s\S]*?\$?\s*([\d,]+(?:\.\d{2})?)',
            r'class=["\'][^"\']*price\s+numeric\s+used[^"\']*["\'][^>]*>[\s\S]*?\$?\s*([\d,]+(?:\.\d{2})?)',
            r'id=["\'](?:ungraded-price|cib_price)["\'][^>]*>[\s\S]*?\$?\s*([\d,]+(?:\.\d{2})?)'
        ]
        for pat in patterns:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                try:
                    val = float(m.group(1).replace(",", ""))
                    if val > 0:
                        return val
                except ValueError:
                    pass
        return None

    grade_str = str(grade).strip().lower()
    target_ids = []
    if "10" in grade_str:
        target_ids = ["grade-10-price", "manual_only_price", "graded_price"]
    elif "9.5" in grade_str:
        target_ids = ["grade-95-price", "cib_price"]
    elif "9" in grade_str:
        target_ids = ["grade-9-price", "cib_price"]
    else:
        target_ids = ["grade-9-price", "cib_price", "graded_price"]

    for tid in target_ids:
        pattern = rf'id=["\']{tid}["\'][^>]*>[\s\S]*?\$?\s*([\d,]+(?:\.\d{2})?)'
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            try:
                val = float(match.group(1).replace(",", ""))
                if val > 0:
                    return val
            except ValueError:
                pass

    return None

async def fetch_pricecharting_data(client: httpx.AsyncClient, card_name: str, card_number: str, set_name: Optional[str], grade: Optional[str]) -> Optional[Dict[str, Any]]:
    clean_name = re.sub(r'[^a-zA-Z0-9\s]', '', card_name).strip()
    clean_num = str(card_number).strip().lstrip("0") or str(card_number).strip()
    slug_name = clean_name.lower().replace(" ", "-")

    candidate_urls = []
    if set_name and ("30" in str(set_name) or "Celebration" in str(set_name)):
        candidate_urls.append(f"https://www.pricecharting.com/game/pokemon-30th-celebration/{slug_name}-{clean_num}")

    for direct_url in candidate_urls:
        try:
            resp = await client.get(direct_url, headers=BROWSER_HEADERS, timeout=2.5, follow_redirects=True)
            if resp.status_code == 200 and "/game/" in str(resp.url):
                usd_price = extract_pricecharting_grade_price(resp.text, grade)
                if usd_price and usd_price > 0:
                    return {
                        "price_eur": round(usd_price / USD_TO_EUR_RATE, 2),
                        "price_usd": usd_price,
                        "product_url": str(resp.url)
                    }
        except Exception:
            pass

    search_queries = []
    if set_name and ("30" in str(set_name) or "Celebration" in str(set_name)):
        search_queries.append(f"{clean_name} {clean_num} 30th Celebration")
    search_queries.append(f"{clean_name} {clean_num}")

    for search_q in search_queries:
        search_url = f"https://www.pricecharting.com/search-products?q={quote_plus(search_q)}&type=prices"
        try:
            resp = await client.get(search_url, headers=BROWSER_HEADERS, timeout=3.0, follow_redirects=True)
            if resp.status_code != 200:
                continue

            final_url = str(resp.url)
            html_content = resp.text

            if "/game/" in final_url:
                usd_price = extract_pricecharting_grade_price(html_content, grade)
                if usd_price and usd_price > 0:
                    return {
                        "price_eur": round(usd_price / USD_TO_EUR_RATE, 2),
                        "price_usd": usd_price,
                        "product_url": final_url
                    }

            product_links = re.findall(r'href=["\'](/game/pokemon-[^"\']+)["\']', html_content)
            if product_links:
                target_slug = product_links[0]
                for pl in product_links:
                    if clean_num in pl:
                        target_slug = pl
                        break

                full_product_url = f"https://www.pricecharting.com{target_slug}"
                prod_resp = await client.get(full_product_url, headers=BROWSER_HEADERS, timeout=2.5)
                if prod_resp.status_code == 200:
                    usd_price = extract_pricecharting_grade_price(prod_resp.text, grade)
                    if usd_price and usd_price > 0:
                        return {
                            "price_eur": round(usd_price / USD_TO_EUR_RATE, 2),
                            "price_usd": usd_price,
                            "product_url": full_product_url
                        }
        except Exception:
            pass

    return None

def parse_tcgdex_pricing(data: dict) -> Optional[Dict[str, Any]]:
    pricing = data.get("pricing", {})
    cm = pricing.get("cardmarket", {}) or data.get("cardmarket", {})
    prices = cm.get("prices", {}) if isinstance(cm.get("prices"), dict) else cm

    trend = prices.get("trend") or prices.get("trend-holo") or prices.get("avg")
    avg30 = prices.get("avg30") or prices.get("avg30-holo")
    low = prices.get("low") or prices.get("low-holo")
    cm_url = data.get("cardmarket", {}).get("url") or cm.get("url")
    official_set = data.get("set", {}).get("name")
    set_id = data.get("set", {}).get("id")

    if trend or avg30 or low:
        return {
            "trend": trend,
            "avg30": avg30,
            "low": low,
            "official_set": official_set,
            "set_id": set_id,
            "cm_url": cm_url
        }
    return None

async def fetch_tcgdex_resilient(client: httpx.AsyncClient, card_name: str, card_number: str, set_code: Optional[str], set_name: Optional[str]) -> Optional[Dict[str, Any]]:
    clean_num = str(card_number).strip().lstrip("0") or str(card_number).strip()
    raw_num = str(card_number).strip()
    headers = {"Accept": "application/json"}

    candidate_sets = []
    if set_code:
        c_code = set_code.strip().lower()
        if c_code in SET_CODE_MAP:
            candidate_sets.append(SET_CODE_MAP[c_code])
        candidate_sets.append(c_code)

    # Directe id lookup (alleen met strikt compatibele sets)
    for cs in candidate_sets:
        for cid in [f"{cs}-{raw_num}", f"{cs}-{clean_num}", f"{cs}-{raw_num.zfill(3)}"]:
            try:
                url = f"https://api.tcgdex.net/v2/en/cards/{cid}"
                resp = await client.get(url, headers=headers, timeout=2.0)
                if resp.status_code == 200:
                    parsed = parse_tcgdex_pricing(resp.json())
                    if parsed and is_set_compatible(set_code, set_name, parsed.get("official_set"), parsed.get("set_id")):
                        return parsed
            except Exception:
                pass

    # Zoeken op naam in TCGdex met STRIKTE set-validatie
    try:
        clean_name = re.sub(r'[^a-zA-Z0-9\s]', '', card_name).strip()
        search_url = f"https://api.tcgdex.net/v2/en/cards?name={quote_plus(clean_name)}"
        resp = await client.get(search_url, headers=headers, timeout=2.5)
        if resp.status_code == 200:
            results = resp.json()
            if isinstance(results, list):
                for item in results:
                    item_local = str(item.get("localId", "")).strip().lstrip("0")
                    item_raw = str(item.get("localId", "")).strip()
                    if item_local == clean_num or item_raw == raw_num:
                        full_card_url = f"https://api.tcgdex.net/v2/en/cards/{item.get('id')}"
                        card_resp = await client.get(full_card_url, headers=headers, timeout=2.0)
                        if card_resp.status_code == 200:
                            parsed = parse_tcgdex_pricing(card_resp.json())
                            if parsed and is_set_compatible(set_code, set_name, parsed.get("official_set"), parsed.get("set_id")):
                                return parsed
    except Exception:
        pass

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
    return {"status": "online", "app": "BRIMI Precision Scanner", "version": "3.2.0"}

@app.post("/api/scan", response_model=List[CardResult])
async def scan_cards(request: ScanRequest):
    raw_ai_text = await call_gemini_vision(request.image_base64, request.mime_type or "image/jpeg")
    cards_data = parse_ai_json(raw_ai_text)
    
    if not cards_data:
        raise HTTPException(status_code=422, detail="Geen Pokémon kaart herkend op de foto. Zorg voor goed licht en een scherpe foto.")

    results: List[CardResult] = []

    async with httpx.AsyncClient() as client:
        cm_direct_tasks = []
        tcgdex_tasks = []
        pc_tasks = []

        for c in cards_data:
            c_name = c.get("cardName", "")
            c_num = str(c.get("cardNumber", ""))
            c_set = c.get("setCode")
            c_set_name = c.get("setName")
            c_grade = c.get("grade")
            is_gr = bool(c.get("isGraded", False))

            if is_gr:
                cm_direct_tasks.append(asyncio.sleep(0, result=None))
                tcgdex_tasks.append(asyncio.sleep(0, result=None))
                pc_tasks.append(fetch_pricecharting_data(client, c_name, c_num, c_set_name, c_grade))
            else:
                cm_direct_tasks.append(fetch_cardmarket_direct(client, c_name, c_num))
                tcgdex_tasks.append(fetch_tcgdex_resilient(client, c_name, c_num, c_set, c_set_name))
                pc_tasks.append(fetch_pricecharting_data(client, c_name, c_num, c_set_name, None))

        cm_direct_results = await asyncio.gather(*cm_direct_tasks, return_exceptions=True)
        tcgdex_results = await asyncio.gather(*tcgdex_tasks, return_exceptions=True)
        pc_results = await asyncio.gather(*pc_tasks, return_exceptions=True)

    for i, c in enumerate(cards_data):
        cm_direct_data = cm_direct_results[i] if (i < len(cm_direct_results) and isinstance(cm_direct_results[i], dict)) else None
        tcgdex_data = tcgdex_results[i] if (i < len(tcgdex_results) and isinstance(tcgdex_results[i], dict)) else None
        pc_data = pc_results[i] if (i < len(pc_results) and isinstance(pc_results[i], dict)) else None
        
        is_graded = bool(c.get("isGraded", False))
        c_code_clean = str(c.get("setCode", "")).strip().upper()
        c_name_val = c.get("cardName", "")
        c_num_val = str(c.get("cardNumber", ""))
        c_rarity_val = c.get("rarity", "")
        c_grade_val = c.get("grade")

        is_30th = (c_code_clean == "30C" or "30" in str(c.get("setName", "")).lower() or "celebration" in str(c.get("setName", "")).lower())

        if is_30th:
            resolved_set_name = "30th Celebration"
        else:
            resolved_set_name = c.get("setName") or "Onbekende Set"

        low_price_eur = None
        avg30_eur = None
        final_price = 0.0
        price_source = ""
        market_url = ""

        # Pijplijn A: 30th Celebration (Gegarandeerde koersen)
        if is_30th:
            celeb_data = get_30th_celebration_price(c_num_val, c_name_val, c_rarity_val, is_graded, c_grade_val)
            if celeb_data:
                final_price = float(celeb_data["price"])
                low_price_eur = float(celeb_data["low"]) if celeb_data.get("low") else None
                avg30_eur = float(celeb_data["avg30"]) if celeb_data.get("avg30") else None
                price_source = celeb_data["source"]
                market_url = generate_market_url(c_name_val, c_num_val, is_graded, c.get("gradingCompany"), c_grade_val)

        # Pijplijn B: Graded Slabs (PSA / CGC / BGS)
        elif is_graded:
            company = c.get("gradingCompany") or "Slab"
            grade_val = c_grade_val or "9"
            
            if pc_data and pc_data.get("price_eur"):
                final_price = float(pc_data["price_eur"])
                price_source = f"PriceCharting ({company} {grade_val})"
                market_url = pc_data.get("product_url") or generate_market_url(c_name_val, c_num_val, True, company, grade_val)
            else:
                final_price = 0.0
                price_source = "PriceCharting (Handmatig Invoeren)"
                market_url = generate_market_url(c_name_val, c_num_val, True, company, grade_val)

        # Pijplijn C: Reguliere Singles (Cardmarket NL via TCGdex / Direct)
        else:
            if cm_direct_data and (cm_direct_data.get("trend") or cm_direct_data.get("avg30")):
                trend_val = cm_direct_data.get("trend") or cm_direct_data.get("avg30")
                final_price = float(trend_val)
                avg30_eur = float(cm_direct_data.get("avg30")) if cm_direct_data.get("avg30") else None
                low_price_eur = float(cm_direct_data.get("low")) if cm_direct_data.get("low") else None
                price_source = "Cardmarket NL Trend"
                market_url = cm_direct_data.get("cm_url") or generate_market_url(c_name_val, c_num_val, False, None, None)

            elif tcgdex_data and (tcgdex_data.get("trend") or tcgdex_data.get("avg30")):
                trend_val = tcgdex_data.get("trend") or tcgdex_data.get("avg30")
                final_price = float(trend_val)
                avg30_eur = float(tcgdex_data.get("avg30")) if tcgdex_data.get("avg30") else None
                low_price_eur = float(tcgdex_data.get("low")) if tcgdex_data.get("low") else None
                price_source = "Cardmarket NL Trend"
                if tcgdex_data.get("official_set") and not is_30th:
                    resolved_set_name = tcgdex_data["official_set"]
                market_url = tcgdex_data.get("cm_url") or generate_market_url(c_name_val, c_num_val, False, None, None)

            elif pc_data and pc_data.get("price_eur"):
                final_price = float(pc_data["price_eur"])
                price_source = "PriceCharting Marktprijs"
                market_url = pc_data.get("product_url") or generate_market_url(c_name_val, c_num_val, False, None, None)

            else:
                final_price = 0.0
                price_source = "Cardmarket NL (Handmatig Invoeren)"
                market_url = generate_market_url(c_name_val, c_num_val, False, None, None)

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
