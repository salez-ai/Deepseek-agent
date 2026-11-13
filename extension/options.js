// Skills manager. Everything that comes from a skill (names, descriptions, instructions...)
// is untrusted text, so it is only ever shown through textContent, never innerHTML.

const MAX_UPLOAD = 12 * 1024 * 1024;
const SKIP_DIRS = ['/node_modules/', '/.git/', '/__pycache__/'];
const RISK_LABEL = { read: 'read-only', write: 'writes files', exec: 'runs programs' };

const $ = (id) => document.getElementById(id);
let staged = null; // { stage_id, manifest, ... } waiting for the user's decision

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids) if (kid != null) el.append(kid);
  return el;
}

function bridge(path, body) {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage({ type: 'bridge', path, body }, (res) => {
        if (chrome.runtime.lastError) resolve({ ok: false, error: chrome.runtime.lastError.message });
        else resolve(res || { ok: false, error: 'No response from the extension.' });
      });
    } catch (e) {
      resolve({ ok: false, error: String(e.message || e) });
    }
  });
}

function notifyChanged() {
  try { chrome.storage.local.set({ skillsChangedAt: Date.now() }); } catch (e) { /* ignore */ }
}

function showMsg(text, kind) {
  const m = $('msg');
  if (!text) { m.hidden = true; return; }
  m.hidden = false;
  m.textContent = text;
  m.className = 'msg' + (kind ? ' ' + kind : '');
}

// ---------------------------------------------------------------------------
// Connection
// ---------------------------------------------------------------------------
async function refreshConn() {
  const el = $('conn');
  const res = await bridge('/ping');
  if (res.ok) {
    el.textContent = 'Bridge connected: ' + res.root;
    el.className = 'conn ok';
  } else {
    el.textContent = res.error || 'Bridge not reachable';
    el.className = 'conn err';
  }
  return res.ok;
}

// ---------------------------------------------------------------------------
// Rendering tools (used by the review panel and by installed skill details)
// ---------------------------------------------------------------------------
function renderTool(t) {
  const params = Object.entries(t.params || {}).map(([k, p]) => {
    let s = `${k} (${p.type}${p.required ? ', required' : ''}${p.enum ? ', one of ' + p.enum.join('/') : ''}${'default' in p ? ', default ' + JSON.stringify(p.default) : ''})`;
    if (p.description) s += ': ' + p.description;
    return s;
  });
  const kids = [
    h('div', { class: 'tool-head' },
      h('span', { class: 'tool-name' }, t.name),
      h('span', { class: 'badge ' + t.risk }, RISK_LABEL[t.risk] || t.risk)),
    h('p', { class: 'tool-desc' }, t.description),
  ];
  if (params.length) kids.push(h('p', { class: 'kv' }, 'Parameters: ', h('code', {}, params.join(' | '))));
  if (t.body) kids.push(h('p', { class: 'kv' }, 'Takes a text body' + (t.body.required ? ' (required)' : '') + (t.body.description ? ': ' + t.body.description : '')));
  if (t.exec) {
    kids.push(h('div', { class: 'run', title: 'Exact command template the bridge runs' },
      t.exec.join(' ')));
    kids.push(h('p', { class: 'kv' }, `Input: ${t.input} | timeout ${t.timeout}s | working folder: ${t.cwd}`));
  }
  return h('div', { class: 'tool' }, ...kids);
}

