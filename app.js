const $ = id => document.getElementById(id);
const url = $('url'), rights = $('rights'), go = $('go');
let tab = 'url', ratio = '9:16', layout = 'fit', style = 'word', tpos = 'top', mode = 'login', shown = -1;
const valid = () => /^https?:\/\/((www|m|clips)\.)?(youtube\.com|youtu\.be|twitch\.tv|kick\.com)\//i.test(url.value.trim());
const sync = () => {
  const ok = tab === 'url' ? valid() : $('file').files.length > 0;
  go.disabled = !(ok && rights.checked);
  $('urlerr').hidden = tab !== 'url' || !url.value.trim() || valid();
};
const wait = ms => new Promise(r => setTimeout(r, ms));
const mmss = s => Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');

url.addEventListener('input', sync); rights.addEventListener('change', sync); $('file').addEventListener('change', sync);
function setTab(t) {
  tab = t; $('tab-url').setAttribute('aria-selected', t === 'url'); $('tab-file').setAttribute('aria-selected', t === 'file');
  $('p-url').hidden = t !== 'url'; $('p-file').hidden = t !== 'file'; sync();
}
$('tab-url').onclick = () => setTab('url'); $('tab-file').onclick = () => setTab('file');
$('form').addEventListener('click', e => {
  const b = e.target.closest('.tile'); if (!b) return;
  b.parentElement.querySelectorAll('.tile').forEach(t => t.classList.toggle('on', t === b));
  ratio = b.dataset.ratio || ratio; layout = b.dataset.layout || layout; style = b.dataset.style || style; tpos = b.dataset.tpos || tpos;
});
const sec = v => { const p = v.trim().split(':').map(Number); return !v.trim() || p.some(isNaN) ? 0 : p.reduce((a, n) => a * 60 + n, 0); };
let pt; url.addEventListener('input', () => {
  clearTimeout(pt); $('prev').hidden = true; if (!valid()) return;
  pt = setTimeout(async () => { try { const r = await fetch('/api/preview?url=' + encodeURIComponent(url.value.trim()));
    if (!r.ok) return; const d = await r.json(); $('pimg').src = d.thumb; $('ptitle').textContent = d.title; $('prev').hidden = false; } catch (e) {} }, 500);
});
$('menu').onclick = () => { const o = $('nav').hidden; $('nav').hidden = !o; $('menu').setAttribute('aria-expanded', o); };
$('nav').addEventListener('click', e => { if (e.target.tagName === 'A') $('nav').hidden = true; });

function show(title, msg, pct) {
  $('status').hidden = false; $('stitle').textContent = title; $('smsg').textContent = msg;
  $('fill').style.width = pct + '%'; document.querySelector('.bar').setAttribute('aria-valuenow', pct);
}
function render(clips) {
  if (clips.length === shown) return; shown = clips.length;
  $('clips').replaceChildren(...clips.map((c, i) => {
    const el = document.createElement('div'); el.className = 'clip';
    const v = Object.assign(document.createElement('video'), { controls: true, preload: 'metadata', src: c.url });
    const d = document.createElement('div'), t = document.createElement('b'), s = document.createElement('small');
    t.textContent = `Clip ${i + 1}`;
    if (c.score) { const bd = document.createElement('span'); bd.className = 'badge'; bd.textContent = `Puntuación ${c.score}/10`; t.append(' ', bd); }
     s.textContent = `${mmss(c.start)} – ${mmss(c.end)} · ${c.title}`;
    const a = Object.assign(document.createElement('a'), { href: c.url, download: `clipforge-${i + 1}.mp4`, textContent: 'Descargar MP4' });
    d.append(t, s, a); el.append(v, d); return el;
  }));
  $('results').hidden = !clips.length;
}
async function poll(id) {
  let fails = 0; shown = -1;
  for (;;) {
    let d;
    try {
      const r = await fetch('/api/jobs/' + id);
      if (r.status === 404) throw new Error('El servidor se reinició o se quedó sin memoria. Vuelve a intentarlo con un video más corto.');
      if (!r.ok) throw new Error((await r.json()).detail || 'Error del servidor');
      d = await r.json(); fails = 0;
    } catch (e) { if (e.message.startsWith('El servidor') || ++fails > 4) throw e; await wait(3000); continue; }
    render(d.clips);
    if (d.state === 'error') throw new Error(d.message);
    if (d.state === 'done') { show('Clips listos', d.message, 100); return loadProjects(); }
    show('Creando tus clips…', d.message + (d.clips.length ? ' Los clips listos ya se pueden ver.' : ''), d.progress || 0);
    await wait(2000);
  }
}
$('form').addEventListener('submit', async e => {
  e.preventDefault(); if (go.disabled) return;
  go.disabled = true; $('results').hidden = true; show('Enviando…', 'Preparando el trabajo.', 2);
  const o = { rights: rights.checked, count: +$('count').value, max_duration: +$('duration').value,
    ratio, quality: +$('quality').value, captions: $('captions').checked, title: $('autotitle').checked, title_pos: tpos, phrases: $('phrases').checked, zoom: $('zoomauto').checked, keycolor: $('keycolor').checked, layout, style,
    topic: $('topic').value.trim(), start_at: sec($('from').value), end_at: sec($('to').value) };
  const f = $('file').files[0];
  try {
    let r;
    if (tab === 'file') {
      show('Subiendo el video…', 'No cierres esta página hasta que termine.', 3);
      r = await fetch('/api/upload?' + new URLSearchParams(o), { method: 'POST', body: f });
    } else {
      r = await fetch('/api/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ youtube_url: url.value.trim(), ...o }) });
    }
    const d = await r.json();
    if (r.status === 401) setUser(null);
    if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : 'Revisa los datos del formulario.');
    await poll(d.job_id);
  } catch (err) { show('No se pudo completar', err.message, 0); }
  finally { sync(); }
});

