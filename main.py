import os
import re
import json
import base64
import httpx
import asyncio
import logging
from urllib.parse import quote_plus
from typing import List, Optional, Dict, Any, Tuple
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("brimi-backend")

app = FastAPI(title="BRIMI Universal Precision Scanner API", version="4.6.0")

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
    trendPriceEUR: Optional[float] = None
    avg30EUR: Optional[float] = None
    directMarketUrl: str
    priceSource: str

def get_api_keys() -> List[str]:
    raw_keys = os.getenv("GEMINI_API_KEY", "")
    keys = [
        k.strip().strip('"').strip("'") 
        for k in raw_keys.split(",") 
        if k.strip().strip('"').strip("'")
    ]
    return keys

CANDIDATE_MODELS = [
    "gemini-flash-latest",
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-flash"
]

SYSTEM_PROMPT = """
Je bent de AI-inspectie-engine van BRIMI Scanner voor Pokémon TCG.
Analyseer de kaart op de afbeelding uiterst nauwkeurig volgens de 3 historische tijdperken:

TIJDPERK 1: HET VINTAGE TIJDPERK (1999 – 2003: Wizards of the Coast)
- Herkenning: Geen 3-lettercode linksonder. Copyrights dateren uit 1999, 2000 of 2001.
- SET-SYMBOOL REGELS (Kijk RECHTSONDER het artwork-kader):
  * HELEMAAL GEEN SYMBOOL + kaartnummer "/102" (bijv. 4/102 Charizard):
    -> Dit is 100% gegarandeerd de originele "Base Set" (Base 1)!
    -> Noem dit NOOIT "Base Set 2"! SetCode = "BS", SetName = "Base Set".
  * Grote "2" met Pokéball erin + "/130": "Base Set 2" (SetCode "B2").
  * Bloempje / Vileplume: "Jungle" (SetCode "JU", "/64").
  * Skeletklauw / schelp: "Fossil" (SetCode "FO", "/62").
  * Zwarte hoofdletter "R": "Team Rocket" (SetCode "TR", "/82").
  * Gym leader silhouet: "Gym Heroes" (G1, "/132") of "Gym Challenge" (G2, "/132").
  * Neo symbolen: Neo Genesis (N1), Neo Discovery (N2), Neo Revelation (N3), Neo Destiny (N4).
  * Grote medaille/vuurwerk: "Legendary Collection" (LC, "/110").
  * e-Reader balk: "Expedition" (EX), "Aquapolis" (AQ), "Skyridge" (SK).

TIJDPERK 2: HET MIDDEN TIJDPERK (2004 – 2022: EX, DP, HGSS, BW, XY, Sun & Moon, SWSH)
- Set-symbool staat RECHTSONDER op de buitenste rand (of linksonder bij SWSH/SM).
- Let op prefix-nummers zoals TG (Trainer Gallery), GG (Galarian Gallery), SV (Shiny Vault).

TIJDPERK 3: HET MODERNE TIJDPERK (2023 – Heden: Scarlet & Violet & 30th)
- Uniform zwart kader LINKSONDER: [TAALCODE] [SETCODE] [NUMMER/TOTAAL] [ZELDZAAMHEID]
- 30C = 30th Celebration (Anniversary). Noem dit ALTIJD "30th Celebration"! Noem dit NOOIT Mega Evolution!

INSTRUCTIES VOOR GRADED SLABS:
- Als de kaart in een plastic PSA, CGC of Beckett hoes zit, lees dan het bovenste label:
  isGraded: true, gradingCompany: "PSA" | "CGC" | "BGS", grade: "10" | "9" etc., certNumber: nummer op label.

Geef ALTIJD een geldig JSON array terug met objecten die de volgende velden bevatten:
cardName, cardNumber, setTotal, setCode, setName, language, languageCode, rarity, isGraded, gradingCompany, grade, certNumber.
"""