// ---------------------------------------------------------------------------
// Import
// ---------------------------------------------------------------------------
function toB64(buf) {
  const bytes = new Uint8Array(buf);
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

async function stage(payload) {
  showMsg('Reading package...', '');
  hideReview();
  const res = await bridge('/skills/stage', payload);
  if (!res.ok) { showMsg('Cannot import this skill:\n' + res.error, 'err'); return; }
  showMsg('');
  staged = res;
  renderReview(res);
}

async function importZip(file) {
  if (file.size > MAX_UPLOAD) { showMsg(`That file is ${(file.size / 1048576).toFixed(1)} MB. The limit is 12 MB.`, 'err'); return; }
  await stage({ zip: toB64(await file.arrayBuffer()) });
}

async function importFiles(entries) { // [{ path, file }]
  const keep = entries.filter((e) => !SKIP_DIRS.some((d) => ('/' + e.path).includes(d)));
  if (!keep.length) { showMsg('That folder is empty.', 'err'); return; }
  if (keep.length > 500) { showMsg(`That folder has ${keep.length} files. A skill can have at most 500.`, 'err'); return; }
  const total = keep.reduce((n, e) => n + e.file.size, 0);
  if (total > MAX_UPLOAD) { showMsg(`That folder is ${(total / 1048576).toFixed(1)} MB. The limit is 12 MB.`, 'err'); return; }
  const files = [];
  for (const e of keep) files.push({ path: e.path, data: toB64(await e.file.arrayBuffer()) });
  await stage({ files });
}

function readEntry(entry, prefix = '') {
  return new Promise((resolve) => {
    if (entry.isFile) {
      entry.file((file) => resolve([{ path: prefix + entry.name, file }]), () => resolve([]));
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      const all = [];
      const pump = () => reader.readEntries(async (batch) => {
        if (!batch.length) {
          const nested = await Promise.all(all.map((e) => readEntry(e, prefix + entry.name + '/')));
          resolve(nested.flat());
        } else { all.push(...batch); pump(); }
      }, () => resolve([]));
      pump();
    } else resolve([]);
  });
}

async function handleDrop(dt) {
  const items = [...(dt.items || [])].map((i) => (i.webkitGetAsEntry ? i.webkitGetAsEntry() : null)).filter(Boolean);
  const files = [...dt.files];
  if (items.length === 1 && items[0].isDirectory) {
    await importFiles(await readEntry(items[0]));
  } else if (files.length === 1 && /\.(zip|dsskill)$/i.test(files[0].name)) {
    await importZip(files[0]);
  } else {
    showMsg('Drop one .zip file or one skill folder.', 'err');
  }
}

function hideReview() {
  $('review').hidden = true;
  staged = null;
}

function renderReview(r) {
  const m = r.manifest;
  $('r-title').textContent = m.name;
  $('r-meta').textContent = `${m.id} | v${m.version}` + (m.author ? ` | by ${m.author}` : '') + (m.license ? ` | ${m.license}` : '');
  $('r-desc').textContent = m.description;
  const rep = $('r-replace');
  if (r.replaces) {
    rep.hidden = false;
    rep.textContent = `Replaces v${r.replaces.version}` + (r.replaces.same_content ? ' (identical)' : '');
  } else rep.hidden = true;

  const w = $('r-warnings');
  w.replaceChildren(...r.warnings.map((x) => h('li', {}, x)));

  $('r-toolcount').textContent = m.tools.length ? `(${m.tools.length})` : '';
  const tools = $('r-tools');
  tools.replaceChildren(...(m.tools.length ? m.tools.map(renderTool) : [h('p', { class: 'muted' }, 'No tools: this skill only adds instructions.')]));

  $('r-ichars').textContent = String(m.instructions_chars);
  $('r-instr').textContent = m.instructions || '(none)';
  $('r-filecount').textContent = String(r.files.length);
  $('r-files').textContent = r.files.map((f) => `${f.path}  (${f.size} B)`).join('\n');

  $('r-install').textContent = r.replaces ? 'Replace' : 'Install';
  $('review').hidden = false;
  $('review').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

async function commit() {
  if (!staged) return;
  const btn = $('r-install');
  btn.disabled = true;
  const res = await bridge('/skills/commit', { stage_id: staged.stage_id, enable: $('r-enable').checked });
  btn.disabled = false;
  if (!res.ok) { showMsg(res.error, 'err'); hideReview(); return; }
  hideReview();
  showMsg(`Installed ${res.name} v${res.version}` + (res.enabled ? ' and enabled it.' : '. It is disabled until you switch it on.'), 'ok');
  notifyChanged();
  await refreshList();
}

// ---------------------------------------------------------------------------
// Installed skills
// ---------------------------------------------------------------------------
function skillCard(s) {
  if (s.broken) {
    return h('article', { class: 'skill' },
      h('h3', {}, s.id), h('p', { class: 'muted' }, 'This skill is damaged (its manifest cannot be read).'),
      h('div', { class: 'row' }, h('button', { class: 'btn danger', type: 'button', onclick: () => remove(s) }, 'Remove')));
  }
  const hasRead = s.tools.some((t) => t.risk === 'read');
  const enabled = h('input', { type: 'checkbox', checked: s.enabled, id: `en-${s.id}` });
  enabled.addEventListener('change', () => setState(s.id, { enabled: enabled.checked }));
  const trusted = h('input', { type: 'checkbox', checked: s.trusted, id: `tr-${s.id}`, disabled: !hasRead });
  trusted.addEventListener('change', () => setState(s.id, { trusted: trusted.checked }));

  const chips = s.tools.map((t) => h('span', { class: 'chip', title: t.description }, t.name));
  const body = [
    h('div', { class: 'skill-head' },
      h('div', {},
        h('div', { class: 'skill-title' }, h('h3', {}, s.name), h('span', { class: 'muted' }, `v${s.version}`),
          s.enabled ? null : h('span', { class: 'badge' }, 'disabled')),
        h('p', { class: 'muted' }, `${s.id}` + (s.author ? ` | by ${s.author}` : '')),
        h('p', {}, s.description))),
  ];
  if (chips.length) body.push(h('div', { class: 'chips' }, ...chips));
  else body.push(h('p', { class: 'hint' }, 'Instructions only. No tools.'));
  if (s.conflicts && s.conflicts.length) {
    body.push(h('p', { class: 'warnings', style: 'margin-top:8px' }, `Not active: ${s.conflicts.join(', ')} already provided by another enabled skill.`));
  }
  body.push(h('div', { class: 'skill-controls' },
    h('label', { class: 'check', for: `en-${s.id}` }, enabled, 'Enabled'),
    h('label', { class: 'check', for: `tr-${s.id}`, title: 'Skips the approval prompt for tools this skill declares read-only' },
      trusted, 'Trusted'),
    h('span', { class: 'hint' }, hasRead ? 'Trusted skills run their read-only tools without asking.' : 'Trust only applies to read-only tools.'),
    h('span', { style: 'flex:1' }),
    h('button', { class: 'btn danger', type: 'button', onclick: () => remove(s) }, 'Remove')));
  const details = h('details', { class: 'fold' }, h('summary', {}, 'Details'));
  details.append(...(s.tools.length ? [h('h4', {}, 'Tools'), ...s.tools.map(renderTool)] : []),
    h('h4', {}, `Instructions (${s.instructions_chars} characters)`), h('pre', { class: 'code' }, s.instructions || '(none)'));
  body.push(details);
  return h('article', { class: 'skill' }, ...body);
}

async function refreshList() {
  const res = await bridge('/skills');
  const list = $('list');
  if (!res.ok) {
    list.replaceChildren();
    $('empty').hidden = false;
    $('empty').textContent = res.error || 'Could not load skills.';
    return;
  }
  $('empty').hidden = res.skills.length > 0;
  $('empty').textContent = 'No skills installed yet.';
  list.replaceChildren(...res.skills.map(skillCard));
}

async function setState(id, change) {
  const res = await bridge('/skills/set', { id, ...change });
  if (!res.ok) showMsg(res.error, 'err');
  notifyChanged();
  await refreshList();
}

async function remove(s) {
  if (!confirm(`Remove "${s.name || s.id}" and delete its files?`)) return;
  const res = await bridge('/skills/remove', { id: s.id });
  showMsg(res.ok ? `Removed ${s.name || s.id}.` : res.error, res.ok ? 'ok' : 'err');
  notifyChanged();
  await refreshList();
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------
$('pick-zip').addEventListener('click', () => $('in-zip').click());
$('pick-dir').addEventListener('click', () => $('in-dir').click());
$('in-zip').addEventListener('change', async (e) => {
  const f = e.target.files[0];
  e.target.value = '';
  if (f) await importZip(f);
});
$('in-dir').addEventListener('change', async (e) => {
  const entries = [...e.target.files].map((f) => ({ path: f.webkitRelativePath || f.name, file: f }));
  e.target.value = '';
  if (entries.length) await importFiles(entries);
});
const drop = $('drop');
['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', (e) => handleDrop(e.dataTransfer));
$('r-cancel').addEventListener('click', () => { hideReview(); showMsg(''); });
$('r-install').addEventListener('click', commit);

(async () => {
  if (await refreshConn()) await refreshList();
  else {
    $('empty').hidden = false;
    $('empty').textContent = 'Start the bridge and set the token in the extension popup, then reload this page.';
  }
})();
