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

app = FastAPI(title="BRIMI Universal Precision Scanner API", version="5.0.0")

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
Je bent de visuele inspectie-engine van BRIMI Scanner voor Pokémon TCG.
Identificeer ALLE kaarten op de foto met uiterste precisie op basis van de 4 officiële tijdperken:

1. VINTAGE (1999 – 2003: Wizards of the Coast):
- HELEMAAL GEEN SYMBOOL rechtsonder het artwork + "/102": 100% gegarandeerd "Base Set" (Base 1). SetCode = "BS", SetName = "Base Set". Noem dit NOOIT Base Set 2!
- Grote "2" met Pokéball + "/130": "Base Set 2" (B2).
- Bloem: "Jungle" (JU). Skeletklauw: "Fossil" (FO). Zwarte "R": "Team Rocket" (TR).

2. MIDDEN TIJDPERK (2004 – 2022: EX, DP, BW, XY, Sun & Moon, SWSH):
- Set-symbool staat rechtsonder op de kaartrand of in een zwart-wit kader.
- Trainer Galleries beginnen met "TG", Galarian Galleries met "GG".

3. SCARLET & VIOLET & MEGA EVOLUTION (2023 – 2026):
- Zwart blokje linksonder met vaste opbouw: [TAAL] [SETCODE] [NUMMER/TOTAAL] [ZELDZAAMHEID].
- "Pikachu with Grey Felt Hat" is ALTIJD: setCode = "SVP", setName = "Scarlet & Violet Promos", cardNumber = "085", setTotal = "225". Noem dit NOOIT "SVI" of gewone Scarlet & Violet!
- ASC / ASH of noemer /217: setCode = "ASC", setName = "Ascended Heroes".
- PFL of noemer /094 (bijv. Dewgong 097/094): setCode = "PFL", setName = "Phantasmal Flames".
- ME01 of noemer /132: setCode = "ME01", setName = "Mega Evolution".
- 30C of noemer /128 (bijv. Pikachu ex 149/128): setCode = "30C", setName = "30th Celebration".

4. TAAL & GRADED SLABS:
- Japanse karakters = "Japans" (JP), anders Engels (EN).
- Slabs (PSA, CGC, BGS): isGraded = true, lees de grader, grade en het certificaatnummer.

