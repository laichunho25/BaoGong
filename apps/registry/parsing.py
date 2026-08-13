"""Pure parsing/normalisation of the official TCSP CSV.

No database access here, so the rules below can be tested against fixtures on
their own. The register's real header row (verified 2026-08-13) is::

    Licence No.(牌照編號),
    Name of TCSP Licensee in English(持牌人的英文姓名／名稱),
    Name of TCSP Licensee in Chinese(持牌人的中文姓名／名稱),
    Business Address(營業地址),
    Remarks in English(英文備註),
    Remarks in Chinese(中文備註)

Headers are matched on the English part only: the parenthesised Chinese is
dropped before lookup so that a punctuation or wording change on the Chinese
side cannot break the import.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass

COLUMN_MAP: dict[str, str] = {
    "licence no": "licence_no",
    "name of tcsp licensee in english": "name_en",
    "name of tcsp licensee in chinese": "name_zh",
    "business address": "business_address",
    "remarks in english": "remarks_en",
    "remarks in chinese": "remarks_zh",
}

REQUIRED_FIELDS = ("licence_no", "name_en", "business_address")

# Fields whose value is compared between syncs to produce a LicenseeChange.
COMPARED_FIELDS = ("name_en", "name_zh", "business_address", "remarks_en", "remarks_zh")

_LICENCE_NO_RE = re.compile(r"^[A-Z]{2}\d{4,10}$")
_WHITESPACE_RE = re.compile(r"\s+")


class CsvFormatError(ValueError):
    """The downloaded file is not the register we expect."""


@dataclass(frozen=True, slots=True)
class LicenseeRow:
    """One normalised row plus the untouched source row."""

    licence_no: str
    name_en: str
    name_zh: str
    business_address: str
    remarks_en: str
    remarks_zh: str
    district: str
    raw: dict[str, str]


def normalise_header(header: str) -> str:
    """Reduce a bilingual header to its lowercase English key.

    ``"Licence No.(牌照編號)"`` -> ``"licence no"``.
    """
    text = header.replace("﻿", "")
    text = unicodedata.normalize("NFKC", text)
    text = re.split(r"[(（]", text, maxsplit=1)[0]
    text = text.replace(".", " ")
    return _WHITESPACE_RE.sub(" ", text).strip().lower()


def normalise_value(value: str | None) -> str:
    """Collapse the whitespace variants the register actually contains.

    NFKC folds full-width punctuation and the ideographic space; the register
    also ships a handful of rows containing NBSP. The untouched original is
    kept in ``LicenseeRow.raw``, so this never destroys information.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value)
    text = text.replace("\xa0", " ")
    return _WHITESPACE_RE.sub(" ", text).strip()


