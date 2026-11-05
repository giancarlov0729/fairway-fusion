const api = window.GolfAPI;
const $ = (selector) => document.querySelector(selector);
const S = { tab: 'session', session: null, shot: null, sessions: [], busy: false, ready: false, scenario: 'straight' };
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const number = (value) => Number.isFinite(value) ? value.toFixed(1) : '—';
const when = (value) => new Date(value).toLocaleString();
const clock = (value) => {
  const seconds = Math.max(0, Math.floor(value / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
};
const direction = (value) => !Number.isFinite(value) ? '—' : Math.abs(value) < 0.05 ? 'Straight' : `${number(Math.abs(value))}° ${value > 0 ? 'left' : 'right'}`;
const fig = (value, unit, label) => `<div><div class="fig">${value}<small>${unit}</small></div><div class="cap">${label}</div></div>`;

function toast(message) {
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { $('#toast').hidden = true; }, 7000);
}

async function syncSession() {
  S.session = await api.activeSession();
  S.ready = true;
}

async function run(action) {
  if (S.busy) return;
  S.busy = true;
  render();
  try {
    await action();
  } catch (error) {
    toast(error.message);
    if (error.connectionLost) S.ready = false;
    else { try { await syncSession(); } catch { S.ready = false; } }
  } finally {
    S.busy = false;
    render();
  }
}

function sessionView() {
  const disabled = S.busy || !S.ready ? 'disabled' : '';
  if (!S.ready) return '<h1>Connecting to server</h1><p>Keep Flask running, then refresh this page if the connection fails.</p>';
  if (!S.session) return `<h1>Ready when you are</h1>
    <p>Start a session to generate simulated ball positions, calculate launch metrics, and save results.</p>
    <button class="btn primary" data-act="start" ${disabled}>Start session</button>
    <p class="muted demo-note">Simulation demo · No cameras connected.</p>`;
  return `<h1>Session in progress</h1>
    <div class="facts">${fig(S.session.shot_count, '', 'Saved shots')}${fig(S.session.id, '', 'Session')}</div>
    <p>Elapsed: <span id="elapsed">${clock(Date.now() - Date.parse(S.session.started_at))}</span></p>
    <div class="row"><label for="scenario">Shot scenario</label>
      <select id="scenario" ${disabled}>
        ${[['straight', 'Straight'], ['high-launch', 'High launch'], ['right-launch', 'Right launch']].map(([value, label]) => `<option value="${value}" ${S.scenario === value ? 'selected' : ''}>${label}</option>`).join('')}
      </select></div>
    <div class="row"><button class="btn go" data-act="simulate" ${disabled}>Simulate shot</button>
      <button class="btn" data-act="end" ${disabled}>End session</button></div>
    <p class="muted small">Simulation demo. Each click calculates and saves one shot in SQLite.</p>`;
}

function resultsView() {
  const shot = S.shot;
  if (!shot) return '<h1>No shot selected</h1><p>Simulate a shot in Session or open a saved shot in History.</p>';
  const samples = shot.samples?.timestamps_s?.length;
  return `<h1>Shot #${shot.id}</h1>
    <p>${escapeHtml(when(shot.created_at))} · Session #${shot.session_id}<br>
    Source: ${escapeHtml(shot.source)} · ${escapeHtml(shot.status)} · Saved in SQLite</p>
    ${shot.reason ? `<p>${escapeHtml(shot.reason)}</p>` : ''}
    <div class="metrics">
      ${fig(number(shot.speed_mph), 'mph', 'Ball speed')}
      ${fig(number(shot.speed_m_s), 'm/s', 'Ball speed')}
      ${fig(number(shot.launch_angle_deg), '°', 'Launch angle')}
      ${fig(direction(shot.launch_direction_deg), '', 'Launch direction')}
    </div>
    <p class="muted demo-note">${samples ? `${samples} position samples used. ` : ''}Spin, carry, total distance, and shot scoring are not calculated in this demo.</p>
    ${samplesTable(shot)}
    <button class="btn go" data-act="tab" data-tab="session">${S.session ? 'Next shot' : 'Go to session'}</button>`;
}

function samplesTable(shot) {
  const times = shot.samples?.timestamps_s;
  const positions = shot.samples?.positions_m;
  if (!Array.isArray(times) || !Array.isArray(positions) || !times.length) return '<p class="muted">Input samples are unavailable for this shot.</p>';
  const precise = (value) => Number.isFinite(value) ? value.toFixed(6) : '�';
  return `<details class="sess samples"><summary><strong>View input samples (${times.length})</strong></summary>
    <p class="empty muted small">Stored inputs used for calculation. X = downrange, Y = left, Z = up. Time is relative to the first sample.</p>
    <div class="table-scroll" tabindex="0" role="region" aria-label="Input samples"><table class="data-table"><thead><tr><th scope="col">Sample</th><th scope="col">Time (s)</th><th scope="col">X (m)</th><th scope="col">Y (m)</th><th scope="col">Z (m)</th></tr></thead><tbody>
    ${times.map((time, i) => `<tr><td>${i + 1}</td><td>${precise(time)}</td>${[0, 1, 2].map((axis) => `<td>${precise(positions[i]?.[axis])}</td>`).join('')}</tr>`).join('')}
    </tbody></table></div></details>`;
}

function historyView() {
  return `<h1>History</h1><p class="muted small">Latest 50 sessions; up to 50 latest shots per session. Saved on the server.</p>
    <div class="row"><button class="btn" data-act="tab" data-tab="history" ${S.busy ? 'disabled' : ''}>Refresh history</button></div>` +
    (S.sessions.length ? S.sessions.map((session) => `<details class="sess"><summary>
      <strong>Session #${session.id} · ${escapeHtml(when(session.started_at))}</strong>
      <span class="sum">${session.shot_count} shots · ${session.active ? 'Active' : 'Ended'}</span></summary>
      ${session.shots.length ? `<div class="table-scroll" tabindex="0" role="region" aria-label="Session ${session.id} shots"><table class="data-table"><thead><tr><th scope="col">Shot</th><th scope="col">Time</th><th scope="col">Speed (mph)</th><th scope="col">Launch (�)</th><th scope="col">Direction</th><th scope="col">Status</th></tr></thead><tbody>
        ${session.shots.map((shot) => `<tr><td><button class="shot-link" data-act="shot" data-id="${shot.id}" ${S.busy ? 'disabled' : ''}>Shot #${shot.id}</button></td><td>${escapeHtml(new Date(shot.created_at).toLocaleTimeString())}</td><td>${number(shot.speed_mph)}</td><td>${number(shot.launch_angle_deg)}</td><td>${direction(shot.launch_direction_deg)}</td><td><span class="badge">${escapeHtml(shot.status)}</span></td></tr>`).join('')}
      </tbody></table></div>` : '<p class="empty">No shots saved.</p>'}
      ${session.shot_count > session.shots.length ? '<p class="empty muted">Showing the latest 50 shots.</p>' : ''}
    </details>`).join('') : '<p>No sessions yet.</p>');
}

async function loadHistory() {
  const sessions = await api.getSessions();
  S.sessions = await Promise.all(sessions.map(async (session) => ({ ...session, shots: await api.getShots(session.id) })));
}

function render() {
  document.querySelectorAll('.tabs button').forEach((button) => {
    button.setAttribute('aria-selected', String(button.dataset.tab === S.tab));
    button.disabled = S.busy;
  });
  for (const [tab, view] of Object.entries({ session: sessionView, results: resultsView, history: historyView })) {
    const element = $('#view-' + tab);
    element.hidden = tab !== S.tab;
    if (tab === S.tab) element.innerHTML = view();
  }
  updateStatus();
  let recovery = $('#recovery');
  if (!recovery) { recovery = document.createElement('div'); recovery.id = 'recovery'; document.querySelector('main').prepend(recovery); }
  recovery.hidden = S.ready;
  recovery.innerHTML = `<p>Connection unavailable. A previous save may have completed. Reconnect and check History before repeating it.</p><button class="btn" data-act="reconnect" ${S.busy ? 'disabled' : ''}>Reconnect</button>`;
}

function updateStatus() {
  $('#status').textContent = S.busy ? 'Working…' : !S.ready ? 'Disconnected' : S.session ? 'Session active · Simulation' : 'Simulation demo';
  $('#status').classList.toggle('live', S.ready && !!S.session);
  if ($('#elapsed') && S.session) $('#elapsed').textContent = clock(Date.now() - Date.parse(S.session.started_at));
}

document.addEventListener('change', (event) => {
  if (event.target.id === 'scenario') S.scenario = event.target.value;
});
document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-act]');
  if (!button || button.disabled || S.busy) return;
  run(async () => {
    switch (button.dataset.act) {
      case 'reconnect':
        await restore();
        if (S.tab === 'history') await loadHistory();
        break;
      case 'start':
        S.session = await api.startSession();
        S.shot = null;
        break;
      case 'end':
        await api.endSession(S.session.id);
        S.session = null;
        await loadHistory();
        S.tab = 'history';
        break;
      case 'simulate':
        S.shot = await api.simulateShot(S.session.id, S.scenario);
        S.tab = 'results';
        await syncSession();
        break;
      case 'shot':
        S.shot = await api.getShot(Number(button.dataset.id));
        S.tab = 'results';
        break;
      case 'tab':
        if (button.dataset.tab === 'history') await loadHistory();
        if (button.dataset.tab === 'session') await syncSession();
        S.tab = button.dataset.tab;
        break;
    }
  });
});
setInterval(updateStatus, 1000);
async function restore() {
  await syncSession();
  const shots = await api.getShots(S.session?.id);
  S.shot = shots.length ? await api.getShot(shots[0].id) : null;
}
run(restore);
