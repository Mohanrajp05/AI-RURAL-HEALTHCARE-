import html
import os
import re
import sys
import time
import unicodedata

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge_base")

REQUEST_DELAY_SECONDS = 2.0          # polite delay between requests
REQUEST_TIMEOUT_SECONDS = 25

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 "
        "rural-health-kb-builder/1.0"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Output fields, in order.  Each becomes a heading in the .txt file.
FIELDS = [
    "symptoms",
    "causes",
    "prevention",
    "treatment",
    "diet recommendations",
    "when to see a doctor",
]

# ---------------------------------------------------------------------------
# Disease -> candidate URLs (tried in order; first valid parse is kept)
# ---------------------------------------------------------------------------
DISEASES = {
    "(vertigo) Paroymsal  Positional Vertigo": [
        "https://en.wikipedia.org/wiki/Vertigo",
        "https://medlineplus.gov/vertigo.html",
    ],
    "AIDS": [
        "https://www.cdc.gov/hiv/basics/index.html",
        "https://www.who.int/news-room/fact-sheets/detail/hiv-aids",
    ],
    "Acne": [
        "https://www.niams.nih.gov/health-topics/acne",
        "https://www.mayoclinic.org/diseases-conditions/acne/symptoms-causes/syc-20368047",
    ],
    "Alcoholic Hepatitis": [
        "https://en.wikipedia.org/wiki/Alcoholic_hepatitis",
        "https://medlineplus.gov/ency/article/000281.htm",
    ],
    "Allergy": [
        "https://en.wikipedia.org/wiki/Allergy",
        "https://www.niaid.nih.gov/diseases-conditions/allergies",
    ],
    "Arthritis": [
        "https://www.cdc.gov/arthritis/basics/index.html",
        "https://www.who.int/news-room/fact-sheets/detail/osteoarthritis",
    ],
    "Bronchial Asthma": [
        "https://www.cdc.gov/asthma/default.htm",
        "https://www.who.int/news-room/fact-sheets/detail/asthma",
    ],
    "Cervical Spondylosis": [
        "https://orthoinfo.aaos.org/en/diseases--conditions/cervical-spondylosis/",
        "https://www.nhs.uk/conditions/cervical-spondylosis/",
    ],
    "Chicken Pox": [
        "https://www.nhs.uk/conditions/chickenpox/",
        "https://en.wikipedia.org/wiki/Chickenpox",
    ],
    "Chronic Cholestasis": [
        "https://www.niddk.nih.gov/health-information/liver-disease",
        "https://www.mayoclinic.org/diseases-conditions/primary-biliary-cholangitis/symptoms-causes/syc-20351497",
    ],
    "Common Cold": [
        "https://en.wikipedia.org/wiki/Common_cold",
        "https://medlineplus.gov/commoncold.html",
    ],
    "Dengue": [
        "https://www.who.int/news-room/fact-sheets/detail/dengue-and-severe-dengue",
        "https://www.cdc.gov/dengue/index.html",
    ],
    "Diabetes": [
        "https://www.cdc.gov/diabetes/basics/diabetes.html",
        "https://www.who.int/news-room/fact-sheets/detail/diabetes",
    ],
    "Dimorphic hemmorhoids(piles)": [
        "https://en.wikipedia.org/wiki/Hemorrhoid",
        "https://medlineplus.gov/hemorrhoids.html",
    ],
    "Drug Reaction": [
        "https://en.wikipedia.org/wiki/Adverse_drug_reaction",
        "https://medlineplus.gov/drugreactions.html",
    ],
    "Fungal Infection": [
        "https://en.wikipedia.org/wiki/Fungal_infection",
        "https://www.niaid.nih.gov/diseases-conditions/fungal-diseases",
    ],
    "GERD": [
        "https://www.niddk.nih.gov/health-information/digestive-diseases/acid-reflux-gerd",
        "https://www.nhs.uk/conditions/heartburn-and-acid-reflux/",
    ],
    "Gastroenteritis": [
        "https://www.cdc.gov/norovirus/index.html",
        "https://www.nhs.uk/conditions/gastroenteritis/",
    ],
    "Heart Attack": [
        "https://en.wikipedia.org/wiki/Myocardial_infarction",
        "https://medlineplus.gov/heartattack.html",
    ],
    "Hepatitis A": [
        "https://www.who.int/news-room/fact-sheets/detail/hepatitis-a",
        "https://www.cdc.gov/hepatitis/hav/index.htm",
    ],
    "Hepatitis B": [
        "https://www.who.int/news-room/fact-sheets/detail/hepatitis-b",
        "https://www.cdc.gov/hepatitis/hbv/index.htm",
    ],
    "Hepatitis C": [
        "https://www.who.int/news-room/fact-sheets/detail/hepatitis-c",
        "https://www.cdc.gov/hepatitis/hcv/index.htm",
    ],
    "Hepatitis D": [
        "https://www.who.int/news-room/fact-sheets/detail/hepatitis-d",
        "https://www.cdc.gov/hepatitis/hdv/index.htm",
    ],
    "Hepatitis E": [
        "https://www.who.int/news-room/fact-sheets/detail/hepatitis-e",
        "https://www.cdc.gov/hepatitis/hev/index.htm",
    ],
    "Hypertension": [
        "https://www.who.int/news-room/fact-sheets/detail/hypertension",
        "https://www.cdc.gov/bloodpressure/index.html",
    ],
    "Hyperthyroidism": [
        "https://www.niddk.nih.gov/health-information/endocrine-diseases/hyperthyroidism",
        "https://www.nhs.uk/conditions/over-active-thyroid-hyperthyroidism/",
    ],
    "Hypoglycemia": [
        "https://www.niddk.nih.gov/health-information/diabetes/overview/preventing-problems/low-blood-glucose-hypoglycemia",
        "https://www.mayoclinic.org/diseases-conditions/hypoglycemia/symptoms-causes/syc-20351497",
    ],
    "Hypothyroidism": [
        "https://en.wikipedia.org/wiki/Hypothyroidism",
        "https://medlineplus.gov/hypothyroidism.html",
    ],
    "Impetigo": [
        "https://www.cdc.gov/groupa-strep/impetigo/index.html",
        "https://www.nhs.uk/conditions/impetigo/",
    ],
    "Jaundice": [
        "https://www.nhs.uk/conditions/jaundice/",
        "https://www.niddk.nih.gov/health-information/liver-disease/jaundice-adults",
    ],
    "Malaria": [
        "https://www.who.int/news-room/fact-sheets/detail/malaria",
        "https://www.cdc.gov/malaria/index.html",
    ],
    "Migraine": [
        "https://www.who.int/news-room/fact-sheets/detail/headache-disorders",
        "https://www.mayoclinic.org/diseases-conditions/migraine-headache/symptoms-causes/syc-20351497",
    ],
    "Osteoarthristis": [
        "https://www.who.int/news-room/fact-sheets/detail/osteoarthritis",
        "https://www.cdc.gov/arthritis/types/osteoarthritis.html",
    ],
    "Paralysis (brain hemorrhage)": [
        "https://www.nhs.uk/conditions/paralysis/",
        "https://www.mayoclinic.org/diseases-conditions/paralysis/symptoms-causes/syc-20351497",
    ],
    "Peptic ulcer diseae": [
        "https://www.niddk.nih.gov/health-information/digestive-diseases/peptic-ulcer-disease",
        "https://www.nhs.uk/conditions/stomach-ulcer/",
    ],
    "Pneumonia": [
        "https://www.who.int/news-room/fact-sheets/detail/pneumonia",
        "https://www.cdc.gov/pneumonia/index.html",
    ],
    "Psoriasis": [
        "https://www.niams.nih.gov/health-topics/psoriasis",
        "https://www.mayoclinic.org/diseases-conditions/psoriasis/symptoms-causes/syc-20351497",
    ],
    "Tuberculosis": [
        "https://www.who.int/news-room/fact-sheets/detail/tuberculosis",
        "https://www.cdc.gov/tb/default.htm",
    ],
    "Typhoid": [
        "https://www.who.int/news-room/fact-sheets/detail/typhoid",
        "https://www.cdc.gov/typhoid-fever/index.html",
    ],
    "Urinary Tract Infection": [
        "https://en.wikipedia.org/wiki/Urinary_tract_infection",
        "https://medlineplus.gov/urinarytractinfections.html",
    ],
    "Varicose Veins": [
        "https://www.nhs.uk/conditions/varicose-veins/",
        "https://www.mayoclinic.org/diseases-conditions/varicose-veins/symptoms-causes/syc-20351497",
    ],
}