async function loadProjects() {
  const r = await fetch('/api/jobs'); if (!r.ok) return;
  const list = await r.json(); $('projects').hidden = !list.length;
  $('plist').replaceChildren(...list.map(p => {
    const b = document.createElement('button'); b.type = 'button'; b.className = 'proj';
    const t = document.createElement('span'); t.textContent = p.title;
    const s = document.createElement('small'); s.textContent = p.state === 'done' ? `${p.clips} clips` : p.state === 'error' ? 'Error' : 'En curso';
    b.append(t, s);
    b.onclick = async () => { shown = -1; const d = await (await fetch('/api/jobs/' + p.id)).json(); render(d.clips);
      show(d.state === 'done' ? 'Clips listos' : 'Proyecto', d.message, d.progress || 0); scrollTo({ top: 0, behavior: 'smooth' }); };
    return b;
  }));
}

function setUser(email) {
  $('auth').hidden = !!email; $('form').hidden = !email; $('who').hidden = !email; $('menu').hidden = !email;
  $('who').textContent = email || ''; $('nav').hidden = true;
  if (!email) { $('status').hidden = true; $('results').hidden = true; $('projects').hidden = true; } else loadProjects();
}
function setMode(m) {
  mode = m; const reg = m === 'register';
  $('atitle').textContent = reg ? 'Crea tu cuenta' : 'Inicia sesión';
  $('abtn').textContent = reg ? 'Crear cuenta' : 'Entrar';
  $('aalt').textContent = reg ? '¿Ya tienes cuenta?' : '¿Aún no tienes cuenta?';
  $('aswitch').textContent = reg ? 'Iniciar sesión' : 'Crear cuenta';
  $('apass').autocomplete = reg ? 'new-password' : 'current-password'; $('aerr').hidden = true;
}
$('aswitch').onclick = () => setMode(mode === 'login' ? 'register' : 'login');
$('auth').addEventListener('submit', async e => {
  e.preventDefault(); $('abtn').disabled = true; $('aerr').hidden = true;
  try {
    const r = await fetch('/api/' + mode, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: $('aemail').value, password: $('apass').value }) });
    const d = await r.json();
    if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : 'Revisa el correo y usa una contraseña de 8 caracteres o más.');
    $('apass').value = ''; setUser(d.email);
  } catch (err) { $('aerr').textContent = err.message; $('aerr').hidden = false; }
  finally { $('abtn').disabled = false; }
});
$('out').onclick = async () => { await fetch('/api/logout', { method: 'POST' }); setUser(null); };
setUser(null); fetch('/api/me').then(r => r.ok ? r.json() : null).then(d => d && setUser(d.email));