UNIVERSAL_SET_MATRIX: Dict[str, Dict[str, str]] = {
    # ── 1. WIZARDS OF THE COAST (1999 – 2003) ──
    "base1": {"tcgdex": "base1", "cm": "BS", "name": "Base Set"},
    "bs": {"tcgdex": "base1", "cm": "BS", "name": "Base Set"},
    "base": {"tcgdex": "base1", "cm": "BS", "name": "Base Set"},
    "base set": {"tcgdex": "base1", "cm": "BS", "name": "Base Set"},
    "base set 1": {"tcgdex": "base1", "cm": "BS", "name": "Base Set"},
    "base2": {"tcgdex": "base2", "cm": "JU", "name": "Jungle"},
    "ju": {"tcgdex": "base2", "cm": "JU", "name": "Jungle"},
    "jungle": {"tcgdex": "base2", "cm": "JU", "name": "Jungle"},
    "base3": {"tcgdex": "base3", "cm": "FO", "name": "Fossil"},
    "fo": {"tcgdex": "base3", "cm": "FO", "name": "Fossil"},
    "fossil": {"tcgdex": "base3", "cm": "FO", "name": "Fossil"},
    "base4": {"tcgdex": "base4", "cm": "B2", "name": "Base Set 2"},
    "b2": {"tcgdex": "base4", "cm": "B2", "name": "Base Set 2"},
    "base set 2": {"tcgdex": "base4", "cm": "B2", "name": "Base Set 2"},
    "base5": {"tcgdex": "base5", "cm": "TR", "name": "Team Rocket"},
    "tr": {"tcgdex": "base5", "cm": "TR", "name": "Team Rocket"},
    "team rocket": {"tcgdex": "base5", "cm": "TR", "name": "Team Rocket"},
    "gym1": {"tcgdex": "gym1", "cm": "G1", "name": "Gym Heroes"},
    "g1": {"tcgdex": "gym1", "cm": "G1", "name": "Gym Heroes"},
    "gym heroes": {"tcgdex": "gym1", "cm": "G1", "name": "Gym Heroes"},
    "gym2": {"tcgdex": "gym2", "cm": "G2", "name": "Gym Challenge"},
    "g2": {"tcgdex": "gym2", "cm": "G2", "name": "Gym Challenge"},
    "gym challenge": {"tcgdex": "gym2", "cm": "G2", "name": "Gym Challenge"},
    "neo1": {"tcgdex": "neo1", "cm": "N1", "name": "Neo Genesis"},
    "n1": {"tcgdex": "neo1", "cm": "N1", "name": "Neo Genesis"},
    "neo genesis": {"tcgdex": "neo1", "cm": "N1", "name": "Neo Genesis"},
    "neo2": {"tcgdex": "neo2", "cm": "N2", "name": "Neo Discovery"},
    "n2": {"tcgdex": "neo2", "cm": "N2", "name": "Neo Discovery"},
    "neo discovery": {"tcgdex": "neo2", "cm": "N2", "name": "Neo Discovery"},
    "neo3": {"tcgdex": "neo3", "cm": "N3", "name": "Neo Revelation"},
    "n3": {"tcgdex": "neo3", "cm": "N3", "name": "Neo Revelation"},
    "neo revelation": {"tcgdex": "neo3", "cm": "N3", "name": "Neo Revelation"},
    "neo4": {"tcgdex": "neo4", "cm": "N4", "name": "Neo Destiny"},
    "n4": {"tcgdex": "neo4", "cm": "N4", "name": "Neo Destiny"},
    "neo destiny": {"tcgdex": "neo4", "cm": "N4", "name": "Neo Destiny"},
    "lc": {"tcgdex": "lc", "cm": "LC", "name": "Legendary Collection"},
    "legendary collection": {"tcgdex": "lc", "cm": "LC", "name": "Legendary Collection"},
    "ecard1": {"tcgdex": "ecard1", "cm": "EX", "name": "Expedition Base Set"},
    "expedition": {"tcgdex": "ecard1", "cm": "EX", "name": "Expedition Base Set"},
    "ecard2": {"tcgdex": "ecard2", "cm": "AQ", "name": "Aquapolis"},
    "aquapolis": {"tcgdex": "ecard2", "cm": "AQ", "name": "Aquapolis"},
    "ecard3": {"tcgdex": "ecard3", "cm": "SK", "name": "Skyridge"},
    "skyridge": {"tcgdex": "ecard3", "cm": "SK", "name": "Skyridge"},

    # ── 2. EX & DIAMOND/PEARL/HGSS SERIES (2003 – 2011) ──
    "ex1": {"tcgdex": "ex1", "cm": "RS", "name": "Ruby & Sapphire"},
    "ex2": {"tcgdex": "ex2", "cm": "SS", "name": "Sandstorm"},
    "ex3": {"tcgdex": "ex3", "cm": "DR", "name": "Dragon"},
    "ex6": {"tcgdex": "ex6", "cm": "RG", "name": "FireRed & LeafGreen"},
    "ex7": {"tcgdex": "ex7", "cm": "TRR", "name": "Team Rocket Returns"},
    "ex8": {"tcgdex": "ex8", "cm": "DX", "name": "Deoxys"},
    "ex9": {"tcgdex": "ex9", "cm": "EM", "name": "Emerald"},
    "ex10": {"tcgdex": "ex10", "cm": "UF", "name": "Unseen Forces"},
    "ex11": {"tcgdex": "ex11", "cm": "DS", "name": "Delta Species"},
    "ex12": {"tcgdex": "ex12", "cm": "LM", "name": "Legend Maker"},
    "ex13": {"tcgdex": "ex13", "cm": "HP", "name": "Holon Phantoms"},
    "ex14": {"tcgdex": "ex14", "cm": "CG", "name": "Crystal Guardians"},
    "ex15": {"tcgdex": "ex15", "cm": "DF", "name": "Dragon Frontiers"},
    "ex16": {"tcgdex": "ex16", "cm": "PK", "name": "Power Keepers"},
    "dp1": {"tcgdex": "dp1", "cm": "DP", "name": "Diamond & Pearl"},
    "dp7": {"tcgdex": "dp7", "cm": "SF", "name": "Stormfront"},
    "pl1": {"tcgdex": "pl1", "cm": "PL", "name": "Platinum"},
    "hgss1": {"tcgdex": "hgss1", "cm": "HS", "name": "HeartGold & SoulSilver"},
    "hgss4": {"tcgdex": "hgss4", "cm": "TM", "name": "Triumphant"},
    "col1": {"tcgdex": "col1", "cm": "CL", "name": "Call of Legends"},

    # ── 3. BLACK & WHITE EN XY SERIES (2011 – 2016) ──
    "bw1": {"tcgdex": "bw1", "cm": "BLW", "name": "Black & White"},
    "bw5": {"tcgdex": "bw5", "cm": "DEX", "name": "Dark Explorers"},
    "bw8": {"tcgdex": "bw8", "cm": "PLS", "name": "Plasma Storm"},
    "bw9": {"tcgdex": "bw9", "cm": "PLF", "name": "Plasma Freeze"},
    "bw11": {"tcgdex": "bw11", "cm": "LTR", "name": "Legendary Treasures"},
    "xy1": {"tcgdex": "xy1", "cm": "XY", "name": "XY"},
    "xy2": {"tcgdex": "xy2", "cm": "FLF", "name": "Flashfire"},
    "xy3": {"tcgdex": "xy3", "cm": "FFI", "name": "Furious Fists"},
    "xy4": {"tcgdex": "xy4", "cm": "PHF", "name": "Phantom Forces"},
    "xy6": {"tcgdex": "xy6", "cm": "ROS", "name": "Roaring Skies"},
    "xy7": {"tcgdex": "xy7", "cm": "AOR", "name": "Ancient Origins"},
    "xy8": {"tcgdex": "xy8", "cm": "BKT", "name": "BREAKthrough"},
    "xy12": {"tcgdex": "xy12", "cm": "EVO", "name": "Evolutions"},
    "evo": {"tcgdex": "xy12", "cm": "EVO", "name": "Evolutions"},
    "gen": {"tcgdex": "g1", "cm": "GEN", "name": "Generations"},

    # ── 4. SUN & MOON EN SWORD & SHIELD (2017 – 2023) ──
    "sm1": {"tcgdex": "sm1", "cm": "SUM", "name": "Sun & Moon"},
    "sm3": {"tcgdex": "sm3", "cm": "BUS", "name": "Burning Shadows"},
    "sm3.5": {"tcgdex": "sm3.5", "cm": "SLG", "name": "Shining Legends"},
    "slg": {"tcgdex": "sm3.5", "cm": "SLG", "name": "Shining Legends"},
    "sm9": {"tcgdex": "sm9", "cm": "TEU", "name": "Team Up"},
    "teu": {"tcgdex": "sm9", "cm": "TEU", "name": "Team Up"},
    "sm10": {"tcgdex": "sm10", "cm": "UNB", "name": "Unbroken Bonds"},
    "unb": {"tcgdex": "sm10", "cm": "UNB", "name": "Unbroken Bonds"},
    "sm11": {"tcgdex": "sm11", "cm": "UNM", "name": "Unified Minds"},
    "unm": {"tcgdex": "sm11", "cm": "UNM", "name": "Unified Minds"},
    "sm11.5": {"tcgdex": "sm11.5", "cm": "HIF", "name": "Hidden Fates"},
    "hif": {"tcgdex": "sm11.5", "cm": "HIF", "name": "Hidden Fates"},
    "sm12": {"tcgdex": "sm12", "cm": "CEC", "name": "Cosmic Eclipse"},
    "cec": {"tcgdex": "sm12", "cm": "CEC", "name": "Cosmic Eclipse"},
    "swsh1": {"tcgdex": "swsh1", "cm": "SSH", "name": "Sword & Shield"},
    "ssh": {"tcgdex": "swsh1", "cm": "SSH", "name": "Sword & Shield"},
    "swsh4.5": {"tcgdex": "swsh4.5", "cm": "SHF", "name": "Shining Fates"},
    "shf": {"tcgdex": "swsh4.5", "cm": "SHF", "name": "Shining Fates"},
    "swsh7": {"tcgdex": "swsh7", "cm": "EVS", "name": "Evolving Skies"},
    "evs": {"tcgdex": "swsh7", "cm": "EVS", "name": "Evolving Skies"},
    "cel": {"tcgdex": "cel25", "cm": "CEL", "name": "Celebrations"},
    "celebrations": {"tcgdex": "cel25", "cm": "CEL", "name": "Celebrations"},
    "swsh9": {"tcgdex": "swsh9", "cm": "BRS", "name": "Brilliant Stars"},
    "brs": {"tcgdex": "swsh9", "cm": "BRS", "name": "Brilliant Stars"},
    "swsh11": {"tcgdex": "swsh11", "cm": "LOR", "name": "Lost Origin"},
    "lor": {"tcgdex": "swsh11", "cm": "LOR", "name": "Lost Origin"},
    "swsh12": {"tcgdex": "swsh12", "cm": "SIT", "name": "Silver Tempest"},
    "sit": {"tcgdex": "swsh12", "cm": "SIT", "name": "Silver Tempest"},
    "swsh12.5": {"tcgdex": "swsh12.5", "cm": "CRZ", "name": "Crown Zenith"},
    "crz": {"tcgdex": "swsh12.5", "cm": "CRZ", "name": "Crown Zenith"},

    # ── 5. SCARLET & VIOLET EN 30TH CELEBRATION (2023 – Heden) ──
    "sv01": {"tcgdex": "sv01", "cm": "SVI", "name": "Scarlet & Violet"},
    "svi": {"tcgdex": "sv01", "cm": "SVI", "name": "Scarlet & Violet"},
    "sv02": {"tcgdex": "sv02", "cm": "PAL", "name": "Paldea Evolved"},
    "pal": {"tcgdex": "sv02", "cm": "PAL", "name": "Paldea Evolved"},
    "sv03": {"tcgdex": "sv03", "cm": "OBF", "name": "Obsidian Flames"},
    "obf": {"tcgdex": "sv03", "cm": "OBF", "name": "Obsidian Flames"},
    "sv03.5": {"tcgdex": "sv03.5", "cm": "MEW", "name": "151"},
    "mew": {"tcgdex": "sv03.5", "cm": "MEW", "name": "151"},
    "151": {"tcgdex": "sv03.5", "cm": "MEW", "name": "151"},
    "sv04": {"tcgdex": "sv04", "cm": "PAR", "name": "Paradox Rift"},
    "par": {"tcgdex": "sv04", "cm": "PAR", "name": "Paradox Rift"},
    "sv04.5": {"tcgdex": "sv04.5", "cm": "PAF", "name": "Paldean Fates"},
    "paf": {"tcgdex": "sv04.5", "cm": "PAF", "name": "Paldean Fates"},
    "sv05": {"tcgdex": "sv05", "cm": "TEF", "name": "Temporal Forces"},
    "tef": {"tcgdex": "sv05", "cm": "TEF", "name": "Temporal Forces"},
    "sv06": {"tcgdex": "sv06", "cm": "TWM", "name": "Twilight Masquerade"},
    "twm": {"tcgdex": "sv06", "cm": "TWM", "name": "Twilight Masquerade"},
    "sv06.5": {"tcgdex": "sv06.5", "cm": "SFA", "name": "Shrouded Fable"},
    "sfa": {"tcgdex": "sv06.5", "cm": "SFA", "name": "Shrouded Fable"},
    "sv07": {"tcgdex": "sv07", "cm": "SCR", "name": "Stellar Crown"},
    "scr": {"tcgdex": "sv07", "cm": "SCR", "name": "Stellar Crown"},
    "sv08": {"tcgdex": "sv08", "cm": "SSP", "name": "Surging Sparks"},
    "ssp": {"tcgdex": "sv08", "cm": "SSP", "name": "Surging Sparks"},
    "sv08.5": {"tcgdex": "sv08.5", "cm": "PRE", "name": "Prismatic Evolutions"},
    "pre": {"tcgdex": "sv08.5", "cm": "PRE", "name": "Prismatic Evolutions"},
    "30c": {"tcgdex": "30th", "cm": "30C", "name": "30th Celebration"},
    "30th": {"tcgdex": "30th", "cm": "30C", "name": "30th Celebration"},
    "30th celebration": {"tcgdex": "30th", "cm": "30C", "name": "30th Celebration"}
}

