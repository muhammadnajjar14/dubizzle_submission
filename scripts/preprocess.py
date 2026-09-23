from __future__ import annotations

import argparse
import html
import json
import os
import re
import time
from pathlib import Path

import pandas as pd
from litellm import completion

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

SOURCE_XLSX = DATA_DIR / "Copy_of_sample_cars_dataset.xlsx"
OUTPUT_CSV = DATA_DIR / "cars.csv"

DEFAULT_SHEET = "cleaned dataset"

ENRICHED_FIELDS = [
    "price_aed",
    "mileage_km",
    "body_type",
    "transmission",
    "fuel_type",
]

OUTPUT_COLUMNS = [
    "listing_id",
    "year",
    "make",
    "model",
    "trim",
    "title",
    "description",
    "photo_url",
    "price_aed",
    "mileage_km",
    "body_type",
    "transmission",
    "fuel_type",
]


ARABIC_DIGIT_TRANSLATION = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)

# Handles examples such as:
#
# 115,750
# 115,750.00
# 89 900
# 25.000
# 55.5k
#
NUMBER_CAPTURE = (
    r"("
    r"\d{1,3}(?:[ ,.]\d{3})+(?:[.,]\d{1,2})?"
    r"|"
    r"\d+(?:[.,]\d{1,2})?"
    r")"
    r"(?:\s*([kK]))?"
)


def clean_text(value) -> str:
    """Normalize listing text while preserving useful line boundaries."""
    if pd.isna(value):
        return ""

    text = html.unescape(str(value))
    text = text.translate(ARABIC_DIGIT_TRANSLATION)

    # Preserve listing line structure before removing HTML.
    text = re.sub(
        r"<br\s*/?>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text,
    )

    text = re.sub(
        r"[ \t]+",
        " ",
        text,
    )

    text = re.sub(
        r"\n\s*\n+",
        "\n",
        text,
    )

    return text.strip()


def parse_numeric_value(
    value: str,
    suffix: str | None = None,
) -> int | None:
    """
    Convert different listing number formats into integers.

    Examples:
        115,750.00 -> 115750
        89 900     -> 89900
        25.000     -> 25000
        55.5k      -> 55500
    """
    if value is None:
        return None

    token = (
        str(value)
        .translate(ARABIC_DIGIT_TRANSLATION)
        .strip()
        .replace(" ", "")
    )

    multiplier = 1

    if suffix and suffix.lower() == "k":
        multiplier = 1000

    if token.lower().endswith("k"):
        multiplier = 1000
        token = token[:-1]

    # Both separators present:
    # 115,750.00
    # 115.750,00
    if "," in token and "." in token:
        last_comma = token.rfind(",")
        last_dot = token.rfind(".")

        if (
            last_dot > last_comma
            and len(token) - last_dot - 1 in (1, 2)
        ):
            token = token.replace(",", "")

        elif (
            last_comma > last_dot
            and len(token) - last_comma - 1 in (1, 2)
        ):
            token = (
                token
                .replace(".", "")
                .replace(",", ".")
            )

        else:
            token = (
                token
                .replace(",", "")
                .replace(".", "")
            )

    # Dot-only formatting.
    elif "." in token:
        if token.count(".") > 1:
            token = token.replace(".", "")

        else:
            whole, decimal = token.split(".", 1)

            # UAE listing data frequently uses periods as thousands
            # separators, e.g. 25.000 AED.
            if len(decimal) == 3:
                token = whole + decimal

    # Comma-only formatting.
    elif "," in token:
        if token.count(",") > 1:
            token = token.replace(",", "")

        else:
            whole, decimal = token.split(",", 1)

            if len(decimal) == 3:
                token = whole + decimal

            elif len(decimal) in (1, 2):
                token = whole + "." + decimal

            else:
                token = whole + decimal

    try:
        return int(float(token) * multiplier)

    except ValueError:
        return None


