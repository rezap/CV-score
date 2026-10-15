"""Where a candidate lives, for relocation logistics: the home country (where the job is,
``location.home_country`` in the config), the EU, the rest of Europe or the world.

Read from the documents without a model: the address or location in the CV's top area
and contact lines, the sender lines of a cover letter, "I live in ..." sentences, and the
country code of the phone number. If none of those name a place, the places mentioned in
the CV (its most recent roles come first) give a weaker guess, marked as not confident.

This is where the documents say the person is, not their nationality or right to work:
an EU citizen living in India counts as "world". Use it to plan logistics (relocation,
permits, interviews), check the details before rejecting anyone, and keep in mind that
filtering by location may need a lawful, job-related reason where you hire.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel

from cvscore.ingest.text_utils import is_section_keyword, normalize_text
from cvscore.models import BlockKind, DocType, SourceDocument
from cvscore.pii import find_phones, has_contact_details

# "home" is the configured home country; the others exclude it.
Region = Literal["home", "eu", "europe", "world"]
DEFAULT_HOME = "NO"

# code: (English name, region, other names: native, Norwegian, abbreviations)
# fmt: off
_COUNTRIES: dict[str, tuple[str, Region, tuple[str, ...]]] = {
    # EU
    "SE": ("Sweden", "eu", ("Sverige", "Schweden")),
    "DK": ("Denmark", "eu", ("Danmark", "Dänemark")),
    "FI": ("Finland", "eu", ("Suomi", "Finnland")),
    "DE": ("Germany", "eu", ("Deutschland", "Tyskland")),
    "FR": ("France", "eu", ("Frankrike", "Frankreich")),
    "NL": ("Netherlands", "eu", ("Nederland", "The Netherlands", "Holland", "Niederlande")),
    "BE": ("Belgium", "eu", ("Belgia", "Belgique", "België", "Belgien")),
    "LU": ("Luxembourg", "eu", ("Luxemburg",)),
    "ES": ("Spain", "eu", ("España", "Spania", "Spanien")),
    "PT": ("Portugal", "eu", ()),
    "IT": ("Italy", "eu", ("Italia", "Italien")),
    "AT": ("Austria", "eu", ("Österreich", "Østerrike")),
    "CZ": ("Czech Republic", "eu", ("Czechia", "Česko", "Tsjekkia", "Tschechien")),
    "SK": ("Slovakia", "eu", ("Slovensko", "Slowakei")),
    "PL": ("Poland", "eu", ("Polska", "Polen")),
    "HU": ("Hungary", "eu", ("Magyarország", "Ungarn")),
    "SI": ("Slovenia", "eu", ("Slovenija", "Slowenien")),
    "HR": ("Croatia", "eu", ("Hrvatska", "Kroatia", "Kroatien")),
    "RO": ("Romania", "eu", ("România", "Rumänien")),
    "BG": ("Bulgaria", "eu", ("Bulgarien",)),
    "GR": ("Greece", "eu", ("Hellas", "Griechenland", "Ελλάδα")),
    "IE": ("Ireland", "eu", ("Irland", "Éire")),
    "EE": ("Estonia", "eu", ("Eesti", "Estland")),
    "LV": ("Latvia", "eu", ("Latvija", "Lettland")),
    "LT": ("Lithuania", "eu", ("Lietuva", "Litauen")),
    "MT": ("Malta", "eu", ()),
    "CY": ("Cyprus", "eu", ("Kypros", "Zypern")),
    # Europe outside the EU
    "NO": ("Norway", "europe", ("Norge", "Noreg", "Norwegen", "Norvège")),
    "GB": (
        "United Kingdom",
        "europe",
        (
            "UK", "U.K.", "England", "Scotland", "Wales", "Northern Ireland", "Great Britain",
            "Storbritannia", "Großbritannien",
        ),
    ),
    "CH": ("Switzerland", "europe", ("Schweiz", "Suisse", "Svizzera", "Sveits")),
    "IS": ("Iceland", "europe", ("Ísland",)),
    "LI": ("Liechtenstein", "europe", ()),
    "RS": ("Serbia", "europe", ("Srbija", "Serbien")),
    "BA": ("Bosnia and Herzegovina", "europe", ("Bosnia", "Bosnia-Herzegovina")),
    "ME": ("Montenegro", "europe", ("Crna Gora",)),
    "MK": ("North Macedonia", "europe", ("Macedonia", "Nord-Makedonia")),
    "AL": ("Albania", "europe", ("Shqipëri", "Albanien")),
    "XK": ("Kosovo", "europe", ()),
    "MD": ("Moldova", "europe", ()),
    "UA": ("Ukraine", "europe", ("Ukraina", "Україна")),
    "BY": ("Belarus", "europe", ("Hviterussland",)),
    "RU": ("Russia", "europe", ("Russland", "Russian Federation", "Россия")),
    "TR": ("Turkey", "europe", ("Türkiye", "Tyrkia", "Türkei")),
    # Rest of the world: the countries most often seen in applications
    "US": ("United States", "world", ("USA", "U.S.A.", "U.S.", "United States of America")),
    "CA": ("Canada", "world", ()),
    "MX": ("Mexico", "world", ("México",)),
    "BR": ("Brazil", "world", ("Brasil",)),
    "AR": ("Argentina", "world", ()),
    "CL": ("Chile", "world", ()),
    "CO": ("Colombia", "world", ()),
    "PE": ("Peru", "world", ("Perú",)),
    "IN": ("India", "world", ()),
    "PK": ("Pakistan", "world", ()),
    "BD": ("Bangladesh", "world", ()),
    "LK": ("Sri Lanka", "world", ()),
    "NP": ("Nepal", "world", ()),
    "AF": ("Afghanistan", "world", ()),
    "CN": ("China", "world", ("Kina", "PRC")),
    "HK": ("Hong Kong", "world", ()),
    "TW": ("Taiwan", "world", ()),
    "JP": ("Japan", "world", ()),
    "KR": ("South Korea", "world", ("Korea", "Republic of Korea", "Sør-Korea")),
    "VN": ("Vietnam", "world", ("Viet Nam",)),
    "PH": ("Philippines", "world", ("Filippinene",)),
    "ID": ("Indonesia", "world", ()),
    "MY": ("Malaysia", "world", ()),
    "SG": ("Singapore", "world", ()),
    "TH": ("Thailand", "world", ()),
    "IR": ("Iran", "world", ()),
    "IQ": ("Iraq", "world", ("Irak",)),
    "SY": ("Syria", "world", ()),
    "IL": ("Israel", "world", ()),
    "LB": ("Lebanon", "world", ("Libanon",)),
    "SA": ("Saudi Arabia", "world", ("Saudi-Arabia",)),
    "AE": ("United Arab Emirates", "world", ("UAE", "U.A.E.", "De forente arabiske emirater")),
    "QA": ("Qatar", "world", ()),
    "EG": ("Egypt", "world", ("Egypten",)),
    "MA": ("Morocco", "world", ("Marokko", "Maroc")),
    "TN": ("Tunisia", "world", ()),
    "DZ": ("Algeria", "world", ()),
    "NG": ("Nigeria", "world", ()),
    "GH": ("Ghana", "world", ()),
    "KE": ("Kenya", "world", ()),
    "ET": ("Ethiopia", "world", ("Etiopia",)),
    "ZA": ("South Africa", "world", ("Sør-Afrika",)),
    "AU": ("Australia", "world", ()),
    "NZ": ("New Zealand", "world", ()),
    "GE": ("Georgia", "world", ("Sakartvelo",)),
    "AM": ("Armenia", "world", ()),
    "AZ": ("Azerbaijan", "world", ()),
    "KZ": ("Kazakhstan", "world", ()),
}
# fmt: on

# Cities and towns, matched as whole capitalised words. Short or ambiguous names (Ski, Os,
# Moss, Nice, Sofia, Jordan, ...) are left out: they are also names and words.
# fmt: off
_CITIES: dict[str, tuple[str, ...]] = {
    "NO": (
        "Oslo", "Bergen", "Trondheim", "Stavanger", "Kristiansand", "Tromsø", "Tromso", "Drammen",
        "Fredrikstad", "Sandnes", "Bodø", "Bodo", "Ålesund", "Aalesund", "Sandvika", "Bærum",
        "Asker", "Lillestrøm", "Lysaker", "Fornebu", "Hamar", "Lillehammer", "Gjøvik", "Sarpsborg",
        "Halden", "Tønsberg", "Sandefjord", "Larvik", "Porsgrunn", "Skien", "Arendal", "Grimstad",
        "Haugesund", "Molde", "Kristiansund", "Harstad", "Narvik", "Steinkjer", "Levanger",
        "Kongsberg", "Jessheim", "Horten", "Elverum", "Førde", "Mo i Rana", "Hønefoss", "Kolbotn",
        "Lørenskog", "Stjørdal",
    ),
    "SE": (
        "Stockholm", "Gothenburg", "Göteborg", "Malmö", "Uppsala", "Linköping", "Lund", "Västerås",
        "Örebro", "Umeå",
    ),
    "DK": ("Copenhagen", "København", "Aarhus", "Århus", "Odense", "Aalborg"),
    "FI": ("Helsinki", "Espoo", "Tampere", "Turku", "Oulu", "Vantaa"),
    "DE": (
        "Berlin", "Munich", "München", "Hamburg", "Frankfurt", "Cologne", "Köln", "Stuttgart",
        "Düsseldorf", "Leipzig", "Dresden", "Hannover", "Heidelberg", "Karlsruhe", "Darmstadt",
        "Aachen", "Bremen", "Nuremberg", "Nürnberg",
    ),
    "FR": (
        "Paris", "Lyon", "Marseille", "Toulouse", "Lille", "Bordeaux", "Grenoble", "Strasbourg",
        "Nantes", "Montpellier",
    ),
    "NL": (
        "Amsterdam", "Rotterdam", "The Hague", "Den Haag", "Utrecht", "Eindhoven", "Delft",
        "Leiden", "Groningen",
    ),
    "BE": (
        "Brussels", "Bruxelles", "Brussel", "Antwerp", "Antwerpen", "Ghent", "Gent", "Leuven",
        "Liège",
    ),
    "ES": ("Madrid", "Barcelona", "Valencia", "Seville", "Sevilla", "Málaga", "Bilbao"),
    "PT": ("Lisbon", "Lisboa", "Porto", "Coimbra", "Braga"),
    "IT": (
        "Rome", "Roma", "Milan", "Milano", "Turin", "Torino", "Naples", "Napoli", "Florence",
        "Firenze", "Bologna", "Padova", "Padua", "Pisa", "Trieste",
    ),
    "AT": ("Vienna", "Wien", "Graz", "Linz", "Salzburg", "Innsbruck"),
    "CZ": ("Prague", "Praha", "Brno", "Ostrava"),
    "SK": ("Bratislava", "Košice"),
    "PL": (
        "Warsaw", "Warszawa", "Kraków", "Krakow", "Wrocław", "Wroclaw", "Gdańsk", "Gdansk",
        "Poznań", "Poznan", "Łódź", "Lodz", "Katowice", "Lublin", "Szczecin",
    ),
    "HU": ("Budapest", "Debrecen", "Szeged"),
    "SI": ("Ljubljana", "Maribor"),
    "HR": ("Zagreb", "Rijeka"),
    "RO": ("Bucharest", "București", "Cluj-Napoca", "Timișoara", "Timisoara", "Iași", "Iasi"),
    "BG": ("Plovdiv", "Varna"),
    "GR": ("Athens", "Thessaloniki", "Patras", "Heraklion"),
    "IE": ("Dublin", "Cork", "Galway", "Limerick"),
    "EE": ("Tallinn", "Tartu"),
    "LV": ("Riga",),
    "LT": ("Vilnius", "Kaunas", "Klaipėda"),
    "MT": ("Valletta",),
    "CY": ("Nicosia", "Limassol", "Larnaca"),
    "GB": (
        "London", "Manchester", "Edinburgh", "Glasgow", "Birmingham", "Cambridge", "Oxford",
        "Bristol", "Leeds", "Belfast", "Liverpool", "Sheffield", "Newcastle", "Nottingham",
        "Aberdeen", "Cardiff", "Southampton",
    ),
    "CH": ("Zurich", "Zürich", "Geneva", "Genève", "Basel", "Bern", "Lausanne", "Lugano"),
    "IS": ("Reykjavik", "Reykjavík"),
    "RS": ("Belgrade", "Beograd", "Novi Sad", "Niš"),
    "BA": ("Sarajevo", "Banja Luka", "Mostar"),
    "MK": ("Skopje",),
    "AL": ("Tirana", "Tiranë"),
    "ME": ("Podgorica",),
    "XK": ("Pristina", "Prishtina"),
    "MD": ("Chișinău", "Chisinau"),
    "UA": ("Kyiv", "Kiev", "Kharkiv", "Lviv", "Odesa", "Odessa", "Dnipro"),
    "BY": ("Minsk",),
    "RU": ("Moscow", "Saint Petersburg", "St. Petersburg", "Novosibirsk", "Kazan"),
    "TR": ("Istanbul", "İstanbul", "Ankara", "Izmir", "İzmir"),
    "US": (
        "New York", "San Francisco", "Boston", "Seattle", "Chicago", "Los Angeles", "Austin",
        "San Diego", "Washington, DC", "Pittsburgh", "Atlanta", "Houston",
    ),
    "CA": ("Toronto", "Vancouver", "Montreal", "Montréal", "Ottawa", "Calgary"),
    "IN": (
        "Bangalore", "Bengaluru", "Mumbai", "Delhi", "New Delhi", "Hyderabad", "Chennai", "Pune",
        "Kolkata", "Noida", "Gurgaon", "Gurugram", "Ahmedabad", "Kochi",
    ),
    "PK": ("Karachi", "Lahore", "Islamabad", "Rawalpindi", "Faisalabad"),
    "BD": ("Dhaka", "Chittagong"),
    "LK": ("Colombo",),
    "NP": ("Kathmandu",),
    "AF": ("Kabul",),
    "IR": ("Tehran", "Isfahan", "Shiraz", "Mashhad", "Tabriz"),
    "IQ": ("Baghdad", "Erbil", "Basra"),
    "CN": ("Beijing", "Shanghai", "Shenzhen", "Guangzhou", "Hangzhou", "Wuhan", "Nanjing"),
    "JP": ("Tokyo", "Osaka", "Kyoto"),
    "KR": ("Seoul", "Busan"),
    "VN": ("Hanoi", "Ho Chi Minh City", "Da Nang"),
    "PH": ("Manila", "Cebu", "Quezon City"),
    "ID": ("Jakarta", "Bandung", "Surabaya"),
    "MY": ("Kuala Lumpur",),
    "TH": ("Bangkok",),
    "IL": ("Tel Aviv", "Jerusalem", "Haifa"),
    "LB": ("Beirut",),
    "SA": ("Riyadh", "Jeddah"),
    "AE": ("Dubai", "Abu Dhabi", "Sharjah"),
    "QA": ("Doha",),
    "EG": ("Cairo", "Alexandria", "Giza"),
    "MA": ("Casablanca", "Rabat", "Marrakech"),
    "TN": ("Tunis",),
    "DZ": ("Algiers",),
    "NG": ("Lagos", "Abuja", "Ibadan"),
    "GH": ("Accra", "Kumasi"),
    "KE": ("Nairobi", "Mombasa"),
    "ET": ("Addis Ababa",),
    "ZA": ("Cape Town", "Johannesburg", "Pretoria", "Durban"),
    "BR": ("São Paulo", "Sao Paulo", "Rio de Janeiro", "Belo Horizonte", "Brasília"),
    "MX": ("Mexico City", "Guadalajara", "Monterrey"),
    "AR": ("Buenos Aires", "Córdoba"),
    "CL": ("Santiago de Chile",),
    "CO": ("Bogotá", "Bogota", "Medellín", "Medellin"),
    "AU": ("Sydney", "Melbourne", "Brisbane", "Perth", "Adelaide", "Canberra"),
    "NZ": ("Auckland", "Wellington", "Christchurch"),
    "GE": ("Tbilisi",),
    "AM": ("Yerevan",),
    "AZ": ("Baku",),
    "KZ": ("Almaty", "Astana"),
}
# fmt: on

# International dialling codes; the longest matching prefix wins.
# fmt: off
_DIAL: dict[str, str] = {
    "47": "NO", "46": "SE", "45": "DK", "358": "FI", "354": "IS", "49": "DE", "33": "FR",
    "31": "NL", "32": "BE", "352": "LU", "34": "ES", "351": "PT", "39": "IT", "43": "AT",
    "420": "CZ", "421": "SK", "48": "PL", "36": "HU", "386": "SI", "385": "HR", "40": "RO",
    "359": "BG", "30": "GR", "353": "IE", "372": "EE", "371": "LV", "370": "LT", "356": "MT",
    "357": "CY", "44": "GB", "41": "CH", "423": "LI", "381": "RS", "387": "BA", "389": "MK",
    "355": "AL", "382": "ME", "383": "XK", "373": "MD", "380": "UA", "375": "BY", "7": "RU",
    "90": "TR", "1": "US", "52": "MX", "55": "BR", "54": "AR", "56": "CL", "57": "CO", "51": "PE",
    "91": "IN", "92": "PK", "880": "BD", "94": "LK", "977": "NP", "93": "AF", "86": "CN",
    "852": "HK", "886": "TW", "81": "JP", "82": "KR", "84": "VN", "63": "PH", "62": "ID",
    "60": "MY", "65": "SG", "66": "TH", "98": "IR", "964": "IQ", "963": "SY", "972": "IL",
    "961": "LB", "966": "SA", "971": "AE", "974": "QA", "20": "EG", "212": "MA", "216": "TN",
    "213": "DZ", "234": "NG", "233": "GH", "254": "KE", "251": "ET", "27": "ZA", "61": "AU",
    "64": "NZ", "995": "GE", "374": "AM", "994": "AZ",
}
# fmt: on

# Lines that state where the person lives.
_ADDRESS_LABEL = re.compile(
    r"^\s*(?:address|home address|location|based in|residence|current location|lives? in|"
    r"city|country|adresse|bosted|bopel|hjemsted|sted|land|wohnort|anschrift|adress|ort)"
    r"\s*[:\-–]",
    re.I,
)
_LIVE_IN = re.compile(
    r"\b(?:I (?:currently )?(?:live|reside|am (?:currently )?(?:based|located|living))|"
    r"(?:currently|presently) (?:based|living|located)|jeg bor|bor for tiden|jeg er bosatt)"
    r"\s+(?:in|i)\s+(?P<rest>[^.;\n]{2,60})",
    re.I,
)
# Relocating in general; moving to the home country is added per country (_relocate_re).
_RELOCATE = r"relocat\w*|willing to move|umzug|umziehen|flytte til|flytta till"
_NO_POSTCODE = re.compile(r"\b(?:N|NO)-\d{4}\b")
# A Norwegian mobile number written without +47: 8 digits starting with 4 or 9.
_NO_MOBILE = re.compile(r"(?<![\d+])[49]\d(?:\s?\d){6}(?!\d)")
# "+47 912 34 567", "0047 912 34 567", "(+82) 10-9030-1843"
_INTL_PHONE_RE = re.compile(r"(?:\+|(?<![\d.])00)\s?\(?(\d[\d\s().-]{6,}\d)")
# "La Verne, CA 91750", "Ithaca, NY": a US state after a comma.
_US_STATE_RE = re.compile(
    r",\s*(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|"
    r"NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)"
    r"(?:\s+\d{5})?\b"
)

_SKIP_SECTIONS = re.compile(
    r"educat|utdann|academic|languages|språk|sprog|references|referanser|interests|"
    r"hobbies|interesser|courses|kurs|certific|publications",
    re.I,
)


class Location(BaseModel):
    region: Region | None = None  # None: not found
    country: str | None = None  # ISO 3166 code
    country_name: str | None = None
    place: str | None = None  # the town or country as written in the documents
    source: str | None = None  # "address", "phone", "letter" or "CV text"
    confident: bool = False
    relocation: bool = False  # mentions relocating / moving to the home country


def country_code(value: str) -> str | None:
    """The ISO code for a country code or name ("NO", "no", "Norway", "Deutschland")."""
    text = value.strip()
    if text.upper() in _COUNTRIES:
        return text.upper()
    for code, (name, _, aliases) in _COUNTRIES.items():
        if text.casefold() in {n.casefold() for n in (name, *aliases)}:
            return code
    return None


def country_name(code: str) -> str:
    return _COUNTRIES[code][0] if code in _COUNTRIES else code


def in_eu(code: str) -> bool:
    return code in _COUNTRIES and _COUNTRIES[code][1] == "eu"


@lru_cache(maxsize=8)
def _relocate_re(home: str) -> re.Pattern[str]:
    name, _, aliases = _COUNTRIES[home]
    places = _alternation([name, *aliases, *_CITIES.get(home, ())])
    return re.compile(rf"\b(?:{_RELOCATE}|(?:move|moving) to (?:{places}))\b", re.I)


def _alternation(words: Iterable[str]) -> str:
    return "|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True))


_PLACE_TO_COUNTRY: dict[str, str] = {}
for _code, (_name, _region, _aliases) in _COUNTRIES.items():
    for _w in (_name, *_aliases):
        _PLACE_TO_COUNTRY.setdefault(_w, _code)
for _code, _cities in _CITIES.items():
    for _w in _cities:
        _PLACE_TO_COUNTRY.setdefault(_w, _code)
# Case-sensitive whole words: "Oslo", "UK", but not "turkey" or "reading".
_PLACE_RE = re.compile(rf"(?<![\w-])(?:{_alternation(_PLACE_TO_COUNTRY)})(?![\w-])")


def _places(text: str, exclude: set[str]) -> list[tuple[str, str]]:
    """(country code, place as written) for every known place in ``text``."""
    found = []
    for m in _PLACE_RE.finditer(text):
        word = m.group()
        if word.lower() in exclude:
            continue
        found.append((_PLACE_TO_COUNTRY[word], word))
    return found


def _phone_country(text: str) -> tuple[str, str] | None:
    """(country code, dialling code) of the first international phone number in ``text``."""
    for m in _INTL_PHONE_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group(1))
        if not 8 <= len(digits) <= 15:
            continue
        for n in (3, 2, 1):
            if digits[:n] in _DIAL:
                return _DIAL[digits[:n]], digits[:n]
    return None


def _top_area(doc: SourceDocument, limit: int) -> list[str]:
    lines: list[str] = []
    for b in doc.blocks[:limit]:
        if b.kind is BlockKind.HEADING and is_section_keyword(b.text) and lines:
            break
        lines.append(b.text)
    return lines


def locate(
    documents: Iterable[SourceDocument], names: Iterable[str] = (), home: str = DEFAULT_HOME
) -> Location:
    """Best guess at where the candidate lives (see the module docstring); ``home`` is the
    ISO code of the country the job is in."""
    docs = list(documents)
    relocate_re = _relocate_re(home)
    # The candidate's own names are not places, even "Israel" or "Paris".
    exclude = {p.lower() for n in names for p in re.split(r"[\s_\-.,]+", n) if p}
    scores: dict[str, float] = {}
    first: dict[str, tuple[str, str]] = {}  # code -> (place, source)
    relocation = False

    def add(code: str, weight: float, place: str, source: str) -> None:
        scores[code] = scores.get(code, 0) + weight
        first.setdefault(code, (place, source))

    for doc in docs:
        cv = doc.doc_type is DocType.CV
        letter = doc.doc_type is DocType.COVER_LETTER
        if cv:
            header = _top_area(doc, 14)
        elif letter:
            # A letter's top area may also hold the employer's address: only the lines next
            # to the sender's email or phone count.
            top = doc.blocks[:12]
            near = {
                j
                for i, b in enumerate(top)
                if b.is_contact or has_contact_details(b.text)
                for j in range(i - 2, i + 3)
            }
            header = [b.text for i, b in enumerate(top) if i in near]
        else:
            header = []
        header += [b.text for b in doc.blocks if b.is_contact]
        header += [b.text for b in doc.blocks if _ADDRESS_LABEL.match(b.text)]
        seen: set[str] = set()
        for line in header:
            line = normalize_text(line)
            if line in seen:
                continue
            seen.add(line)
            labelled = bool(_ADDRESS_LABEL.match(line))
            for code, place in _places(line, exclude):
                add(code, 4 if labelled else 3, place, "address" if cv or labelled else "letter")
            if state := _US_STATE_RE.search(line):
                add("US", 3, state.group().strip(", "), "address" if cv else "letter")
            if _NO_POSTCODE.search(line):
                add("NO", 3, "Norwegian postcode", "address")
            phone = _phone_country(line)
            if phone:
                add(phone[0], 2, f"phone +{phone[1]}", "phone")
            elif _NO_MOBILE.search(line) and find_phones(line):
                add("NO", 1, "Norwegian mobile number", "phone")
        for b in doc.blocks:
            if letter or cv:
                for m in _LIVE_IN.finditer(b.text):
                    for code, place in _places(m.group("rest"), exclude)[:1]:
                        add(code, 4, place, "letter" if letter else "CV text")
            if relocate_re.search(b.text):
                relocation = True

    confident = bool(scores) and max(scores.values()) >= 3
    if not scores:
        # Weak fallback: places in the CV's roles, earlier (more recent) ones count more.
        for doc in docs:
            if doc.doc_type is not DocType.CV:
                continue
            body = [b for b in doc.blocks if not (b.section and _SKIP_SECTIONS.search(b.section))]
            for i, b in enumerate(body):
                for code, place in _places(b.text, exclude):
                    add(code, 1 / (1 + i / 10), place, "CV text")
    if not scores:
        return Location(relocation=relocation)
    code = max(scores, key=lambda c: scores[c])
    name, region, _ = _COUNTRIES[code]
    place, source = first[code]
    return Location(
        region="home" if code == home else region,
        country=code,
        country_name=name,
        place=place,
        source=source,
        confident=confident,
        relocation=relocation,
    )