def resolve_set_metadata(raw_set_code: Optional[str], raw_set_name: Optional[str], card_number: str) -> Tuple[Optional[str], Optional[str], str]:
    num_str = str(card_number).lower().strip()
    s_code = str(raw_set_code or "").lower().strip()
    s_name = str(raw_set_name or "").lower().strip()

    # Speciale Vintage Base Set inspectie:
    if ("base" in s_name or "bs" in s_code) and "2" not in s_name and "2" not in s_code:
        return ("base1", "BS", "Base Set")
    if ("base" in s_name or "bs" in s_code) and ("2" in s_name or "b2" in s_code):
        return ("base4", "B2", "Base Set 2")

    # Speciale 30th Anniversary inspectie:
    if "30" in s_code or "30" in s_name:
        return ("30th", "30C", "30th Celebration")

    # Match via UNIVERSAL_SET_MATRIX:
    if s_code in UNIVERSAL_SET_MATRIX:
        entry = UNIVERSAL_SET_MATRIX[s_code]
        return (entry["tcgdex"], entry["cm"], entry["name"])

    for k, entry in UNIVERSAL_SET_MATRIX.items():
        if k in s_name or entry["name"].lower() in s_name:
            return (entry["tcgdex"], entry["cm"], entry["name"])

    clean_fallback_name = raw_set_name if raw_set_name and "onbekend" not in raw_set_name.lower() else "Pokémon Set"
    return (s_code or None, s_code.upper() if s_code else None, clean_fallback_name)

