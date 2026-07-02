"""Geo-Filter: hartes Reject für Nicht-EU/UK-Jobs.

Drei-Stufen-Klassifikation:
  1) country-Feld → falls EU/UK gesetzt: KEEP. Falls explizit Nicht-EU: REJECT.
  2) Location-Text (+ Title + erste 300 Zeichen Description) → EU-Keyword schlägt
     Overseas-Keyword. EU-Keyword wins → KEEP. Overseas ohne EU → REJECT.
  3) Mehrdeutig (z.B. nur "Remote" ohne weitere Hinweise) → REJECT (strict mode).

Damit verschwinden US/LATAM/APAC/Mittlerer-Osten/Afrika-Jobs konsequent aus dem
Output. Vereinzelt rutschen reine "Remote"-Jobs raus, die in EU sein könnten —
das ist der Preis für "konsequent raus".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schema import JobPosting

# --- ISO-Codes ---
EU_PLUS_UK_COUNTRY_CODES: frozenset[str] = frozenset({
    # EU-27
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR",
    "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO",
    "SE", "SI", "SK",
    # UK + assoziierte europäische Länder (Schengen / EFTA / Mikrostaaten)
    "UK", "GB", "CH", "NO", "IS", "LI", "MC", "AD", "SM", "VA",
})

# Explizite Nicht-EU-Codes (zur Erkennung)
NON_EU_COUNTRY_CODES: frozenset[str] = frozenset({
    "US", "USA", "CA", "MX", "BR", "AR", "CL", "CO", "PE", "UY", "VE",
    "CN", "JP", "KR", "IN", "ID", "MY", "PH", "SG", "TH", "VN", "TW", "HK", "PK",
    "AU", "NZ", "FJ",
    "RU", "UA", "BY", "TR", "IL", "AE", "SA", "QA", "KW", "BH", "OM", "JO", "LB", "EG",
    "ZA", "NG", "KE", "MA", "TN", "GH",
})

# --- Volltext-Keywords (lowercase) ---
# Diese Strings im Location/Title/Description-Text führen zum REJECT,
# außer ein EU-Keyword steht ebenfalls drin (dann wins EU).
OVERSEAS_KEYWORDS: frozenset[str] = frozenset({
    # USA
    "united states", "usa", "u.s.a", "u.s.", "us-remote",
    "remote us", "remote (us)", "us remote", "us only", "north america",
    "new york", "nyc", "san francisco", "los angeles", "chicago", "boston",
    "seattle", "austin", "denver", "atlanta", "miami", "dallas", "houston",
    "philadelphia", "washington dc", "washington d.c", "silicon valley", "bay area",
    # Kanada
    "canada", "toronto", "montreal", "vancouver", "calgary", "ottawa",
    # LATAM
    "mexico", "brazil", "brasil", "argentina", "chile", "colombia",
    "peru", "uruguay", "venezuela", "latam", "latin america", "south america",
    "rio de janeiro", "são paulo", "buenos aires", "lima", "bogotá", "bogota",
    "mexico city", "ciudad de méxico",
    # APAC
    "india", "bangalore", "bengaluru", "mumbai", "delhi", "hyderabad", "chennai", "pune",
    "china", "shanghai", "beijing", "shenzhen", "hong kong",
    "japan", "tokyo", "osaka",
    "korea", "seoul",
    "singapore", "indonesia", "jakarta", "malaysia", "kuala lumpur",
    "thailand", "bangkok", "vietnam", "hanoi", "ho chi minh",
    "philippines", "manila", "apac", "asia pacific", "south asia", "southeast asia",
    "taiwan", "taipei",
    # Ozeanien
    "australia", "sydney", "melbourne", "brisbane", "perth",
    "new zealand", "auckland", "wellington", "anz",
    # Mittlerer Osten
    "uae", "dubai", "abu dhabi", "saudi arabia", "riyadh", "jeddah",
    "qatar", "doha", "kuwait", "bahrain", "oman", "muscat",
    "israel", "tel aviv", "jerusalem", "middle east",
    # Afrika
    "south africa", "johannesburg", "cape town", "nigeria", "lagos",
    "kenya", "nairobi", "egypt", "cairo", "morocco", "casablanca",
    "africa", "north africa",
})

# EU-Signal-Keywords — schlagen ein zufälliges Overseas-Keyword
EU_KEYWORDS: frozenset[str] = frozenset({
    # Region/Block-Begriffe
    "europe", "european", "eu region", "eu-only", "emea",
    "dach", "d-a-ch", "central europe", "western europe", "northern europe",
    "european union", "schengen",
    "remote europe", "remote (europe)", "europe-remote", "remote-europe",
    "remote, europe", "remote in europe", "remote eu",
    # Länder (englisch + deutsch + landessprachlich)
    "germany", "deutschland", "austria", "österreich", "osterreich", "switzerland", "schweiz",
    "france", "francia", "spain", "españa", "espana", "italy", "italia",
    "portugal", "ireland", "irl",
    "united kingdom", "great britain", "england", "scotland", "wales", "northern ireland",
    "netherlands", "niederlande", "nederland", "belgium", "belgien", "belgique",
    "luxembourg", "luxemburg", "denmark", "danemark", "dänemark",
    "sweden", "schweden", "sverige", "norway", "norwegen", "norge",
    "finland", "finnland", "suomi", "iceland", "island",
    "poland", "polen", "polska", "czech republic", "czechia", "tschechien",
    "hungary", "ungarn", "magyarország",
    "greece", "griechenland", "ελλάδα", "romania", "rumänien", "bulgaria", "bulgarien",
    "croatia", "kroatien", "estonia", "estland", "latvia", "lettland",
    "lithuania", "litauen", "slovenia", "slowenien", "slovakia", "slowakei",
    "malta", "cyprus", "zypern",
    # Großstädte / Tech-Hubs
    "berlin", "munich", "münchen", "muenchen", "hamburg", "frankfurt",
    "köln", "koeln", "cologne", "stuttgart", "düsseldorf", "duesseldorf",
    "leipzig", "dresden", "hannover", "nürnberg", "nuernberg",
    "vienna", "wien", "graz", "linz", "salzburg",
    "zurich", "zürich", "zuerich", "basel", "bern", "geneva", "genf", "lausanne",
    "paris", "lyon", "marseille", "toulouse", "bordeaux", "nice",
    "london", "manchester", "birmingham", "bristol", "edinburgh", "glasgow",
    "dublin", "cork", "galway",
    "madrid", "barcelona", "valencia", "sevilla", "bilbao",
    "amsterdam", "rotterdam", "the hague", "den haag", "utrecht", "eindhoven",
    "brussels", "bruxelles", "antwerpen", "antwerp",
    "milan", "milano", "rome", "roma", "turin", "torino", "naples",
    "lisbon", "lisboa", "porto",
    "warsaw", "warszawa", "kraków", "krakow", "wrocław", "wroclaw",
    "prague", "praha", "brno",
    "budapest",
    "stockholm", "gothenburg", "göteborg",
    "copenhagen", "københavn", "aarhus",
    "helsinki", "tampere",
    "oslo", "bergen",
    "athens", "thessaloniki",
    "valletta", "sliema", "st julians",
    # Englische "X based in Y"-Phrasen
    "based in europe", "based in eu",
})

# Reine-Remote-Hints (ohne weiteren Kontext mehrdeutig)
REMOTE_ONLY_HINTS: frozenset[str] = frozenset({
    "remote", "fully remote", "100% remote", "work from home", "wfh",
    "worldwide", "global", "anywhere",
})


def _normalize(text: str) -> str:
    return (text or "").lower().strip()


def is_european(job: "JobPosting") -> tuple[bool, str]:
    """Klassifiziert einen Job. Gibt (is_eu, reason) zurück — reason für Logging.

    WICHTIG: Das `country`-Feld der ATS-Quellen (Greenhouse, Ashby, Lever) zeigt
    NICHT das Land des Jobs, sondern den Firmensitz. Stripe (US-HQ) setzt
    country='US' auch für seine Berlin/London/Dublin-Stellen. HelloFresh (DE-HQ)
    setzt country='DE' auch für seine Manila-Stelle.

    Deshalb ist der Volltext (location + title + erste 500 Zeichen Description)
    die VORRANGIGE Quelle — das ist das was bei der konkreten Stelle steht.
    Der ISO-Code wird nur als letzter Tiebreaker konsultiert, wenn der Volltext
    überhaupt nichts hergibt.

    Strict mode: alles Mehrdeutige wird rejected.
    """
    haystack = " ".join([
        _normalize(job.location),
        _normalize(job.title),
        _normalize(job.description_text or "")[:500],
    ])

    has_overseas = any(kw in haystack for kw in OVERSEAS_KEYWORDS)
    has_eu = any(kw in haystack for kw in EU_KEYWORDS)

    # 1) Beide Signale → EU gewinnt (z.B. "Stripe is based in San Francisco —
    #    this role is in Berlin" hat sowohl SF als auch Berlin)
    if has_eu and has_overseas:
        # Aber: wenn das Location-Feld selbst ein Overseas-Keyword enthält,
        # zählt das stärker (Location ist meist der Job-Standort).
        loc = _normalize(job.location)
        loc_is_overseas = any(kw in loc for kw in OVERSEAS_KEYWORDS)
        loc_is_eu = any(kw in loc for kw in EU_KEYWORDS)
        if loc_is_overseas and not loc_is_eu:
            return False, "location-feld zeigt overseas, eu nur in description"
        return True, "eu-keyword im text"

    if has_eu:
        return True, "eu-keyword im text"
    if has_overseas:
        return False, "overseas-keyword im text, kein EU-match"

    # 2) Volltext leer/mehrdeutig — ISO-Code als Fallback
    #    Nur wenn weder location noch title noch description Geo-Info enthalten
    cc = _normalize(job.country).upper()
    if cc in EU_PLUS_UK_COUNTRY_CODES:
        return True, f"kein volltext-signal, country={cc} (EU/UK fallback)"
    if cc in NON_EU_COUNTRY_CODES:
        return False, f"kein volltext-signal, country={cc} (overseas fallback)"

    # 3) Nur "Remote"/"Worldwide" ohne weitere Signale → strict reject
    is_pure_remote = any(h in haystack for h in REMOTE_ONLY_HINTS)
    if is_pure_remote:
        return False, "remote-only ohne EU-hinweis"
    if not haystack.strip():
        return False, "keine location/title/description-daten"

    # 4) Ganz ohne Geo-Hinweis — strikter Reject
    return False, "kein EU-signal gefunden"
