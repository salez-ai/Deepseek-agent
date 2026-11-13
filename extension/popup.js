const DEFAULTS = { bridgeUrl: 'http://127.0.0.1:8765', token: '' };
const $ = (id) => document.getElementById(id);

function show(text, cls) {
  const m = $('msg');
  m.textContent = text;
  m.className = cls || '';
}

async function test() {
  show('Testing...');
  const res = await chrome.runtime.sendMessage({ type: 'bridge', path: '/ping' });
  if (res && res.ok) show('Connected. Project: ' + res.root, 'ok');
  else show((res && res.error) || 'No response', 'err');
}

const OPTIONS = { collapseThinking: true, collapseTools: true, collapseAgentMsgs: true, theme: true };

(async () => {
  const opts = await chrome.storage.local.get(OPTIONS);   // saved the moment you tick a box
  for (const key of Object.keys(OPTIONS)) {
    const box = $(key);
    box.checked = !!opts[key];
    box.addEventListener('change', () => chrome.storage.local.set({ [key]: box.checked }));
  }
  const saved = await chrome.storage.local.get(DEFAULTS);
  $('url').value = saved.bridgeUrl;
  $('token').value = saved.token;
  if (saved.token) test();
})();

$('save').addEventListener('click', async () => {
  const bridgeUrl = $('url').value.trim() || DEFAULTS.bridgeUrl;
  if (!/^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?\/?$/.test(bridgeUrl)) {
    show('The address must be http://127.0.0.1:<port> or http://localhost:<port>', 'err');
    return;
  }
  await chrome.storage.local.set({ bridgeUrl, token: $('token').value.trim() });
  test();
});

$('open-deepseek').addEventListener('click', () => {
  chrome.tabs.create({ url: 'https://chat.deepseek.com' });
});

// ---- alerts ------------------------------------------------------------------------------------
const ALERT = DSAgentAlert.DEFAULTS;
(async () => {
  const a = await chrome.storage.local.get(ALERT);
  $('alertSound').checked = !!a.alertSound;
  $('alertNotify').checked = !!a.alertNotify;
  $('alertTone').value = a.alertTone;
  $('alertVolume').value = Math.round(a.alertVolume * 100);
  const rep = $('alertRepeatSec');
  if (![...rep.options].some((o) => o.value === String(a.alertRepeatSec))) {   // a value set elsewhere, e.g. 45
    const o = new Option(`after ${a.alertRepeatSec} s`, String(a.alertRepeatSec));
    rep.add(o);
  }
  rep.value = String(a.alertRepeatSec);
  for (const key of Object.values(DSAgentAlert.KIND_SETTING)) $(key).checked = a[key] !== false;
  DSAgentAlert.configure(a);
})();
for (const key of Object.values(DSAgentAlert.KIND_SETTING)) {
  $(key).addEventListener('change', (e) => saveAlert({ [key]: e.target.checked }));
}
const saveAlert = (patch) => { chrome.storage.local.set(patch); DSAgentAlert.configure(patch); };
$('alertSound').addEventListener('change', (e) => saveAlert({ alertSound: e.target.checked }));
$('alertNotify').addEventListener('change', (e) => saveAlert({ alertNotify: e.target.checked }));
$('alertTone').addEventListener('change', (e) => { saveAlert({ alertTone: e.target.value }); DSAgentAlert.test('approval'); });
$('alertVolume').addEventListener('input', (e) => saveAlert({ alertVolume: +e.target.value / 100 }));
$('alertVolume').addEventListener('change', () => DSAgentAlert.test('approval'));
$('alertRepeatSec').addEventListener('change', (e) => saveAlert({ alertRepeatSec: +e.target.value }));
$('testSound').addEventListener('click', () => DSAgentAlert.test('approval'));
$('testNotify').addEventListener('click', () => DSAgentAlert.test('notification'));

// ---- image generation -----------------------------------------------------------------------------
const IMG = { imageGen: true, imageGenUrl: 'https://perchance.org/5jsm6cis4r', imageGenMode: 'auto', imageGenTimeoutSec: 120 };
const goodGeneratorUrl = (t) => {
  try {
    const u = new URL(t);
    return u.protocol === 'https:' || (u.protocol === 'http:' && (u.hostname === '127.0.0.1' || u.hostname === 'localhost'));
  } catch (e) { return false; }
};
function genStatus(text, cls) { const m = $('genMsg'); m.textContent = text; m.className = 'gen-msg ' + (cls || ''); }
(async () => {
  const g = await chrome.storage.local.get(IMG);
  $('imageGen').checked = !!g.imageGen;
  $('imageGenUrl').value = g.imageGenUrl;
  $('imageGenMode').value = g.imageGenMode;
  $('imageGenTimeoutSec').value = String(g.imageGenTimeoutSec);
})();
$('imageGen').addEventListener('change', (e) => chrome.storage.local.set({ imageGen: e.target.checked }));
$('imageGenMode').addEventListener('change', (e) => chrome.storage.local.set({ imageGenMode: e.target.value }));
$('imageGenTimeoutSec').addEventListener('change', (e) => chrome.storage.local.set({ imageGenTimeoutSec: +e.target.value }));
$('imageGenUrl').addEventListener('change', (e) => {
  const v = e.target.value.trim();
  if (!goodGeneratorUrl(v)) { genStatus('The address must start with https:// (or http://localhost).', 'err'); return; }
  genStatus('Saved.', 'ok');
  chrome.storage.local.set({ imageGenUrl: v });
});
$('testGen').addEventListener('click', async () => {
  $('genThumb').hidden = true;
  if (!goodGeneratorUrl($('imageGenUrl').value.trim())) { genStatus('Fix the generator address first.', 'err'); return; }
  await chrome.storage.local.set({ imageGenUrl: $('imageGenUrl').value.trim() });
  $('testGen').disabled = true;
  genStatus('Asking the generator for a test picture... (up to a minute)');
  const r = await chrome.runtime.sendMessage({ type: 'generateImage', test: true, params: { prompt: 'a small red apple on a white table, simple illustration', size: 'square' } });
  $('testGen').disabled = false;
  if (r && r.ok) {
    genStatus(`It works: a picture arrived in ${r.seconds} s (${r.how}).`, 'ok');
    $('genThumb').src = r.dataUrl;
    $('genThumb').hidden = false;
  } else {
    genStatus((r && r.error) || 'No answer from the extension.', 'err');
  }
});

$('skills').addEventListener('click', () => { chrome.runtime.openOptionsPage(); window.close(); });
