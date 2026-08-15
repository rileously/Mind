from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone


@dataclass(frozen=True)
class ConversionResult:
    category: str  # "currency", "unit", "timezone"
    title: str
    icon: str
    input_text: str
    output_text: str


# ---------------------------------------------------------------------------
# 1. CURRENCY CONVERSION
# ---------------------------------------------------------------------------

# Approximate exchange rates (Units per 1 USD)
_RATES_TO_USD: dict[str, float] = {
    "USD": 1.0,
    "MVR": 15.42,
    "EUR": 0.92,
    "GBP": 0.78,
    "INR": 83.50,
    "AED": 3.67,
    "CAD": 1.36,
    "AUD": 1.52,
    "SGD": 1.34,
    "MYR": 4.70,
    "THB": 36.50,
    "CNY": 7.23,
    "JPY": 155.00,
    "SAR": 3.75,
    "KRW": 1380.00,
    "TRY": 32.50,
    "LKR": 302.00,
    "BTC": 0.000015,
    "ETH": 0.00031,
}

_CURRENCY_SYMBOLS: dict[str, str] = {
    "$": "USD",
    "US$": "USD",
    "USD": "USD",
    "DOLLAR": "USD",
    "DOLLARS": "USD",
    "MVR": "MVR",
    "RF": "MVR",
    "MRF": "MVR",
    "ރ": "MVR",
    "ރ.": "MVR",
    "RUFIYAA": "MVR",
    "€": "EUR",
    "EUR": "EUR",
    "EURO": "EUR",
    "EUROS": "EUR",
    "£": "GBP",
    "GBP": "GBP",
    "POUND": "GBP",
    "POUNDS": "GBP",
    "₹": "INR",
    "INR": "INR",
    "RS": "INR",
    "RS.": "INR",
    "RUPEE": "INR",
    "RUPEES": "INR",
    "AED": "AED",
    "DHS": "AED",
    "DIRHAM": "AED",
    "CAD": "CAD",
    "C$": "CAD",
    "AUD": "AUD",
    "A$": "AUD",
    "SGD": "SGD",
    "S$": "SGD",
    "MYR": "MYR",
    "RM": "MYR",
    "RINGGIT": "MYR",
    "THB": "THB",
    "฿": "THB",
    "BAHT": "THB",
    "CNY": "CNY",
    "RMB": "CNY",
    "YUAN": "CNY",
    "¥": "JPY",
    "JPY": "JPY",
    "YEN": "JPY",
    "SAR": "SAR",
    "KRW": "KRW",
    "₩": "KRW",
    "WON": "KRW",
    "TRY": "TRY",
    "₺": "TRY",
    "LIRA": "TRY",
    "LKR": "LKR",
    "BTC": "BTC",
    "₿": "BTC",
    "ETH": "ETH",
    "Ξ": "ETH",
}

_CURRENCY_PREFIX_REGEX = re.compile(
    r"^(?P<sym>\$|US\$|€|£|¥|₹|฿|₩|₺|₿|Ξ|MVR|USD|EUR|GBP|INR|AED|CAD|AUD|SGD|MYR|THB|CNY|JPY|SAR|KRW|TRY|LKR|BTC|ETH|rf|MRF|ރ\.?)\s*(?P<val>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)$",
    re.IGNORECASE,
)

_CURRENCY_SUFFIX_REGEX = re.compile(
    r"^(?P<val>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?P<sym>\$|US\$|€|£|¥|₹|฿|₩|₺|₿|Ξ|MVR|USD|EUR|GBP|INR|AED|CAD|AUD|SGD|MYR|THB|CNY|JPY|SAR|KRW|TRY|LKR|BTC|ETH|rf|MRF|ރ\.?|dollars?|euros?|pounds?|rupees?|dirhams?|yen|ringgit|baht|yuan|rufイヤaa?|rufiyaa)$",
    re.IGNORECASE,
)


def _format_curr_val(amount: float, curr: str) -> str:
    if curr in ("BTC", "ETH"):
        return f"{amount:.4f} {curr}"
    if curr in ("JPY", "KRW"):
        return f"{amount:,.0f} {curr}"
    return f"{amount:,.2f} {curr}"