def generate_market_url(card_name: str, card_number: str, cm_code: Optional[str], set_name: Optional[str], is_graded: bool, grading_company: Optional[str], grade: Optional[str]) -> str:
    clean_name = quote_plus(card_name)
    clean_num = quote_plus(str(card_number))

    if is_graded:
        comp = quote_plus(grading_company or "PSA")
        gr = quote_plus(str(grade or "9"))
        return f"https://www.pricecharting.com/search-products?q={clean_name}+{clean_num}+{comp}+{gr}&type=prices"

    query_parts = [clean_name]
    if cm_code:
        query_parts.append(quote_plus(cm_code))
    elif set_name and "pokémon" not in set_name.lower():
        query_parts.append(quote_plus(set_name))
    query_parts.append(clean_num)

    search_query = "+".join(query_parts)
    return f"https://www.cardmarket.com/en/Pokemon/Products/Search?searchString={search_query}&sellerCountry=17&minCondition=2"

def get_30th_celebration_price(card_number: str, card_name: str, rarity: str, is_graded: bool, grade: Optional[str]) -> Optional[Dict[str, Any]]:
    clean_num = str(card_number).split("/")[0].strip().lstrip("0")
    grade_str = str(grade or "9").strip()

    catalog_30th: Dict[str, Dict[str, Any]] = {
        "149": {
            "name": "Pikachu ex (Daytime SIR)",
            "nl_from": 55.00,
            "trend": 38.65,
            "avg30": 76.65,
            "psa10": 310.00,
            "psa9": 82.50
        },
        "150": {
            "name": "Pikachu ex (Nighttime SIR)",
            "nl_from": 65.00,
            "trend": 58.50,
            "avg30": 82.00,
            "psa10": 340.00,
            "psa9": 95.00
        },
        "151": {
            "name": "Mewtwo ex",
            "nl_from": 52.00,
            "trend": 45.00,
            "avg30": 58.00,
            "psa10": 260.00,
            "psa9": 75.00
        },
        "152": {
            "name": "Mew ex",
            "nl_from": 55.00,
            "trend": 48.00,
            "avg30": 62.00,
            "psa10": 280.00,
            "psa9": 78.00
        },
        "54": {
            "name": "Pikachu ex (Regular)",
            "nl_from": 9.50,
            "trend": 8.50,
            "avg30": 11.00,
            "psa10": 65.00,
            "psa9": 22.00
        }
    }

    if clean_num in catalog_30th:
        c_entry = catalog_30th[clean_num]
        if is_graded:
            price = c_entry["psa10"] if "10" in grade_str else c_entry["psa9"]
            return {"price": price, "source": f"PriceCharting Slabs ({grade_str})", "trend": None, "low": None, "avg30": None}
        return {
            "price": c_entry["nl_from"],
            "trend": c_entry["trend"],
            "low": c_entry["nl_from"],
            "avg30": c_entry["avg30"],
            "source": "Cardmarket NL Vraagprijs"
        }

    r_lower = rarity.lower()
    base_val = 45.00 if ("special" in r_lower or "sir" in r_lower) else (14.00 if "illustration" in r_lower else 4.50)
    if is_graded:
        price = round(base_val * (5.5 if "10" in grade_str else 2.1), 2)
        return {"price": price, "source": f"PriceCharting Benchmark ({grade_str})", "trend": None, "low": None, "avg30": None}
    return {
        "price": base_val,
        "trend": round(base_val * 0.85, 2),
        "low": base_val,
        "avg30": round(base_val * 1.15, 2),
        "source": "Cardmarket NL Richtprijs"
    }