def _number_from_match(match) -> int | None:
    value = match.group(1)

    suffix = (
        match.group(2)
        if match.lastindex
        and match.lastindex >= 2
        else None
    )

    return parse_numeric_value(
        value,
        suffix,
    )


# Price extraction

def _valid_price(value: int | None) -> bool:
    return (
        value is not None
        and 1000 <= value <= 20_000_000
    )


def _price_context_penalty(
    before: str,
    after: str,
) -> int:
    """
    Penalize monetary values that clearly refer to financing,
    salary requirements, fees, etc.
    """
    before = before.lower()
    after = after.lower()

    penalty = 0

    before_markers = [
        "salary",
        "processing fee",
        "bank processing",
        "registration fee",
        "evaluation fee",
        "insurance",
        "deposit",
        "down payment",
        "monthly installment",
        "monthly instalment",
        "installment",
        "instalment",
        "emi",
        "القسط الشهري",
        "راتب",
    ]

    if any(
        marker in before
        for marker in before_markers
    ):
        penalty -= 200

    # Only penalize monthly wording immediately after this amount.
    # This prevents:
    #
    # 1,349,999 AED or 25,683 AED per Month
    #
    # from incorrectly rejecting the first number.
    if re.match(
        r"""
        ^\s*
        (?:
            /\s*mo\b
            |
            /\s*month\b
            |
            /\s*monthly\b
            |
            per\s+mo(?:nth)?\b
            |
            p\.?\s*/?\s*m\.?
            |
            monthly\b
            |
            installment\b
            |
            instalment\b
            |
            emi\b
        )
        """,
        after,
        flags=re.IGNORECASE | re.VERBOSE,
    ):
        penalty -= 200

    if (
        "شهريا" in after[:25]
        or "شهري" in after[:25]
    ):
        penalty -= 200

    return penalty


def extract_price(text: str) -> int | None:
    """
    Extract the most likely total asking price.

    Rather than choosing the first AED value, every monetary candidate
    is scored using its local context.
    """
    candidates = []

    lines = text.splitlines()

    patterns = [
        # Currency before number.
        (
            rf"\b(?:AED|DHS|DIRHAMS?)\s*{NUMBER_CAPTURE}",
            10,
        ),

        # Currency after number.
        (
            rf"\b{NUMBER_CAPTURE}\s*(?:AED|DHS|DIRHAMS?)\b",
            10,
        ),

        # Arabic dirham.
        (
            rf"{NUMBER_CAPTURE}\s*درهم\b",
            10,
        ),

        # Explicit English price label even if currency is omitted.
        (
            rf"""
            \b
            (?:
                selling\s+price
                |
                asking\s+price
                |
                cash\s+price
                |
                price\s+reduced
                |
                price
            )
            \s*[:\-]?\s*
            {NUMBER_CAPTURE}
            """,
            20,
        ),

        # Explicit Arabic price label.
        (
            rf"""
            السعر
            \s*[:\-]?\s*
            {NUMBER_CAPTURE}
            """,
            20,
        ),
    ]

    for line_index, line in enumerate(lines):
        lower_line = line.lower()

        for pattern, base_score in patterns:
            for match in re.finditer(
                pattern,
                line,
                flags=re.IGNORECASE | re.VERBOSE,
            ):
                value = _number_from_match(
                    match
                )

                if not _valid_price(value):
                    continue

                before = line[
                    max(0, match.start() - 40):
                    match.start()
                ]

                after = line[
                    match.end():
                    min(len(line), match.end() + 30)
                ]

                score = base_score

                # Strong explicit total-price indicators.
                if re.search(
                    r"""
                    selling\s+price
                    |
                    asking\s+price
                    |
                    cash\s+price
                    |
                    price\s+reduced
                    """,
                    lower_line,
                    flags=re.VERBOSE,
                ):
                    score += 100

                elif re.search(
                    r"\bprice\b",
                    lower_line,
                ):
                    score += 60

                if "السعر" in line:
                    score += 100

                # "AED 165,000 cash" / "AED 119,750 in cash"
                if re.match(
                    r"^\s*(?:in\s+cash|cash)\b",
                    after,
                    flags=re.IGNORECASE,
                ):
                    score += 80

                score += _price_context_penalty(
                    before,
                    after,
                )

                candidates.append({
                    "value": value,
                    "score": score,
                    "line_index": line_index,
                })

    # Only trust positively-scored candidates.
    valid = [
        candidate
        for candidate in candidates
        if candidate["score"] > 0
    ]

    if not valid:
        return None

    best = max(
        valid,
        key=lambda candidate: (
            candidate["score"],
            candidate["value"],
        ),
    )

    return best["value"]


