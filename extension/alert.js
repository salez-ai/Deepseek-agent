// Alerts: tell the person when the agent is waiting for them.
//
//   approval  a command/tool needs your Allow
//   continue  DeepSeek stopped mid-reply and shows its "Continue" button
//   paused    the agent paused after its automatic steps
//   error     the agent stopped because of a problem
//   done      the agent finished and waits for your next message (only when you are away)
//
// Each alert can play a ringtone (synthesised with Web Audio, so there is no audio file and
// nothing for the page's security policy to block), show a desktop notification (through the
// background worker), mark the tab title, and repeat as a reminder until you act.
//
// Runs as a content script on the chat page and also on the extension's popup (for "Test").

(function (root) {
  'use strict';

  const DEFAULTS = {
    alertSound: true, alertNotify: true, alertTone: 'chime', alertVolume: 0.6, alertRepeatSec: 60,
    // which events alert you at all (sound, notification and title mark)
    alertOnApproval: true, alertOnContinue: true, alertOnPaused: true, alertOnError: true, alertOnDone: true,
  };
  const KIND_SETTING = { approval: 'alertOnApproval', continue: 'alertOnContinue', paused: 'alertOnPaused',
                         error: 'alertOnError', done: 'alertOnDone' };
  const TONES = ['chime', 'bell', 'digital'];
  const URGENT = new Set(['approval', 'continue', 'paused', 'error']);
  const MAX_REMINDERS = 3;
  const TITLE_MARK = '\u{1F514} ';
  const TITLE_SEP = ' \u00b7 ';

  // ---------------------------------------------------------------------
  // Sound design. Pure: works on any BaseAudioContext, including an
  // OfflineAudioContext, which is how the tests measure it.
  // ---------------------------------------------------------------------
  function strike(ctx, dest, freq, t0, o) {
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.type = o.type || 'sine';
    osc.frequency.value = freq;
    const attack = o.attack || 0.012;
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(o.peak, t0 + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + attack + o.decay);
    osc.connect(g);
    g.connect(dest);
    osc.start(t0);
    osc.stop(t0 + attack + o.decay + 0.05);
    return t0 + attack + o.decay;
  }

  // Each tone is scaled so that all three are about equally loud at the same volume setting,
  // and the loudest peak at full volume stays safely below clipping.
  const TONE_GAIN = { chime: 1.6, bell: 1.1, digital: 1.7 };

  // Returns the duration in seconds. `urgent` plays the pattern twice; `soft` is quieter and lower.
  function ringtone(ctx, tone, opts) {
    const volume = Math.max(0, Math.min(1, Number(opts && opts.volume != null ? opts.volume : 0.6)));
    const urgent = !!(opts && opts.urgent);
    const soft = !!(opts && opts.soft);
    const master = ctx.createGain();
    master.gain.value = volume * (soft ? 0.7 : 0.9) * (TONE_GAIN[tone] || 1);
    master.connect(ctx.destination);

    const start = ctx.currentTime + 0.03;
    const repeats = urgent ? 2 : 1;
    let end = start;

    for (let r = 0; r < repeats; r++) {
      let t = start + r * (tone === 'digital' ? 0.62 : 0.95);
      if (tone === 'bell') {
        const base = soft ? 659.25 : 783.99;
        [[1, 0.5, 1.5], [2, 0.22, 1.0], [2.76, 0.16, 0.8], [5.4, 0.08, 0.5]].forEach(([ratio, peak, decay]) => {
          end = Math.max(end, strike(ctx, master, base * ratio, t, { peak, decay }));
        });
      } else if (tone === 'digital') {
        const f = soft ? 784 : 988;
        for (let i = 0; i < 3; i++) {
          end = Math.max(end, strike(ctx, master, f, t + i * 0.16, { type: 'triangle', peak: 0.55, attack: 0.01, decay: 0.09 }));
        }
      } else {            // chime: two rising notes with a soft octave above
        const [a, b] = soft ? [523.25, 783.99] : [659.25, 880];
        end = Math.max(end, strike(ctx, master, a, t, { peak: 0.5, decay: 0.5 }));
        end = Math.max(end, strike(ctx, master, a * 2, t, { peak: 0.1, decay: 0.28 }));
        end = Math.max(end, strike(ctx, master, b, t + 0.17, { peak: 0.5, decay: 0.7 }));
        end = Math.max(end, strike(ctx, master, b * 2, t + 0.17, { peak: 0.1, decay: 0.35 }));
      }
    }
    return end - start;
  }

  function playTone(ctx, kind, settings) {
    const s = Object.assign({}, DEFAULTS, settings || {});
    const tone = TONES.includes(s.alertTone) ? s.alertTone : 'chime';
    return ringtone(ctx, tone, { volume: s.alertVolume, urgent: URGENT.has(kind), soft: kind === 'done' });
  }

  // ---------------------------------------------------------------------
  // Runtime
  // ---------------------------------------------------------------------
  const state = {
    settings: Object.assign({}, DEFAULTS),
    ctx: null,
    pending: new Map(),          // kind -> { info, reminders, timer }
    stats: { sounds: 0, notifications: 0, lastKind: '', audio: 'none' },
    el: null,                    // optional element that mirrors the state in data-* attributes (used by tests)
    listeners: [],
  };

  function doc() { return root.document; }

  function away() {
    const d = doc();
    return !d || d.hidden || !d.hasFocus();
  }

  function ensureContext() {
    if (state.ctx) return state.ctx;
    const AC = root.AudioContext || root.webkitAudioContext;
    if (!AC) return null;
    try { state.ctx = new AC(); } catch (e) { return null; }
    return state.ctx;
  }

  // Browsers only allow sound after the person has interacted with the page.
  function unlock() {
    const c = ensureContext();
    if (c && c.state === 'suspended') c.resume().catch(() => {});
    publish();
  }

  function publish() {
    state.stats.audio = state.ctx ? state.ctx.state : 'none';
    if (!state.el) return;
    state.el.dataset.alertPending = [...state.pending.keys()].join(',');
    state.el.dataset.alertSounds = String(state.stats.sounds);
    state.el.dataset.alertNotifs = String(state.stats.notifications);
    state.el.dataset.alertLast = state.stats.lastKind;
    state.el.dataset.audioState = state.stats.audio;
  }

  function playSound(kind) {
    const c = ensureContext();
    if (!c) return false;
    if (c.state === 'suspended') c.resume().catch(() => {});
    try { playTone(c, kind, state.settings); } catch (e) { return false; }
    state.stats.sounds++;
    return true;
  }

  function sendNotification(kind, info, test) {
    try {
      root.chrome.runtime.sendMessage({
        type: 'notify', kind, test: !!test, urgent: URGENT.has(kind),
        title: info.title, message: info.message,
      }, () => { void root.chrome.runtime.lastError; });
      state.stats.notifications++;
    } catch (e) { /* extension was reloaded */ }
  }

  function baseTitle(t) {
    const i = t.startsWith(TITLE_MARK) ? t.indexOf(TITLE_SEP) : -1;
    return i >= 0 ? t.slice(i + TITLE_SEP.length) : t;
  }

  function markTitle(short) {
    const d = doc();
    if (!d || !away()) return;
    d.title = TITLE_MARK + short + TITLE_SEP + baseTitle(d.title);
  }

  function restoreTitle() {
    const d = doc();
    if (d && d.title.startsWith(TITLE_MARK)) d.title = baseTitle(d.title);
  }

  function fire(kind, info, isReminder) {
    state.stats.lastKind = kind;
    if (state.settings.alertSound) playSound(kind);
    if (state.settings.alertNotify && away()) {
      sendNotification(kind, isReminder ? Object.assign({}, info, { title: 'Still waiting: ' + info.title }) : info, false);
    }
    markTitle(info.short || info.title);
    publish();
  }

  function scheduleReminder(kind) {
    const entry = state.pending.get(kind);
    const secs = Number(state.settings.alertRepeatSec);
    if (!entry || !URGENT.has(kind) || !(secs >= 2) || entry.reminders >= MAX_REMINDERS) return;
    entry.timer = setTimeout(() => {
      const cur = state.pending.get(kind);
      if (!cur) return;
      cur.reminders++;
      fire(kind, cur.info, true);
      scheduleReminder(kind);
    }, secs * 1000);
  }

  // info: { title, message, short }
  function notify(kind, info) {
    if (state.settings[KIND_SETTING[kind]] === false) return false;   // you turned this event off
    if (kind === 'done' && !away()) return false;         // you are looking at the page
    const existing = state.pending.get(kind);
    if (existing) { existing.info = info; return false; } // already alerting for this
    state.pending.set(kind, { info, reminders: 0, timer: null });
    fire(kind, info, false);
    scheduleReminder(kind);
    return true;
  }

  function clear(kind) {
    const entry = state.pending.get(kind);
    if (!entry) return;
    clearTimeout(entry.timer);
    state.pending.delete(kind);
    try { root.chrome.runtime.sendMessage({ type: 'clearNotify', kind }, () => { void root.chrome.runtime.lastError; }); } catch (e) { /* reloaded */ }
    if (!state.pending.size) restoreTitle();
    publish();
  }

  function clearAll() {
    for (const kind of [...state.pending.keys()]) clear(kind);
  }

  // "Test" buttons in the popup
  function test(what) {
    unlock();
    if (what === 'notification') {
      sendNotification('approval', { title: 'DeepSeek Agent', message: 'This is how a notification looks. Click it to jump to DeepSeek.' }, true);
      return;
    }
    playSound(what === 'done' ? 'done' : 'approval');
    publish();
  }

  function configure(partial) {
    for (const k of Object.keys(DEFAULTS)) if (partial && partial[k] !== undefined) state.settings[k] = partial[k];
    for (const [kind, key] of Object.entries(KIND_SETTING)) if (state.settings[key] === false) clear(kind);   // switched off: stop alerting
    state.listeners.forEach((fn) => { try { fn(state.settings); } catch (e) { /* ignore */ } });
    publish();
  }

  function init() {
    try {
      root.chrome.storage.local.get(DEFAULTS).then(configure);
      root.chrome.storage.onChanged.addListener((changes, area) => {
        if (area !== 'local') return;
        const next = {};
        for (const k of Object.keys(DEFAULTS)) if (changes[k]) next[k] = changes[k].newValue;
        if (Object.keys(next).length) configure(next);
      });
    } catch (e) { /* no storage (tests): defaults apply */ }
    const d = doc();
    if (d) {
      ['pointerdown', 'keydown', 'click', 'touchstart'].forEach((ev) =>
        d.addEventListener(ev, (e) => { if (e.isTrusted) unlock(); }, { capture: true, passive: true }));
      root.addEventListener('focus', restoreTitle);
    }
  }

  root.DSAgentAlert = {
    DEFAULTS, TONES, URGENT, KIND_SETTING,
    ringtone, playTone, init, notify, clear, clearAll, test, configure, unlock,
    attach(el) { state.el = el; publish(); },
    onSettings(fn) { state.listeners.push(fn); },
    get settings() { return state.settings; },
    debug() {
      return { pending: [...state.pending.keys()], audio: state.ctx ? state.ctx.state : 'none', sounds: state.stats.sounds,
               notifications: state.stats.notifications, away: away(), settings: Object.assign({}, state.settings) };
    },
  };
})(typeof window !== 'undefined' ? window : globalThis);
