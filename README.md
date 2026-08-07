# Expected Move

A ranked list of the US stocks most likely to move furthest in a given session.
Built overnight, **frozen at 8:25 AM ET**, published as a static site, and
graded against realized moves after the close.

```
17:00 ET ──── draft opens ────► 20:00 ──── overnight refresh ────► 07:40
                                                                     │
                              ┌──────────────────────────────────────┘
                              ▼
                        07:40 final pass ──► 08:25 FREEZE ──► 08:30 deadline
                                                                     │
                              ┌──────────────────────────────────────┘
                              ▼
                        09:30 open ─── today only, read-only ─── 16:00 close
                                                                     │
                                                          16:00–17:00 scoring
```

---

## Before you start: about the data you based this on

The summary this repo was specified from should not be treated as ground truth.
`SPCX` is not a listed US equity, several ticker/company pairings in it don't
match, and the macro framing (a Fed "rate hike," a specific jobless-claims
print) doesn't hold up. That kind of drift is exactly what happens when market
figures pass through a summarizer.

So this repo hardcodes **none** of it. Every number is pulled fresh from a
primary market-data API at run time, and anything a provider can't supply is
recorded as absent rather than filled in with a plausible guess. If a source
fails, the affected feature drops out and the confidence score falls — the
pipeline never invents a value to keep a row on the list.

---

## What it predicts, and what it refuses to predict

It ranks **expected absolute move** — how far a name is likely to travel, in
either direction.

It does **not** call direction. That's a deliberate limit, not an omission.
Direction on an earnings reaction is close to a coin flip conditional on public
information; magnitude is genuinely forecastable from implied volatility,
historical reaction size, and event type. Ranking magnitude is a claim the data
can support. Ranking direction is not, and a site that did it anyway would just
be laundering noise through a nice typeface.

Every row ships with the inputs that produced it (`drivers`), a confidence
score, and an uncertainty band that widens when inputs are missing.

---

## Setup

```bash
git clone https://github.com/OWNER/REPO && cd REPO
pip install -r requirements.txt
cp .env.example .env        # add your keys
```

### API keys

| Key | Required | Used for | Free tier |
|---|---|---|---|
| `POLYGON_API_KEY` | **yes** | Daily bars, full-market scan, snapshots | Yes, 15-min delayed |
| `FINNHUB_API_KEY` | strongly recommended | Earnings calendar with BMO/AMC flag | Yes, 60 req/min |
| `FMP_API_KEY` | optional | Second earnings calendar, cross-check only | Yes, 250 req/day |

Polygon is required because `/v2/aggs/grouped` returns every US equity's daily
bar in one request — that's what makes a full-market scan cheap enough to run
on a cron. Without Finnhub, event weighting is disabled and the list degrades
to a plain volatility screen; it still works, just worse.

Add all three as repository secrets under **Settings → Secrets → Actions**.

### First run

```bash
python scripts/make_sample.py          # synthetic data so the site renders
python -m mmd.cli phase                # what phase are we in right now?
python -m mmd.cli build --session 2026-08-07
python -m mmd.cli feed
python -m http.server 8000 -d site     # open localhost:8000
```

Enable **Settings → Pages → Source: GitHub Actions**. The pipeline deploys on
every run.

---

## How the deadline is actually met

The 8:30 requirement is the hard part, and GitHub Actions is not a reliable
scheduler. Two things bite:

**Cron is UTC and ignores US daylight saving.** `0 12 * * *` is 8:00 AM ET in
summer and 7:00 AM in winter. Workflows therefore over-schedule across both
offsets, and `src/mmd/clock.py` decides from the America/New_York wall clock
whether a given run should act. Every phase boundary is a wall-clock time, and
`tests/test_clock.py` asserts both offsets land in the same phase.

**Scheduled runs get delayed**, routinely 5–20 minutes at the top of the hour.
Mitigations:

- The pre-market window runs every 15 minutes, offset off `:00` to dodge the
  peak-load queue.
- A separate `lock-guard` workflow runs at ~08:14 and ~08:20 ET. It never
  builds — it only freezes and publishes the most recent good draft, so a
  failed or delayed build still leaves a list on the site before the bell.
- Locking is one-way. `collect.write()` refuses to overwrite a file already
  marked `locked`, so a late run can't mutate a published list.
- If the guard itself fails, it opens an issue labelled `deadline-miss`.
- A build failure never blanks the site. The feed is rebuilt from whatever is
  on disk and the page shows a banner saying the list is provisional or stale.