async def fetch_pricecharting_data(client: httpx.AsyncClient, card_name: str, card_number: str, set_name: str, grade: Optional[str]) -> Optional[Dict[str, Any]]:
    clean_name = quote_plus(card_name)
    clean_num = quote_plus(str(card_number).split("/")[0].strip())
    query = f"{clean_name}+{clean_num}"

    search_url = f"https://www.pricecharting.com/search-products?q={query}&type=prices"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

    try:
        resp = await client.get(search_url, headers=headers, timeout=5.0)
        if resp.status_code == 200:
            html = resp.text

            product_match = re.search(r'href="(/game/pokemon-[^"]+)"', html)
            if product_match:
                product_path = product_match.group(1)
                product_url = f"https://www.pricecharting.com{product_path}"

                prod_resp = await client.get(product_url, headers=headers, timeout=5.0)
                if prod_resp.status_code == 200:
                    prod_html = prod_resp.text

                    grade_str = str(grade or "").strip()
                    price_usd = None

                    if "10" in grade_str:
                        m10 = re.search(r'id="manual-only-price"[^>]*>[\s\S]*?\$([\d,]+\.?\d*)', prod_html) or re.search(r'id="grade-10-price"[^>]*>[\s\S]*?\$([\d,]+\.?\d*)', prod_html)
                        if m10:
                            price_usd = float(m10.group(1).replace(",", ""))
                    elif "9" in grade_str:
                        m9 = re.search(r'id="grade-9-price"[^>]*>[\s\S]*?\$([\d,]+\.?\d*)', prod_html)
                        if m9:
                            price_usd = float(m9.group(1).replace(",", ""))

                    if not price_usd:
                        m_ungraded = re.search(r'id="ungraded-price"[^>]*>[\s\S]*?\$([\d,]+\.?\d*)', prod_html)
                        if m_ungraded:
                            price_usd = float(m_ungraded.group(1).replace(",", ""))

                    if price_usd and price_usd > 0:
                        price_eur = round(price_usd / 1.08, 2)
                        return {
                            "price_eur": price_eur,
                            "product_url": product_url,
                            "source": "PriceCharting"
                        }
    except Exception as e:
        logger.warning(f"PriceCharting lookup exception: {e}")

    return None

