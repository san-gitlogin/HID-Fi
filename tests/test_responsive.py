"""Responsive sweep of the dashboard across screen shapes, from a 240px feature
phone to an 8K wall.

The dashboard is one HTML file with no build step, so a layout regression is
invisible until someone opens it on the one device that breaks. This drives a
real browser over a matrix of viewports and asserts the things that actually go
wrong: content wider than the screen, text clipped inside its own button, the
trackpad card changing size when it flips to the keyboard, and controls sliding
under each other.

    python tests/test_responsive.py            all sizes
    python tests/test_responsive.py --quick    a representative subset

Needs playwright:  python -m playwright install chromium
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
UI = HERE.parent / "hid_fi" / "web_ui.h"
VIEWS = ["pad", "keys", "short", "media", "game", "power", "setup"]

# (label, w, h). Grouped by what they are, because a failure reads very
# differently depending on whether it is a real phone or a synthetic extreme.
SIZES = [
    # --- below anything shipping today, kept as a floor ---
    ("feature phone", 240, 320),
    ("Galaxy Fold cover", 280, 653),
    ("small legacy", 300, 600),
    ("iPhone SE 1", 320, 480),
    ("iPhone 5", 320, 568),
    # --- phones, portrait ---
    ("Android small", 360, 640),
    ("Android common", 360, 740),
    ("Pixel 5", 393, 851),
    ("iPhone SE 3", 375, 667),
    ("iPhone X", 375, 812),
    ("iPhone 12/13", 390, 844),
    ("iPhone 15", 393, 852),
    ("iPhone 17", 402, 874),
    ("Pixel 7 Pro", 412, 892),
    ("Android large", 412, 915),
    ("iPhone 11", 414, 896),
    ("iPhone 14 Plus", 428, 926),
    ("iPhone 15 Pro Max", 430, 932),
    ("iPhone 17 Pro Max", 440, 956),
    # --- phones, landscape ---
    ("iPhone 5 land", 568, 320),
    ("Android small land", 640, 360),
    ("iPhone SE land", 667, 375),
    ("iPhone X land", 812, 375),
    ("iPhone 12 land", 844, 390),
    ("iPhone 15 land", 852, 393),
    ("Android large land", 915, 412),
    ("iPhone 11 land", 896, 414),
    ("iPhone 15 PM land", 932, 430),
    # --- foldables ---
    ("Fold inner", 673, 841),
    ("Fold inner land", 841, 673),
    ("Surface Duo", 540, 720),
    ("Pixel Fold open", 841, 701),
    # --- tablets ---
    ("iPad mini", 744, 1133),
    ("iPad", 810, 1080),
    ("iPad Air", 820, 1180),
    ("iPad Pro 11", 834, 1194),
    ("iPad Pro 12.9", 1024, 1366),
    ("iPad mini land", 1133, 744),
    ("iPad Air land", 1180, 820),
    ("iPad Pro 12.9 land", 1366, 1024),
    # --- laptops and desktops (CSS pixels) ---
    ("laptop 720p", 1280, 720),
    ("laptop 768", 1366, 768),
    ("MacBook Air", 1440, 900),
    ("MacBook Pro 14", 1512, 982),
    ("MacBook Pro 16", 1728, 1117),
    ("1080p", 1920, 1080),
    ("WUXGA", 1920, 1200),
    ("DCI 2K", 2048, 1080),
    ("QHD", 2560, 1440),
    ("WQXGA", 2560, 1600),
    # --- ultrawide ---
    ("ultrawide 21:9", 3440, 1440),
    ("ultrawide 24:10", 3840, 1600),
    ("super ultrawide 32:9", 5120, 1440),
    # --- 4K and up, at DPR 1 so the CSS pixel count really is this big ---
    ("4K UHD", 3840, 2160),
    ("DCI 4K", 4096, 2160),
    ("ProRes 5K", 5120, 2700),
    ("Studio Display 5K", 5120, 2880),
    ("Pro Display XDR 6K", 6016, 3384),
    ("8K UHD", 7680, 4320),
    ("DCI 8K", 8192, 4320),
    # --- synthetic aspect ratio extremes ---
    ("square", 800, 800),
    ("very tall 1:3", 400, 1200),
    ("very wide 3:1", 1200, 400),
    ("extreme tall 1:5", 360, 1800),
    ("extreme wide 5:1", 1800, 360),
    ("slit wide", 2000, 300),
    ("slit tall", 300, 2000),
]

QUICK = {"Galaxy Fold cover", "iPhone SE 3", "iPhone 15", "Android large",
         "iPhone 12 land", "iPad Air", "1080p", "4K UHD", "8K UHD",
         "super ultrawide 32:9", "very tall 1:3", "very wide 3:1"}

# Below the supported floor. Ten key columns in 200px is 16px a key whatever the
# CSS does, and no browser ships on a screen this narrow. Still measured and
# still printed, so a change here is visible - just not counted as a regression.
FLOOR = {"feature phone"}


def extract_html():
    src = UI.read_text(encoding="utf-8")
    s = src.index('R"rawliteral(')
    s = src.index("\n", s) + 1
    e = src.index(')rawliteral"', s)
    return src[s:e]


# Runs in the page. Returns every way this viewport is wrong, or an empty list.
PROBE = r"""
(views) => {
  const bad = [], info = {};
  // Large displays scale the whole app with zoom on the root. Under zoom,
  // innerWidth and getBoundingClientRect do not agree on units, so the usable
  // width is measured from a real full-size element - that is in the same space
  // as every other rect here, whatever the browser is doing underneath.
  const measureViewport = () => {
    const p = document.createElement('div');
    p.style.cssText = 'position:fixed;inset:0;pointer-events:none;visibility:hidden';
    document.body.appendChild(p);
    const r = p.getBoundingClientRect();
    p.remove();
    return r;
  };
  const vp = measureViewport();
  const W = vp.width, H = vp.height;
  const Z = parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
  info.zoom = Z;
  info.cssViewport = Math.round(W) + 'x' + Math.round(H);
  const de = document.documentElement, main = document.getElementById('main');

  const visible = el => {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };

  // Anything that legitimately lives off screen: closed sheets and toasts slide
  // in from outside, and the fullscreen pad is not part of page flow.
  const parked = el => !!el.closest('.sheet,.toast,.padwrap.fs,#joinBox,.overlay,dialog');

  const scanOverflow = where => {
    if (de.scrollWidth > de.clientWidth + 1)
      bad.push(where + ': page scrolls sideways by ' + (de.scrollWidth - de.clientWidth) + 'px');
    if (main && main.scrollWidth > main.clientWidth + 1)
      bad.push(where + ': content scrolls sideways by ' + (main.scrollWidth - main.clientWidth) + 'px');
    const view = document.querySelector('.view.on');
    if (!view) return;
    let worst = null;
    view.querySelectorAll('*').forEach(el => {
      if (!visible(el) || parked(el)) return;
      const r = el.getBoundingClientRect();
      const over = Math.max(r.right - W, -r.left);
      if (over > 1 && (!worst || over > worst.over))
        worst = {over, left: r.left, right: r.right,
                 tag: el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
                 (typeof el.className === 'string' && el.className ? '.' + el.className.trim().split(/\s+/)[0] : '')};
    });
    if (worst) bad.push(where + ': ' + worst.tag + ' spans ' + Math.round(worst.left) + '..' +
                        Math.round(worst.right) + ' in a ' + Math.round(W) + 'px viewport');
  };

  const scanClipped = where => {
    document.querySelectorAll('.view.on [data-k], .padwrap [data-k]').forEach(b => {
      if (!visible(b)) return;
      const r = b.getBoundingClientRect();
      if (b.scrollWidth > r.width + 1)
        bad.push(where + ': key "' + b.dataset.k + '" label clipped (' +
                 b.scrollWidth + ' into ' + r.width.toFixed(0) + 'px)');
    });
  };

  // A keyboard that is on screen must be able to type. Not "nothing overflows" --
  // actually reachable. A whole row once vanished because a :nth-child rule
  // followed the row order rather than the row, and the digits row was hidden on
  // short screens even on layouts that have no 123 layer to reach them from.
  //
  // Layer switches count: a key on the 123 layer is reachable. KLAYER is flipped
  // directly rather than by clicking, because a click rebuilds every keyboard at
  // once and the button to click back is gone by the time it is needed.
  const BASIC = 'abcdefghijklmnopqrstuvwxyz0123456789'.split('')
      .concat(['ENTER','BACKSPACE','SPACE','TAB',
               'UP','DOWN','LEFT','RIGHT','#SHIFT','#CTRL','#ALT','#GUI',
               '-','=','[',']','\\',';',"'",',','.','/','`']);
  // The deck is a companion inside the trackpad card and cannot afford the
  // function row; Esc is one tap away on the Type tab, which must have it.
  const NEEDS = {kbd: BASIC.concat(['ESC']), kbd2: BASIC};

  const keysIn = k => {
    const out = new Set();
    k.querySelectorAll('button[data-k]').forEach(b => {
      if (visible(b)) out.add(b.dataset.k);
    });
    return out;
  };

  const scanComplete = where => {
    document.querySelectorAll('.kbd').forEach(k => {
      if (!visible(k)) return;
      const need = NEEDS[k.id];
      if (!need) return;
      const have = keysIn(k);
      if (have.size < 5) return;                  // not a real keyboard
      if (typeof KLAYER !== 'undefined' && typeof buildKbd === 'function') {
        const was = KLAYER;
        KLAYER = (was === 'abc') ? 'sym' : 'abc';
        buildKbd();
        keysIn(k).forEach(x => have.add(x));
        KLAYER = was;
        buildKbd();
      }
      const missing = need.filter(c => !have.has(c));
      if (missing.length)
        bad.push(where + ': keyboard #' + k.id + ' cannot reach ' +
                 missing.join(' '));
    });
  };

  for (const v of views) {
    go(v);
    scanOverflow(v);
    scanClipped(v);
    scanComplete(v);
  }
  go('pad');

  // --- the trackpad card must not change size when it flips ---
  const deck = document.getElementById('padkbd');
  const wrap = document.getElementById('padwrap');
  const pad  = document.getElementById('pad');
  const btn  = document.getElementById('btnDeck');
  const show = on => { if ((getComputedStyle(deck).display !== 'none') !== on) btn.click(); };

  show(false);
  const padBox = wrap.getBoundingClientRect();
  const padH = pad.getBoundingClientRect().height;
  show(true);
  const deckBox = wrap.getBoundingClientRect();
  const d = deck.getBoundingClientRect();

  if (Math.abs(deckBox.width - padBox.width) > 1.5)
    bad.push('deck: card width jumps ' + padBox.width.toFixed(0) + ' -> ' + deckBox.width.toFixed(0));
  if (Math.abs(d.height - padH) > 1.5)
    bad.push('deck: card height jumps ' + padH.toFixed(0) + ' -> ' + d.height.toFixed(0));

  const kb = deck.querySelector('.kbd').getBoundingClientRect();
  if (kb.bottom > d.bottom + 1 || kb.right > d.right + 1 || kb.left < d.left - 1)
    bad.push('deck: keys escape the card');

  const inp = document.getElementById('pkText').getBoundingClientRect();
  const ent = document.getElementById('pkEnter').getBoundingClientRect();
  const tools = document.querySelector('.padtools').getBoundingClientRect();
  const typeRowShown = getComputedStyle(document.querySelector('#padkbd .pkrow')).display !== 'none';
  if (typeRowShown) {
    if (ent.right > tools.left + 1 && ent.top < tools.bottom - 1)
      bad.push('deck: type row runs under the corner buttons by ' + Math.round(ent.right - tools.left) + 'px');
    if (inp.width < 40)
      bad.push('deck: type field only ' + inp.width.toFixed(0) + 'px wide');
  }

  scanOverflow('pad+deck');
  scanClipped('pad+deck');
  scanComplete('pad+deck');

  // Short screens drop the digits row on purpose, so only measure what is shown.
  const keys = [...deck.querySelectorAll('.krow:not(.fn):not(.nav) button')]
                 .filter(visible).map(b => b.getBoundingClientRect());
  info.deckKey = {w: +Math.min(...keys.map(k => k.width)).toFixed(1),
                  h: +Math.min(...keys.map(k => k.height)).toFixed(1)};
  info.rows = [...deck.querySelectorAll('.krow')].filter(visible).length;
  info.card = {w: +deckBox.width.toFixed(0), h: +d.height.toFixed(0)};
  info.typeField = typeRowShown ? +inp.width.toFixed(0) : 0;
  if (info.deckKey.w < 18 || info.deckKey.h < 18)
    bad.push('deck: keys down to ' + info.deckKey.w + 'x' + info.deckKey.h + 'px');

  show(false);

  // --- navigation must be reachable ---
  const rail = document.querySelector('.rail'), tabs = document.querySelector('.tabbar');
  const railOn = rail && visible(rail), tabsOn = tabs && visible(tabs);
  if (!railOn && !tabsOn) bad.push('no navigation is visible');
  info.nav = railOn ? 'rail' : (tabsOn ? 'tabbar' : 'none');

  return {bad, info};
}
"""

FS_PROBE = r"""
() => {
  const bad = [];
  const probe = document.createElement('div');
  probe.style.cssText = 'position:fixed;inset:0;pointer-events:none;visibility:hidden';
  document.body.appendChild(probe);
  const vp = probe.getBoundingClientRect();
  probe.remove();
  const VW = vp.width, VH = vp.height;
  const wrap = document.getElementById('padwrap'), deck = document.getElementById('padkbd');
  const btn = document.getElementById('btnDeck');
  if (getComputedStyle(deck).display === 'none') btn.click();
  document.getElementById('btnFs').click();
  const w = wrap.getBoundingClientRect(), d = deck.getBoundingClientRect();
  const kb = deck.querySelector('.kbd').getBoundingClientRect();
  const exit = document.getElementById('btnFsExitTop').getBoundingClientRect();
  if (Math.abs(w.width - VW) > 2 || Math.abs(w.height - VH) > 2)
    bad.push('fullscreen: pad is ' + w.width.toFixed(0) + 'x' + w.height.toFixed(0) +
             ' not ' + Math.round(VW) + 'x' + Math.round(VH));
  if (kb.bottom > d.bottom + 1 || kb.right > d.right + 1)
    bad.push('fullscreen: keys escape the deck');
  if (exit.width < 40 || exit.top < 0 || exit.bottom > VH)
    bad.push('fullscreen: no way out on screen');
  if (getComputedStyle(document.querySelector('.fsbar')).display !== 'none')
    bad.push('fullscreen: mouse button bar drawn over the keyboard');
  document.querySelectorAll('.padwrap [data-k]').forEach(b => {
    const r = b.getBoundingClientRect();
    if (r.width > 0 && b.scrollWidth > r.width + 1)
      bad.push('fullscreen: key "' + b.dataset.k + '" clipped');
  });
  // Fullscreen is the case with the most room, so a letter missing here is never
  // a space problem -- it is a rule deleting the wrong row.
  {
    const have = new Set();
    document.querySelectorAll('.padwrap .kbd button[data-k]').forEach(b => {
      if (b.getBoundingClientRect().width > 0 && /^[a-z]$/.test(b.dataset.k))
        have.add(b.dataset.k);
    });
    if (have.size) {
      const missing = 'abcdefghijklmnopqrstuvwxyz'.split('').filter(c => !have.has(c));
      if (missing.length)
        bad.push('fullscreen: keyboard is missing letters ' + missing.join(''));
    }
  }
  document.getElementById('btnFsExitTop').click();
  if (getComputedStyle(deck).display !== 'none') btn.click();
  return bad;
}
"""


def main():
    quick = "--quick" in sys.argv
    sizes = [s for s in SIZES if s[0] in QUICK] if quick else SIZES

    html = extract_html()
    tmp = HERE / "_responsive.html"
    tmp.write_text(html, encoding="utf-8")

    failures, rows = [], []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            # Take the native fullscreen API away before the page runs. The board
            # never gets it on iOS Safari either, so this is the path most phones
            # actually take - and it leaves the window resizable between sizes.
            page.add_init_script(
                "Element.prototype.requestFullscreen=undefined;"
                "Element.prototype.webkitRequestFullscreen=undefined;"
            )
            page.goto(tmp.as_uri())
            page.wait_for_timeout(400)

            print("=" * 74)
            print("  Responsive sweep -- {} viewports".format(len(sizes)))
            print("=" * 74)

            for label, w, h in sizes:
                page.set_viewport_size({"width": w, "height": h})
                page.wait_for_timeout(140)
                res = page.evaluate(PROBE, VIEWS)
                bad = list(res["bad"])
                bad += page.evaluate(FS_PROBE)
                info = res["info"]
                rows.append((label, w, h, info))
                below = label in FLOOR
                tag = "OK " if not bad else ("below" if below else "FAIL")
                print("  [{}] {:<22} {:>5}x{:<5} ar {:>5}  zoom {:<4} css {:<10} key {:>4}x{:<4} rows {} {}"
                      .format(tag, label, w, h, round(w / h, 2),
                              info["zoom"], info["cssViewport"],
                              info["deckKey"]["w"], info["deckKey"]["h"],
                              info["rows"], info["nav"]))
                for b in bad:
                    print("         - " + b)
                    if not below:
                        failures.append((label, w, h, b))
            browser.close()
    finally:
        tmp.unlink(missing_ok=True)

    print("=" * 74)
    if failures:
        print("  {} problem(s) across {} viewports".format(len(failures), len({f[0] for f in failures})))
    else:
        print("  {} viewports, no layout problems".format(len(rows)))
        if any(l in FLOOR for l, _, _, _ in rows):
            print("  (sizes marked 'below' are under the supported floor and are not counted)")
    print("=" * 74)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
