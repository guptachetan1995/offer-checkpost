/* The recorder's overlays, installed in every document the page loads: the caption, the ring
around what is about to be clicked, the MCP client's panel and the end card, plus scrolling that
keeps a target clear of the sticky header and the caption. Styles are set through the CSSOM,
which the app's Content-Security-Policy (no inline styles) allows; text goes in as textContent
only. */
(() => {
  const FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif';
  const MONO = 'ui-monospace, "SF Mono", Menlo, monospace';
  const style = (el, s) => Object.assign(el.style, s);
  const make = (tag, s, text) => {
    const el = document.createElement(tag);
    style(el, s);
    if (text !== undefined) el.textContent = text;
    return el;
  };
  const layer = () => {
    let el = document.getElementById('__oc_layer');
    if (!el) {
      el = make('div', { position: 'fixed', inset: '0', pointerEvents: 'none', zIndex: '2147483647' });
      el.id = '__oc_layer';
      document.documentElement.appendChild(el);
    }
    return el;
  };
  const part = (id) => {
    let el = document.getElementById(id);
    if (!el) {
      el = make('div', {});
      el.id = id;
      layer().appendChild(el);
    }
    return el;
  };
  const stuck = () => {
    const top = document.getElementById('top');
    return top && getComputedStyle(top).position === 'sticky' ? top.getBoundingClientRect().bottom : 0;
  };
  const CAPTION_ROOM = 96;
  const scrollTo = (y) => window.scrollTo({ top: Math.max(0, y), behavior: 'smooth' });
  const find = (root, text) => {
    const base = document.querySelector(root);
    if (!base) return null;
    let best = null;
    for (const el of base.querySelectorAll('*')) {
      if (el.textContent.includes(text)) best = el;
    }
    return best;
  };
  window.__oc = {
    caption(text) {
      const el = part('__oc_caption');
      el.replaceChildren();
      if (!text) return;
      el.appendChild(make('div', {
        position: 'fixed', left: '50%', bottom: '22px', transform: 'translateX(-50%)',
        maxWidth: '1100px', padding: '12px 24px', borderRadius: '12px',
        background: 'rgba(17, 17, 17, 0.9)', color: '#fff', font: `600 25px/1.3 ${FONT}`,
        textAlign: 'center', boxShadow: '0 6px 24px rgba(0,0,0,0.25)', whiteSpace: 'nowrap',
      }, text));
    },
    visible(sel) {
      const el = document.querySelector(sel);
      if (!el) return false;
      const r = el.getBoundingClientRect();
      return r.top >= stuck() && r.bottom <= window.innerHeight - CAPTION_ROOM;
    },
    center(sel) {
      const el = document.querySelector(sel);
      if (!el) return false;
      const r = el.getBoundingClientRect();
      const room = window.innerHeight - CAPTION_ROOM - stuck();
      scrollTo(window.scrollY + r.top - stuck() - Math.max(16, (room - r.height) / 2));
      return true;
    },
    show(sel) {
      const el = document.querySelector(sel);
      if (!el) return false;
      scrollTo(window.scrollY + el.getBoundingClientRect().top - stuck() - 16);
      return true;
    },
    showText(root, text) {
      const el = find(root, text);
      if (!el) return false;
      const r = el.getBoundingClientRect();
      const room = window.innerHeight - CAPTION_ROOM - stuck();
      scrollTo(window.scrollY + r.top - stuck() - Math.max(16, (room - r.height) / 2));
      return true;
    },
    point(sel) {
      const el = document.querySelector(sel);
      if (!el) return false;
      const r = el.getBoundingClientRect();
      const ring = make('div', {
        position: 'fixed', left: `${r.left - 6}px`, top: `${r.top - 6}px`,
        width: `${r.width + 12}px`, height: `${r.height + 12}px`, borderRadius: '10px',
        border: '3px solid #e8590c', boxShadow: '0 0 0 6px rgba(232, 89, 12, 0.22)',
        transition: 'opacity 400ms', opacity: '1',
      });
      layer().appendChild(ring);
      setTimeout(() => { ring.style.opacity = '0'; }, 1100);
      setTimeout(() => ring.remove(), 1600);
      return true;
    },
    panel(title, lines) {
      const el = part('__oc_panel');
      el.replaceChildren();
      if (!title) return;
      const box = make('div', {
        position: 'fixed', left: '24px', top: '168px', width: '600px', padding: '16px 18px',
        borderRadius: '12px', background: '#16161a', color: '#e9e9ee',
        boxShadow: '0 10px 30px rgba(0,0,0,0.3)', font: `14px/1.45 ${MONO}`,
      });
      box.appendChild(make('div', { font: `700 15px/1.3 ${FONT}`, color: '#ffb37a', marginBottom: '10px' }, title));
      for (const [kind, text] of lines) {
        const color = kind === 'error' ? '#ff8a80' : kind === 'dim' ? '#9a9aa6' : '#e9e9ee';
        box.appendChild(make('div', { color, marginTop: '6px', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }, text));
      }
      el.appendChild(box);
    },
    card(lines) {
      const el = part('__oc_card');
      el.replaceChildren();
      if (!lines) return;
      const box = make('div', {
        position: 'fixed', inset: '0', background: '#1c1b18', color: '#fbfaf7',
        display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center',
        gap: '18px', font: `20px/1.4 ${FONT}`, textAlign: 'center', padding: '0 80px',
      });
      const sizes = { title: `800 64px/1.1 ${FONT}`, sub: `500 26px/1.35 ${FONT}`,
        url: `600 28px/1.3 ${MONO}`, line: `500 22px/1.4 ${FONT}`, fine: `400 18px/1.4 ${FONT}` };
      for (const [kind, text] of lines) {
        box.appendChild(make('div', { font: sizes[kind], color: kind === 'fine' ? '#bdb9ae' : kind === 'url' ? '#ffb37a' : '#fbfaf7' }, text));
      }
      el.appendChild(box);
    },
  };
})();