# ---------------------------------------------------------------------------
# Boilerplate removal  (unchanged from original)
# ---------------------------------------------------------------------------
_BOILERPLATE_TOKENS = [
    "ad", "ads", "advert", "sponsor", "promo", "banner",
    "nav", "menu", "breadcrumb", "footer", "header",
    "widget", "social", "share", "related", "comment",
    "newsletter", "subscribe", "cookie", "consent",
    "side", "popup", "modal", "toolbar", "search",
    "skip", "affiliate", "recommendation",
]
_BOILERPLATE_ROLES = {
    "navigation", "banner", "contentinfo", "complementary", "advertisement",
}
_SKIP_TAGS = {
    "script", "style", "noscript", "iframe", "svg", "canvas",
    "button", "form", "input", "select", "textarea", "aside", "nav",
    "footer", "header",   # NOTE: "header" added -- the original list omitted
                            # it, letting site chrome wrapped in <header> leak
                            # through on sites that don't tag it with a
                            # boilerplate-looking class/id.
}


def _clean_text(raw) -> str:
    if not raw:
        return ""
    text = html.unescape(str(raw))
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text)
    text = text.replace("\u00a0", " ")
    return text.strip()


def _element_identifier(el) -> str:
    parts = []
    if el.get("id"):
        parts.append(el["id"])
    classes = el.get("class") if isinstance(el.get("class"), (list, tuple)) else []
    if classes:
        parts.extend(classes)
    return " ".join(str(p).lower() for p in parts)