# Mileage extraction

def _valid_mileage(value: int | None) -> bool:
    return (
        value is not None
        and 0 <= value <= 2_000_000
    )

    
def extract_mileage(text: str) -> int | None:
    """
    Extract odometer mileage using contextual scoring.

    Service intervals, warranty limits, EV range, and speed figures
    are deliberately rejected.
    """
    candidates = []

    lines = text.splitlines()

    pattern = (
        rf"{NUMBER_CAPTURE}"
        r"\s*"
        r"(km/h|kph|kms?|kilometers?|kilometres?|كم)"
        r"\b"
    )

    bad_context_markers = [
        "last service",
        "next service",
        "service plan",
        "service contract",
        "warranty",
        "driving range",
        "range on",
        "single charge",
        "top speed",
        "max speed",
        "0-100",
        "0 - 100",
        "acceleration",
        "wltp",
        "nedc",
        "battery capacity",
        "until",
    ]

    for line_index, line in enumerate(lines):
        lower_line = line.lower()

        for match in re.finditer(
            pattern,
            line,
            flags=re.IGNORECASE | re.VERBOSE,
        ):
            value = _number_from_match(
                match
            )

            if not _valid_mileage(value):
                continue

            unit = match.group(3).lower()

            score = 0

            # Speed, not mileage.
            if unit in {
                "km/h",
                "kph",
            }:
                score -= 300

            local_before = line[
                max(0, match.start() - 45):
                match.start()
            ].lower()

            local_after = line[
                match.end():
                min(len(line), match.end() + 30)
            ].lower()

            local_context = (
                local_before
                + " "
                + local_after
            )

            if any(
                marker in local_context
                for marker in bad_context_markers
            ):
                score -= 200        

            # High-confidence odometer labels.
            if re.search(
                r"\bmileage\b|\bodometer\b",
                local_before,
            ):
                score += 120

            if "عداد المسافات" in line:
                score += 120

            # e.g. "done 42,000 KM"
            if re.search(
                r"\bdone\b",
                lower_line,
            ):
                score += 80

            if re.search(
                r"\bonly\b",
                lower_line,
            ):
                score += 25

            # A standalone line such as:
            # 77820 kms
            # 83.000KM
            # 113000km
            stripped = re.sub(
                r"^[\s•\-–—:]+|[\s•\-–—:]+$",
                "",
                line,
            )

            standalone_pattern = (
                rf"{NUMBER_CAPTURE}"
                r"\s*"
                r"(?:kms?|kilometers?|kilometres?|كم)"
                r"(?:\s*only)?"
            )

            if re.fullmatch(
                standalone_pattern,
                stripped,
                flags=re.IGNORECASE | re.VERBOSE,
            ):
                score += 60

            # Explicit 0KM listings are valid for new cars.
            if (
                value == 0
                and re.search(
                    r"\b0\s*km\b",
                    lower_line,
                )
            ):
                score += 50

            candidates.append({
                "value": value,
                "score": score,
                "line_index": line_index,
            })

    valid = [
        candidate
        for candidate in candidates
        if candidate["score"] > 0
    ]

    if not valid:
        return None

    # Prefer stronger context first. Earlier occurrence breaks ties.
    best = max(
        valid,
        key=lambda candidate: (
            candidate["score"],
            -candidate["line_index"],
        ),
    )

    return best["value"]