def convert_currency(text: str) -> ConversionResult | None:
    cleaned = text.strip().strip("\"'`“”‘’`")
    match = _CURRENCY_PREFIX_REGEX.match(cleaned) or _CURRENCY_SUFFIX_REGEX.match(cleaned)
    if not match:
        return None

    raw_sym = match.group("sym").upper().rstrip(".")
    sym_code = _CURRENCY_SYMBOLS.get(raw_sym) or _CURRENCY_SYMBOLS.get(raw_sym.rstrip("S"))
    if not sym_code:
        return None

    try:
        val = float(match.group("val").replace(",", ""))
    except ValueError:
        return None

    if val <= 0:
        return None

    # Convert to USD first
    rate_to_usd = _RATES_TO_USD.get(sym_code)
    if not rate_to_usd:
        return None

    val_in_usd = val / rate_to_usd

    # Format result depending on source currency
    outputs = []
    if sym_code == "USD":
        mvr = val_in_usd * _RATES_TO_USD["MVR"]
        eur = val_in_usd * _RATES_TO_USD["EUR"]
        gbp = val_in_usd * _RATES_TO_USD["GBP"]
        outputs.append(f"≈ {_format_curr_val(mvr, 'MVR')}")
        outputs.append(f"{_format_curr_val(eur, 'EUR')}")
        outputs.append(f"{_format_curr_val(gbp, 'GBP')}")
    elif sym_code == "MVR":
        usd = val_in_usd
        eur = val_in_usd * _RATES_TO_USD["EUR"]
        inr = val_in_usd * _RATES_TO_USD["INR"]
        outputs.append(f"≈ {_format_curr_val(usd, 'USD')}")
        outputs.append(f"{_format_curr_val(eur, 'EUR')}")
        outputs.append(f"{_format_curr_val(inr, 'INR')}")
    else:
        usd = val_in_usd
        mvr = val_in_usd * _RATES_TO_USD["MVR"]
        outputs.append(f"≈ {_format_curr_val(usd, 'USD')}")
        outputs.append(f"({_format_curr_val(mvr, 'MVR')})")

    return ConversionResult(
        category="currency",
        title="Currency Converter",
        icon="💱",
        input_text=cleaned,
        output_text=" · ".join(outputs),
    )


# ---------------------------------------------------------------------------
# 2. UNIT CONVERSIONS (Temperature, Length, Weight, Speed, Volume, Area, Data)
# ---------------------------------------------------------------------------

_TEMP_REGEX = re.compile(
    r"^(?P<val>[-+]?\d+(?:\.\d+)?)\s*(?:°?\s*(?P<unit>[FCKfc])\b|degrees?\s*(?P<name>fahrenheit|celsius|kelvin)\b)$",
    re.IGNORECASE,
)

_HEIGHT_FEET_INCHES_REGEX = re.compile(
    r"^(?P<ft>\d+)\s*(?:'|ft|feet)\s*(?P<in>\d+(?:\.\d+)?)\s*(?:\"|in|inches)?$",
    re.IGNORECASE,
)

_GENERIC_UNIT_REGEX = re.compile(
    r"^(?P<val>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?P<unit>[a-zA-Z°²³/%]+(?:\s+[a-zA-Z]+)?)$"
)


def _convert_temperature(cleaned: str) -> ConversionResult | None:
    match = _TEMP_REGEX.match(cleaned)
    if not match:
        return None

    try:
        val = float(match.group("val"))
    except ValueError:
        return None

    unit = (match.group("unit") or match.group("name") or "")[0].upper()
    outputs = []

    if unit == "F":
        c = (val - 32) * 5 / 9
        k = c + 273.15
        outputs.append(f"{c:.1f}°C")
        outputs.append(f"{k:.1f} K")
    elif unit == "C":
        f = (val * 9 / 5) + 32
        k = val + 273.15
        outputs.append(f"{f:.1f}°F")
        outputs.append(f"{k:.1f} K")
    elif unit == "K":
        c = val - 273.15
        f = (c * 9 / 5) + 32
        outputs.append(f"{c:.1f}°C")
        outputs.append(f"{f:.1f}°F")
    else:
        return None

    return ConversionResult(
        category="unit",
        title="Temperature Conversion",
        icon="🌡️",
        input_text=cleaned,
        output_text=" · ".join(outputs),
    )