def _is_boilerplate(el) -> bool:
    name = (el.name or "").lower()
    if name in _SKIP_TAGS:
        return True
    if el.find(["h1", "h2", "h3", "h4", "h5", "h6"]):
        return False
    ident = _element_identifier(el)
    for tok in _BOILERPLATE_TOKENS:
        if re.search(rf"\b{re.escape(tok)}\b", ident):
            return True
    return (el.get("role") or "").lower() in _BOILERPLATE_ROLES


def _purge_boilerplate(soup: BeautifulSoup) -> None:
    for el in soup.find_all(True):
        try:
            if _is_boilerplate(el):
                el.decompose()
        except Exception:
            continue


# ---------------------------------------------------------------------------
# Section keyword mapping  ---  FIX 2: broadened, not anchored to ^
# ---------------------------------------------------------------------------
# The original patterns used `^\s*(?:word|...)` which only matches when the
# heading LITERALLY STARTS with the exact word. Real pages phrase things
# naturally -- e.g. NHS's chickenpox treatment section is titled
# "How you can treat chickenpox yourself", which never matched "^treatment".
# Fix: search anywhere in the heading (re.search, no ^ anchor) and add the
# natural-language phrasings actually seen on WHO / NHS / CDC / Mayo pages.
_SECTION_PATTERNS = {
    "symptoms": re.compile(
        r"signs?\s*(?:and|&)?\s*symptoms|\bsymptoms\b|\bsymp\.?\b|presentation|"
        r"clinical features|what are the symptoms|warning signs",
        re.IGNORECASE,
    ),
    "causes": re.compile(
        r"\bcauses?\b|risk factors|etiology|what causes|"
        r"\bspreads?\b|\btransmitt(?:ed|ing|ion)\b|\bcatch(?:ing)?\b|"
        r"\bcaught\b|contagious",
        re.IGNORECASE,
    ),
    "prevention": re.compile(
        r"\bprevention\b|preventing|how to prevent|reduce (?:your )?risk|"
        r"protect yourself|avoid(?:ing)?\s+(?:it|infection|mosquito|getting)|"
        r"vaccin",
        re.IGNORECASE,
    ),
    "treatment": re.compile(
        r"\btreatments?\b|treatment options|\bmanag(?:e|ing|ement)\b|"
        r"medications?|self.care|how (?:you can |to )?treat|"
        r"what to do|home remed|how (?:it'?s|is) treated",
        re.IGNORECASE,
    ),
    "diet recommendations": re.compile(
        r"\bdiet\b|nutrition|foods?\s+to\s+(?:eat|avoid)|eating (?:habits|well|healthy)",
        re.IGNORECASE,
    ),
    "when to see a doctor": re.compile(
        r"when to see a doctor|when to (?:see|call|contact|seek|get)\b|"
        r"when (?:and where )?to get (?:medical )?help|red flags|emergency|"
        r"seek (?:immediate |urgent )?(?:medical (?:help|attention|care)|care)|"
        r"urgent advice|non-urgent advice|get help from",
        re.IGNORECASE,
    ),
}