# Body type extraction

BODY_TYPE_PATTERNS = {
    "suv": [
        r"\bsuv\b",
        r"\bsport utility\b",
    ],
    "sedan": [
        r"\bsedan\b",
        r"\bsaloon\b",
    ],
    "hatchback": [
        r"\bhatchback\b",
    ],
    "coupe": [
        r"\bcoupe\b",
        r"\bcoupé\b",
    ],
    "convertible": [
        r"\bconvertible\b",
        r"\bcabriolet\b",
    ],
    "pickup": [
        r"\bpickup\b",
        r"\bpick-up\b",
    ],
    "van": [
        r"\bminivan\b",
        r"\bvan\b",
    ],
    "wagon": [
        r"\bwagon\b",
        r"\bestate\b",
    ],
}


def extract_body_type(text: str) -> str | None:
    """
    Extract body type conservatively.

    If multiple conflicting body types are explicitly mentioned,
    deterministic extraction leaves the field unresolved for the LLM.
    """
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    # Highest confidence: explicit Body Type label.
    for line in lines:
        if re.search(
            r"\bbody\s*type\b",
            line,
            flags=re.IGNORECASE,
        ):
            for body_type, patterns in BODY_TYPE_PATTERNS.items():
                if any(
                    re.search(
                        pattern,
                        line,
                        flags=re.IGNORECASE,
                    )
                    for pattern in patterns
                ):
                    return body_type

    # Vehicle identification is usually near the top of the listing.
    early_text = "\n".join(
        lines[:10]
    )

    found = set()

    for body_type, patterns in BODY_TYPE_PATTERNS.items():
        if any(
            re.search(
                pattern,
                early_text,
                flags=re.IGNORECASE,
            )
            for pattern in patterns
        ):
            found.add(body_type)

    if len(found) == 1:
        return next(iter(found))

    # Conflicting descriptions such as "SUV / coupe-like"
    # are deliberately left unresolved.
    return None


# Transmission extraction

def extract_transmission(text: str) -> str | None:
    """
    Extract transmission only from meaningful drivetrain context.

    This avoids treating phrases like "Automatic Power Mirror" or
    "Automatic Climate Control" as transmission evidence.
    """

    automatic_patterns = [
        r"\bautomatic\s+transmission\b",
        r"\btransmission\s*[:\-]?\s*(?:\d+\s*[- ]?speed\s*)?automatic\b",
        r"\b\d+\s*[- ]?speed\s+(?:tiptronic\s+)?automatic\b",
        r"\bautomatic\s+gearbox\b",
        r"\bautomatic\s*\(AT\)\b",
        r"\btiptronic\s+gears?\b",
        r"\btiptronic\s+transmission\b",
        r"\bdual[- ]clutch\s+transmission\b",
        r"\bDCT\b",
        r"\bCVT\b",
    ]

    manual_patterns = [
        r"\bmanual\s+transmission\b",
        r"\btransmission\s*[:\-]?\s*(?:\d+\s*[- ]?speed\s*)?manual\b",
        r"\b\d+\s*[- ]?speed\s+manual\b",
        r"\bmanual\s+gearbox\b",
        r"\bmanual\s*\(MT\)\b",
    ]

    if any(
        re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )
        for pattern in automatic_patterns
    ):
        return "automatic"

    if any(
        re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )
        for pattern in manual_patterns
    ):
        return "manual"

    # Some listings only say "AUTOMATIC" in the title or on its own line.
    # Accept that only when it is not attached to an accessory.
    accessory_words = {
        "mirror",
        "mirrors",
        "climate",
        "tailgate",
        "roof",
        "headlamp",
        "headlamps",
        "light",
        "lights",
        "wiper",
        "wipers",
        "brake",
        "braking",
        "hold",
        "seat",
        "seats",
        "door",
        "doors",
    }

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    for line in lines[:5]:
        lower_line = line.lower()

        if re.search(
            r"\bautomatic\b",
            lower_line,
        ):
            if not any(
                word in lower_line
                for word in accessory_words
            ):
                return "automatic"

        if re.fullmatch(
            r"[\s•\-]*manual[\s•\-]*",
            lower_line,
        ):
            return "manual"

    return None


