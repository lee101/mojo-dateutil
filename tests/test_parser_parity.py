from datetime import datetime, timedelta, timezone

import pytest
from dateutil import parser as upstream

from mojo_dateutil import parser


@pytest.mark.parametrize(
    "value",
    [
        "2024",
        "2024-02",
        "2024-02-29",
        "20240229",
        "2024-060",
        "2024060",
        "2024-W09",
        "2024-W09-4",
        "2024W094",
        "2024-02-29T12",
        "2024-02-29T12:34",
        "20240229T123456",
        "2024-02-29 12:34:56.123",
        "2024-02-29T12:34:56,123456",
        "2024-02-29T12:34:56.123456789",
        "2024-02-29X12:34:56",
        "2024-02-29T12:34:56Z",
        "2024-02-29T12:34:56+05",
        "2024-02-29T12:34:56+0530",
        "2024-02-29T12:34:56-05:30",
        "2020-01-01T24:00Z",
        b"1999-12-31T23:59:59.999999+00:00",
    ],
)
def test_isoparse_matches_upstream(value):
    assert parser.isoparse(value) == upstream.isoparse(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "2023-02-29",
        "2024-13-01",
        "2024-01-32",
        "2024-W54-1",
        "2024-W01-8",
        "2024-001T25:00",
        "2024-01-01T24:00:01",
        "2024-01-01T12:60",
        "2024-01-01T12:00:60",
        "2024-01-01T12:00+24:00",
        "2024T12:00",
        "2024-02T12:00",
    ],
)
def test_isoparse_rejects_invalid_values_like_upstream(value):
    with pytest.raises(ValueError):
        upstream.isoparse(value)
    with pytest.raises(ValueError):
        parser.isoparse(value)


@pytest.mark.parametrize(
    "value, options",
    [
        ("January 5, 2024", {}),
        ("5 Jan 2024", {}),
        ("12/31/2024", {}),
        ("31/12/2024", {"dayfirst": True}),
        ("2024/12/31", {"yearfirst": True}),
        ("2024-03-10 11:30 PM", {}),
        ("2024-01-01T12:30:00+02:00", {"ignoretz": True}),
    ],
)
def test_parse_common_forms_matches_upstream(value, options):
    assert parser.parse(value, **options) == upstream.parse(value, **options)


def test_parse_many_matches_individual_upstream_results():
    values = [f"2024-03-{day:02d}T12:34:{day:02d}Z" for day in range(1, 29)]
    assert parser.parse_many(values) == [upstream.isoparse(value) for value in values]


def test_parse_many_reuses_fixed_offset_objects():
    values = parser.parse_many(["2024-01-01T00:00:00+05:30"] * 3)
    assert values[0].tzinfo is values[1].tzinfo is values[2].tzinfo


@pytest.mark.parametrize("count", [4095, 4096])
def test_parse_many_serial_and_parallel_thresholds(count):
    values = [
        "2024-02-29T12:34:56.123456+05:30",
        "2024-W09-4T06:07:08Z",
    ] * (count // 2) + ["2024-060T24:00-03:00"] * (count % 2)
    assert parser.parse_many(values) == [upstream.isoparse(value) for value in values]


def test_parse_many_parallel_error_index():
    values = ["2024-01-01T00:00:00Z"] * 4096
    values[3073] = "2024-02-30"
    with pytest.raises(ValueError, match="index 3073"):
        parser.parse_many(values)


def test_isoparse_simd_tail_preserves_timezone_presence():
    value = "1999-12-31T23:59:59.999999-07:30"
    assert parser.isoparse(value) == upstream.isoparse(value)


@pytest.mark.parametrize("separator", [bytes([value]) for value in range(128)])
def test_every_ascii_one_byte_datetime_separator_matches_upstream(separator):
    value = b"2024-02-29" + separator + b"12:34:56"
    assert parser.isoparse(value) == upstream.isoparse(value)


def test_fixed_offsets_have_identical_utc_instants():
    got = parser.isoparse("2024-01-01T12:00:00-07:30")
    assert got.utcoffset() == timedelta(hours=-7, minutes=-30)
    assert got.astimezone(timezone.utc) == datetime(2024, 1, 1, 19, 30, tzinfo=timezone.utc)


def test_parser_class_uses_same_api():
    assert parser.parser().parse("2024-05-06") == upstream.parser().parse("2024-05-06")


def test_parserinfo_compatibility_marker_is_available():
    assert isinstance(parser.parserinfo(), parser.parserinfo)


def test_named_fixed_offset_matches_upstream():
    value = "2024-05-06 12:30 BRST"
    zones = {"BRST": -7200}
    assert parser.parse(value, tzinfos=zones) == upstream.parse(value, tzinfos=zones)
