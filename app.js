/* Renders site/feed.json. The feed is the whole API surface.

   Visibility is decided in Python before the file is written — this script
   never has tomorrow's list in hand during the session, so there is nothing
   to leak. It only draws what it is given. */

const $ = (id) => document.getElementById(id);

const PHASE_ORDER = ['draft', 'refresh', 'final', 'locked', 'session', 'settle'];

// Where each phase sits on the 5pm→4pm rail, matching the CSS segments.
const RAIL = {
  draft:   [0, 12.5],
  refresh: [12.5, 52],
  final:   [52, 60],
  locked:  [60, 64.5],
  session: [64.5, 92],
  settle:  [92, 100],
  closed:  [0, 0],
};

const TAG_LABEL = {
  earnings_bmo: 'reports pre-open',
  earnings_amc_prior: 'reported after close',
  earnings_amc_tonight: 'reports tonight',
  momentum_carryover: 'carryover',
  macro_sensitive: 'macro',
  rating_change: 'rating change',
  none: 'volatility',
};

function fmtTime(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleString('en-US', {
    timeZone: 'America/New_York', month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit',
  }) + ' ET';
}

function fmtSession(iso) {
  if (!iso) return '';
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString('en-US', {
    timeZone: 'UTC', weekday: 'long', month: 'long', day: 'numeric',
  });
}

/* ---- the rail ---------------------------------------------------------- */

function drawRail(cycle, secondsToLock) {
  const phase = cycle.phase;
  document.querySelectorAll('.seg').forEach((el) => {
    el.classList.toggle('is-now', el.dataset.phase === phase);
  });

  const [from, to] = RAIL[phase] || [0, 0];
  let pos = from;

  // Within the pre-lock phases, interpolate using the countdown so the marker
  // creeps toward the freeze rather than sitting still for hours.
  if (['draft', 'refresh', 'final'].includes(phase) && secondsToLock > 0) {
    const spanStart = RAIL.draft[0];
    const spanEnd = RAIL.final[1];
    const totalWindow = 15.5 * 3600; // 17:00 ET to 08:25 ET
    const elapsed = Math.max(0, Math.min(1, 1 - secondsToLock / totalWindow));
    pos = spanStart + (spanEnd - spanStart) * elapsed;
  } else {
    pos = from + (to - from) * 0.5;
  }

  requestAnimationFrame(() => {
    $('marker').style.setProperty('--at', pos.toFixed(2));
  });
}

function countdown(seconds, phase) {
  const el = $('countdown');
  if (phase === 'session') {
    el.textContent = 'Tomorrow\u2019s list opens at 5:00 PM ET.';
    return;
  }
  if (phase === 'settle' || phase === 'closed') {
    el.textContent = 'Next list opens at 5:00 PM ET.';
    return;
  }
  if (seconds <= 0) { el.textContent = ''; return; }
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  el.textContent = `Freezes in ${h}h ${String(m).padStart(2, '0')}m \u2014 published before 8:30 AM ET.`;
}

/* ---- the list ---------------------------------------------------------- */