# Fuel type extraction

def extract_fuel_type(text: str) -> str | None:
    """
    Extract powertrain/fuel type.

    Bare 'electric' is not enough because descriptions frequently
    mention electric seats, tailgates, mirrors, etc.
    """
    lower_text = text.lower()

    # Hybrid before electric/petrol because hybrid listings may
    # legitimately mention both.
    if re.search(
        r"""
        \bplug[- ]?in\s+hybrid\b
        |
        \bphev\b
        |
        \bhybrid\b
        """,
        lower_text,
        flags=re.VERBOSE,
    ):
        return "hybrid"

    if re.search(
        r"""
        \ball[- ]electric\b
        |
        \belectric\s+vehicle\b
        |
        \belectric\s+motor\b
        |
        \belectric\s+powertrain\b
        |
        \bbattery[- ]electric\b
        |
        \bev\b
        """,
        lower_text,
        flags=re.VERBOSE,
    ):
        return "electric"

    if re.search(
        r"\bdiesel\b",
        lower_text,
    ):
        return "diesel"

    if re.search(
        r"\bpetrol\b|\bgasoline\b",
        lower_text,
    ):
        return "petrol"

    return None


# Deterministic extraction

def deterministic_extract(
    text: str,
) -> dict:
    return {
        "price_aed": extract_price(text),
        "mileage_km": extract_mileage(text),
        "body_type": extract_body_type(text),
        "transmission": extract_transmission(text),
        "fuel_type": extract_fuel_type(text),
    }


# Compact evidence for LLM fallback

EVIDENCE_TERMS = [
    "price",
    "aed",
    "dhs",
    "cash",
    "monthly",
    "mileage",
    "odometer",
    " km",
    "kms",
    "transmission",
    "gearbox",
    "tiptronic",
    "automatic",
    "manual",
    "petrol",
    "gasoline",
    "diesel",
    "hybrid",
    "electric",
    " ev ",
    "suv",
    "sedan",
    "coupe",
    "convertible",
    "hatchback",
    "pickup",
    "van",
    "wagon",
    "السعر",
    "درهم",
    "عداد المسافات",
    "القسط",
]


def build_llm_evidence(
    title: str,
    description: str,
    max_chars: int = 4000,
) -> str:
    """
    Give the fallback model relevant listing evidence rather than
    blindly truncating the first N characters of a long dealer advert.
    """
    lines = [
        line.strip()
        for line in description.splitlines()
        if line.strip()
    ]

    selected = []

    if title:
        selected.append(
            f"TITLE: {title}"
        )

    # Keep the opening context.
    selected.extend(
        lines[:8]
    )

    # Add any later lines likely to contain useful structured facts.
    for line in lines:
        lower_line = f" {line.lower()} "

        if any(
            term in lower_line
            for term in EVIDENCE_TERMS
        ):
            selected.append(line)

    # Preserve order while removing duplicates.
    unique = []
    seen = set()

    for line in selected:
        normalized = line.strip()

        if (
            normalized
            and normalized not in seen
        ):
            seen.add(normalized)
            unique.append(normalized)

    evidence = "\n".join(
        unique
    )

    return evidence[:max_chars]


# LLM extraction schema