# Address locality -> one of the 18 official districts. The register prints
# free-text addresses with no district column, so this is a best-effort mapping
# used only for the P2 filter; an unrecognised address yields "" rather than a
# guess. Longer keys are matched first so that "SHEUNG WAN" wins over "WAN".
_LOCALITY_TO_DISTRICT: dict[str, str] = {
    "SHEUNG WAN": "Central and Western",
    "SAI YING PUN": "Central and Western",
    "KENNEDY TOWN": "Central and Western",
    "ADMIRALTY": "Central and Western",
    "CENTRAL": "Central and Western",
    "MID-LEVELS": "Central and Western",
    "SAI WAN": "Central and Western",
    "WAN CHAI": "Wan Chai",
    "WANCHAI": "Wan Chai",
    "CAUSEWAY BAY": "Wan Chai",
    "HAPPY VALLEY": "Wan Chai",
    "TIN HAU": "Eastern",
    "NORTH POINT": "Eastern",
    "QUARRY BAY": "Eastern",
    "TAIKOO": "Eastern",
    "SAI WAN HO": "Eastern",
    "SHAU KEI WAN": "Eastern",
    "CHAI WAN": "Eastern",
    "FORTRESS HILL": "Eastern",
    "ABERDEEN": "Southern",
    "WONG CHUK HANG": "Southern",
    "AP LEI CHAU": "Southern",
    "POK FU LAM": "Southern",
    "REPULSE BAY": "Southern",
    "CYBERPORT": "Southern",
    "TSIM SHA TSUI": "Yau Tsim Mong",
    "JORDAN": "Yau Tsim Mong",
    "YAU MA TEI": "Yau Tsim Mong",
    "MONG KOK": "Yau Tsim Mong",
    "MONGKOK": "Yau Tsim Mong",
    "PRINCE EDWARD": "Yau Tsim Mong",
    "TAI KOK TSUI": "Yau Tsim Mong",
    "SHAM SHUI PO": "Sham Shui Po",
    "CHEUNG SHA WAN": "Sham Shui Po",
    "LAI CHI KOK": "Sham Shui Po",
    "MEI FOO": "Sham Shui Po",
    "HUNG HOM": "Kowloon City",
    "TO KWA WAN": "Kowloon City",
    "KOWLOON CITY": "Kowloon City",
    "KOWLOON TONG": "Kowloon City",
    "HO MAN TIN": "Kowloon City",
    "WONG TAI SIN": "Wong Tai Sin",
    "SAN PO KONG": "Wong Tai Sin",
    "DIAMOND HILL": "Wong Tai Sin",
    "NGAU CHI WAN": "Wong Tai Sin",
    "KWUN TONG": "Kwun Tong",
    "KOWLOON BAY": "Kwun Tong",
    "NGAU TAU KOK": "Kwun Tong",
    "LAM TIN": "Kwun Tong",
    "YAU TONG": "Kwun Tong",
    "KWAI CHUNG": "Kwai Tsing",
    "KWAI FONG": "Kwai Tsing",
    "TSING YI": "Kwai Tsing",
    "TSUEN WAN": "Tsuen Wan",
    "TUEN MUN": "Tuen Mun",
    "YUEN LONG": "Yuen Long",
    "TIN SHUI WAI": "Yuen Long",
    "SHEUNG SHUI": "North",
    "FANLING": "North",
    "TAI PO": "Tai Po",
    "SHA TIN": "Sha Tin",
    "SHATIN": "Sha Tin",
    "FO TAN": "Sha Tin",
    "MA ON SHAN": "Sha Tin",
    "TAI WAI": "Sha Tin",
    "SAI KUNG": "Sai Kung",
    "TSEUNG KWAN O": "Sai Kung",
    "TUNG CHUNG": "Islands",
    "LANTAU": "Islands",
    "DISCOVERY BAY": "Islands",
    "CHEUNG CHAU": "Islands",
}

_LOCALITIES_BY_LENGTH = sorted(_LOCALITY_TO_DISTRICT, key=len, reverse=True)


def parse_district(business_address: str) -> str:
    """Best-effort district from a free-text address; ``""`` when unrecognised.

    Deliberately conservative - a wrong district is worse than a missing one,
    because the directory filter would hide a licensee from its own customers.
    """
    haystack = normalise_value(business_address).upper()
    for locality in _LOCALITIES_BY_LENGTH:
        if locality in haystack:
            return _LOCALITY_TO_DISTRICT[locality]
    return ""


def parse_csv(content: bytes) -> list[LicenseeRow]:
    """Decode, validate and normalise the register.

    Raises ``CsvFormatError`` when the header is missing an expected column or
    a data row has no licence number - both mean the upstream format changed
    and the sync must abort rather than write half-understood data.
    """
    text = content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        raise CsvFormatError("The register is empty: no header row.") from None

    mapping: dict[int, str] = {}
    for index, raw_header in enumerate(header):
        field = COLUMN_MAP.get(normalise_header(raw_header))
        if field is not None:
            mapping[index] = field

    missing = [f for f in REQUIRED_FIELDS if f not in mapping.values()]
    if missing:
        raise CsvFormatError(
            f"The register is missing required column(s) {missing}. Header seen: {header!r}"
        )

    rows: list[LicenseeRow] = []
    seen: set[str] = set()
    for line_no, record in enumerate(reader, start=2):
        if not any(cell.strip() for cell in record):
            continue
        values = {
            field: normalise_value(record[index] if index < len(record) else "")
            for index, field in mapping.items()
        }
        licence_no = values["licence_no"].upper().replace(" ", "")
        if not _LICENCE_NO_RE.match(licence_no):
            raise CsvFormatError(f"Line {line_no}: unusable licence number {licence_no!r}.")
        if licence_no in seen:
            raise CsvFormatError(f"Line {line_no}: duplicate licence number {licence_no!r}.")
        seen.add(licence_no)

        raw = {
            header[index] if index < len(header) else f"column_{index}": value
            for index, value in enumerate(record)
        }
        rows.append(
            LicenseeRow(
                licence_no=licence_no,
                name_en=values["name_en"],
                name_zh=values.get("name_zh", ""),
                business_address=values["business_address"],
                remarks_en=values.get("remarks_en", ""),
                remarks_zh=values.get("remarks_zh", ""),
                district=parse_district(values["business_address"]),
                raw=raw,
            )
        )

    if not rows:
        raise CsvFormatError("The register contains a header but no data rows.")
    return rows