# Order matters when a heading could plausibly match more than one pattern --
# check the most specific/urgent category first so e.g. "Urgent advice: get
# help from NHS 111" is filed as "when to see a doctor", not swallowed by a
# broader pattern.
_FIELD_CHECK_ORDER = [
    "when to see a doctor", "causes", "diet recommendations",
    "prevention", "treatment", "symptoms",
]


def _match_section(heading: str):
    for field in _FIELD_CHECK_ORDER:
        if _SECTION_PATTERNS[field].search(heading):
            return field
    return None


# ---------------------------------------------------------------------------
# Fallback keyword-sentence extraction  ---  FIX 3
# ---------------------------------------------------------------------------
# Some content (esp. "causes" and "when to see a doctor") is never under its
# own heading at all -- it's a sentence or two embedded inside a different
# section (e.g. "People with severe symptoms should get emergency care right
# away" living inside WHO's "Symptoms" section). If the heading-based pass
# leaves a field empty, scan the page's plain text for sentences containing
# strong trigger phrases for that field and use those instead of giving up.
_FALLBACK_TRIGGERS = {
    "causes": [
        r"caused by", r"is caused", r"results? from", r"due to (?:a|an|the)",
        r"spreads? (?:to|through|by|from)", r"transmitted",
    ],
    "prevention": [
        r"can be prevented", r"to prevent", r"reduce (?:the |your )?risk",
        r"avoid(?:ing)?",
    ],
    "treatment": [
        r"treated with", r"treatment (?:includes|involves|for)",
        r"medicines? (?:used|include|for)", r"you can treat",
    ],
    "diet recommendations": [
        r"\bdiet\b", r"foods? to (?:eat|avoid)", r"nutrition",
    ],
    "when to see a doctor": [
        r"see a (?:doctor|gp|physician)", r"call (?:111|911|emergency)",
        r"seek (?:immediate |urgent )?medical", r"emergency care",
        r"get help from", r"urgent advice", r"non-urgent advice",
    ],
}

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _fallback_fill(field: str, main_text: str, max_chars: int = 600) -> str:
    triggers = _FALLBACK_TRIGGERS.get(field)
    if not triggers or not main_text:
        return ""
    pattern = re.compile("|".join(triggers), re.IGNORECASE)
    hits = []
    total = 0
    for sentence in _SENTENCE_SPLIT.split(main_text):
        sentence = sentence.strip()
        if len(sentence) < 15:
            continue
        if pattern.search(sentence):
            hits.append(sentence)
            total += len(sentence)
            if total >= max_chars:
                break
    return _clean_text(" ".join(hits))


# ---------------------------------------------------------------------------
# DOM readers
# ---------------------------------------------------------------------------
def _fetch_soup(url: str) -> "BeautifulSoup | None":
    try:
        resp = requests.get(
            url, headers=HEADERS, timeout=REQUEST_TIMEOUT_SECONDS, allow_redirects=True
        )
        if resp.status_code != 200:
            return None
        if not resp.text or len(resp.text) < 500:
            return None
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException:
        return None


def _prefer_main_text(soup: BeautifulSoup) -> str:
    for sel in ("main", "[role='main']", "article"):
        for el in soup.select(sel):
            text = el.get_text(" ", strip=True)
            if text and len(text) > 80:
                return text
    if soup.body:
        return soup.body.get_text(" ", strip=True)
    return ""


def _heading_level(tag_name: str) -> int:
    m = re.match(r"^h([1-6])$", tag_name)
    return int(m.group(1)) if m else 99