EXTRACT_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_car_attributes",
        "description": (
            "Extract structured facts from a used-car listing. "
            "Only provide values supported by the supplied evidence. "
            "Return null when a field cannot be determined."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "price_aed": {
                    "type": ["integer", "null"],
                    "description": (
                        "Total vehicle asking/cash price in AED. "
                        "Never use monthly payments, installments, "
                        "down payments, salary requirements, fees, "
                        "insurance costs, or registration costs."
                    ),
                },
                "mileage_km": {
                    "type": ["integer", "null"],
                    "description": (
                        "Current odometer mileage only. "
                        "Do not use warranty mileage, service mileage, "
                        "EV driving range, top speed, or km/h figures."
                    ),
                },
                "body_type": {
                    "type": ["string", "null"],
                    "enum": [
                        "suv",
                        "sedan",
                        "hatchback",
                        "coupe",
                        "convertible",
                        "pickup",
                        "van",
                        "wagon",
                        "other",
                        None,
                    ],
                },
                "transmission": {
                    "type": ["string", "null"],
                    "enum": [
                        "automatic",
                        "manual",
                        None,
                    ],
                },
                "fuel_type": {
                    "type": ["string", "null"],
                    "enum": [
                        "petrol",
                        "diesel",
                        "hybrid",
                        "electric",
                        None,
                    ],
                },
            },
            "required": ENRICHED_FIELDS,
        },
    },
}


BODY_TYPES = {
    "suv",
    "sedan",
    "hatchback",
    "coupe",
    "convertible",
    "pickup",
    "van",
    "wagon",
    "other",
}

TRANSMISSIONS = {
    "automatic",
    "manual",
}

FUEL_TYPES = {
    "petrol",
    "diesel",
    "hybrid",
    "electric",
}


def normalize_enum(
    value,
    allowed: set[str],
) -> str | None:
    if value is None:
        return None

    normalized = (
        str(value)
        .strip()
        .lower()
    )

    return (
        normalized
        if normalized in allowed
        else None
    )


def normalize_llm_result(
    attrs: dict,
) -> dict:
    result = {
        "price_aed": None,
        "mileage_km": None,
        "body_type": None,
        "transmission": None,
        "fuel_type": None,
    }

    price = attrs.get(
        "price_aed"
    )

    if isinstance(
        price,
        (int, float),
    ):
        price = int(price)

        if _valid_price(price):
            result["price_aed"] = price

    mileage = attrs.get(
        "mileage_km"
    )

    if isinstance(
        mileage,
        (int, float),
    ):
        mileage = int(mileage)

        if _valid_mileage(mileage):
            result["mileage_km"] = mileage

    result["body_type"] = normalize_enum(
        attrs.get("body_type"),
        BODY_TYPES,
    )

    result["transmission"] = normalize_enum(
        attrs.get("transmission"),
        TRANSMISSIONS,
    )

    result["fuel_type"] = normalize_enum(
        attrs.get("fuel_type"),
        FUEL_TYPES,
    )

    return result


# LLM fallback

def extract_with_llm(
    title: str,
    description: str,
    missing_fields: list[str],
) -> tuple[dict, bool]:
    """Use the LLM only for fields deterministic parsing could not resolve."""

    evidence = build_llm_evidence(
        title,
        description,
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You extract structured facts from used-car listings. "
                "Use only facts grounded in the supplied listing evidence. "
                "Do not guess numeric values. "
                "Return null when the evidence is insufficient."
            ),
        },
        {
            "role": "user",
            "content": (
                "Fill only these missing fields:\n"
                f"{', '.join(missing_fields)}\n\n"
                "Important rules:\n"
                "- price_aed is the total asking/cash price only\n"
                "- ignore monthly installments and down payments\n"
                "- ignore salary requirements, insurance and other fees\n"
                "- mileage_km is current odometer mileage only\n"
                "- ignore service mileage and warranty mileage\n"
                "- ignore EV driving range\n"
                "- ignore top speed and all km/h figures\n"
                "- do not interpret electric accessories as electric fuel type\n"
                "- return null when uncertain\n\n"
                f"LISTING EVIDENCE:\n{evidence}"
            ),
        },
    ]

    primary_model = os.getenv(
        "ENRICHMENT_MODEL",
        os.getenv(
            "DUBIZZLE_LLM_MODEL",
            "gemini/gemini-3.5-flash-lite",
        ),
    )

    fallback_model = os.getenv(
        "ENRICHMENT_FALLBACK_MODEL",
        os.getenv(
            "DUBIZZLE_LLM_FALLBACK",
            "gemini/gemini-3.1-flash-lite",
        ),
    )

    models = [
        primary_model
    ]

    if (
        fallback_model
        and fallback_model != primary_model
    ):
        models.append(
            fallback_model
        )

    for model in models:
        try:
            response = completion(
                model=model,
                messages=messages,
                tools=[EXTRACT_TOOL],
                tool_choice="auto",
                num_retries=3,
            )

            message = (
                response
                .choices[0]
                .message
            )

            if not message.tool_calls:
                print(
                    f"  Model {model} returned no extraction tool call."
                )
                continue

            arguments = (
                message
                .tool_calls[0]
                .function
                .arguments
            )

            if isinstance(
                arguments,
                str,
            ):
                attrs = json.loads(
                    arguments
                )

            else:
                attrs = arguments

            return (
                normalize_llm_result(
                    attrs
                ),
                True,
            )

        except Exception as exc:
            print(
                f"  Enrichment failed with {model}: "
                f"{type(exc).__name__}: {exc}"
            )

    return {}, False