function drawList(feed) {
  const list = feed.list;
  const rows = $('rows');
  const empty = $('empty');
  rows.replaceChildren();

  if (!list || !list.predictions || !list.predictions.length) {
    empty.hidden = false;
    empty.textContent = feed.cycle.phase === 'draft'
      ? 'The first pass for the next session is still building. Check back shortly.'
      : 'No list is published for this session.';
    $('listMeta').textContent = '';
    return;
  }
  empty.hidden = true;

  const heading = document.querySelector('#list-h');
  heading.textContent = feed.cycle.phase === 'session'
    ? 'Today\u2019s list'
    : `List for ${fmtSession(list.target_session)}`;

  const bits = [];
  if (list.locked) bits.push(`locked ${fmtTime(list.locked_at_et)}`);
  else bits.push(`updated ${fmtTime(list.generated_at_et)}`);
  if (list.counts) bits.push(`${list.counts.published} of ${list.counts.universe} screened`);
  if (list.sources) bits.push(list.sources.join(' + '));
  $('listMeta').textContent = bits.join(' \u00b7 ');

  // Scale every bar to the largest high in the list so widths are comparable.
  const ceiling = Math.max(...list.predictions.map((p) => p.band[1])) || 1;

  list.predictions.forEach((p, i) => {
    const li = document.createElement('li');
    li.className = 'row';
    li.style.setProperty('--d', `${Math.min(i * 0.028, 0.7)}s`);

    const lo = (p.band[0] / ceiling) * 100;
    const hi = (p.band[1] / ceiling) * 100;
    const pt = (p.expected_move_pct / ceiling) * 100;

    const tags = (p.catalysts || []).map(
      (c) => `<span class="tag" data-t="${c}">${TAG_LABEL[c] || c}</span>`
    ).join('');

    li.innerHTML = `
      <div class="rank">${String(p.rank).padStart(2, '0')}</div>
      <div>
        <p class="sym">${p.symbol}</p>
        <div class="tags">${tags}</div>
        ${p.note ? `<p class="why">${p.note}</p>` : ''}
      </div>
      <div class="est">
        <span class="est-num">\u00b1${p.expected_move_pct.toFixed(1)}%</span>
        <span class="est-band">${p.band[0].toFixed(1)}\u2013${p.band[1].toFixed(1)}% \u00b7 conf ${p.confidence.toFixed(2)}</span>
      </div>
      <div class="bar">
        <div class="bar-span" style="--lo:${lo.toFixed(1)};--hi:${hi.toFixed(1)}"></div>
        <div class="bar-point" style="--pt:${pt.toFixed(1)}"></div>
      </div>`;
    rows.append(li);
  });
}

/* ---- accuracy ---------------------------------------------------------- */

function drawAccuracy(feed) {
  const a = feed.accuracy || {};
  const cells = [
    ['Sessions graded', a.sessions ?? 0],
    ['Rank correlation', a.rank_ic == null ? '\u2014' : a.rank_ic.toFixed(2)],
    ['Inside the band', a.hit_rate == null ? '\u2014' : `${Math.round(a.hit_rate * 100)}%`],
    ['Mean error', a.mae_pp == null ? '\u2014' : `${a.mae_pp.toFixed(1)}pp`],
  ];
  $('metrics').innerHTML = cells.map(
    ([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`
  ).join('');

  const card = feed.scorecard;
  const host = $('scorecard');
  if (!card) { host.replaceChildren(); return; }
  const top = (card.rows || []).filter((r) => r.status === 'scored').slice(0, 5);
  host.innerHTML = top.length ? `
    <p class="scale-key" style="margin-top:1.6rem">
      Last graded session \u2014 <b>${fmtSession(card.session)}</b>.
      Predicted against realized close-to-close move.
    </p>
    <ol class="rows">${top.map((r) => `
      <li class="row">
        <div class="rank">${String(r.rank).padStart(2, '0')}</div>
        <div><p class="sym">${r.symbol}</p>
          <p class="why">Called \u00b1${r.predicted_pct.toFixed(1)}%, moved ${r.realized_pct.toFixed(1)}%
          \u2014 ${r.in_band ? 'inside the band' : 'outside the band'}.</p></div>
        <div class="est"><span class="est-num">${r.realized_pct.toFixed(1)}%</span></div>
      </li>`).join('')}</ol>` : '';
}

/* ---- boot -------------------------------------------------------------- */

async function load() {
  let feed;
  try {
    const res = await fetch(`feed.json?t=${Date.now()}`, { cache: 'no-store' });
    if (!res.ok) throw new Error(res.status);
    feed = await res.json();
  } catch (err) {
    $('phaseLabel').textContent = 'Feed unavailable';
    $('phaseDetail').textContent =
      'The published feed could not be loaded. The list on screen may be out of date.';
    return;
  }

  const copy = feed.phase_copy || {};
  $('phaseLabel').textContent = copy.label || feed.cycle.phase;
  $('phaseDetail').textContent = copy.detail || '';

  const banner = $('banner');
  const status = feed.status || {};
  if (status.state && status.state !== 'ok') {
    banner.hidden = false;
    banner.dataset.state = status.state;
    banner.textContent = status.message;
  } else {
    banner.hidden = true;
  }

  drawRail(feed.cycle, feed.seconds_to_lock);
  countdown(feed.seconds_to_lock, feed.cycle.phase);
  drawList(feed);
  drawAccuracy(feed);
  $('builtAt').textContent = `Feed built ${fmtTime(feed.built_at_et)}`;
}

load();
// The list is static once locked; polling is only to catch a phase flip.
setInterval(load, 60_000);