Geef ALTIJD een geldig JSON array terug met objecten:
cardName, cardNumber, setTotal, setCode, setName, language, languageCode, rarity, isGraded, gradingCompany, grade, certNumber.
"""

# COMPLETE UNIVERSELE SET MATRIX (1999 – 2026)
UNIVERSAL_SET_MATRIX: Dict[str, Dict[str, str]] = {
    # ── MEGA EVOLUTION SERIE & 30TH (2025 – 2026) ──
    "asc": {"tcgdex": "me02.5", "cm": "ASC", "name": "Ascended Heroes"},
    "ash": {"tcgdex": "me02.5", "cm": "ASC", "name": "Ascended Heroes"},
    "ascended heroes": {"tcgdex": "me02.5", "cm": "ASC", "name": "Ascended Heroes"},
    "pfl": {"tcgdex": "me02", "cm": "PFL", "name": "Phantasmal Flames"},
    "phantasmal flames": {"tcgdex": "me02", "cm": "PFL", "name": "Phantasmal Flames"},
    "me01": {"tcgdex": "me01", "cm": "ME01", "name": "Mega Evolution"},
    "30c": {"tcgdex": "30th", "cm": "30C", "name": "30th Celebration"},
    "30th": {"tcgdex": "30th", "cm": "30C", "name": "30th Celebration"},
    "30th celebration": {"tcgdex": "30th", "cm": "30C", "name": "30th Celebration"},

    # ── SCARLET & VIOLET (2023 – 2025) ──
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

    # ── SWORD & SHIELD (2020 – 2023) ──
    "swsh1": {"tcgdex": "swsh1", "cm": "SSH", "name": "Sword & Shield"},
    "swsh2": {"tcgdex": "swsh2", "cm": "RCL", "name": "Rebel Clash"},
    "swsh3": {"tcgdex": "swsh3", "cm": "DAA", "name": "Darkness Ablaze"},
    "swsh3.5": {"tcgdex": "swsh3.5", "cm": "CPA", "name": "Champion's Path"},
    "swsh4": {"tcgdex": "swsh4", "cm": "VIV", "name": "Vivid Voltage"},
    "swsh4.5": {"tcgdex": "swsh4.5", "cm": "SHF", "name": "Shining Fates"},
    "swsh5": {"tcgdex": "swsh5", "cm": "BST", "name": "Battle Styles"},
    "swsh6": {"tcgdex": "swsh6", "cm": "CRE", "name": "Chilling Reign"},
    "swsh7": {"tcgdex": "swsh7", "cm": "EVS", "name": "Evolving Skies"},
    "evs": {"tcgdex": "swsh7", "cm": "EVS", "name": "Evolving Skies"},
    "cel": {"tcgdex": "cel25", "cm": "CEL", "name": "Celebrations"},
    "celebrations": {"tcgdex": "cel25", "cm": "CEL", "name": "Celebrations"},
    "swsh8": {"tcgdex": "swsh8", "cm": "FST", "name": "Fusion Strike"},
    "swsh9": {"tcgdex": "swsh9", "cm": "BRS", "name": "Brilliant Stars"},
    "swsh10": {"tcgdex": "swsh10", "cm": "ASR", "name": "Astral Radiance"},
    "pgo": {"tcgdex": "pgo", "cm": "PGO", "name": "Pokemon GO"},
    "swsh11": {"tcgdex": "swsh11", "cm": "LOR", "name": "Lost Origin"},
    "swsh12": {"tcgdex": "swsh12", "cm": "SIT", "name": "Silver Tempest"},
    "swsh12.5": {"tcgdex": "swsh12.5", "cm": "CRZ", "name": "Crown Zenith"},
    "crz": {"tcgdex": "swsh12.5", "cm": "CRZ", "name": "Crown Zenith"},

    # ── SUN & MOON (2017 – 2019) ──
    "sm1": {"tcgdex": "sm1", "cm": "SUM", "name": "Sun & Moon"},
    "sm2": {"tcgdex": "sm2", "cm": "GRI", "name": "Guardians Rising"},
    "sm3": {"tcgdex": "sm3", "cm": "BUS", "name": "Burning Shadows"},
    "sm3.5": {"tcgdex": "sm3.5", "cm": "SLG", "name": "Shining Legends"},
    "sm4": {"tcgdex": "sm4", "cm": "CIN", "name": "Crimson Invasion"},
    "sm5": {"tcgdex": "sm5", "cm": "UPR", "name": "Ultra Prism"},
    "sm6": {"tcgdex": "sm6", "cm": "FLI", "name": "Forbidden Light"},
    "sm7": {"tcgdex": "sm7", "cm": "CES", "name": "Celestial Storm"},
    "sm7.5": {"tcgdex": "sm7.5", "cm": "DRM", "name": "Dragon Majesty"},
    "sm8": {"tcgdex": "sm8", "cm": "LOT", "name": "Lost Thunder"},
    "sm9": {"tcgdex": "sm9", "cm": "TEU", "name": "Team Up"},
    "sm10": {"tcgdex": "sm10", "cm": "UNB", "name": "Unbroken Bonds"},
    "sm11": {"tcgdex": "sm11", "cm": "UNM", "name": "Unified Minds"},
    "sm11.5": {"tcgdex": "sm11.5", "cm": "HIF", "name": "Hidden Fates"},
    "sm12": {"tcgdex": "sm12", "cm": "CEC", "name": "Cosmic Eclipse"},

    # ── XY SERIE (2013 – 2016) ──
    "xy1": {"tcgdex": "xy1", "cm": "XY", "name": "XY"},
    "xy2": {"tcgdex": "xy2", "cm": "FLF", "name": "Flashfire"},
    "xy3": {"tcgdex": "xy3", "cm": "FFI", "name": "Furious Fists"},
    "xy4": {"tcgdex": "xy4", "cm": "PHF", "name": "Phantom Forces"},
    "xy5": {"tcgdex": "xy5", "cm": "PRC", "name": "Primal Clash"},
    "dc": {"tcgdex": "dc1", "cm": "DCR", "name": "Double Crisis"},
    "xy6": {"tcgdex": "xy6", "cm": "ROS", "name": "Roaring Skies"},
    "xy7": {"tcgdex": "xy7", "cm": "AOR", "name": "Ancient Origins"},
    "xy8": {"tcgdex": "xy8", "cm": "BKT", "name": "BREAKthrough"},
    "xy9": {"tcgdex": "xy9", "cm": "BKP", "name": "BREAKpoint"},
    "gen": {"tcgdex": "g1", "cm": "GEN", "name": "Generations"},
    "xy10": {"tcgdex": "xy10", "cm": "FCO", "name": "Fates Collide"},
    "xy11": {"tcgdex": "xy11", "cm": "STS", "name": "Steam Siege"},
    "xy12": {"tcgdex": "xy12", "cm": "EVO", "name": "Evolutions"},

    # ── BLACK & WHITE (2011 – 2013) ──
    "bw1": {"tcgdex": "bw1", "cm": "BLW", "name": "Black & White"},
    "bw2": {"tcgdex": "bw2", "cm": "EPO", "name": "Emerging Powers"},
    "bw3": {"tcgdex": "bw3", "cm": "NVI", "name": "Noble Victories"},
    "bw4": {"tcgdex": "bw4", "cm": "NXD", "name": "Next Destinies"},
    "bw5": {"tcgdex": "bw5", "cm": "DEX", "name": "Dark Explorers"},
    "bw6": {"tcgdex": "bw6", "cm": "DRX", "name": "Dragons Exalted"},
    "bw7": {"tcgdex": "bw7", "cm": "BCR", "name": "Boundaries Crossed"},
    "bw8": {"tcgdex": "bw8", "cm": "PLS", "name": "Plasma Storm"},
    "bw9": {"tcgdex": "bw9", "cm": "PLF", "name": "Plasma Freeze"},
    "bw10": {"tcgdex": "bw10", "cm": "PLB", "name": "Plasma Blast"},
    "bw11": {"tcgdex": "bw11", "cm": "LTR", "name": "Legendary Treasures"},

    # ── CALL OF LEGENDS & HGSS (2010 – 2011) ──
    "col": {"tcgdex": "col1", "cm": "CL", "name": "Call of Legends"},
    "hgss1": {"tcgdex": "hgss1", "cm": "HS", "name": "HeartGold SoulSilver"},
    "hgss2": {"tcgdex": "hgss2", "cm": "UL", "name": "HS Unleashed"},
    "hgss3": {"tcgdex": "hgss3", "cm": "UD", "name": "HS Undaunted"},
    "hgss4": {"tcgdex": "hgss4", "cm": "TM", "name": "HS Triumphant"},

    # ── PLATINUM (2009 – 2010) ──
    "pl1": {"tcgdex": "pl1", "cm": "PL", "name": "Platinum"},
    "pl2": {"tcgdex": "pl2", "cm": "RR", "name": "Rising Rivals"},
    "pl3": {"tcgdex": "pl3", "cm": "SV", "name": "Supreme Victors"},
    "pl4": {"tcgdex": "pl4", "cm": "AR", "name": "Arceus"},

    # ── DIAMOND & PEARL (2007 – 2008) ──
    "dp1": {"tcgdex": "dp1", "cm": "DP", "name": "Diamond & Pearl"},
    "dp2": {"tcgdex": "dp2", "cm": "MT", "name": "Mysterious Treasures"},
    "dp3": {"tcgdex": "dp3", "cm": "SW", "name": "Secret Wonders"},
    "dp4": {"tcgdex": "dp4", "cm": "GE", "name": "Great Encounters"},
    "dp5": {"tcgdex": "dp5", "cm": "MD", "name": "Majestic Dawn"},
    "dp6": {"tcgdex": "dp6", "cm": "LA", "name": "Legends Awakened"},
    "dp7": {"tcgdex": "dp7", "cm": "SF", "name": "Stormfront"},

    # ── EX SERIES (2003 – 2007) ──
    "ex1": {"tcgdex": "ex1", "cm": "RS", "name": "Ruby & Sapphire"},
    "ex2": {"tcgdex": "ex2", "cm": "SS", "name": "Sandstorm"},
    "ex3": {"tcgdex": "ex3", "cm": "DR", "name": "Dragon"},
    "ex4": {"tcgdex": "ex4", "cm": "MA", "name": "Team Magma vs Team Aqua"},
    "ex5": {"tcgdex": "ex5", "cm": "HL", "name": "Hidden Legends"},
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

    # ── E-SERIES & VINTAGE WIZARDS (1999 – 2003) ──
    "ecard1": {"tcgdex": "ecard1", "cm": "EXP", "name": "Expedition Base Set"},
    "ecard2": {"tcgdex": "ecard2", "cm": "AQ", "name": "Aquapolis"},
    "ecard3": {"tcgdex": "ecard3", "cm": "SK", "name": "Skyridge"},
    "lc": {"tcgdex": "lc", "cm": "LC", "name": "Legendary Collection"},
    "neo4": {"tcgdex": "neo4", "cm": "N4", "name": "Neo Destiny"},
    "neo3": {"tcgdex": "neo3", "cm": "N3", "name": "Neo Revelation"},
    "neo2": {"tcgdex": "neo2", "cm": "N2", "name": "Neo Discovery"},
    "neo1": {"tcgdex": "neo1", "cm": "N1", "name": "Neo Genesis"},
    "gym2": {"tcgdex": "gym2", "cm": "G2", "name": "Gym Challenge"},
    "gym1": {"tcgdex": "gym1", "cm": "G1", "name": "Gym Heroes"},
    "base5": {"tcgdex": "base5", "cm": "TR", "name": "Team Rocket"},
    "tr": {"tcgdex": "base5", "cm": "TR", "name": "Team Rocket"},
    "team rocket": {"tcgdex": "base5", "cm": "TR", "name": "Team Rocket"},
    "base4": {"tcgdex": "base4", "cm": "B2", "name": "Base Set 2"},
    "b2": {"tcgdex": "base4", "cm": "B2", "name": "Base Set 2"},
    "base set 2": {"tcgdex": "base4", "cm": "B2", "name": "Base Set 2"},
    "base3": {"tcgdex": "base3", "cm": "FO", "name": "Fossil"},
    "fo": {"tcgdex": "base3", "cm": "FO", "name": "Fossil"},
    "fossil": {"tcgdex": "base3", "cm": "FO", "name": "Fossil"},
    "base2": {"tcgdex": "base2", "cm": "JU", "name": "Jungle"},
    "ju": {"tcgdex": "base2", "cm": "JU", "name": "Jungle"},
    "jungle": {"tcgdex": "base2", "cm": "JU", "name": "Jungle"},
    "base1": {"tcgdex": "base1", "cm": "BS", "name": "Base Set"},
    "bs": {"tcgdex": "base1", "cm": "BS", "name": "Base Set"},
    "base set": {"tcgdex": "base1", "cm": "BS", "name": "Base Set"},

    # ── BLACK STAR PROMOS ──
    "svp": {"tcgdex": "svp", "cm": "SVP", "name": "Scarlet & Violet Promos"},
    "swshp": {"tcgdex": "swshp", "cm": "SWSH", "name": "SWSH Black Star Promos"},
    "smp": {"tcgdex": "smp", "cm": "SMP", "name": "SM Black Star Promos"},
    "xyp": {"tcgdex": "xyp", "cm": "XYP", "name": "XY Black Star Promos"},
    "bwp": {"tcgdex": "bwp", "cm": "BWP", "name": "BW Black Star Promos"},
    "basep": {"tcgdex": "basep", "cm": "PR", "name": "Wizards Black Star Promos"}
}

def resolve_set_metadata(raw_set_code: Optional[str], raw_set_name: Optional[str], card_number: str, card_name: str) -> Tuple[Optional[str], Optional[str], str]:
    num_str = str(card_number).lower().strip()
    s_code = str(raw_set_code or "").lower().strip()
    s_name = str(raw_set_name or "").lower().strip()
    c_name = str(card_name or "").lower().strip()

    # 1. Specifieke Promos
    if "grey felt hat" in c_name or ("felt hat" in c_name and "85" in num_str):
        return ("svp", "SVP", "Scarlet & Violet Promos")

    # 2. Phantasmal Flames
    if "dewgong" in c_name and ("97" in num_str or "097" in num_str):
        return ("me02", "PFL", "Phantasmal Flames")
    if "pfl" in s_code or "phantasmal" in s_name or "094" in num_str:
        return ("me02", "PFL", "Phantasmal Flames")

    # 3. Ascended Heroes (/217)
    if "217" in num_str or "asc" in s_code or "ash" in s_code or "ascen" in s_name:
        return ("me02.5", "ASC", "Ascended Heroes")

    # 4. 30th Celebration
    if "30" in s_code or "30" in s_name or "128" in num_str:
        return ("30th", "30C", "30th Celebration")

    # 5. Promos algemeen
    if "svp" in s_code or "promo" in s_name or "promo" in s_code:
        if "swsh" in s_name or "swsh" in s_code:
            return ("swshp", "SWSH", "SWSH Black Star Promos")
        if "sm" in s_name or "sm" in s_code:
            return ("smp", "SMP", "SM Black Star Promos")
        return ("svp", "SVP", "Scarlet & Violet Promos")

    # 6. Vintage Base Set vs Base Set 2
    if ("base" in s_name or "bs" in s_code) and "2" not in s_name and "2" not in s_code:
        return ("base1", "BS", "Base Set")
    if ("base" in s_name or "bs" in s_code) and ("2" in s_name or "b2" in s_code):
        return ("base4", "B2", "Base Set 2")

    # 7. Universele Matrix Match
    if s_code in UNIVERSAL_SET_MATRIX:
        entry = UNIVERSAL_SET_MATRIX[s_code]
        return (entry["tcgdex"], entry["cm"], entry["name"])

    for k, entry in UNIVERSAL_SET_MATRIX.items():
        if entry["name"].lower() == s_name:
            return (entry["tcgdex"], entry["cm"], entry["name"])

    clean_fallback_name = raw_set_name if raw_set_name and "onbekend" not in raw_set_name.lower() else "Pokémon Set"
    return (s_code or None, s_code.upper() if s_code else None, clean_fallback_name)

def generate_market_url(card_name: str, card_number: str, cm_code: Optional[str], set_name: Optional[str], is_graded: bool, grading_company: Optional[str], grade: Optional[str]) -> str:
    clean_name = quote_plus(card_name)
    clean_num = quote_plus(str(card_number).split("/")[0].strip().lstrip("0"))

    if is_graded:
        comp = quote_plus(grading_company or "PSA")
        gr = quote_plus(str(grade or "9"))
        return f"https://www.pricecharting.com/search-products?q={clean_name}+{clean_num}+{comp}+{gr}&type=prices"

    query_parts = [clean_name]
    if cm_code in ["BS", "JU", "FO", "B2", "TR", "SVP", "30C", "ASC", "PFL"]:
        query_parts.append(quote_plus(cm_code))
    query_parts.append(clean_num)
    search_query = "+".join(query_parts)
    return f"https://www.cardmarket.com/en/Pokemon/Products/Search?searchString={search_query}&sellerCountry=17&minCondition=2"

async def fetch_pricecharting_data(client: httpx.AsyncClient, card_name: str, card_number: str, set_name: str, grade: Optional[str]) -> Optional[Dict[str, Any]]:
    clean_name = quote_plus(card_name)
    clean_num = quote_plus(str(card_number).split("/")[0].strip().lstrip("0"))
    query = f"{clean_name}+{clean_num}"

    search_url = f"https://www.pricecharting.com/search-products?q={query}&type=prices"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

    try:
        resp = await client.get(search_url, headers=headers, timeout=6.0)
        if resp.status_code == 200:
            html = resp.text
            product_match = re.search(r'href="(/game/pokemon-[^"]+)"', html)
            if product_match:
                product_path = product_match.group(1)
                product_url = f"https://www.pricecharting.com{product_path}"

                prod_resp = await client.get(product_url, headers=headers, timeout=6.0)
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
                            "source": "PriceCharting Marktprijs"
                        }
    except Exception as e:
        logger.warning(f"PriceCharting lookup exception: {e}")

    return None

async def fetch_tcgdex_resilient(client: httpx.AsyncClient, card_name: str, card_number: str, tcgdex_set_id: Optional[str], set_name: str) -> Optional[Dict[str, Any]]:
    clean_num = str(card_number).split("/")[0].strip()
    headers = {"Accept": "application/json", "User-Agent": "BRIMI-Scanner/5.0"}

    if tcgdex_set_id:
        url_candidates = [
            f"https://api.tcgdex.net/v2/en/cards/{tcgdex_set_id}-{clean_num}",
            f"https://api.tcgdex.net/v2/en/cards/{tcgdex_set_id}-{clean_num.lstrip('0')}",
            f"https://api.tcgdex.net/v2/en/cards/{tcgdex_set_id}-{clean_num.zfill(3)}"
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
                        val = float(trend or avg30 or low)
                        # Validatie: negeer foute common-prijzen voor zeldzame kaarten
                        if val < 0.20 and ("illustration" in set_name.lower() or "promo" in set_name.lower() or "secret" in set_name.lower()):
                            continue
                        return {
                            "trend": float(trend) if trend else None,
                            "low": float(low) if low else None,
                            "avg30": float(avg30) if avg30 else None,
                            "official_set": official_set or set_name,
                            "cm_url": data.get("cardmarket", {}).get("url")
                        }
            except Exception:
                pass

    try:
        search_url = f"https://api.tcgdex.net/v2/en/cards?name={quote_plus(card_name)}"
        resp = await client.get(search_url, headers=headers, timeout=4.5)
        if resp.status_code == 200:
            cards = resp.json()
            if isinstance(cards, list):
                num_plain = clean_num.lstrip("0")
                for c in cards:
                    c_local = str(c.get("localId", "")).strip().lstrip("0")
                    if c_local == num_plain or clean_num in str(c.get("localId", "")):
                        card_id = c.get("id")
                        c_resp = await client.get(f"https://api.tcgdex.net/v2/en/cards/{card_id}", headers=headers, timeout=3.5)
                        if c_resp.status_code == 200:
                            data = c_resp.json()
                            pricing = data.get("pricing", {}).get("cardmarket") or data.get("cardmarket", {}).get("prices") or {}
                            trend = pricing.get("trend") or pricing.get("trend-holo")
                            low = pricing.get("low")
                            avg30 = pricing.get("avg30")
                            if trend or avg30 or low:
                                return {
                                    "trend": float(trend) if trend else None,
                                    "low": float(low) if low else None,
                                    "avg30": float(avg30) if avg30 else None,
                                    "official_set": data.get("set", {}).get("name") or set_name,
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
                        continue
                    else:
                        last_error_msg = f"{model_name} (HTTP {response.status_code})"
                except Exception as ex:
                    last_error_msg = f"{model_name} Netwerkfout: {str(ex)}"

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

    return []

@app.get("/")
def read_root():
    return {
        "status": "online",
        "app": "BRIMI Universal Precision Scanner API",
        "version": "5.0.0",
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

            tcgdex_id, cm_code, clean_sname = resolve_set_metadata(c_set_raw, c_sname_raw, c_num, c_name)

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

        tcgdex_id, cm_code, resolved_set_name = resolve_set_metadata(c.get("setCode"), c.get("setName"), c_num_val, c_name_val)

        low_price_eur = None
        trend_price_eur = None
        avg30_eur = None
        final_price = 0.0
        price_source = ""
        market_url = generate_market_url(c_name_val, c_num_val, cm_code, resolved_set_name, is_graded, c.get("gradingCompany"), c_grade_val)

        # 1. Graded Slabs (PriceCharting)
        if is_graded:
            company = c.get("gradingCompany") or "Slab"
            grade_val = c_grade_val or "9"
            if pc_data and pc_data.get("price_eur"):
                final_price = float(pc_data["price_eur"])
                price_source = f"PriceCharting ({company} {grade_val})"
                if pc_data.get("product_url"):
                    market_url = pc_data["product_url"]
            else:
                final_price = 0.0
                price_source = "PriceCharting (Handmatig Invoeren)"

        # 2. Live Cardmarket NL (via TCGdex)
        elif tcgdex_data and (tcgdex_data.get("trend") or tcgdex_data.get("avg30") or tcgdex_data.get("low")):
            trend_val = tcgdex_data.get("trend") or tcgdex_data.get("avg30") or tcgdex_data.get("low")
            final_price = float(trend_val)
            trend_price_eur = float(trend_val)
            avg30_eur = float(tcgdex_data.get("avg30")) if tcgdex_data.get("avg30") else None
            low_price_eur = float(tcgdex_data.get("low")) if tcgdex_data.get("low") else None
            price_source = "Cardmarket NL Trend"
            if tcgdex_data.get("official_set"):
                resolved_set_name = tcgdex_data["official_set"]
            if tcgdex_data.get("cm_url"):
                market_url = tcgdex_data["cm_url"]

        # 3. Live PriceCharting Fallback (bijv. gloednieuwe sets of veilingkoersen)
        elif pc_data and pc_data.get("price_eur") and pc_data["price_eur"] > 0:
            final_price = float(pc_data["price_eur"])
            price_source = "PriceCharting Marktprijs"
            if pc_data.get("product_url"):
                market_url = pc_data["product_url"]

        # 4. Eerlijke Handmatige Fallback (GEEN VERZONNEN PRIJZEN)
        else:
            final_price = 0.0
            price_source = "Cardmarket NL (Handmatig Invoeren)"

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