# Hybrid enrichment

def enrich_listing(
    title: str,
    description: str,
    use_llm: bool = True,
) -> tuple[dict, str]:
    """
    Deterministic extraction always runs first.

    The LLM can only fill missing fields and never overwrites
    deterministic values.
    """
    searchable_text = (
        f"{title}\n{description}"
    )

    attrs = deterministic_extract(
        searchable_text
    )

    missing_fields = [
        field
        for field in ENRICHED_FIELDS
        if attrs.get(field) is None
    ]

    if not missing_fields:
        return (
            attrs,
            "not_needed",
        )

    if not use_llm:
        return (
            attrs,
            "disabled",
        )

    llm_attrs, success = extract_with_llm(
        title,
        description,
        missing_fields,
    )

    if success:
        for field in missing_fields:
            value = llm_attrs.get(
                field
            )

            if value is not None:
                attrs[field] = value

        return (
            attrs,
            "ok",
        )

    return (
        attrs,
        "unavailable",
    )


# Existing output / checkpoint handling

def load_existing_output() -> pd.DataFrame:
    if not OUTPUT_CSV.exists():
        return pd.DataFrame()

    try:
        return pd.read_csv(
            OUTPUT_CSV
        )

    except Exception:
        return pd.DataFrame()


def flush_rows(
    existing: pd.DataFrame,
    new_rows: list[dict],
) -> pd.DataFrame:
    if not new_rows:
        return existing

    new_df = pd.DataFrame(
        new_rows
    )

    if existing.empty:
        combined = new_df

    else:
        combined = pd.concat(
            [
                existing,
                new_df,
            ],
            ignore_index=True,
        )

    combined = combined.drop_duplicates(
        subset="listing_id",
        keep="last",
    )

    combined = combined.sort_values(
        "listing_id"
    )

    available_columns = [
        column
        for column in OUTPUT_COLUMNS
        if column in combined.columns
    ]

    combined = combined[
        available_columns
    ]

    combined.to_csv(
        OUTPUT_CSV,
        index=False,
    )

    return combined


# Coverage reporting

def print_coverage(
    df: pd.DataFrame,
):
    print("\nField coverage:")

    if df.empty:
        print("No rows processed.")
        return

    for column in ENRICHED_FIELDS:
        populated = (
            df[column]
            .notna()
            .sum()
        )

        percentage = (
            populated
            / len(df)
            * 100
        )

        print(
            f"{column:15} "
            f"{populated:4}/{len(df)} "
            f"({percentage:.1f}%)"
        )