def _convert_length(val: float, unit_str: str) -> list[str] | None:
    u = unit_str.lower().rstrip(".")
    if u in ("in", "inch", "inches", '"'):
        cm = val * 2.54
        ft = val / 12
        return [f"{cm:.2f} cm", f"{ft:.2f} ft"]
    if u in ("ft", "feet", "'"):
        m = val * 0.3048
        cm = m * 100
        return [f"{m:.2f} m ({cm:.1f} cm)"]
    if u in ("yd", "yard", "yards"):
        m = val * 0.9144
        ft = val * 3
        return [f"{m:.2f} m", f"{ft:.1f} ft"]
    if u in ("mi", "mile", "miles"):
        km = val * 1.60934
        nmi = val * 0.868976
        return [f"{km:.2f} km", f"{nmi:.2f} nmi"]
    if u in ("nmi", "nautical mile", "nautical miles"):
        km = val * 1.852
        mi = val * 1.15078
        return [f"{km:.2f} km", f"{mi:.2f} miles"]
    if u in ("mm", "millimeter", "millimeters"):
        inch = val / 25.4
        return [f"{inch:.3f} in"]
    if u in ("cm", "centimeter", "centimeters"):
        inch = val / 2.54
        ft = inch / 12
        return [f"{inch:.2f} in", f"{ft:.2f} ft"]
    if u in ("m", "meter", "meters"):
        ft = val * 3.28084
        yd = val * 1.09361
        return [f"{ft:.2f} ft", f"{yd:.2f} yd"]
    if u in ("km", "kilometer", "kilometers"):
        mi = val * 0.621371
        nmi = val * 0.539957
        return [f"{mi:.2f} miles", f"{nmi:.2f} nmi"]
    return None


def _convert_weight(val: float, unit_str: str) -> list[str] | None:
    u = unit_str.lower().rstrip(".")
    if u in ("lb", "lbs", "pound", "pounds"):
        kg = val * 0.453592
        g = kg * 1000
        return [f"{kg:.2f} kg", f"{g:,.0f} g"]
    if u in ("oz", "ounce", "ounces"):
        g = val * 28.3495
        lb = val / 16
        return [f"{g:.1f} g", f"{lb:.2f} lbs"]
    if u in ("ton", "tons", "short ton"):
        kg = val * 907.185
        t = val * 0.907185
        return [f"{kg:,.0f} kg", f"{t:.2f} tonnes"]
    if u in ("g", "gram", "grams"):
        oz = val / 28.3495
        lb = val / 453.592
        return [f"{oz:.2f} oz", f"{lb:.3f} lbs"]
    if u in ("kg", "kilo", "kilos", "kilogram", "kilograms"):
        lb = val * 2.20462
        oz = val * 35.274
        return [f"{lb:.2f} lbs", f"{oz:.1f} oz"]
    if u in ("t", "tonne", "tonnes", "metric ton", "metric tons"):
        lb = val * 2204.62
        st = val * 1.10231
        return [f"{lb:,.0f} lbs", f"{st:.2f} short tons"]
    return None


def _convert_speed(val: float, unit_str: str) -> list[str] | None:
    u = unit_str.lower().replace(" ", "")
    if u in ("mph", "mi/h", "miles/hour", "milesperhour"):
        kmh = val * 1.60934
        ms = val * 0.44704
        return [f"{kmh:.2f} km/h", f"{ms:.2f} m/s"]
    if u in ("km/h", "kmh", "kph", "kmperhour"):
        mph = val * 0.621371
        ms = val / 3.6
        return [f"{mph:.2f} mph", f"{ms:.2f} m/s"]
    if u in ("m/s", "mps", "meter/s", "meterpersecond"):
        kmh = val * 3.6
        mph = val * 2.23694
        return [f"{kmh:.2f} km/h", f"{mph:.2f} mph"]
    if u in ("knot", "knots", "kt", "kts"):
        kmh = val * 1.852
        mph = val * 1.15078
        return [f"{kmh:.2f} km/h", f"{mph:.2f} mph"]
    return None


def _convert_volume(val: float, unit_str: str) -> list[str] | None:
    u = unit_str.lower().rstrip(".")
    if u in ("gal", "gallon", "gallons"):
        l = val * 3.78541
        return [f"{l:.2f} L"]
    if u in ("qt", "quart", "quarts"):
        l = val * 0.946353
        return [f"{l:.2f} L"]
    if u in ("pt", "pint", "pints"):
        ml = val * 473.176
        return [f"{ml:.1f} ml"]
    if u in ("cup", "cups"):
        ml = val * 236.588
        floz = val * 8
        return [f"{ml:.1f} ml", f"{floz:.1f} fl oz"]
    if u in ("fl oz", "floz", "fluid ounce", "fluid ounces"):
        ml = val * 29.5735
        cups = val / 8
        return [f"{ml:.1f} ml", f"{cups:.2f} cups"]
    if u in ("ml", "milliliter", "milliliters"):
        floz = val / 29.5735
        cups = val / 236.588
        return [f"{floz:.2f} fl oz", f"{cups:.2f} cups"]
    if u in ("l", "liter", "liters", "litre", "litres"):
        gal = val / 3.78541
        floz = val * 33.814
        return [f"{gal:.2f} gal", f"{floz:.1f} fl oz"]
    return None


