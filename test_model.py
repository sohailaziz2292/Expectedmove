from mmd.features import Features
from mmd.model import CATALYST_WEIGHT, rank, score_one


def feat(**kw):
    base = dict(
        symbol="TEST", close=50.0, dollar_volume=50_000_000,
        atr_pct=2.0, realized_vol_pct=1.8, rvol=1.0,
        prior_move_pct=0.5, gap_pct=0.0,
    )
    base.update(kw)
    return Features(**base)


def test_illiquid_names_are_dropped():
    assert score_one(feat(dollar_volume=100_000), ["earnings_bmo"]) is None
    assert score_one(feat(close=0.40), ["earnings_bmo"]) is None


def test_earnings_beats_quiet_baseline():
    quiet = score_one(feat(), ["none"])
    reporting = score_one(feat(), ["earnings_bmo"])
    assert reporting.expected_move_pct > quiet.expected_move_pct * 2


def test_history_pulls_estimate_toward_past_reactions():
    """A name that routinely moves 14% should not be scored like a 2% ATR name."""
    p = score_one(feat(hist_earnings_move=14.0, n_earnings_obs=8), ["earnings_bmo"])
    assert p.expected_move_pct > 9.0
    assert "hist_earnings_move" in p.drivers


def test_gap_sets_a_floor():
    p = score_one(feat(gap_pct=-11.0), ["earnings_amc_prior"])
    assert p.expected_move_pct >= 11.0 * 1.25 - 1e-9


def test_band_widens_as_confidence_falls():
    sure = score_one(feat(hist_earnings_move=8.0, n_earnings_obs=8), ["earnings_bmo"])
    unsure = score_one(
        feat(realized_vol_pct=None, rvol=None, prior_move_pct=None, gap_pct=None),
        ["none"],
    )
    sure_w = (sure.band_high_pct - sure.band_low_pct) / sure.expected_move_pct
    unsure_w = (unsure.band_high_pct - unsure.band_low_pct) / unsure.expected_move_pct
    assert unsure_w > sure_w


def test_no_features_means_no_prediction():
    assert score_one(feat(atr_pct=None, realized_vol_pct=None), ["earnings_bmo"]) is None


def test_rank_caps_a_single_catalyst():
    preds = []
    for i in range(20):
        p = score_one(feat(symbol=f"S{i}", atr_pct=5.0 - i * 0.1), ["earnings_bmo"])
        preds.append(p)
    out = rank(preds, limit=25, max_per_catalyst=5)
    assert len(out) == 5
    assert [p.rank for p in out] == [1, 2, 3, 4, 5]


def test_amc_tonight_is_not_treated_as_today():
    """A company reporting after today's close moves tomorrow, not today."""
    assert CATALYST_WEIGHT["earnings_amc_tonight"] < CATALYST_WEIGHT["earnings_bmo"]