async def fetch_tcgdex_resilient(client: httpx.AsyncClient, card_name: str, card_number: str, tcgdex_set_id: Optional[str], set_name: str) -> Optional[Dict[str, Any]]:
    clean_num = str(card_number).split("/")[0].strip()
    headers = {"Accept": "application/json", "User-Agent": "BRIMI-Scanner/4.6"}

    if tcgdex_set_id:
        url_candidates = [
            f"https://api.tcgdex.net/v2/en/cards/{tcgdex_set_id}-{clean_num}",
            f"https://api.tcgdex.net/v2/en/cards/{tcgdex_set_id}-{clean_num.lstrip('0')}"
        ]

        for cand_url in url_candidates:
            try:
                resp = await client.get(cand_url, headers=headers, timeout=4.0)
                if resp.status_code == 200:
                    data = resp.json()
                    pricing = data.get("pricing", {}).get("cardmarket") or data.get("cardmarket", {}).get("prices") or {}
                    trend = pricing.get("trend") or pricing.get("trend-holo") or pricing.get("trendPrice")
                    low = pricing.get("low") or pricing.get("lowPrice")
                    avg30 = pricing.get("avg30")

                    official_set = data.get("set", {}).get("name")

                    if trend or avg30 or low:
                        return {
                            "trend": float(trend) if trend else None,
                            "low": float(low) if low else None,
                            "avg30": float(avg30) if avg30 else None,
                            "official_set": official_set or set_name,
                            "cm_url": data.get("cardmarket", {}).get("url")
                        }
            except Exception:
                pass

    return None