# Main

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Create a cleaned and enriched used-car inventory."
        )
    )

    parser.add_argument(
        "--sheet",
        default=DEFAULT_SHEET,
        help="Excel sheet to read.",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N listings.",
    )

    parser.add_argument(
        "--sleep",
        type=float,
        default=1.0,
        help="Seconds to wait between listing enrichment calls.",
    )

    parser.add_argument(
        "--restart",
        action="store_true",
        help="Delete cars.csv and rebuild it from scratch.",
    )

    parser.add_argument(
        "--no-llm",
        action="store_true",
        help=(
            "Run deterministic extraction only. "
            "Useful for testing preprocessing without Gemini."
        ),
    )

    args = parser.parse_args()

    if (
        args.restart
        and OUTPUT_CSV.exists()
    ):
        OUTPUT_CSV.unlink()

        print(
            f"Removed existing "
            f"{OUTPUT_CSV.name}"
        )

    if not SOURCE_XLSX.exists():
        raise FileNotFoundError(
            f"Source dataset not found: "
            f"{SOURCE_XLSX}"
        )

    df = pd.read_excel(
        SOURCE_XLSX,
        sheet_name=args.sheet,
        engine="openpyxl",
    )

    df.columns = [
        column
        .strip()
        .lower()
        .replace(" ", "_")
        for column in df.columns
    ]

    required_source_columns = {
        "listing_id",
        "year",
        "make",
        "model",
        "trim",
        "title",
        "description",
        "photo_url",
    }

    missing_columns = (
        required_source_columns
        - set(df.columns)
    )

    if missing_columns:
        raise ValueError(
            "Source dataset is missing required columns: "
            + ", ".join(
                sorted(
                    missing_columns
                )
            )
        )

    if args.limit is not None:
        df = df.head(
            args.limit
        )

    existing = load_existing_output()

    if existing.empty:
        completed_ids = set()

    else:
        completed_ids = set(
            existing["listing_id"]
            .dropna()
            .astype(int)
            .tolist()
        )

        print(
            f"Resuming: "
            f"{len(completed_ids)} listings "
            f"already saved in "
            f"{OUTPUT_CSV.name}"
        )

    pending_rows = []

    total = len(df)

    for position, (_, row) in enumerate(
        df.iterrows(),
        start=1,
    ):
        listing_id = int(
            row["listing_id"]
        )

        if listing_id in completed_ids:
            continue

        title = clean_text(
            row.get("title")
        )

        description = clean_text(
            row.get("description")
        )

        attrs, llm_status = enrich_listing(
            title,
            description,
            use_llm=not args.no_llm,
        )

        year = (
            int(row["year"])
            if not pd.isna(
                row["year"]
            )
            else None
        )

        output_row = {
            "listing_id": listing_id,
            "year": year,
            "make": row.get("make"),
            "model": row.get("model"),
            "trim": row.get("trim"),
            "title": title,
            "description": description,
            "photo_url": row.get(
                "photo_url"
            ),
            **attrs,
        }

        pending_rows.append(
            output_row
        )

        print(
            f"[{position}/{total}] "
            f"id={listing_id} "
            f"{row.get('make')} "
            f"{row.get('model')} | "
            f"price={attrs.get('price_aed')} | "
            f"mileage={attrs.get('mileage_km')} | "
            f"body={attrs.get('body_type')} | "
            f"transmission={attrs.get('transmission')} | "
            f"fuel={attrs.get('fuel_type')} | "
            f"llm={llm_status}"
        )

        if len(pending_rows) >= 10:
            existing = flush_rows(
                existing,
                pending_rows,
            )

            completed_ids.update(
                int(
                    item["listing_id"]
                )
                for item in pending_rows
            )

            pending_rows = []

        if (
            not args.no_llm
            and args.sleep > 0
        ):
            time.sleep(
                args.sleep
            )

    existing = flush_rows(
        existing,
        pending_rows,
    )

    print(
        "\nDone. Wrote enriched inventory to:"
    )

    print(
        OUTPUT_CSV
    )

    print_coverage(
        existing
    )


if __name__ == "__main__":
    main()