def _extract_sections(soup: BeautifulSoup) -> dict:
    """
    Walk the document and pull clean text under each matched heading.

    FIX 1: the original version reset current_field on ANY heading, so a
    sub-heading (e.g. <h3>Vector control</h3> nested under <h2>Prevention</h2>)
    silently truncated the section. This version tracks the heading LEVEL
    that started each section and only ends it when a heading of the SAME
    level or shallower appears -- deeper sub-headings' text stays attached
    to the parent field.
    """
    out = {field: "" for field in FIELDS}
    body = soup.body if soup.body else soup
    current_field = None
    current_level = None
    current_parts = []
    order = []

    def flush():
        nonlocal current_parts
        if current_field and current_parts:
            text = _clean_text(" ".join(current_parts))
            if text and len(text) > 20:
                order.append((current_field, text))
        current_parts = []

    for node in body.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li"]):
        tag = node.name.lower()
        if re.match(r"^h[1-6]$", tag):
            level = _heading_level(tag)
            heading = _clean_text(node.get_text(" ", strip=True))
            matched = _match_section(heading)

            # A heading at the same level or shallower than the one that
            # opened the current section ends that section.
            if current_field is not None and level <= current_level:
                flush()
                current_field = None
                current_level = None

            if matched:
                if current_field != matched:
                    flush()
                current_field = matched
                current_level = level
            # else: an unmatched sub-heading deeper than current_level --
            # keep collecting under the still-open current_field.
        elif current_field:
            text = _clean_text(node.get_text(" ", strip=True))
            if text:
                current_parts.append(text)
    flush()

    for field, text in order:
        if field in out and not out[field]:
            out[field] = text

    # FIX 3: fill any still-empty field from inline sentences elsewhere on
    # the page, before falling back to the "not found" placeholder.
    main_text = None
    for field in FIELDS:
        if out[field]:
            continue
        if main_text is None:
            main_text = _prefer_main_text(soup)
        filled = _fallback_fill(field, main_text)
        if filled:
            out[field] = filled

    return out


# ---------------------------------------------------------------------------
# Core per-disease logic  (unchanged)
# ---------------------------------------------------------------------------
def scrape_disease(disease: str, urls: list) -> dict | None:
    for url in urls:
        soup = _fetch_soup(url)
        time.sleep(REQUEST_DELAY_SECONDS)
        if soup is None:
            continue
        _purge_boilerplate(soup)
        sections = _extract_sections(soup)
        any_hit = any(bool(sections[f]) for f in FIELDS)
        if not any_hit:
            main = _prefer_main_text(soup)
            if main and len(main) > 100:
                sections["symptoms"] = main
                any_hit = True
        if any_hit:
            return {"disease": disease, "source_url": url, "sections": sections}
    return None


def _prepare_document(sections: dict) -> str:
    lines = []
    for field in FIELDS:
        body = _clean_text(sections.get(field, ""))
        if not body:
            body = "Information not found on the source page for this field."
        lines.append(f"## {field.title()}")
        lines.append(body)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _build_output(disease: str, source_url: str, sections: dict) -> str:
    header = [
        f"# {disease}",
        "",
        f"**Source:** {source_url}",
        "",
    ]
    body = _prepare_document(sections)
    return "\n".join(header) + "\n" + body


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    missing_only = "--missing-only" in sys.argv
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"Output directory: {OUTPUT_DIR}")
    print(f"Total diseases: {len(DISEASES)} | missing-only: {missing_only}\n")

    ok = 0
    failed = 0
    skipped = 0
    fields_found_total = 0
    fields_missing_total = 0

    for idx, (disease, urls) in enumerate(DISEASES.items(), 1):
        slug = re.sub(r"[^A-Za-z0-9]+", "_", disease).strip("_").lower()
        filepath = os.path.join(OUTPUT_DIR, f"{slug}.txt")
        if missing_only and os.path.exists(filepath):
            skipped += 1
            print(f"[{idx:02d}/{len(DISEASES)}] [SKIP] {disease} (already exists)")
            continue
        result = scrape_disease(disease, urls)
        if result is None:
            failed += 1
            print(f"[{idx:02d}/{len(DISEASES)}] [FAIL] {disease}")
            continue

        # Report per-disease fill rate so gaps are visible immediately
        # instead of discovered later by reading .txt files by hand.
        found = [f for f in FIELDS if result["sections"].get(f)]
        missing = [f for f in FIELDS if not result["sections"].get(f)]
        fields_found_total += len(found)
        fields_missing_total += len(missing)

        with open(filepath, "w", encoding="utf-8") as fh:
            fh.write(_build_output(result["disease"], result["source_url"], result["sections"]))
        ok += 1
        missing_note = f" (missing: {', '.join(missing)})" if missing else ""
        print(f"[{idx:02d}/{len(DISEASES)}] [OK]   {disease} -> {os.path.basename(filepath)}"
              f"  [{len(found)}/{len(FIELDS)} fields]{missing_note}")

    total_fields = fields_found_total + fields_missing_total
    fill_rate = (fields_found_total / total_fields * 100) if total_fields else 0
    print(f"\nDone. Success: {ok} | Failed: {failed} | Skipped (existing): {skipped}")
    print(f"Overall field fill rate: {fields_found_total}/{total_fields} ({fill_rate:.1f}%)")


if __name__ == "__main__":
    main()