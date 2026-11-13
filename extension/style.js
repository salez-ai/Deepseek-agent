// Restyles the DeepSeek page:
//  - collapses the "thinking" block by default
//  - shows tool calls (and the agent's own messages) as collapsible cards
//  - applies a cleaner typographic theme (theme.css)
//
// Nothing here edits DeepSeek's own nodes beyond hiding some of them with an
// attribute. The cards are separate elements inserted next to the originals,
// and the originals stay in the DOM so the agent loop can still read them.

(function (root) {
  'use strict';

  const DEFAULTS = { collapseThinking: true, collapseTools: true, collapseAgentMsgs: true, theme: true };

  const SEL = {
    reply: '.ds-markdown',
    thinkContent: '.ds-think-content, [class*="think-content"]',
    // reasoning ("DeepThink") text is never treated as an answer
    ignoreInside: '.ds-think-content, [class*="ds-think"], [class*="think-content"]',
  };
  const PROMPT_PREFIX = 'You are a coding agent working on a real project';
  const SKILLS_PREFIX = 'Skills update for the coding agent';
  const RESUME_PREFIX = 'Resuming the coding agent session';
  const THINK_HEADER = /^(thought for\b|thinking\b|deep ?think|reasoning|\u5df2\u601d\u8003|\u601d\u8003\u4e2d|\u6df1\u5ea6\u601d\u8003)/i;
  const MAX_SHOWN = 20000;

  const state = {
    settings: Object.assign({}, DEFAULTS),
    active: false,
    results: new Map(),     // block hash -> [{status, text}] in execution order
    history: new Set(),     // "hash:occurrence" of tool calls that were already in the chat when the session began
    thinkFirstSeen: new WeakMap(),
    timer: null,
  };

  // ---- shared helpers (content.js uses these too, so hashes always agree) ----
  function hash(s) {
    let h = 5381;
    for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
    return (h >>> 0).toString(36) + ':' + s.length;
  }

  function preText(pre) {
    let t = pre.textContent || '';
    if (!t.includes('\n') && pre.innerText && pre.innerText.includes('\n')) t = pre.innerText;
    return t;
  }

  // Text of a reply WITHOUT our own cards, so adding/updating a card never looks like
  // DeepSeek changing its answer.
  function replyText(el) {
    if (!el.querySelector('.dsa-ui')) return el.textContent || '';
    const copy = el.cloneNode(true);
    copy.querySelectorAll('.dsa-ui').forEach((n) => n.remove());
    return copy.textContent || '';
  }

  function replyEls() {
    return [...document.querySelectorAll(SEL.reply)].filter((e) => !e.closest(SEL.ignoreInside));
  }

  function isToolBlockText(t) {
    return /^\s*name:\s*[a-z][a-z0-9_]*\s*(\n|$)/i.test(t);
  }

  function make(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function clip(t) {
    t = String(t || '');
    return t.length > MAX_SHOWN ? t.slice(0, MAX_SHOWN) + '\n[...cut for display]' : t;
  }

  // ---- results registry: content.js reports what each tool call did ----
  function start(blockText) {
    const k = hash(blockText);
    const rec = { status: 'running', text: '' };
    const list = state.results.get(k) || [];
    list.push(rec);
    state.results.set(k, list);
    schedule();
    return {
      finish(ok, text) {
        rec.status = ok ? 'ok' : 'err';
        rec.text = clip(text);
        schedule();
      },
      // [{ path, url, error }] taken when the tool finished, so an old card keeps showing the old picture
      attachImages(list) {
        rec.images = list;
        schedule();
      },
    };
  }

  function setActive(on) {
    state.active = !!on;
    schedule();
  }

  // Tool calls already on the page when a session starts or resumes are history: they ran in an earlier
  // session (or never ran) and must not look like work that is still queued.
  function markHistory() {
    state.history = new Set();
    const seen = new Map();
    for (const reply of replyEls()) {
      reply.querySelectorAll('pre').forEach((pre) => {
        if (pre.closest('.dsa-ui')) return;
        const text = preText(pre);
        if (!isToolBlockText(text)) return;
        const k = hash(text);
        const n = seen.get(k) || 0;
        seen.set(k, n + 1);
        state.history.add(k + ':' + n);
      });
    }
    schedule();
  }

  // ---- cards ----------------------------------------------------------------
  function makeCard(kind) {
    const d = make('details', 'dsa-ui dsa-tool');
    d.dataset.kind = kind;
    const s = make('summary', 'dsa-tsum');
    s.append(make('span', 'dsa-chev'), make('span', 'dsa-tname'), make('span', 'dsa-targ'), make('span', 'dsa-chip'));
    d.append(s, make('div', 'dsa-tbody'));
    return d;
  }

  function previews(images) {
    const wrap = make('div', 'dsa-previews');
    for (const im of images) {
      const fig = make('figure', 'dsa-fig');
      if (im.url) {
        const img = make('img', 'dsa-img');
        img.alt = im.path;
        img.addEventListener('error', () => img.replaceWith(make('p', 'dsa-note', 'The page blocked the inline preview. Press Open to see the image.')));
        img.src = im.url;
        fig.append(img);
      } else {
        fig.append(make('p', 'dsa-note', im.error || 'Preview not available. Press Open to see the image.'));
      }
      const cap = make('figcaption', 'dsa-figcap');
      const open = make('button', 'dsa-open', 'Open');
      open.type = 'button';
      open.title = 'Open full size, with PNG download';
      open.addEventListener('click', () => {
        try { chrome.runtime.sendMessage({ type: 'openImage', path: im.path }); } catch (e) { /* extension reloaded */ }
      });
      cap.append(make('span', 'dsa-figname', im.path), open);
      fig.append(cap);
      wrap.append(fig);
    }
    return wrap;
  }

  function specSig(spec) {   // images can be large: sign them by size, not by content
    const lite = Object.assign({}, spec, { images: (spec.images || []).map((i) => [i.path, i.url ? i.url.length : 0, i.error || '']) });
    return JSON.stringify(lite);
  }

  function updateCard(card, spec) {
    const sig = specSig(spec);
    if (card.dataset.sig === sig) return; // unchanged: leave the DOM (and open/closed state) alone
    card.dataset.sig = sig;
    card.dataset.state = spec.state;
    card.querySelector('.dsa-tname').textContent = spec.name;
    card.querySelector('.dsa-targ').textContent = spec.arg || '';
    card.querySelector('.dsa-chip').textContent = spec.chip;
    const body = card.querySelector('.dsa-tbody');
    body.replaceChildren();
    if (spec.images && spec.images.length) body.append(previews(spec.images));
    for (const sec of spec.sections) {
      body.append(make('div', 'dsa-sec', sec.label), make('div', 'dsa-code', sec.text));
    }
  }

  function ensureCard(original, kind, spec) {
    let card = original.previousElementSibling;
    if (!(card && card.classList.contains('dsa-tool') && card.dataset.kind === kind)) {
      card = makeCard(kind);
      original.insertAdjacentElement('beforebegin', card);
    }
    original.setAttribute('data-dsa-orig', '1');
    original.setAttribute('data-dsa-kind', kind);
    updateCard(card, spec);
    if (spec.images && spec.images.length && !card.dataset.autoopened) {
      card.open = true;                       // show the picture the first time; the user may close it
      card.dataset.autoopened = '1';
    }
  }

  function toolSpec(text, res, earlier) {
    const name = ((text.match(/^\s*name:\s*([a-z][a-z0-9_]*)/im) || [])[1]) || 'tool';
    // headline argument: path/command/pattern if present, else the first parameter written
    const first = text.split('\n').slice(1).map((l) => l.match(/^\s*[a-z][a-z0-9_]*:\s?(.+)$/i)).find(Boolean);
    const arg = (((text.match(/^\s*(?:path|command|pattern):\s*(.+)$/m) || [])[1]) || (first && first[1]) || '').trim();
    let st = 'pending';
    let chip = earlier ? 'Earlier' : state.active ? 'Queued' : 'Not run';
    if (res) {
      st = res.status;
      chip = res.status === 'running' ? 'Running' : res.status === 'ok' ? 'Done' : 'Failed';
    }
    const sections = [{ label: 'Call', text: text.trim() }];
    if (res && res.status !== 'running') {
      sections.push({ label: res.status === 'ok' ? 'Result' : 'Error', text: res.text || '(empty)' });
    }
    return { name, arg, state: st, chip, sections, images: (res && res.images) || [] };
  }

  function agentSpec(text) {
    const t = text.trim();
    if (t.startsWith('<<<RESULT')) {
      const items = [...t.matchAll(/<<<RESULT (\d+) (\S+) (OK|ERROR)>>>/g)];
      const errs = items.filter((i) => i[3] === 'ERROR').length;
      return {
        name: 'Tool results',
        arg: items.map((i) => i[2]).join(', '),
        state: errs ? 'err' : 'ok',
        chip: errs ? `${errs} failed` : 'OK',
        sections: [{ label: 'Sent to DeepSeek', text: clip(t) }],
      };
    }
    if (t.startsWith(RESUME_PREFIX)) {
      return { name: 'Session resumed', arg: 'refreshed instructions and the current project files', state: 'ok', chip: 'Sent',
               sections: [{ label: 'Sent to DeepSeek', text: clip(t) }] };
    }
    if (t.startsWith(SKILLS_PREFIX)) {
      return { name: 'Skills update', arg: 'sent after you changed skills', state: 'ok', chip: 'Sent',
               sections: [{ label: 'Sent to DeepSeek', text: clip(t) }] };
    }
    return {
      name: 'Agent instructions',
      arg: 'sent when the session started',
      state: 'ok',
      chip: 'Sent',
      sections: [{ label: 'Sent to DeepSeek', text: clip(t) }],
    };
  }

  // The block-level element (direct child of the reply) that holds a tool call's <pre>,
  // so the language label / copy bar that DeepSeek puts around code is hidden with it.
  function blockContainer(pre, reply) {
    let node = pre;
    while (node.parentElement && node.parentElement !== reply) node = node.parentElement;
    if (node === pre) return pre;
    const extra = (node.textContent || '').length - (pre.textContent || '').length;
    if (node.querySelectorAll('pre').length !== 1 || extra > 60) return pre; // a list/quote with more in it
    return node;
  }

  function enhanceTools() {
    const seen = new Map();
    for (const reply of replyEls()) {
      reply.querySelectorAll('pre').forEach((pre) => {
        if (pre.closest('.dsa-ui')) return;
        const text = preText(pre);
        if (!isToolBlockText(text)) return;
        const k = hash(text);
        const n = seen.get(k) || 0;
        seen.set(k, n + 1);
        const res = (state.results.get(k) || [])[n];
        ensureCard(blockContainer(pre, reply), 'tool', toolSpec(text, res, !res && state.history.has(k + ':' + n)));
      });
    }
  }

  function enhanceAgentMsgs() {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode(n) {
        if (n.nodeValue.length < 12) return NodeFilter.FILTER_REJECT;
        const t = n.nodeValue.trimStart();
        if (!(t.startsWith('<<<RESULT') || t.startsWith(PROMPT_PREFIX) || t.startsWith(SKILLS_PREFIX) || t.startsWith(RESUME_PREFIX))) return NodeFilter.FILTER_REJECT;
        const p = n.parentElement;
        if (!p || p.closest('.dsa-ui, .dsa-panel, textarea, [data-dsa-orig], .ds-markdown')) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    const hosts = [];
    for (let n = walker.nextNode(); n; n = walker.nextNode()) hosts.push(n.parentElement);
    for (let host of hosts) {
      while (host.parentElement && getComputedStyle(host).display.startsWith('inline')) host = host.parentElement;
      ensureCard(host, 'agent', agentSpec(host.textContent || ''));
    }
  }

  function cleanupOrphans() {
    document.querySelectorAll('.dsa-tool').forEach((c) => {
      const next = c.nextElementSibling;
      if (!next || !next.hasAttribute('data-dsa-orig')) c.remove();
    });
    document.querySelectorAll('.dsa-think-toggle').forEach((b) => {
      const next = b.nextElementSibling;
      if (!next || !next.hasAttribute('data-dsa-think')) b.remove();
    });
  }

  function removeKind(kind) {
    document.querySelectorAll(`.dsa-tool[data-kind="${kind}"]`).forEach((c) => c.remove());
    document.querySelectorAll(`[data-dsa-orig][data-dsa-kind="${kind}"]`).forEach((o) => {
      o.removeAttribute('data-dsa-orig');
      o.removeAttribute('data-dsa-kind');
    });
  }

  // ---- thinking -------------------------------------------------------------
  function visible(e) {
    return e.getBoundingClientRect().height > 2 && getComputedStyle(e).display !== 'none';
  }

  function findThinkHeader(content) {
    let node = content;
    for (let i = 0; node && i < 3; i++, node = node.parentElement) {
      const sib = node.previousElementSibling;
      const t = sib ? (sib.textContent || '').trim() : '';
      if (sib && t.length < 100 && THINK_HEADER.test(t)) return sib;
    }
    return null;
  }

  function cssCollapse(content) {
    if (content.hasAttribute('data-dsa-think')) return;
    content.setAttribute('data-dsa-think', '1');
    content.setAttribute('data-dsa-collapsed', '1');
    const btn = make('button', 'dsa-ui dsa-think-toggle', 'Show reasoning');
    btn.type = 'button';
    btn.addEventListener('click', () => {
      const hidden = content.toggleAttribute('data-dsa-collapsed');
      btn.textContent = hidden ? 'Show reasoning' : 'Hide reasoning';
    });
    content.insertAdjacentElement('beforebegin', btn);
  }

  function enhanceThinking() {
    document.querySelectorAll(SEL.thinkContent).forEach((content) => {
      if (content.closest('.dsa-ui')) return;
      if (content.parentElement && content.parentElement.closest(SEL.thinkContent)) return; // nested match
      const first = state.thinkFirstSeen.get(content);
      if (first === 'done') return;
      const now = Date.now();
      if (!first) state.thinkFirstSeen.set(content, now);

      const header = findThinkHeader(content);
      if (!header) {                       // no native header to use: collapse it ourselves
        cssCollapse(content);
        state.thinkFirstSeen.set(content, 'done');
        return;
      }
      if (visible(content)) {              // use DeepSeek's own toggle so its UI stays consistent
        const btn = header.matches('[role="button"], button')
          ? header : (header.querySelector('[role="button"], button') || header);
        btn.click();
        state.thinkFirstSeen.set(content, 'done');
        setTimeout(() => { if (visible(content)) cssCollapse(content); }, 600); // click did nothing: fall back
      } else if (now - (first || now) > 4000) {
        state.thinkFirstSeen.set(content, 'done'); // already collapsed; never fight the user after this
      }
    });
  }

  function restoreThinking() {
    document.querySelectorAll('[data-dsa-think]').forEach((c) => {
      c.removeAttribute('data-dsa-think');
      c.removeAttribute('data-dsa-collapsed');
    });
    document.querySelectorAll('.dsa-think-toggle').forEach((b) => b.remove());
  }

  // ---- scheduling -----------------------------------------------------------
  function applyTheme() {
    document.documentElement.classList.toggle('dsa-theme', !!state.settings.theme);
  }

  function scan() {
    try {
      const s = state.settings;
      if (s.collapseThinking) enhanceThinking(); else restoreThinking();
      if (s.collapseTools) enhanceTools(); else removeKind('tool');
      if (s.collapseAgentMsgs) enhanceAgentMsgs(); else removeKind('agent');
      cleanupOrphans();
    } catch (e) {
      console.warn('[DeepSeek Agent] style scan failed:', e);
    }
  }

  function schedule() {
    if (state.timer) return;
    state.timer = setTimeout(() => { state.timer = null; scan(); }, 250);
  }

  function relevant(m) {
    const t = m.target.nodeType === 1 ? m.target : m.target.parentElement;
    if (t && t.closest && t.closest('.dsa-ui, .dsa-panel')) return false;
    if (m.type === 'childList') {
      const nodes = [...m.addedNodes, ...m.removedNodes];
      if (nodes.length && nodes.every((n) => n.nodeType === 1 && (n.classList.contains('dsa-ui') || n.classList.contains('dsa-panel')))) {
        return false;
      }
    }
    return true;
  }

  function stats() {
    return {
      thinkingBlocks: document.querySelectorAll(SEL.thinkContent).length,
      thinkingCollapsedByUs: document.querySelectorAll('[data-dsa-think]').length,
      toolCards: document.querySelectorAll('.dsa-tool[data-kind="tool"]').length,
      agentCards: document.querySelectorAll('.dsa-tool[data-kind="agent"]').length,
    };
  }

  function init() {
    try {
      chrome.storage.local.get(DEFAULTS).then((saved) => {
        Object.assign(state.settings, saved);
        applyTheme();
        schedule();
      });
      chrome.storage.onChanged.addListener((changes, area) => {
        if (area !== 'local') return;
        let touched = false;
        for (const k of Object.keys(DEFAULTS)) {
          if (changes[k]) { state.settings[k] = changes[k].newValue; touched = true; }
        }
        if (touched) { applyTheme(); schedule(); }
      });
    } catch (e) { /* no storage (tests): defaults apply */ }
    new MutationObserver((muts) => { if (muts.some(relevant)) schedule(); })
      .observe(document.documentElement, { childList: true, subtree: true, characterData: true });
    applyTheme();
    schedule();
  }

  const api = {
    SEL, hash, preText, replyText, replyEls, isToolBlockText, start, setActive, markHistory, init, stats,
    PROMPT_PREFIX, RESUME_PREFIX,
    scanNow: scan, settings: state.settings, applyTheme,
  };
  root.DSAgentStyle = api;
})(window);