def _convert_area(val: float, unit_str: str) -> list[str] | None:
    u = unit_str.lower().replace(" ", "")
    if u in ("sqft", "squarefeet", "squarefoot", "ft2", "ft²"):
        sqm = val * 0.092903
        return [f"{sqm:.2f} m²"]
    if u in ("sqm", "squaremeter", "squaremeters", "m2", "m²"):
        sqft = val * 10.7639
        return [f"{sqft:.2f} sq ft"]
    if u in ("acre", "acres"):
        ha = val * 0.404686
        sqm = val * 4046.86
        return [f"{ha:.2f} ha", f"{sqm:,.0f} m²"]
    if u in ("ha", "hectare", "hectares"):
        acre = val * 2.47105
        return [f"{acre:.2f} acres"]
    return None


def _convert_data(val: float, unit_str: str) -> list[str] | None:
    u = unit_str.upper()
    if u in ("KB", "KILOBYTE", "KILOBYTES"):
        mb = val / 1024
        return [f"{mb:.2f} MB"]
    if u in ("MB", "MEGABYTE", "MEGABYTES"):
        gb = val / 1024
        kb = val * 1024
        return [f"{gb:.2f} GB", f"{kb:,.0f} KB"]
    if u in ("GB", "GIGABYTE", "GIGABYTES"):
        tb = val / 1024
        mb = val * 1024
        return [f"{tb:.2f} TB", f"{mb:,.0f} MB"]
    if u in ("TB", "TERABYTE", "TERABYTES"):
        gb = val * 1024
        return [f"{gb:,.0f} GB"]
    if u in ("MBPS", "MB/S"):
        mbs = val / 8
        return [f"{mbs:.2f} MB/s"]
    if u in ("GBPS", "GB/S"):
        gbs = val / 8
        return [f"{gbs:.2f} GB/s"]
    return None


def convert_unit(text: str) -> ConversionResult | None:
    cleaned = text.strip().strip("\"'`“”‘’`")

    # 1. Temperature check
    temp_res = _convert_temperature(cleaned)
    if temp_res:
        return temp_res

    # 2. Height in Feet & Inches (e.g. 5'11" or 6ft 2in)
    match_height = _HEIGHT_FEET_INCHES_REGEX.match(cleaned)
    if match_height:
        ft = float(match_height.group("ft"))
        inches = float(match_height.group("in"))
        total_inches = (ft * 12) + inches
        cm = total_inches * 2.54
        m = cm / 100
        return ConversionResult(
            category="unit",
            title="Height & Length Conversion",
            icon="📏",
            input_text=cleaned,
            output_text=f"{cm:.1f} cm · {m:.2f} m",
        )

    # 3. Generic unit matching
    match = _GENERIC_UNIT_REGEX.match(cleaned)
    if not match:
        return None

    try:
        val = float(match.group("val").replace(",", ""))
    except ValueError:
        return None

    unit_str = match.group("unit")

    # Try sub-handlers
    length_res = _convert_length(val, unit_str)
    if length_res:
        return ConversionResult(
            category="unit",
            title="Length Conversion",
            icon="📏",
            input_text=cleaned,
            output_text=" · ".join(length_res),
        )

    weight_res = _convert_weight(val, unit_str)
    if weight_res:
        return ConversionResult(
            category="unit",
            title="Weight & Mass Conversion",
            icon="⚖️",
            input_text=cleaned,
            output_text=" · ".join(weight_res),
        )

    speed_res = _convert_speed(val, unit_str)
    if speed_res:
        return ConversionResult(
            category="unit",
            title="Speed Conversion",
            icon="🏎️",
            input_text=cleaned,
            output_text=" · ".join(speed_res),
        )

    volume_res = _convert_volume(val, unit_str)
    if volume_res:
        return ConversionResult(
            category="unit",
            title="Volume Conversion",
            icon="🧪",
            input_text=cleaned,
            output_text=" · ".join(volume_res),
        )

    area_res = _convert_area(val, unit_str)
    if area_res:
        return ConversionResult(
            category="unit",
            title="Area Conversion",
            icon="📐",
            input_text=cleaned,
            output_text=" · ".join(area_res),
        )

    data_res = _convert_data(val, unit_str)
    if data_res:
        return ConversionResult(
            category="unit",
            title="Digital Storage Conversion",
            icon="💾",
            input_text=cleaned,
            output_text=" · ".join(data_res),
        )

    return None