## How the visibility rules are enforced

> the website should only have predictions for the same day until the closing
> bell, and the next day should be presented starting 5PM

Enforced in Python, in `publish.build_feed()`, **before** the file is written —
not hidden in JavaScript. Between the opening and closing bell, tomorrow's
predictions are not written into `feed.json` at all, so there's nothing to
uncover in dev tools. During `SESSION`, only `display_session` is loaded. At
17:00 ET the phase flips to `DRAFT` and the next session's file starts
appearing.

---

## The model

Universe is the union of four buckets, capped by a liquidity filter
(≥ $1.50, ≥ $5M/day — below that a 40% "move" is one print wide):

1. Confirmed earnings before tomorrow's open
2. Companies that reported after today's close
3. Prior-session outliers — moves ≥ 8% and the top of the dollar-volume book
4. A short macro-sensitive list that reprices on scheduled data

Scoring, per name:

```
baseline   = mean(ATR14%, realized_vol_20d% × 1.15)
expected   = baseline × catalyst_weight
           → blended with median historical earnings-day move (if ≥3 reports)
           → blended with the options-implied move (if available; weighted highest)
           → scaled up by unusual volume
           → floored by any overnight gap already realized
confidence = f(input completeness, implied vol present, earnings history depth)
band       = expected × (1 ± spread), spread widening as confidence falls
```

Catalyst weights live in `src/mmd/model.py` and are calibrated against realized
moves. A company reporting *after* today's close is weighted low, because that
move lands tomorrow — a distinction most naive screens get wrong.

## Scoring

After the close, `mmd score` grades the locked list:

- **rank_ic** — Spearman correlation between predicted and realized \|move\|.
  This is the metric that matters; the product is an ordering.
- **hit_rate** — share of names whose realized move landed inside the band.
- **mae_pp** — mean absolute error on the point estimate.

All three are published on the site. A forecast list without a visible hit rate
is unfalsifiable, and therefore worthless.

Calibrate expectations: a rank IC of 0.25–0.45 on this kind of task is a real,
useful signal. If you see 0.8, something is leaking future information — check
that `grouped_daily` isn't being called for the target session during a build.

---

## Layout

```
src/mmd/
  clock.py       ET calendar + phase state machine     ← the heart of it
  collect.py     universe, catalyst tagging, orchestration
  features.py    ATR, realized vol, rvol, gaps, earnings-reaction history
  model.py       expected-move scoring and ranking
  score.py       post-close grading, rolling accuracy
  publish.py     builds site/feed.json, enforces visibility
  cli.py         mmd run | build | lock | score | feed | phase
  providers/     polygon, finnhub, fmp — each optional, each degrades cleanly
.github/workflows/
  pipeline.yml   the schedule; over-scheduled, phase-gated in Python
  lock-guard.yml the 8:30 insurance policy
site/            static front end, reads one JSON file
data/sessions/   one directory per session, committed as an audit trail
```

`data/` is committed on purpose. Every list is timestamped and immutable once
locked, so you can always prove what was published before the bell rather than
grading yourself on a list you edited afterwards.

## Maintenance

- **Every December**: refresh `MARKET_HOLIDAYS` and `HALF_DAYS` in `clock.py`,
  and the `macro_releases` dates in `config.yaml`. Both are dated through 2027.
- **Quarterly**: re-fit catalyst weights against the accumulated scorecards.
- **Watch for**: silent provider schema changes. `warnings` in each
  `predictions.json` is the first place a broken feed shows up.

---

## Limitations

Worth being blunt about:

- **Free tiers are delayed 15 minutes.** For an 8:25 lock that's usually fine —
  overnight data is settled — but pre-market gap detection will lag.
- **No options data by default.** The implied-move input is the single most
  predictive feature and Polygon's options endpoints are paid. Without it the
  model leans on historical reaction size, which is weaker.
- **Earnings calendars are unreliable.** Timing flags get revised, sometimes
  the morning of. Two sources are reconciled and conflicts are downgraded to
  `unknown` rather than resolved by guessing.
- **The list is survivorship-friendly.** Names are picked partly *because*
  they're volatile, so a high realized move is partly selection, not skill.
  Rank IC is the honest metric; hit rate flatters the model.
- **This won't make you money.** Knowing a stock will move ±9% tells you
  nothing about which way, and options are priced for exactly that. This is a
  research and attention tool.

## Licence

MIT. Not investment advice; no warranty of accuracy or fitness for trading.
