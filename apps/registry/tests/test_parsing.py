"""Parsing rules, tested without a database."""

from pathlib import Path

import pytest

from apps.registry.parsing import (
    CsvFormatError,
    normalise_header,
    normalise_value,
    parse_csv,
    parse_district,
)

HEADER = (
    "Licence No.(牌照編號),"
    "Name of TCSP Licensee in English(持牌人的英文姓名／名稱),"
    "Name of TCSP Licensee in Chinese(持牌人的中文姓名／名稱),"
    "Business Address(營業地址),"
    "Remarks in English(英文備註),"
    "Remarks in Chinese(中文備註)"
)


def csv_bytes(*rows: str, header: str = HEADER) -> bytes:
    return ("\r\n".join([header, *rows]) + "\r\n").encode("utf-8")


class TestNormaliseHeader:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Licence No.(牌照編號)", "licence no"),
            (
                "Name of TCSP Licensee in English(持牌人的英文姓名／名稱)",
                "name of tcsp licensee in english",
            ),
            ("Business Address（營業地址）", "business address"),
            ("﻿Licence No.(牌照編號)", "licence no"),
            ("  Remarks in English (英文備註) ", "remarks in english"),
        ],
    )
    def test_reduces_to_the_english_key(self, raw: str, expected: str) -> None:
        assert normalise_header(raw) == expected


class TestNormaliseValue:
    def test_folds_the_whitespace_variants_the_register_contains(self) -> None:
        assert normalise_value("ACME LIMITED") == "ACME LIMITED"
        assert normalise_value("ROOM 1,　2/F") == "ROOM 1, 2/F"
        assert normalise_value("  double  space  ") == "double space"

    def test_treats_missing_as_empty(self) -> None:
        assert normalise_value(None) == ""
        assert normalise_value("") == ""


class TestParseDistrict:
    @pytest.mark.parametrize(
        ("address", "expected"),
        [
            ("UNIT B, 423 HENNESSY ROAD, WAN CHAI, HONG KONG", "Wan Chai"),
            ("7/F, 62 MODY ROAD, TSIM SHA TSUI, KOWLOON, HONG KONG", "Yau Tsim Mong"),
            ("3/F, KWAI CHUNG PLAZA, KWAI CHUNG, NEW TERRITORIES", "Kwai Tsing"),
            # "SHEUNG WAN" must win over the shorter "WAN CHAI"-style substrings.
            ("ROOM 1, 100 DES VOEUX ROAD CENTRAL, SHEUNG WAN, HONG KONG", "Central and Western"),
        ],
    )
    def test_maps_known_localities(self, address: str, expected: str) -> None:
        assert parse_district(address) == expected

    def test_returns_empty_rather_than_guessing(self) -> None:
        # A wrong district hides a licensee from its own customers in the P2 filter.
        assert parse_district("SOMEWHERE UNRECOGNISABLE, HONG KONG") == ""


class TestParseCsv:
    def test_parses_the_real_shape(self, baseline_csv: Path) -> None:
        rows = parse_csv(baseline_csv.read_bytes())
        assert len(rows) == 6
        assert rows[0].licence_no == "TC000002"
        assert rows[0].name_en == "FULLYEAR CONSULTANTS LIMITED"
        assert rows[0].district == "Wan Chai"

    def test_keeps_the_untouched_row_in_raw(self, baseline_csv: Path) -> None:
        # CLAUDE.md rule 1: normalisation must never destroy the official value.
        rows = parse_csv(baseline_csv.read_bytes())
        winship = next(r for r in rows if r.licence_no == "TC000006")
        assert winship.name_en == "WINSHIP CONSULTANTS LIMITED"
        # The NBSP the register actually ships survives in raw.
        assert chr(0xA0) in "".join(winship.raw.values())

    def test_allows_a_missing_chinese_name(self, baseline_csv: Path) -> None:
        rows = parse_csv(baseline_csv.read_bytes())
        assert next(r for r in rows if r.licence_no == "TC000005").name_zh == ""

    def test_keeps_the_trustee_ordinance_remark(self, baseline_csv: Path) -> None:
        rows = parse_csv(baseline_csv.read_bytes())
        remark = next(r for r in rows if r.licence_no == "TC000005").remarks_en
        assert "Trustee Ordinance" in remark

    def test_rejects_a_header_missing_a_required_column(self) -> None:
        header = "Licence No.(牌照編號),Business Address(營業地址)"
        with pytest.raises(CsvFormatError, match="name_en"):
            parse_csv(csv_bytes('TC000002,"SOMEWHERE, HONG KONG"', header=header))

    def test_rejects_an_unusable_licence_number(self) -> None:
        with pytest.raises(CsvFormatError, match="unusable licence number"):
            parse_csv(csv_bytes('NOT-A-LICENCE,ACME LIMITED,,"SOMEWHERE, HONG KONG",,'))

    def test_rejects_duplicate_licence_numbers(self) -> None:
        row = 'TC000002,ACME LIMITED,,"SOMEWHERE, HONG KONG",,'
        with pytest.raises(CsvFormatError, match="duplicate"):
            parse_csv(csv_bytes(row, row))

    def test_rejects_a_header_only_file(self) -> None:
        with pytest.raises(CsvFormatError, match="no data rows"):
            parse_csv(csv_bytes())

    def test_rejects_an_empty_file(self) -> None:
        with pytest.raises(CsvFormatError, match="no header row"):
            parse_csv(b"")

    def test_skips_blank_lines(self) -> None:
        rows = parse_csv(csv_bytes('TC000002,ACME LIMITED,,"WAN CHAI, HONG KONG",,', ",,,,,"))
        assert len(rows) == 1