# ---------------------------------------------------------------------------
# 3. TIMEZONE CONVERSION
# ---------------------------------------------------------------------------

_TIMEZONE_OFFSETS: dict[str, float] = {
    "UTC": 0.0,
    "GMT": 0.0,
    "Z": 0.0,
    "EST": -5.0,
    "EDT": -4.0,
    "CST": -6.0,
    "CDT": -5.0,
    "MST": -7.0,
    "MDT": -6.0,
    "PST": -8.0,
    "PDT": -7.0,
    "BST": 1.0,
    "CET": 1.0,
    "CEST": 2.0,
    "EET": 2.0,
    "EEST": 3.0,
    "MSK": 3.0,
    "GST": 4.0,
    "PKT": 5.0,
    "MVT": 5.0,    # Maldives Time
    "IST": 5.5,    # India Standard Time
    "NPT": 5.75,   # Nepal Time
    "BST6": 6.0,   # Bangladesh
    "ICT": 7.0,    # Indochina Time
    "SGT": 8.0,    # Singapore
    "CST8": 8.0,   # China Standard Time
    "HKT": 8.0,    # Hong Kong
    "JST": 9.0,    # Japan
    "KST": 9.0,    # Korea
    "ACST": 9.5,
    "AEST": 10.0,  # Australian Eastern
    "AEDT": 11.0,
    "NZST": 12.0,  # New Zealand
    "NZDT": 13.0,
}

_TIMEZONE_REGEX = re.compile(
    r"^(?:at\s+)?(?P<hr>\d{1,2})(?::(?P<min>\d{2}))?\s*(?P<ampm>am|pm)?\s*(?P<tz>EST|EDT|PST|PDT|CST|CDT|MST|MDT|UTC|GMT|BST|CET|CEST|EET|EEST|MSK|GST|PKT|MVT|IST|NPT|ICT|SGT|HKT|JST|KST|AEST|AEDT|NZST|NZDT)\b$",
    re.IGNORECASE,
)


def convert_timezone(text: str) -> ConversionResult | None:
    cleaned = text.strip().strip("\"'`“”‘’`")
    match = _TIMEZONE_REGEX.match(cleaned)
    if not match:
        return None

    hr = int(match.group("hr"))
    minute = int(match.group("min") or 0)
    ampm = (match.group("ampm") or "").lower()
    tz_str = match.group("tz").upper()

    if hr > 24 or minute >= 60:
        return None

    if ampm == "pm" and hr < 12:
        hr += 12
    elif ampm == "am" and hr == 12:
        hr = 0

    if hr > 23:
        return None

    source_offset = _TIMEZONE_OFFSETS.get(tz_str)
    if source_offset is None:
        return None

    # Base reference date
    today = datetime.now(timezone.utc).date()
    source_tz = timezone(timedelta(hours=source_offset))
    src_dt = datetime(today.year, today.month, today.day, hr, minute, tzinfo=source_tz)

    # Convert to MVT (UTC+5)
    mvt_tz = timezone(timedelta(hours=5))
    mvt_dt = src_dt.astimezone(mvt_tz)

    # Convert to UTC
    utc_dt = src_dt.astimezone(timezone.utc)

    # Day offset label relative to source
    day_diff_mvt = (mvt_dt.date() - src_dt.date()).days
    day_suffix = ""
    if day_diff_mvt > 0:
        day_suffix = " (next day)"
    elif day_diff_mvt < 0:
        day_suffix = " (prev day)"

    mvt_str = mvt_dt.strftime("%I:%M %p").lstrip("0") + f"{day_suffix} MVT"
    utc_str = utc_dt.strftime("%I:%M %p").lstrip("0") + " UTC"

    outputs = [mvt_str, utc_str]

    return ConversionResult(
        category="timezone",
        title="Timezone Converter",
        icon="⏱️",
        input_text=cleaned,
        output_text=" · ".join(outputs),
    )


# ---------------------------------------------------------------------------
# MAIN UNIFIED DETECTION & CONVERSION FUNCTION
# ---------------------------------------------------------------------------

def detect_and_convert(text: str) -> ConversionResult | None:
    if not text:
        return None
    cleaned = text.strip().strip("\"'`“”‘’`")
    if len(cleaned) < 2 or len(cleaned) > 60:
        return None

    # 1. Currency
    curr_res = convert_currency(cleaned)
    if curr_res:
        return curr_res

    # 2. Timezone
    tz_res = convert_timezone(cleaned)
    if tz_res:
        return tz_res

    # 3. Units
    unit_res = convert_unit(cleaned)
    if unit_res:
        return unit_res

    return None