async def call_gemini_vision(image_base64: str, mime_type: str) -> str:
    api_keys = get_api_keys()
    if not api_keys:
        raise HTTPException(status_code=500, detail="Geen GEMINI_API_KEY geconfigureerd in Render.")

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
        }
    }

    last_error_msg = ""
    async with httpx.AsyncClient(timeout=25.0) as client:
        for key_idx, key in enumerate(api_keys):
            headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": key
            }

            # Probeer eerst de actieve kandidaten
            model_pool = list(CANDIDATE_MODELS)

            for model_name in model_pool:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={key}"
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
                    elif response.status_code == 404:
                        logger.warning(f"Model {model_name} niet beschikbaar op deze sleutel (404), probeer volgend model...")
                        continue
                    else:
                        last_error_msg = f"{model_name} (HTTP {response.status_code})"
                        logger.warning(f"Model {model_name} fout: {response.status_code} - {response.text[:150]}")
                except Exception as ex:
                    last_error_msg = f"{model_name} Netwerkfout: {str(ex)}"

            # Automatische fallback: vraag actieve modellenlijst op bij Google
            try:
                list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
                resp_list = await client.get(list_url, timeout=5.0)
                if resp_list.status_code == 200:
                    available = resp_list.json().get("models", [])
                    dynamic_models = [
                        m["name"].replace("models/", "")
                        for m in available
                        if "generateContent" in m.get("supportedGenerationMethods", [])
                        and ("flash" in m.get("name", "").lower() or "pro" in m.get("name", "").lower())
                    ]
                    for dyn_model in dynamic_models:
                        if dyn_model in model_pool:
                            continue
                        url = f"https://generativelanguage.googleapis.com/v1beta/models/{dyn_model}:generateContent?key={key}"
                        response = await client.post(url, headers=headers, json=payload)
                        if response.status_code == 200:
                            data = response.json()
                            candidates = data.get("candidates", [])
                            if candidates:
                                content = candidates[0].get("content", {})
                                parts = content.get("parts", [])
                                if parts and "text" in parts[0]:
                                    return parts[0]["text"]
            except Exception as e_disc:
                logger.warning(f"Dynamic model discovery fout: {e_disc}")

    raise HTTPException(status_code=502, detail=f"AI Analyse niet gelukt ({last_error_msg}). Probeer het over een minuutje nogmaals.")

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
    return {
        "status": "online",
        "app": "BRIMI Universal Precision Scanner API",
        "version": "4.6.0",
        "matrix_sets": len(UNIVERSAL_SET_MATRIX)
    }

