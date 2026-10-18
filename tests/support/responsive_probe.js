// Runs inside the dashboard. window.__hidfiProbe(state) measures the page as it is
// drawn right now and returns {bad: [...], info: {...}}. Every check is something
// that has gone wrong on a real screen before (see CHANGELOG), or a promise the
// layout makes: nothing wider than the screen, nothing cut off, nothing hidden
// under something else, everything reachable, one way to navigate, keys a finger
// can hit. Messages are stable text, so they can be compared run to run.
(() => {
  const CONTROL = 'button,input,select,textarea,[data-k],[data-knob],.tile,.chip,.navbtn,.stick,[data-hat],[data-gb]';

  const viewport = () => {
    // Under root zoom (large displays) innerWidth and getBoundingClientRect are in
    // different units; a full-size fixed element is in the same space as every rect.
    const p = document.createElement('div');
    p.style.cssText = 'position:fixed;inset:0;pointer-events:none;visibility:hidden';
    document.body.appendChild(p);
    const r = p.getBoundingClientRect();
    p.remove();
    return r;
  };

  // Chromium's own answer (display, visibility, opacity of the element and every
  // ancestor), then a real box. Far cheaper than walking computed styles.
  const shown = el => {
    if (!el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0.5 && r.height > 0.5;
  };

  // A stable name for an element, for the messages.
  const name = el => {
    if (el.id && !/^(b|kp|kt)\d+$/.test(el.id)) return '#' + el.id;
    for (const a of ['data-k', 'data-v', 'data-sc', 'data-q', 'data-knob', 'data-kedit', 'data-pr', 'data-md',
                     'data-gs', 'data-mb', 'data-gb', 'data-hat', 'data-os', 'data-os-set', 'data-pwr', 'data-ptr',
                     'data-uni', 'data-uni-test', 'data-scroll', 'data-sys', 'data-ms', 'data-ssid', 'data-vtype',
                     'data-vshow', 'data-ved', 'data-pcgo', 'data-pced', 'data-pcdel', 'data-scg'])
      if (el.hasAttribute(a)) return '[' + a + '="' + el.getAttribute(a).slice(0, 24) + '"]';
    const t = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ').slice(0, 24);
    const c = typeof el.className === 'string' && el.className.trim() ? '.' + el.className.trim().split(/\s+/)[0] : '';
    return el.tagName.toLowerCase() + c + (t ? ' "' + t + '"' : '');
  };

  // Things that live outside the page flow on purpose, when they are not the
  // thing being looked at.
  const parkedFor = state => el => {
    if (!state.startsWith('sheet') && el.closest('.sheet')) return true;
    if (!state.startsWith('overlay') && el.closest('#boot,#apnew,#asw')) return true;
    if (!state.startsWith('fullscreen') && el.closest('.padwrap.fs')) return true;
    if (state !== 'toast' && el.closest('#toasts')) return true;
    return !!el.closest('.sprite');
  };

  // The element whose box should hold the controls being checked.
  const scope = state => {
    if (state.startsWith('sheet')) return document.getElementById('sheetin');
    if (state === 'overlay boot') return document.getElementById('boot');
    if (state === 'overlay apnew') return document.getElementById('apnew');
    if (state === 'overlay asw') return document.getElementById('asw');
    if (state === 'toast') return document.getElementById('toasts');
    if (state.startsWith('fullscreen')) return document.getElementById('padwrap');
    return document.querySelector('.view.on');
  };

  const scroller = state => {
    if (state.startsWith('sheet')) return document.getElementById('sheetin');
    // The reboot and new-network overlays scroll as a last resort on tiny screens.
    if (state === 'overlay boot') return document.getElementById('boot');
    if (state === 'overlay apnew') return document.getElementById('apnew');
    if (state.startsWith('overlay') || state.startsWith('fullscreen') || state === 'toast') return null;
    return document.getElementById('main');
  };

  window.__hidfiProbe = state => {
    const bad = [], info = {};
    const vp = viewport(), W = vp.width, H = vp.height;
    const Z = parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
    info.css = Math.round(W) + 'x' + Math.round(H);
    info.zoom = Z;
    const root = scope(state);
    if (!root || !shown(root)) { bad.push(state + ': nothing is drawn for this state'); return {bad, info}; }
    const parked = parkedFor(state);
    const say = m => bad.push(state + ': ' + m);

    // --- 1. nothing wider than the screen --------------------------------------------
    const de = document.documentElement, main = document.getElementById('main');
    if (de.scrollWidth > de.clientWidth + 1) say('page scrolls sideways by ' + (de.scrollWidth - de.clientWidth) + 'px');
    if (main && shown(main) && main.scrollWidth > main.clientWidth + 1)
      say('content scrolls sideways by ' + (main.scrollWidth - main.clientWidth) + 'px');
    let worst = null;
    root.querySelectorAll('*').forEach(el => {
      if (parked(el) || !shown(el)) return;
      const r = el.getBoundingClientRect();
      const over = Math.max(r.right - W, -r.left);
      if (over > 1 && (!worst || over > worst.over)) worst = {over, el};
    });
    if (worst) say(name(worst.el) + ' sticks out of the screen by ' + Math.round(worst.over) + 'px');

    // --- 2. nothing cut off inside its own control -----------------------------------
    const clipped = new Set();
    root.querySelectorAll(CONTROL + ',.tx b,.tx span,label,h2,h3').forEach(el => {
      if (parked(el) || !shown(el) || el.tagName === 'INPUT' || el.tagName === 'SELECT' || el.tagName === 'TEXTAREA') return;
      const s = getComputedStyle(el);
      const isControl = el.matches(CONTROL);
      // Intended truncation: a long name in a list row ends in "...". A control's
      // own label never may - that is how "Cmd" became "C...".
      if (!isControl && s.textOverflow === 'ellipsis') return;
      if (!isControl && s.overflowX === 'visible' && s.whiteSpace !== 'nowrap') return;
      if (el.scrollWidth > el.clientWidth + 1 && el.clientWidth > 0)
        clipped.add(name(el) + ' (' + el.scrollWidth + ' into ' + el.clientWidth + 'px)');
    });
    clipped.forEach(c => say('label cut off: ' + c));

    // --- 3. every control reachable and not under something else ----------------------
    // A card switched off behind its own prompt (the controller before it is
    // enabled) is covered on purpose; its prompt is what must be reachable.
    const controls = [...root.querySelectorAll(CONTROL)].filter(el => !parked(el) && shown(el) && !el.disabled &&
                                                                   !el.closest('.gpoff > :not(.gpmsg)'));
    const ok = new Set(), coveredBy = new Map();
    const sc = scroller(state);
    const positions = [];
    if (sc && sc.scrollHeight > sc.clientHeight + 1) {
      const step = Math.max(80, sc.clientHeight * 0.6);
      for (let y = 0; y < sc.scrollHeight - sc.clientHeight + step; y += step) positions.push(Math.min(y, sc.scrollHeight - sc.clientHeight));
    } else positions.push(null);
    const startY = sc ? sc.scrollTop : 0;
    for (const y of positions) {
      if (sc && y !== null) sc.scrollTop = y;
      for (const el of controls) {
        if (ok.has(el)) continue;
        const r = el.getBoundingClientRect();
        if (r.top < 0 || r.left < 0 || r.bottom > H + 0.5 || r.right > W + 0.5) continue;
        const cx = Math.min(r.left + r.width / 2, W - 1), cy = Math.min(r.top + r.height / 2, H - 1);
        const hit = document.elementFromPoint(cx, cy);
        if (hit && (hit === el || el.contains(hit) || hit.contains(el) && hit.matches('label'))) ok.add(el);
        else if (hit) coveredBy.set(el, name(hit.closest(CONTROL + ',.tabbar,.topbar,.rail,.sheetin,.overlay,.toast') || hit));
      }
    }
    // The steps can jump over the one position where a tall control fits between
    // the top bar and the tab bar. Before calling anything unreachable, scroll it
    // to the middle - which is what a person does - and look once more.
    if (sc) {
      for (const el of controls.filter(c => !ok.has(c)).slice(0, 300)) {
        el.scrollIntoView({block: 'center', inline: 'nearest'});
        const r = el.getBoundingClientRect();
        if (r.top < 0 || r.left < 0 || r.bottom > H + 0.5 || r.right > W + 0.5) continue;
        const hit = document.elementFromPoint(Math.min(r.left + r.width / 2, W - 1), Math.min(r.top + r.height / 2, H - 1));
        if (hit && (hit === el || el.contains(hit))) { ok.add(el); coveredBy.delete(el); }
        else if (hit) coveredBy.set(el, name(hit.closest(CONTROL + ',.tabbar,.topbar,.rail,.sheetin,.overlay,.toast') || hit));
      }
      sc.scrollTop = startY;
    }
    const lost = controls.filter(el => !ok.has(el));
    // Keys are reported as one line per keyboard, everything else one per control.
    const lostKeys = lost.filter(el => el.hasAttribute('data-k'));
    if (lostKeys.length) say(lostKeys.length + ' key(s) cannot be tapped (' +
                             lostKeys.slice(0, 6).map(name).join(' ') + (lostKeys.length > 6 ? ' ...' : '') + ')');
    lost.filter(el => !el.hasAttribute('data-k')).forEach(el =>
      say(name(el) + ' cannot be tapped' + (coveredBy.has(el) ? ' - under ' + coveredBy.get(el) : ' - never fully on screen')));

    // --- 4. big enough for a finger, on a touch screen --------------------------------
    if (matchMedia('(hover:none)').matches) {
      let smallKey = null, smallCtl = [];
      for (const el of controls) {
        const r = el.getBoundingClientRect();
        const m = Math.min(r.width, r.height);
        if (el.hasAttribute('data-k')) { if (m < 18 && (!smallKey || m < smallKey.m)) smallKey = {m, el}; }
        else if (m < 24 && !el.closest('.pinrow')) smallCtl.push(name(el) + ' ' + Math.round(r.width) + 'x' + Math.round(r.height));
      }
      if (smallKey) say('keys down to ' + Math.round(smallKey.m) + 'px (' + name(smallKey.el) + ')');
      smallCtl.slice(0, 8).forEach(c => say('too small to tap: ' + c));
      info.minKey = smallKey ? Math.round(smallKey.m) : null;
    }

    // --- 5. readable text ---------------------------------------------------------------
    let tiny = null;
    const seen = new Set();
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const el = n.parentElement;
      if (!el || seen.has(el) || !n.textContent.trim()) continue;
      seen.add(el);
      if (parked(el) || !shown(el)) continue;
      const px = parseFloat(getComputedStyle(el).fontSize) * Z;
      if (!tiny || px < tiny.px) tiny = {px, el};
    }
    if (tiny) {
      info.minFont = +tiny.px.toFixed(1);
      if (tiny.px < 9) say('text down to ' + tiny.px.toFixed(1) + 'px (' + name(tiny.el) + ')');
    }

    // --- 6. one way to navigate, one logo ----------------------------------------------
    if (!state.startsWith('fullscreen') && !state.startsWith('overlay') && state !== 'toast') {
      const rail = document.getElementById('rail'), tabs = document.getElementById('tabbar');
      const r = rail && shown(rail), t = tabs && shown(tabs);
      if (!r && !t) say('no navigation is visible');
      if (r && t) say('both the sidebar and the tab bar are showing');
      info.nav = r ? 'rail' : t ? 'tabbar' : 'none';
      const logos = [...document.querySelectorAll('.rail .mark, .topbar .tmark')].filter(shown);
      if (logos.length !== 1) say(logos.length + ' logos in the frame');
    } else if (state.startsWith('fullscreen')) {
      const tabs = document.getElementById('tabbar');
      if (tabs && shown(tabs)) say('the tab bar shows over the fullscreen pad');
      const exit = document.getElementById('btnFsExitTop');
      if (!exit || !shown(exit) || !ok.has(exit)) say('no way out of fullscreen on screen');
      const w = document.getElementById('padwrap').getBoundingClientRect();
      if (Math.abs(w.width - W) > 2 || Math.abs(w.height - H) > 2)
        say('fullscreen pad is ' + Math.round(w.width) + 'x' + Math.round(w.height) + ', the screen ' + Math.round(W) + 'x' + Math.round(H));
    }

    // --- 7. keyboards: the right layout, every key, arrows in line ---------------------
    document.querySelectorAll('.kbd').forEach(k => {
      if (!shown(k) || parked(k) || !root.contains(k)) return;
      const keys = [...k.querySelectorAll('button[data-k]')].filter(shown);
      if (keys.length < 5) return;
      const have = new Set(keys.map(b => b.dataset.k));
      const letters = 'abcdefghijklmnopqrstuvwxyz'.split('');
      if (typeof KLAYER === 'undefined' || KLAYER === 'abc') {
        const missing = letters.filter(c => !have.has(c));
        if (missing.length) say('keyboard #' + k.id + ' is missing letters ' + missing.join(''));
      }
      const up = k.querySelector('[data-k="UP"]'), down = k.querySelector('[data-k="DOWN"]');
      // Under 310px the compact rows wrap on purpose (web_ui.h: every key readable
      // beats an inverted T there), so the arrows cannot line up by design.
      if (up && down && shown(up) && shown(down) && !matchMedia('(max-width:309px)').matches) {
        const a = up.getBoundingClientRect(), b = down.getBoundingClientRect();
        if (b.top > a.top + 1 && Math.abs((a.left + a.right) / 2 - (b.left + b.right) / 2) > 1.5)
          say('keyboard #' + k.id + ' arrows out of line by ' + Math.round(Math.abs((a.left + a.right) / 2 - (b.left + b.right) / 2)) + 'px');
      }
      if (typeof kbTierFor === 'function' && typeof kbTierOf !== 'undefined') {
        const want = kbTierFor(k), got = kbTierOf[k.id];
        if (want !== got) say('keyboard #' + k.id + ' drawn as ' + got + ' but it has room for ' + want);
        info['tier_' + k.id] = got;
      }
      info['keys_' + k.id] = keys.length;
    });

    // --- 8. inside the safe area (notch, home indicator) -------------------------------
    const ins = window.__hidfiInsets;
    if (ins && (ins[0] || ins[1] || ins[2] || ins[3])) {
      const hit = [];
      for (const el of controls) {
        if (!ok.has(el)) continue;
        const r = el.getBoundingClientRect();
        if (r.top < ins[0] - 1 || r.right > W - ins[1] + 1 || r.bottom > H - ins[2] + 1 || r.left < ins[3] - 1) {
          // A control scrolled under the notch is fine; one held there by the layout is not.
          if (getComputedStyle(el).position === 'fixed' || el.closest('.tabbar,.topbar,.rail,.fsbar,.padtools,.padwrap.fs,.overlay,#boot,#apnew,#asw'))
            hit.push(name(el));
        }
      }
      if (hit.length) say('under the notch or home indicator: ' + hit.slice(0, 6).join(' '));
    }

    // --- 9. a sheet fits, and every part of it can be reached ---------------------------
    if (state.startsWith('sheet')) {
      const s = document.getElementById('sheetin').getBoundingClientRect();
      if (s.top < -1 || s.bottom > H + 1 || s.left < -1 || s.right > W + 1)
        say('the sheet is ' + Math.round(s.width) + 'x' + Math.round(s.height) + ' at ' + Math.round(s.left) + ',' + Math.round(s.top) +
            ' on a ' + Math.round(W) + 'x' + Math.round(H) + ' screen');
      const close = document.querySelector('#sheetin .sheeth .iconbtn');
      if (close && !ok.has(close)) say('the sheet\'s close button cannot be tapped');
    }

    // --- 10. centred where it says it is -----------------------------------------------
    const gp = document.querySelector('#gpCard .gpmsg');
    if (gp && shown(gp) && root.contains(gp)) {
      const xs = [...gp.children].filter(shown).map(c => { const r = c.getBoundingClientRect(); return (r.left + r.right) / 2; });
      if (xs.length > 1 && Math.max(...xs) - Math.min(...xs) > 2) say('the controller prompt is not on one centre line');
    }

    info.controls = controls.length;
    return {bad, info};
  };
})();
