from datetime import date, datetime

import pytest

from mmd.clock import ET, Phase, is_trading_day, next_trading_day, resolve


def at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


class TestPhases:
    def test_session_hides_tomorrow(self):
        """The core promise: during the session, only today exists."""
        c = resolve(at(2026, 8, 6, 11, 0))  # Thursday, mid-session
        assert c.phase is Phase.SESSION
        assert c.display_session == date(2026, 8, 6)
        assert c.target_session == date(2026, 8, 6)
        assert c.locked
        assert not c.should_build

    def test_tomorrow_opens_at_five(self):
        assert resolve(at(2026, 8, 6, 16, 59)).phase is Phase.SETTLE
        c = resolve(at(2026, 8, 6, 17, 0))
        assert c.phase is Phase.DRAFT
        assert c.target_session == date(2026, 8, 7)
        assert c.should_build

    def test_final_window_then_lock(self):
        assert resolve(at(2026, 8, 7, 7, 39)).phase is Phase.REFRESH
        assert resolve(at(2026, 8, 7, 7, 40)).phase is Phase.FINAL
        assert resolve(at(2026, 8, 7, 8, 24)).phase is Phase.FINAL
        locked = resolve(at(2026, 8, 7, 8, 25))
        assert locked.phase is Phase.LOCKED
        assert locked.locked
        assert not locked.should_build

    def test_list_exists_before_the_deadline(self):
        """At 08:29 the list must be frozen and for today."""
        c = resolve(at(2026, 8, 7, 8, 29))
        assert c.locked
        assert c.target_session == date(2026, 8, 7)

    def test_friday_evening_targets_monday(self):
        c = resolve(at(2026, 8, 7, 18, 0))  # Friday
        assert c.target_session == date(2026, 8, 10)

    def test_holiday_is_skipped(self):
        assert not is_trading_day(date(2026, 12, 25))
        assert next_trading_day(date(2026, 12, 24)) == date(2026, 12, 28)

    @pytest.mark.parametrize("hour,expected", [
        (17, Phase.DRAFT), (21, Phase.REFRESH), (3, Phase.REFRESH),
        (8, Phase.FINAL), (10, Phase.SESSION), (16, Phase.SETTLE),
    ])
    def test_phase_coverage(self, hour, expected):
        assert resolve(at(2026, 8, 6, hour, 0)).phase is expected

    def test_half_day_close(self):
        """Nov 27 2026 closes at 13:00; 14:00 is settle, not session."""
        assert resolve(at(2026, 11, 27, 12, 30)).phase is Phase.SESSION
        assert resolve(at(2026, 11, 27, 14, 0)).phase is Phase.SETTLE

    def test_dst_boundaries_resolve(self):
        """Both offsets must produce the same wall-clock phase."""
        assert resolve(at(2026, 1, 15, 8, 0)).phase is Phase.FINAL   # EST
        assert resolve(at(2026, 7, 15, 8, 0)).phase is Phase.FINAL   # EDT