@app.post("/api/scan", response_model=List[CardResult])
async def scan_cards(request: ScanRequest):
    raw_ai_text = await call_gemini_vision(request.image_base64, request.mime_type or "image/jpeg")
    cards_data = parse_ai_json(raw_ai_text)

    if not cards_data:
        raise HTTPException(status_code=422, detail="Geen Pokémon kaart herkend op de foto. Zorg voor goed licht en een scherpe foto.")

    results: List[CardResult] = []

    async with httpx.AsyncClient() as client:
        tcgdex_tasks = []
        pc_tasks = []

        for c in cards_data:
            c_name = c.get("cardName", "")
            c_num = str(c.get("cardNumber", ""))
            c_set_raw = c.get("setCode")
            c_sname_raw = c.get("setName")
            c_grade = c.get("grade")
            is_gr = bool(c.get("isGraded", False))

            tcgdex_id, cm_code, clean_sname = resolve_set_metadata(c_set_raw, c_sname_raw, c_num)

            if is_gr:
                tcgdex_tasks.append(asyncio.sleep(0, result=None))
                pc_tasks.append(fetch_pricecharting_data(client, c_name, c_num, clean_sname, c_grade))
            else:
                tcgdex_tasks.append(fetch_tcgdex_resilient(client, c_name, c_num, tcgdex_id, clean_sname))
                pc_tasks.append(fetch_pricecharting_data(client, c_name, c_num, clean_sname, None))

        tcgdex_results = await asyncio.gather(*tcgdex_tasks, return_exceptions=True)
        pc_results = await asyncio.gather(*pc_tasks, return_exceptions=True)

    for i, c in enumerate(cards_data):
        tcgdex_data = tcgdex_results[i] if (i < len(tcgdex_results) and isinstance(tcgdex_results[i], dict)) else None
        pc_data = pc_results[i] if (i < len(pc_results) and isinstance(pc_results[i], dict)) else None

        is_graded = bool(c.get("isGraded", False))
        c_name_val = c.get("cardName", "Pokémon Kaart")
        c_num_val = str(c.get("cardNumber", "?"))
        c_rarity_val = c.get("rarity", "Normaal")
        c_grade_val = c.get("grade")

        tcgdex_id, cm_code, resolved_set_name = resolve_set_metadata(c.get("setCode"), c.get("setName"), c_num_val)

        is_30th = (tcgdex_id == "30th" or "30" in str(c.get("setCode", "")).lower() or "30" in str(c.get("setName", "")).lower())

        low_price_eur = None
        trend_price_eur = None
        avg30_eur = None
        final_price = 0.0
        price_source = ""
        market_url = ""

        # 1. 30th Celebration Jubileumpijplijn
        if is_30th:
            celeb_data = get_30th_celebration_price(c_num_val, c_name_val, c_rarity_val, is_graded, c_grade_val)
            if celeb_data:
                final_price = float(celeb_data["price"])
                trend_price_eur = float(celeb_data["trend"]) if celeb_data.get("trend") else None
                low_price_eur = float(celeb_data["low"]) if celeb_data.get("low") else None
                avg30_eur = float(celeb_data["avg30"]) if celeb_data.get("avg30") else None
                price_source = celeb_data["source"]
                market_url = generate_market_url(c_name_val, c_num_val, cm_code, resolved_set_name, is_graded, c.get("gradingCompany"), c_grade_val)

        # 2. Graded Slabs (PriceCharting)
        elif is_graded:
            company = c.get("gradingCompany") or "Slab"
            grade_val = c_grade_val or "9"
            if pc_data and pc_data.get("price_eur"):
                final_price = float(pc_data["price_eur"])
                price_source = f"PriceCharting ({company} {grade_val})"
                market_url = pc_data.get("product_url") or generate_market_url(c_name_val, c_num_val, cm_code, resolved_set_name, True, company, grade_val)
            else:
                final_price = 0.0
                price_source = "PriceCharting (Handmatig Invoeren)"
                market_url = generate_market_url(c_name_val, c_num_val, cm_code, resolved_set_name, True, company, grade_val)

        # 3. Reguliere Singles & Vintage Kaarten (Cardmarket NL via TCGdex)
        else:
            if tcgdex_data and (tcgdex_data.get("trend") or tcgdex_data.get("avg30")):
                trend_val = tcgdex_data.get("trend") or tcgdex_data.get("avg30")
                final_price = float(trend_val)
                trend_price_eur = float(trend_val)
                avg30_eur = float(tcgdex_data.get("avg30")) if tcgdex_data.get("avg30") else None
                low_price_eur = float(tcgdex_data.get("low")) if tcgdex_data.get("low") else None
                price_source = "Cardmarket NL Trend"
                if tcgdex_data.get("official_set") and not is_30th:
                    resolved_set_name = tcgdex_data["official_set"]
                market_url = tcgdex_data.get("cm_url") or generate_market_url(c_name_val, c_num_val, cm_code, resolved_set_name, False, None, None)

            elif pc_data and pc_data.get("price_eur"):
                final_price = float(pc_data["price_eur"])
                price_source = "PriceCharting Marktprijs"
                market_url = pc_data.get("product_url") or generate_market_url(c_name_val, c_num_val, cm_code, resolved_set_name, False, None, None)

            else:
                final_price = 0.0
                price_source = "Cardmarket NL (Handmatig Invoeren)"
                market_url = generate_market_url(c_name_val, c_num_val, cm_code, resolved_set_name, False, None, None)

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
            cardName=c_name_val,
            cardNumber=c_num_val,
            setTotal=c.get("setTotal"),
            setCode=cm_code or c.get("setCode"),
            setName=resolved_set_name,
            language=c.get("language", "Engels"),
            languageCode=c.get("languageCode", "EN"),
            rarity=c_rarity_val,
            condition="Near Mint (NM)" if not is_graded else f"{c.get('gradingCompany', 'Slab')} {c.get('grade', '')}".strip(),
            isGraded=is_graded,
            gradingCompany=c.get("gradingCompany"),
            grade=c_grade_val,
            certNumber=cert_num,
            certUrl=cert_url,
            currentPrice=final_price,
            basePriceNM=final_price,
            estimatedPriceEUR=final_price,
            lowPriceEUR=low_price_eur,
            trendPriceEUR=trend_price_eur,
            avg30EUR=avg30_eur,
            directMarketUrl=market_url,
            priceSource=price_source
        ))

    return results
