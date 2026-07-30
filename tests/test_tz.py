from datetime import timedelta

from dateutil import tz as upstream

from mojo_dateutil import tz


def test_utc_singleton_matches_standard_utc_behavior():
    assert tz.tzutc() is tz.UTC
    assert tz.UTC.utcoffset(None) == upstream.UTC.utcoffset(None)


def test_named_fixed_offset_matches_upstream():
    ours = tz.tzoffset("BRST", timedelta(hours=-2))
    theirs = upstream.tzoffset("BRST", -7200)
    assert ours.utcoffset(None) == theirs.utcoffset(None)
    assert ours.tzname(None) == theirs.tzname(None)
