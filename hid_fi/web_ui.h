// ============================================================================
//  web_ui.h - dashboard served at http://<ip>/  (firmware v3.6)
//
//  Split out of hid_fi.ino so the sketch stays readable.
//
//  Design notes that are not obvious from the markup:
//   * No emoji anywhere. Every glyph is an inline SVG symbol from one sprite,
//     drawn on a 24x24 grid with a 1.75 stroke, round caps/joins, so the whole
//     icon set reads as one family at any size.
//   * Pointer, joystick and knob traffic go out as binary WebSocket frames.
//     Nothing on an input hot path allocates, logs, or waits for a reply.
//   * One <use> reference per icon instance, so N buttons cost one path each.
//   * Views are all in the DOM but display:none + contain, so switching is a
//     class flip with no reflow of the inactive ones.
// ============================================================================
#pragma once

const char WEB_INTERFACE[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="theme-color" content="#07090f">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="color-scheme" content="dark">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Crect width='24' height='24' rx='5' fill='%2300d2ff'/%3E%3Cg transform='translate(3.6 3.6) scale(.7)' fill='none' stroke='%2304121a' stroke-width='2.6' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M11.67 21.45A8.9 8.9 0 1 1 21.45 11.67'/%3E%3Cpath d='M8.64 8.64 14.96 20.66 15.7 15.7 20.66 14.96Z' fill='%2304121a' stroke-width='1.8'/%3E%3C/g%3E%3C/svg%3E">
<meta name="apple-mobile-web-app-title" content="HID-Fi">
<title>HID-Fi</title>
<style>
:root{
  --bg:#07090f; --bg2:#0d1119; --surface:rgba(255,255,255,.045); --surface2:rgba(255,255,255,.075);
  --line:rgba(255,255,255,.10); --line2:rgba(0,210,255,.28);
  --fg:#eaf2f7; --fg2:#93a4b3; --fg3:#5f7180;
  --acc:#00d2ff; --acc2:#3a7bd5; --ok:#22d39a; --warn:#ffb02e; --bad:#ff5a5a;
  --r:14px; --r2:10px; --rail:212px; --railc:68px; --tab:60px;
  /* Width the rail is actually using. A media query cannot express "narrow by
     default but the user may override", so this is driven by a body class and
     the breakpoint only supplies the starting value. */
  --railw:var(--rail);
  --sp:14px; --ease:cubic-bezier(.2,.8,.2,1);
  /* Rotate a notched phone and the camera cutout moves to the left or the right
     depending on which way you turned it, while the home indicator stays at the
     bottom. Nothing in this layout may assume the viewport is the safe area.
     These are variables rather than bare env() so the values can be overridden
     to reproduce a notch on a desktop browser, which is the only way to test it. */
  --sat:env(safe-area-inset-top,0px);
  --sar:env(safe-area-inset-right,0px);
  --sab:env(safe-area-inset-bottom,0px);
  --sal:env(safe-area-inset-left,0px);
}
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html{-webkit-text-size-adjust:100%;height:100%}
body{
  height:100%;font:400 15px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  color:var(--fg);background:var(--bg);overflow:hidden;overscroll-behavior:none;
  /* manipulation drops the double-tap-to-zoom delay, and with it the double tap
     itself - the single most likely way to zoom a page you are tapping quickly. */
  touch-action:manipulation;
  /* Every hold in this UI means something - grab, latch a modifier, open an
     editor, raise the app switcher. Without this the phone starts selecting the
     button's own label instead, and pops up its copy bar over the control. */
  user-select:none;-webkit-user-select:none;-webkit-touch-callout:none;
}
/* the only things worth selecting are the ones you might want to copy out */
input,textarea,.log,.kv2 dd{user-select:text;-webkit-user-select:text;-webkit-touch-callout:default}
body::before{
  content:'';position:fixed;inset:0;z-index:-1;pointer-events:none;
  background:
    radial-gradient(70vw 50vh at 78% -8%,rgba(0,210,255,.13),transparent 60%),
    radial-gradient(60vw 45vh at 8% 106%,rgba(58,123,213,.14),transparent 60%),
    linear-gradient(170deg,#07090f,#0d1119 55%,#0a0f18);
}
button{font:inherit;color:inherit;background:none;border:none;cursor:pointer;touch-action:manipulation}
input,select,textarea{font:inherit;color:inherit}
.ic{width:22px;height:22px;flex:0 0 auto;fill:none;stroke:currentColor;stroke-width:1.75;stroke-linecap:round;stroke-linejoin:round;display:block}
.ic-sm{width:18px;height:18px}.ic-lg{width:26px;height:26px}
.sprite{position:absolute;width:0;height:0;overflow:hidden}

/* ---------------- shell ---------------- */
.app{display:flex;height:100%;width:100%}
.rail{
  /* The rail must GROW by the side inset, not just pad by it. With border-box a
     68px collapsed rail carrying 71px of padding has a zero-width content box,
     and the logo spills straight back out under the camera cutout. */
  width:calc(var(--railw) + var(--sal));flex:0 0 calc(var(--railw) + var(--sal));
  display:flex;flex-direction:column;gap:4px;
  padding:calc(16px + var(--sat)) 12px calc(16px + var(--sab));
  padding-left:calc(12px + var(--sal));
  border-right:1px solid var(--line);background:rgba(8,11,17,.7);backdrop-filter:blur(16px);
  transition:width .22s var(--ease),flex-basis .22s var(--ease);
}
.brand{display:flex;align-items:center;gap:11px;padding:6px 8px 18px}
.brand .mark{
  width:34px;height:34px;flex:0 0 34px;border-radius:10px;display:grid;place-items:center;
  background:linear-gradient(140deg,var(--acc),var(--acc2));color:#04121a;box-shadow:0 6px 18px rgba(0,210,255,.28);
}
.brand .mark .ic{stroke-width:2.1}
.brand .txt{min-width:0}
.brand b{display:block;font-size:14px;font-weight:650;letter-spacing:.2px;white-space:nowrap}
.brand span{display:block;font-size:11px;color:var(--fg3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.navbtn{
  display:flex;align-items:center;gap:12px;padding:11px 12px 11px 17px;border-radius:var(--r2);
  color:var(--fg2);position:relative;transition:background .16s,color .16s;width:100%;text-align:left;
}
.navbtn span{font-size:14px;font-weight:520;white-space:nowrap}
.navbtn:hover{background:var(--surface);color:var(--fg)}
.navbtn.on{background:linear-gradient(90deg,rgba(0,210,255,.16),rgba(0,210,255,.04));color:var(--fg)}
.navbtn.on::before{content:'';position:absolute;left:0;top:20%;height:60%;width:3px;border-radius:0 3px 3px 0;background:var(--acc)}
.rail .spacer{flex:1}
.linkstat{display:flex;align-items:center;justify-content:center;gap:9px;padding:10px 12px;border-radius:var(--r2);background:var(--surface);font-size:12px;color:var(--fg2)}
/* The nav list is the only part allowed to scroll, so a short landscape screen
   never pushes the brand or the collapse control off the ends. The scrollbar
   itself is hidden - it would sit inside a 68px rail and cover the icons. */
#navlist{flex:1 1 auto;min-height:0;overflow-y:auto;display:flex;flex-direction:column;gap:4px;
  scrollbar-width:none;-ms-overflow-style:none}
#navlist::-webkit-scrollbar{width:0;height:0;display:none}
.railtoggle{
  display:flex;align-items:center;gap:12px;padding:10px 12px;margin-top:4px;width:100%;
  border-radius:var(--r2);color:var(--fg3);font-size:13px;font-weight:520;text-align:left;
  transition:background .16s,color .16s;
}
.railtoggle:hover{background:var(--surface);color:var(--fg)}
.railtoggle .ic{transition:transform .22s var(--ease)}
.railtoggle span{white-space:nowrap}
/* collapsed rail: labels off, everything centred on the icon column */
body.rail-slim{--railw:var(--railc)}
body.rail-slim .rail .navbtn span,
body.rail-slim .brand .txt,
body.rail-slim .linkstat span:not(.dot),
body.rail-slim .railtoggle span{display:none}
body.rail-slim .navbtn{justify-content:center;padding:12px 0}
body.rail-slim .brand{justify-content:center;padding:6px 0 16px}
/* Collapsed, the status pill has no text left to hold, so the container is
   dropped and only the indicator remains - on the same centre line as the icons
   above it rather than floating in an empty grey box. */
body.rail-slim .linkstat{justify-content:center;padding:12px 0;background:none;border:none}
body.rail-slim .linkstat .dot{width:9px;height:9px;flex-basis:9px}
body.rail-slim .railtoggle{justify-content:center;padding:12px 0}
body.rail-slim .railtoggle .ic{transform:rotate(180deg)}

.content{flex:1;min-width:0;display:flex;flex-direction:column}
.topbar{
  display:flex;align-items:center;gap:10px;padding:12px 18px;padding-top:calc(12px + var(--sat));
  padding-right:calc(18px + var(--sar));
  border-bottom:1px solid var(--line);background:rgba(8,11,17,.55);backdrop-filter:blur(14px);
}
.topbar h1{font-size:17px;font-weight:620;letter-spacing:.2px;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
/* The rail carries the mark wherever the rail exists, so this is the stand-in
   for the widths where it does not - the phone layout switches it on below.
   Showing both put the same logo on screen twice. flex:0 0 auto for the same
   reason the icon buttons need it - otherwise a crowded bar squashes the tile
   while leaving the glyph full size, and the glyph spills past the corner. */
.topbar .tmark{
  display:none;
  width:30px;height:30px;flex:0 0 30px;border-radius:9px;place-items:center;
  background:linear-gradient(140deg,var(--acc),var(--acc2));color:#04121a;
  box-shadow:0 3px 10px rgba(0,210,255,.22);
}
.topbar .tmark .ic{width:19px;height:19px}
.pill{
  display:inline-flex;align-items:center;gap:6px;height:30px;padding:0 11px;border-radius:999px;flex:0 0 auto;
  background:var(--surface);border:1px solid var(--line);font-size:12px;color:var(--fg2);white-space:nowrap;
}
.pill b{color:var(--fg);font-weight:600;font-variant-numeric:tabular-nums}
.pill .ic{width:14px;height:14px;flex:0 0 14px;color:var(--fg3)}
/* More than one dashboard is normal - every phone that opens it is one - but it
   is also the first thing to check when the pointer feels slow, so it is worth
   noticing rather than hunting for in Settings. */
.pill.conn.multi .ic,.pill.conn.multi b{color:var(--warn)}
.dot{width:7px;height:7px;border-radius:50%;flex:0 0 7px;background:var(--fg3)}
.dot.ok{background:var(--ok);box-shadow:0 0 0 3px rgba(34,211,154,.15)}
.dot.bad{background:var(--bad);box-shadow:0 0 0 3px rgba(255,90,90,.15)}
.dot.warn{background:var(--warn);box-shadow:0 0 0 3px rgba(255,176,46,.15)}
/* flex:0 0 auto matters here. Without it these shrink under pressure from a
   crowded topbar while keeping their icon at full size, so the icon spills out
   past the rounded border and looks like it is overlapping the frame. */
.iconbtn{width:38px;height:38px;flex:0 0 auto;border-radius:11px;display:grid;place-items:center;color:var(--fg2);background:var(--surface);border:1px solid var(--line);transition:.16s}
.iconbtn:hover{color:var(--fg);border-color:var(--line2)}
.iconbtn:active{transform:scale(.93)}
.iconbtn.on{background:rgba(0,210,255,.18);border-color:var(--acc);color:var(--fg)}
/* Segmented control. The selected half is its own rounded pill sitting inside a
   padded track, rather than a square block clipped by the parent's radius -
   that clipping was shaving the corners off the highlight and the icons. */
.seg{display:flex;gap:2px;padding:2px;border:1px solid var(--line);border-radius:999px;
  background:var(--surface);flex:0 0 auto;max-width:100%}
.seg button{display:flex;align-items:center;justify-content:center;gap:5px;height:26px;padding:0 10px;
  flex:0 0 auto;min-width:0;font-size:11.5px;font-weight:560;border-radius:999px;
  color:var(--fg3);transition:background .16s,color .16s,box-shadow .16s}
.seg button.on{background:rgba(0,210,255,.18);color:var(--fg);
  box-shadow:inset 0 0 0 1px var(--acc),0 0 12px rgba(0,210,255,.30)}
.seg button:active{transform:scale(.95)}
.seg button .ic{flex:0 0 auto}

main{flex:1;overflow-y:auto;overscroll-behavior:contain;padding:var(--sp);
  padding-bottom:calc(var(--sp) + var(--sab));
  padding-right:calc(var(--sp) + var(--sar))}
/* No `contain:layout` here. Layout containment makes an element a containing block
   for position:fixed descendants, which broke the fullscreen trackpad: it anchored
   to the card instead of the viewport, and since it had left normal flow the card
   collapsed to zero height, so the pad's height:100% resolved to nothing. */
.view{display:none}
.view.on{display:block;animation:fade .2s var(--ease)}
@keyframes fade{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
/* min() on the track size matters: a bare minmax(300px,1fr) refuses to go below
   300px, so on a 320px phone the card ends up wider than the screen and gets
   clipped by body{overflow:hidden} - which is why the keyboard lost its right
   edge with no way to scroll to it.
   The max-width is the other end of the same problem: on a 3440px display the
   cards stretch to 3200px, which gives 300px keyboard keys and text lines nobody
   can read across. Capped and centred instead. */
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(300px,100%),1fr));gap:var(--sp);
  align-items:start;max-width:1600px;margin:0 auto}
/* min-width:0 because a grid item refuses to go under its own min-content by
   default, so one stubborn row inside a card made the card - and every other
   card in the row, since the tracks are equal - wider than a 280px screen */
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:16px;min-width:0}
/* no overflow:hidden - it would clip the pad once it goes fullscreen */
.card.pad0{padding:0;border-radius:var(--r)}
.card.span{grid-column:1/-1}
.ch{display:flex;align-items:center;gap:9px;margin-bottom:13px;color:var(--acc)}
.ch h2{font-size:14px;font-weight:620;letter-spacing:.3px;text-transform:uppercase;flex:1;min-width:0;color:var(--fg)}
.ch .ic{color:var(--acc)}
.hint{font-size:12px;line-height:1.55;color:var(--fg3)}
.sep{height:1px;background:var(--line);margin:14px 0}

/* ---------------- buttons ---------------- */
.btn{
  display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:44px;padding:0 16px;
  border-radius:var(--r2);background:var(--surface2);border:1px solid var(--line);
  font-size:14px;font-weight:560;transition:transform .12s var(--ease),background .16s,border-color .16s;
}
.btn:hover{border-color:var(--line2)}
.btn:active{transform:scale(.965)}
.btn.pri{background:linear-gradient(135deg,var(--acc2),var(--acc));border-color:transparent;color:#04121a;font-weight:650}
.btn.ok{background:linear-gradient(135deg,#0f9b6c,var(--ok));border-color:transparent;color:#04121a;font-weight:650}
.btn.bad{background:linear-gradient(135deg,#c0392b,var(--bad));border-color:transparent;color:#fff;font-weight:650}
.btn.on{background:rgba(0,210,255,.2);border-color:var(--acc)}
.btn.sm{min-height:38px;font-size:13px;padding:0 12px}
.btn.blk{width:100%}
.row{display:flex;gap:9px;flex-wrap:wrap}
.row>*{flex:1 1 0;min-width:0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(88px,1fr));gap:9px}
.tile{
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:7px;
  min-height:76px;padding:10px 6px;border-radius:var(--r2);
  background:var(--surface2);border:1px solid var(--line);transition:transform .12s var(--ease),background .16s,border-color .16s;
}
.tile:hover{border-color:var(--line2)}
.tile:active{transform:scale(.94);background:rgba(0,210,255,.16)}
.tile.on{background:rgba(0,210,255,.2);border-color:var(--acc)}
/* latched state - the dashboard cannot read the host back, so this is what we
   believe we set, shown clearly enough that a desync is obvious */
.tile.act{background:rgba(255,90,90,.16);border-color:var(--bad);box-shadow:0 0 14px rgba(255,90,90,.22)}
.tile.act .ic{color:var(--bad)}
.tile.act span{color:var(--fg)}
.tile.act.cy{background:rgba(0,210,255,.18);border-color:var(--acc);box-shadow:0 0 14px rgba(0,210,255,.26)}
.tile.act.cy .ic{color:var(--acc)}
@keyframes flash{from{box-shadow:0 0 0 0 rgba(0,210,255,.45)}to{box-shadow:0 0 0 15px rgba(0,210,255,0)}}
.flash{animation:flash .45s var(--ease)}
.tile.danger:active{background:rgba(255,90,90,.2)}
.tile span{font-size:11px;font-weight:520;color:var(--fg2);text-align:center;line-height:1.25;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.tile .ic{color:var(--fg)}

/* ---------------- fields ---------------- */
.fld{margin:11px 0}
.fld label{display:block;font-size:11px;font-weight:620;letter-spacing:.5px;text-transform:uppercase;color:var(--fg3);margin-bottom:6px}
.fld input,.fld select,.fld textarea{
  width:100%;min-height:44px;padding:11px 12px;border-radius:var(--r2);
  background:rgba(0,0,0,.32);border:1px solid var(--line);font-size:16px;transition:border-color .16s,box-shadow .16s;
}
.fld textarea{min-height:110px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:14px;resize:vertical}
.fld input:focus,.fld select:focus,.fld textarea:focus{outline:none;border-color:var(--acc);box-shadow:0 0 0 3px rgba(0,210,255,.13)}
.slider{display:flex;align-items:center;gap:12px;margin:12px 0}
.slider label{flex:0 0 66px;font-size:12px;color:var(--fg2)}
/* min-width:0 because a range input reports an intrinsic width of about 130px,
   and a flex item cannot go under that on its own - the row pushed the whole
   card wider than a 280px screen */
.slider input[type=range]{flex:1;min-width:0;height:30px;accent-color:var(--acc);background:none}
.slider .v{flex:0 0 38px;text-align:right;font-size:13px;font-variant-numeric:tabular-nums;color:var(--fg)}
.chips{display:flex;gap:8px;flex-wrap:wrap}
.chip{display:inline-flex;align-items:center;gap:7px;min-height:36px;padding:0 13px;border-radius:999px;
  background:var(--surface2);border:1px solid var(--line);font-size:12.5px;color:var(--fg2);transition:.16s}
.chip.on{background:rgba(0,210,255,.2);border-color:var(--acc);color:var(--fg)}
.chip.off{opacity:.45}
.scanfoot{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:10px}
.scanfoot span{font-size:11.5px;color:var(--fg3)}
.zoomout{position:fixed;left:50%;transform:translateX(-50%);z-index:900;
  top:calc(8px + var(--sat));height:34px;padding:0 15px;border-radius:999px;
  font-size:12.5px;font-weight:600;color:#04121a;background:var(--acc);
  box-shadow:0 6px 20px rgba(0,0,0,.45);border:none}
.chip:active{transform:scale(.95)}

/* ---------------- trackpad ---------------- */
/* One height for the card, whichever face it is showing. The pad and the
   keyboard deck both read it, so flipping between them cannot move the page and
   a future change to the pad size cannot leave the deck behind. */
.padwrap{--padh:clamp(210px,38vh,340px);
  position:relative;border-radius:var(--r);overflow:hidden;background:#080b12;border:1px solid var(--line)}
/* inset:0 alone sizes this to the containing block, which is the one measurement
   that stays right when the root is zoomed for a large display - vw and vh do
   not follow that zoom and made the fullscreen pad overhang the screen. The
   dvh override below is for browsers whose chrome overlays the viewport; those
   are phones, and phones never reach the zoom breakpoints. */
.padwrap.fs{position:fixed;inset:0;z-index:600;border-radius:0;border:none;
  padding:env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left)}
@media(max-width:2399px){.padwrap.fs{width:100vw;height:100vh;height:100dvh}}
#pad{
  position:relative;width:100%;height:var(--padh);touch-action:none;user-select:none;-webkit-user-select:none;
  background:
    radial-gradient(120% 90% at 50% 0%,rgba(0,210,255,.10),transparent 62%),
    repeating-linear-gradient(0deg,rgba(255,255,255,.028) 0 1px,transparent 1px 22px),
    repeating-linear-gradient(90deg,rgba(255,255,255,.028) 0 1px,transparent 1px 22px);
  transition:box-shadow .18s;
}
.padwrap.fs{--padh:100%}
.padwrap.fs #pad{height:100%}
#pad.live{box-shadow:inset 0 0 0 1.5px rgba(0,210,255,.5)}
#pad.grab{box-shadow:inset 0 0 0 2px var(--ok)}

/* ---- keyboard deck: the same card, showing keys instead of the pad ----
   The deck takes its height from the identical clamp the pad uses, so flipping
   between them never moves the page under your thumb. */
#padkbd{display:none;flex-direction:column;gap:8px;padding:var(--dpad);box-sizing:border-box;
  height:var(--padh);--dpad:10px}
.padwrap.kb #pad{display:none}
.padwrap.kb #padkbd{display:flex}
/* Everything in the corner that means "trackpad" steps aside. Grab is a pointer
   idea with no keyboard equivalent, and the badge reports pointer state. */
.padwrap.kb .padbadge,.padwrap.kb .padtools #btnGrab{display:none}
/* The fullscreen button bar is Left/Right/Grab. It has to be beaten on
   specificity, not order: .padwrap.fs .fsbar{display:flex} is declared later. */
.padwrap.kb .fsbar,.padwrap.fs.kb .fsbar{display:none}
/* In a card the deck is one block, but fullscreen gives it the whole screen and
   it may grow a nav cluster and a numpad beside the letters. Stretch rather than
   bottom align, because here the rows divide the available height instead of
   being measured from the top. */
#padkbd .kbd{margin:0;flex:1 1 auto;min-height:0;align-items:stretch}
#padkbd .kblock{min-height:0;justify-content:flex-end}
/* The function and navigation rows do not fit in a card-sized deck. They come
   back in fullscreen, and the Type tab carries them at every size - so nothing
   here is the only way to reach a key. */
#padkbd .krow.fn,#padkbd .krow.nav{display:none}
/* The rows divide whatever height the card has, rather than being computed from
   it. Working out key height by arithmetic meant every change to the padding or
   the type row could push the total past the card, and on short landscape
   screens it did. Sharing the space cannot overflow by construction; the cap
   stops keys becoming absurd on a tall display. */
#padkbd .krow{flex:1 1 0;min-height:0;max-height:60px}
#padkbd .krow button{min-height:0;height:100%}
/* The numpad is a grid of fixed rows everywhere else; in the deck it has to
   share the height like every other block or it overflows the screen. The cap
   matters: deck rows stop growing at 92px, and without the same ceiling here the
   numpad's five rows stretch past them and Num/ / / * stops sitting level with
   the number row. */
#padkbd .kpad{flex:1 1 auto;min-height:0;grid-auto-rows:minmax(0,1fr)}
#padkbd .kpad button{min-height:0;height:auto}
.padwrap.fs #padkbd .kpad{max-height:calc(92px * 5 + 6px * 4)}
@media(max-height:700px){
  #padkbd{gap:6px;--dpad:7px}
  .pkrow input,.pkrow .btn{height:34px}
}
/* Nothing is hidden for lack of height any more. The digits row used to go on a
   short screen, which was survivable while the compact layout kept every digit
   one tap away on the 123 layer - but the wider layouts have no 123 layer, so
   hiding that row took the numbers away entirely with no way to reach them. The
   rows share the card's height instead, which cannot overflow by construction.
   Shorter still and the type field goes, because the Type tab has one and keys
   people can hit matter more here. */
@media(max-height:360px){.padwrap:not(.fs) .pkrow{display:none}}
/* Below 310px the shared rule lets rows wrap onto a second line for readability.
   The Type tab can grow to absorb that; a deck locked to the pad's height cannot
   - letting it wrap was measured at 12.7px tall keys, which is not a key at all.
   So the deck keeps its strict columns and shrinks its labels instead, since a
   short label on a key you can hit beats a tidy one on a key you cannot. */
@media(max-width:309px){
  /* :not(.fn):not(.nav) is load bearing. This selector carries an id, so it
     outranks the auto-fit rule those two rows rely on, and without the guard
     "Ctrl+C" and "Alt+Tab" get a 23px column in fullscreen and are cut off. */
  #padkbd .kmain.c12 .krow:not(.fn):not(.nav){grid-template-columns:repeat(12,1fr)}
  /* Twelve columns in a 280px card only clears an 18px key if the gaps and the
     deck's own padding give their pixels back. .ic-sm is 18px and the column is
     narrower than that, so the glyphs come down with the text as well. */
  #padkbd{--dpad:2px;gap:4px}
  #padkbd .krow{gap:2px}
  #padkbd .krow button{font-size:9px;padding:0;border-radius:5px}
  #padkbd .krow button .ic{width:12px;height:12px}
}
/* The corner tools are two 34px buttons inset by 10px, so they occupy the last
   88px of the card. Measured from the card rather than the row, or the deck's
   own padding would silently eat into the gap and the Enter key would slide
   under the fullscreen button. */
.padwrap.kb .pkrow{padding-right:calc(88px - var(--dpad))}
.pkrow{display:flex;gap:7px;flex:0 0 auto}
/* width:0 is load bearing. A text input reports a default content width of about
   twenty characters, and a flex item's min-content contribution is what the grid
   track above sizes itself from - so without this the card grew ~50px wider than
   the trackpad face and pushed itself off the side of the screen. */
.pkrow input{flex:1 1 auto;width:0;min-width:0;height:38px;padding:0 11px;border-radius:10px;
  background:rgba(0,0,0,.34);border:1px solid var(--line);color:var(--fg);font-size:16px}
.pkrow .btn{flex:0 0 auto;height:38px;padding:0 12px}
@media(max-width:400px){.pkrow .btn{padding:0 9px;font-size:12px}}
/* Fullscreen has room for the whole keyboard. The tools and the exit pill live
   across the top, so the deck starts below them rather than reserving a corner.
   The extra bottom padding keeps the last row of keys - arrows and space - out
   from under Safari's toolbar, which env() does not always report. */
.padwrap.fs #padkbd{height:100%;padding-top:calc(58px + env(safe-area-inset-top));padding-bottom:16px}
.padwrap.fs.kb .pkrow{padding-right:0}
.padwrap.fs #padkbd .krow.fn,.padwrap.fs #padkbd .krow.nav{display:grid}
/* Fullscreen rows may run taller than in the card, but not without limit - on a
   4K display an unbounded row would give a single key the height of a laptop. */
.padwrap.fs #padkbd .krow{max-height:92px}
@media(max-height:520px){
  .padwrap.fs #padkbd .krow.fn,.padwrap.fs #padkbd .krow.nav{display:none}
}
/* the phone-width bleed below is for a full-width card and would run under the
   pad card's rounded corners; the id selector above keeps the deck out of it */
.padhint{
  position:absolute;left:50%;bottom:12px;transform:translateX(-50%);
  display:flex;gap:6px;flex-wrap:wrap;justify-content:center;max-width:calc(100% - 20px);
  pointer-events:none;transition:opacity .2s;
}
.padhint i{
  font-style:normal;font-size:10.5px;line-height:1;letter-spacing:.2px;color:var(--fg3);
  background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.07);border-radius:999px;padding:5px 9px;white-space:nowrap;
}
#pad.live .padhint{opacity:0}
@media(max-height:520px){.padhint{display:none}}
.padtools{position:absolute;top:10px;right:10px;display:flex;gap:7px;z-index:2}
.padtools .iconbtn{width:34px;height:34px;background:rgba(0,0,0,.45);backdrop-filter:blur(6px)}
.padbadge{position:absolute;top:10px;left:12px;font-size:11px;font-variant-numeric:tabular-nums;color:var(--acc);
  letter-spacing:.4px;pointer-events:none;z-index:2;text-transform:uppercase}
.blip{position:absolute;width:52px;height:52px;margin:-26px 0 0 -26px;border-radius:50%;pointer-events:none;
  background:radial-gradient(circle,rgba(0,210,255,.30),rgba(0,210,255,.06) 65%,transparent 70%);
  border:1px solid rgba(0,210,255,.45);will-change:transform}
/* long press progress - tells you the grab is coming instead of leaving you guessing */
.hold{position:absolute;width:58px;height:58px;margin:-29px 0 0 -29px;border-radius:50%;pointer-events:none;
  border:2px solid var(--ok);opacity:0;transform:scale(.32);will-change:transform,opacity}
.hold.go{animation:holdgrow linear forwards}
@keyframes holdgrow{from{opacity:.12;transform:scale(.32)}to{opacity:.9;transform:scale(1)}}
.padwrap.fs .fsbar{
  position:absolute;left:0;right:0;bottom:0;z-index:3;display:flex;gap:8px;padding:10px 12px;
  /* Safari draws its own toolbar over the bottom of the viewport and does not
     always report it through env(), so the bar is lifted clear by a floor value
     as well. Without this the Exit button sits underneath browser chrome and
     cannot be tapped at all. */
  padding-bottom:calc(10px + max(env(safe-area-inset-bottom), 20px));
  background:linear-gradient(0deg,rgba(6,8,13,.94),rgba(6,8,13,0));
}
.fsbar{display:none}
.padwrap.fs .fsbar{display:flex}
/* The bar holds pad controls only. Leaving fullscreen lives at the top, out of
   reach of the thumbs that are working the pad, so a drag cannot end the session
   by accident. */
.padwrap.fs .fsbar .btn{flex:1 1 0;min-width:0}
/* the hints would otherwise sit behind that bar */
.padwrap.fs .padhint{bottom:calc(80px + env(safe-area-inset-bottom))}
/* the labelled pill says it better than an icon, so the corner toggle steps
   aside and leaves Grab alone up there */
.padwrap.fs .padtools #btnFs{display:none}
/* During real fullscreen only padwrap is rendered, so the way out has to live
   inside it. Top-left, labelled, and clear of every browser's chrome. */
.fsexit{display:none}
.padwrap.fs .fsexit{
  display:inline-flex;align-items:center;gap:7px;position:absolute;z-index:4;
  top:calc(10px + env(safe-area-inset-top));left:calc(12px + env(safe-area-inset-left));
  height:36px;padding:0 14px;border-radius:999px;font-size:13px;font-weight:600;
  background:rgba(0,0,0,.55);backdrop-filter:blur(8px);
  border:1px solid rgba(255,255,255,.18);color:var(--fg);
}
.padwrap.fs .fsexit:active{transform:scale(.95);background:rgba(0,0,0,.72)}
.padwrap.fs .padtools{top:calc(10px + env(safe-area-inset-top));right:calc(10px + env(safe-area-inset-right))}
/* the app's own chrome must never sit over a fullscreen pad */
body.fsmode .tabbar,body.fsmode .topbar{display:none}

/* ---------------- knobs ---------------- */
.knobs{display:grid;grid-template-columns:repeat(auto-fit,minmax(146px,1fr));gap:14px}
.knob{display:flex;flex-direction:column;align-items:center;gap:10px;padding:14px 8px 12px;border-radius:var(--r2);
  background:var(--surface2);border:1px solid var(--line);position:relative}
.knob .dial{position:relative;width:100%;max-width:138px;aspect-ratio:1;touch-action:none;user-select:none;cursor:grab}
.knob .dial:active{cursor:grabbing}
.knob .tks{position:absolute;inset:0;width:100%;height:100%;display:block;overflow:visible}
.tk{stroke:rgba(255,255,255,.16);stroke-width:1.9;stroke-linecap:round;transition:stroke .1s linear}
.tk.on{stroke:var(--acc)}
/* one soft bloom behind the lit ticks instead of a filter on each - filters per
   element are what makes a dial like this stutter on a phone */
.knob .glow{position:absolute;inset:6%;border-radius:50%;pointer-events:none;opacity:0;transition:opacity .18s;
  background:radial-gradient(circle,transparent 58%,rgba(0,210,255,.20) 72%,transparent 82%)}
.knob .dial.act .glow{opacity:1}
.knob .cap{
  position:absolute;inset:14%;border-radius:50%;pointer-events:none;
  background:radial-gradient(circle at 34% 26%,#333b47 0%,#1b212b 44%,#0b0e14 100%);
  border:1px solid rgba(255,255,255,.07);
  box-shadow:
    0 12px 24px rgba(0,0,0,.7),
    0 3px 6px rgba(0,0,0,.55),
    inset 0 1.5px 0 rgba(255,255,255,.13),
    inset 0 -12px 20px rgba(0,0,0,.6);
}
.knob .cap::after{
  content:'';position:absolute;inset:7%;border-radius:50%;
  background:linear-gradient(158deg,rgba(255,255,255,.07),transparent 52%);
}
.knob .ptr{position:absolute;inset:14%;border-radius:50%;pointer-events:none;will-change:transform}
.knob .ptr i{
  position:absolute;left:50%;top:8%;width:7px;height:7px;margin-left:-3.5px;border-radius:50%;
  background:var(--acc);box-shadow:0 0 8px 2px rgba(0,210,255,.7),0 0 3px rgba(255,255,255,.9);
}
.knob .kv{position:absolute;inset:0;display:grid;place-items:center;pointer-events:none;text-align:center}
.knob .kv em{display:block;font-style:normal;font-size:14px;font-weight:600;font-variant-numeric:tabular-nums;color:var(--fg2)}
.knob .mn,.knob .mx{position:absolute;bottom:2%;font-size:8.5px;letter-spacing:1px;color:var(--fg3);pointer-events:none}
.knob .mn{left:2%}.knob .mx{right:2%}
.knob .kn{display:flex;align-items:center;justify-content:center;gap:6px;font-size:12px;font-weight:560;
  color:var(--fg2);text-align:center;line-height:1.2}
.knob .kn .ic{width:16px;height:16px;color:var(--fg3)}
.knob .edit{position:absolute;top:6px;right:6px;width:28px;height:28px;border-radius:8px;display:grid;place-items:center;
  color:var(--fg3);opacity:0;transition:.16s;z-index:2}
.knob:hover .edit,.knob:focus-within .edit{opacity:1}
@media(hover:none){.knob .edit{opacity:.55}}

/* ---------------- beta badge ---------------- */
.beta{display:inline-block;margin-left:6px;padding:1px 6px;border-radius:6px;font-size:9px;
  font-weight:700;letter-spacing:.5px;text-transform:uppercase;vertical-align:middle;
  color:#ffcf6b;background:rgba(255,180,60,.14);border:1px solid rgba(255,180,60,.5)}
.navbtn .beta{font-size:8px;padding:0 4px;margin-left:5px}
.tabbar .hasbeta i::after{content:"\2022";color:#ffcf6b;margin-left:3px;font-size:11px}

/* ---------------- gamepad ---------------- */
/* always three across, whatever the width - a controller that stacks vertically
   on a phone is unusable, so the parts scale instead of wrapping */
.padlayout{display:grid;gap:10px;grid-template-columns:1fr auto 1fr;align-items:center}
.stick{width:100%;max-width:clamp(92px,25vw,190px);aspect-ratio:1;margin:0 auto;position:relative;touch-action:none;user-select:none}
.stick .base{position:absolute;inset:0;border-radius:50%;
  background:radial-gradient(circle at 50% 38%,#182233,#0b1018 70%);
  border:1px solid var(--line);box-shadow:inset 0 8px 22px rgba(0,0,0,.6),0 4px 18px rgba(0,0,0,.4)}
.stick .ring{position:absolute;inset:13%;border-radius:50%;border:1px dashed rgba(255,255,255,.09)}
.stick .cross{position:absolute;inset:0;opacity:.22}
.stick .cross i{position:absolute;background:var(--fg3)}
.stick .cross i:nth-child(1){left:50%;top:8%;bottom:8%;width:1px;transform:translateX(-.5px)}
.stick .cross i:nth-child(2){top:50%;left:8%;right:8%;height:1px;transform:translateY(-.5px)}
.stick .cap{
  position:absolute;left:50%;top:50%;width:46%;height:46%;margin:-23% 0 0 -23%;border-radius:50%;
  background:radial-gradient(circle at 42% 32%,#3d4a5e,#141b26 72%);
  border:1px solid rgba(255,255,255,.14);
  box-shadow:0 8px 18px rgba(0,0,0,.55),inset 0 -6px 12px rgba(0,0,0,.5),inset 0 4px 8px rgba(255,255,255,.06);
  will-change:transform;transition:transform .16s var(--ease);
}
.stick.act .cap{transition:none}
.stick.act .base{border-color:var(--line2)}
.stick .lbl{position:absolute;left:0;right:0;bottom:-19px;text-align:center;font-size:10px;color:var(--fg3);letter-spacing:1px}
.dpad{width:clamp(102px,29vw,150px);aspect-ratio:1;position:relative;margin:0 auto;touch-action:none}
.dpad button{position:absolute;background:var(--surface2);border:1px solid var(--line);display:grid;place-items:center;color:var(--fg2)}
.dpad .u{left:33.3%;top:0;width:33.4%;height:34.7%;border-radius:10px 10px 0 0}
.dpad .d{left:33.3%;bottom:0;width:33.4%;height:34.7%;border-radius:0 0 10px 10px}
.dpad .l{top:33.3%;left:0;width:34.7%;height:33.4%;border-radius:10px 0 0 10px}
.dpad .r{top:33.3%;right:0;width:34.7%;height:33.4%;border-radius:0 10px 10px 0}
.dpad .c{left:33.3%;top:33.3%;width:33.4%;height:33.4%;background:var(--surface);border:1px solid var(--line)}
.dpad button:active,.dpad button.on{background:rgba(0,210,255,.25);border-color:var(--acc);color:var(--fg)}
.faces{width:clamp(102px,29vw,150px);aspect-ratio:1;position:relative;margin:0 auto}
.faces button{position:absolute;width:34.7%;height:34.7%;border-radius:50%;font-weight:700;font-size:clamp(12px,3.4vw,15px);
  background:var(--surface2);border:1px solid var(--line);display:grid;place-items:center}
.faces .n{left:32.7%;top:0}.faces .s{left:32.7%;bottom:0}.faces .w{left:0;top:32.7%}.faces .e{right:0;top:32.7%}
.faces .s{color:#7ee787;border-color:rgba(126,231,135,.35)}
.faces .e{color:#ff7b72;border-color:rgba(255,123,114,.35)}
.faces .w{color:#79c0ff;border-color:rgba(121,192,255,.35)}
.faces .n{color:#f2cc60;border-color:rgba(242,204,96,.35)}
.faces button:active,.faces button.on{background:rgba(0,210,255,.22);border-color:var(--acc)}
.gpoff{position:relative}
.gpoff::after{content:'';position:absolute;inset:-4px;border-radius:var(--r);background:rgba(7,9,15,.72);backdrop-filter:blur(2px)}
.gpoff>.gpmsg{position:absolute;inset:0;z-index:2;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;text-align:center;padding:20px}
.gpoff>.gpmsg .ic{width:30px;height:30px;flex:0 0 auto;color:var(--fg3)}
.gpoff>.gpmsg p{max-width:34ch}

/* ---------------- keyboard ---------------- */
/* The keyboard is up to three blocks side by side, exactly as a physical one is:
   the alphanumeric block, the navigation cluster and the numpad. Which blocks
   exist is decided in JS from the measured width - see kbTier - so a wider
   screen gets more keys rather than the same few keys stretched.

   The flex ratios are the real proportions: ANSI main is 15u, the nav cluster is
   3u and the numpad is 4u. Giving the blocks those numbers means one key is the
   same width in all three, which is what makes them read as one keyboard.

   Blocks are bottom aligned, so the space bar and the arrow cluster share a
   baseline however many clusters sit above them. Every row is a grid, because
   flexbox could not hold the columns: with flex-basis:0 the unit width depends
   on the number of gaps in the row, so the up arrow and the down arrow landed a
   couple of pixels apart. */
.kbd{display:flex;align-items:flex-end;gap:10px;margin-top:12px;
  /* One row height for every block, or they cannot line up. Six rows plus the
     type field sit between the header and the tab bar, so a short window needs
     short keys and a tall one can afford more. */
  --kh:clamp(26px, calc((100dvh - 250px) / 7), 52px)}
.kblock{display:flex;flex-direction:column;gap:6px;min-width:0}
.kmain{flex:15 1 0}
/* The nav cluster is a fifth of the main block's width and its legends are
   words, so it is the one block whose type has to be sized from its own width
   rather than from the tier. It is a query container for that reason - and it
   holds no fixed descendant, so the containment it brings is harmless here. */
.kside{flex:3 1 0;container-type:inline-size}
.knum{flex:4 1 0}
.krow{display:grid;gap:6px}
/* Twelve columns for a phone held upright: enough for a left Shift, the letters,
   the slash, a right Shift and a real inverted-T arrow cluster, which ten could
   not hold at once. */
.kmain.c12 .krow{grid-template-columns:repeat(12,1fr)}
/* ANSI is exactly 15u wide, so sixty columns makes 1u four columns and every
   real key width - 1.25u, 1.5u, 1.75u, 2u, 2.25u, 2.75u, 6.25u - lands on a
   whole number. That is what makes these rows authentic rather than guessed. */
.kmain.ansi .krow{grid-template-columns:repeat(60,1fr)}
.kside .krow{grid-template-columns:repeat(3,1fr)}
.kpad{display:grid;grid-template-columns:repeat(4,1fr);grid-auto-rows:var(--kh);gap:6px}
/* On the compact layout the function and navigation rows carry long labels and
   leave the twelve column grid rather than truncate them. Neither holds an
   arrow, so nothing depends on their alignment. */
.kmain.c12 .krow.fn{grid-template-columns:repeat(auto-fit,minmax(36px,1fr))}
.kmain.c12 .krow.nav{grid-template-columns:repeat(auto-fit,minmax(58px,1fr))}
.kgap{height:var(--kh)}
.krow button,.kpad button{
  min-width:0;padding:0 2px;border-radius:9px;height:var(--kh);
  background:var(--surface2);border:1px solid var(--line);font-size:13px;font-weight:560;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;transition:transform .1s var(--ease),background .14s;
  /* fast repeated taps otherwise start selecting the key's own label and pop up
     the copy bar over the keyboard */
  user-select:none;-webkit-user-select:none;-webkit-touch-callout:none;
}
/* The tall Plus and Enter span two rows, so they have to grow with the gap too. */
.kpad button{height:auto;min-height:var(--kh)}
.krow button:active,.kpad button:active{transform:scale(.92);background:rgba(0,210,255,.28)}
.krow button.dn,.kpad button.dn{background:rgba(0,210,255,.34);border-color:var(--acc);transform:scale(.94)}
.krow button.mod{background:rgba(0,210,255,.08);color:var(--acc)}
.krow button.mod.on{background:rgba(0,210,255,.34);border-color:var(--acc);color:var(--fg)}
.krow button .ic,.kpad button .ic{margin:0 auto}
/* The wider layouts carry far more keys, so their caps have to be smaller or the
   labels collide. Sized per tier rather than per breakpoint, because what
   matters is how many keys are in the row, not how wide the window is. */
.kbd.t-ansi .krow button{font-size:12px}
.kbd.t-tkl .krow button,.kbd.t-full .krow button,
.kbd.t-tkl .kpad button,.kbd.t-full .kpad button{font-size:11.5px}
/* Three columns and two gaps, so one nav key is a shade under a third of the
   block: 9cqi keeps a five character legend like PrtSc inside its cap at every
   width. A fixed size cannot - 10px fitted the cluster on a laptop and cut Home
   in half on a 25px column, which is what shipped before this.
   The leading .kbd is load bearing: without it this loses to the tier rules
   above on specificity and the cluster silently keeps their 12px type. */
.kbd .kside .krow button{font-size:clamp(6px,9cqi,11px);padding:0 1px;letter-spacing:-.01em}
/* Phone keyboards win their key size by giving up page padding, not by shrinking
   the keys. Same trade here, so a 375px screen gets ~30px keys like iOS does. */
@media(max-width:430px){
  .krow,.kblock{gap:5px}
  .kbd{gap:6px}
  /* 1px of side padding rather than 2. With twelve columns a 360px phone gives a
     23px key, and the widest modifier cap needs every pixel back that the
     padding was taking. */
  .krow button{font-size:12px;border-radius:8px;padding:0 1px}
  .kmain.c12 .krow.fn{grid-template-columns:repeat(auto-fit,minmax(34px,1fr))}
  .kmain.c12 .krow.nav{grid-template-columns:repeat(auto-fit,minmax(54px,1fr))}
  /* A phone keyboard buys its key size from the page margin, not by shrinking
     the keys. Same trade: the keys card gives up side padding and the keyboard
     itself bleeds a little past it. */
  #v-keys .card{padding-left:9px;padding-right:9px}
  .kbd{margin-left:-4px;margin-right:-4px}
}
@media(max-width:359px){
  .krow,.kblock{gap:4px}
  .krow button{font-size:10.5px;border-radius:7px;padding:0 1px}
  .krow button .ic{width:15px;height:15px}
  .kmain.c12 .krow.fn{grid-template-columns:repeat(auto-fit,minmax(30px,1fr))}
  .kmain.c12 .krow.nav{grid-template-columns:repeat(auto-fit,minmax(48px,1fr))}
  #v-keys .card{padding-left:7px;padding-right:7px}
  .kbd{margin-left:-5px;margin-right:-5px}
}
/* Past this a full-size layout is already on screen, so the cap only stops the
   keys becoming absurdly wide on a very large display. */
@media(min-width:1500px){
  .kbd{max-width:1460px;margin-left:auto;margin-right:auto}
}
/* Under about 440px of height the compact layout is tight, so the card heading
   steps aside to give the keys the room. Rows are never hidden: Esc, Del, PgUp
   and PgDn exist on those two rows and nowhere else, so dropping them would take
   keys away with no way to reach them. */
@media(max-height:440px){
  #v-keys .card>.ch{display:none}
  .kbd{--kh:clamp(24px, calc((100dvh - 150px) / 7), 44px);margin-top:8px}
}
/* Below about 300px a twelve column row gives 17px keys with the labels cut off.
   Wrapping is the lesser evil: arrow alignment is lost, but every key is
   readable and hittable, which matters more on a screen this small. */
@media(max-width:309px){
  .kmain.c12 .krow{grid-template-columns:repeat(auto-fit,minmax(30px,1fr))}
  .kmain.c12 .krow.nav{grid-template-columns:repeat(auto-fit,minmax(44px,1fr))}
  .krow button{font-size:10.5px}
  .krow button{font-size:10.5px}
}

/* ---------------- shortcuts ---------------- */
.scbar{display:flex;flex-wrap:wrap;gap:9px;align-items:center;margin-bottom:13px}
.scbar .seg{flex:1 1 210px}
.scbar .seg button{flex:1 1 0;padding:0 8px}
.scsearch{flex:2 1 170px;min-width:0;min-height:40px;padding:9px 12px;border-radius:var(--r2);
  background:rgba(0,0,0,.32);border:1px solid var(--line);font-size:16px}
.scsearch:focus{outline:none;border-color:var(--acc);box-shadow:0 0 0 3px rgba(0,210,255,.13)}
/* Collapsible group. No overflow:hidden - the header rounds its own top corners
   instead, so nothing is ever clipped against the card's radius. */
.scg{border:1px solid var(--line);border-radius:var(--r2);background:rgba(255,255,255,.02)}
.scg+.scg{margin-top:9px}
.scgh{display:flex;align-items:center;gap:10px;width:100%;padding:12px 13px;text-align:left;
  border-radius:var(--r2);transition:background .16s}
.scgh .ic{color:var(--acc)}
.scgh h2{flex:1;min-width:0;font-size:13px;font-weight:620;letter-spacing:.3px;text-transform:uppercase}
.scgh .cnt{font-size:11px;color:var(--fg3);font-variant-numeric:tabular-nums}
.scgh .chev{color:var(--fg3);transition:transform .18s var(--ease)}
.scg.open .scgh{background:rgba(0,210,255,.06);border-radius:var(--r2) var(--r2) 0 0}
.scg.open .scgh .chev{transform:rotate(180deg)}
/* 132px is the widest minimum that still gives two columns inside a card on a
   360px phone, once main, card and grid padding are taken out. */
.scgrid{display:none;grid-template-columns:repeat(auto-fill,minmax(132px,1fr));gap:8px;padding:0 10px 10px}
.scg.open .scgrid{display:grid}
/* label above, keys below - a wide row wastes most of a phone's width */
.sck{display:flex;flex-direction:column;align-items:flex-start;justify-content:space-between;gap:8px;
  min-height:70px;padding:10px;border-radius:10px;background:var(--surface2);border:1px solid var(--line);
  text-align:left;transition:transform .12s var(--ease),background .16s,border-color .16s}
.sck b{font-size:12.5px;font-weight:520;line-height:1.3;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.sck:active{transform:scale(.96);background:rgba(0,210,255,.18);border-color:var(--acc)}
.sck .keys{max-width:100%;justify-content:flex-start}
.keys{display:flex;align-items:center;gap:3px;flex:0 0 auto;flex-wrap:wrap;justify-content:flex-end;max-width:56%}
/* a key cap, not a code span - the thicker bottom border is the whole illusion */
.kcap{display:inline-flex;align-items:center;justify-content:center;min-width:23px;height:23px;padding:0 6px;
  border-radius:6px;background:rgba(255,255,255,.07);border:1px solid var(--line);border-bottom-width:2px;
  font-size:11px;font-weight:600;color:var(--fg2);white-space:nowrap}
.kcap.m{color:var(--acc);background:rgba(0,210,255,.10);border-color:rgba(0,210,255,.30);border-bottom-width:2px}
.kjoin{font-size:10px;color:var(--fg3)}

/* ---------------- app switcher ---------------- */
/* Mirrors what the host is already drawing: Alt stays down, one Tab per step,
   and letting go is the thing that actually raises the window. */
.asw{position:fixed;inset:0;z-index:700;display:none;flex-direction:column;align-items:center;justify-content:center;
  gap:15px;padding:24px;background:rgba(4,6,10,.88);backdrop-filter:blur(8px);text-align:center}
.asw.on{display:flex}
.asw h3{font-size:18px;font-weight:620}
.asw p{font-size:13px;color:var(--fg3);max-width:330px;line-height:1.5}
/* Four way, because the host's switcher is a grid of thumbnails, not a list.
   Left and right step with Tab, up and down move a row with the arrow keys. */
.aswpad{display:grid;grid-template-columns:repeat(3,auto);gap:10px;justify-content:center;align-items:center}
#aswUp{grid-area:1/2}#aswPrev{grid-area:2/1}#aswN{grid-area:2/2}#aswNext{grid-area:2/3}#aswDown{grid-area:3/2}
.aswbig{width:66px;height:66px;border-radius:50%;display:grid;place-items:center;
  background:var(--surface2);border:1px solid var(--line);color:var(--fg);transition:transform .12s var(--ease),background .14s}
.aswbig .ic{width:28px;height:28px}
.aswbig:active,.aswbig.hot{background:rgba(0,210,255,.24);border-color:var(--acc);transform:scale(.93)}
.aswn{font-size:26px;font-weight:650;font-variant-numeric:tabular-nums;color:var(--acc);text-align:center}
.asw .row{width:min(330px,100%)}
/* this tile is slid across, not tapped, so the browser must not treat the drag
   as a scroll and steal the pointer half way through */
[data-gs="switch_app"]{touch-action:none}

/* ---------------- PIN entry ---------------- */
/* .pinrow .pinbox, not .pinbox alone - `.fld input` sets width:100% and would
   otherwise win on specificity and stretch every box across the card. */
.pinrow{display:flex;gap:10px;justify-content:center;margin:6px 0 2px}
.pinrow .pinbox{
  width:56px;height:64px;flex:0 0 auto;padding:0;text-align:center;
  font-size:26px;font-weight:650;font-variant-numeric:tabular-nums;
  border-radius:13px;background:rgba(0,0,0,.34);border:1px solid var(--line);
  caret-color:var(--acc);transition:border-color .15s,box-shadow .15s,background .15s;
}
.pinrow .pinbox:focus{outline:none;border-color:var(--acc);box-shadow:0 0 0 3px rgba(0,210,255,.16)}
.pwrow{display:flex;gap:8px;align-items:center}
.pwrow input{flex:1 1 auto;width:0;min-width:0}
.pwrow .btn{flex:0 0 auto}

/* ---------------- colophon ---------------- */
.colophon{display:flex;flex-direction:column;align-items:center;text-align:center;gap:7px;padding:26px 18px}
.colophon .mark{width:46px;height:46px;fill:none;stroke:var(--acc);stroke-width:2;
  stroke-linecap:round;stroke-linejoin:round;color:var(--acc)}
.colophon .name{font-size:21px;font-weight:700;letter-spacing:.4px;color:var(--fg)}
.colophon .blurb{font-size:12.5px;color:var(--fg3);max-width:34ch;line-height:1.5}
.colophon .by{display:flex;align-items:center;gap:6px;margin-top:5px;font-size:13px;color:var(--fg2)}
.colophon .by .heart{width:14px;height:14px;color:#ff5a7a}
.colophon .ver{font-size:11px;color:var(--fg3);font-variant-numeric:tabular-nums;letter-spacing:.3px}
.pinrow .pinbox.filled{border-color:var(--acc2);background:rgba(0,210,255,.09)}
.pinrow.bad .pinbox{border-color:var(--bad);background:rgba(255,90,90,.10)}
.pinrow.bad{animation:shake .32s var(--ease)}
@keyframes shake{20%{transform:translateX(-6px)}60%{transform:translateX(6px)}100%{transform:none}}
@media(max-width:359px){.pinrow{gap:7px}.pinrow .pinbox{width:48px;height:56px;font-size:22px}}

/* ---------------- mouse view ---------------- */
/* Shaped like the thing it stands for, so left and right need no label to be
   understood. One radius value is shared by the shell and the clip below it. */
.mouse{--mshape:47% 47% 36% 36%/40% 40% 28% 28%;
  width:min(178px,62%);aspect-ratio:.72;margin:0 auto;position:relative}
.mouse .shell{position:absolute;inset:0;border-radius:var(--mshape);
  background:linear-gradient(170deg,rgba(255,255,255,.07),rgba(255,255,255,.02));
  border:1px solid var(--line);box-shadow:inset 0 -14px 26px rgba(0,0,0,.45),0 8px 22px rgba(0,0,0,.35)}
/* The halves are plain rectangles clipped by the shell's own outline, so a press
   lights exactly the shape of that button - curved at the shoulder, straight
   where the two meet - instead of an approximation of it. */
.mouse .btns{position:absolute;inset:0;border-radius:var(--mshape);overflow:hidden}
.mouse .btns button{position:absolute;top:0;height:47%;width:50%;padding:15% 0 0;
  background:none;border:0;color:var(--fg3);font-size:10.5px;font-weight:500;letter-spacing:.3px;
  display:flex;align-items:flex-start;justify-content:center;transition:background .13s,color .13s}
.mouse .btns .l{left:0}
.mouse .btns .r{right:0}
.mouse .btns button:active{background:rgba(0,210,255,.26);color:var(--fg)}
/* the line stops short of the wheel at both ends, so nothing shows through it */
.mouse .split{position:absolute;left:50%;top:2%;height:45%;width:1px;transform:translateX(-.5px);
  background:linear-gradient(var(--line) 0 24%,transparent 24% 80%,var(--line) 80% 100%)}
.mouse .m{position:absolute;left:39.5%;top:13%;width:21%;height:25%;padding:0;border-radius:999px;
  touch-action:none;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14%;overflow:hidden;
}
/* Ridges, so it reads as something you drag rather than something you press. */
.mouse .m i{display:block;width:52%;height:1.5px;border-radius:1px;background:rgba(255,255,255,.22);
  transition:background .14s}
.mouse .m.spin i{background:rgba(0,210,255,.65)}
.mouse .m.spin{background:rgba(0,210,255,.20);border-color:var(--acc)}
  background:#131922;border:1px solid var(--line);transition:background .13s,border-color .13s}
.mouse .m:active{background:rgba(0,210,255,.32);border-color:var(--acc)}

/* ---------------- lists / logs ---------------- */
.list{display:flex;flex-direction:column;gap:7px}
.item{display:flex;align-items:center;gap:11px;padding:11px 12px;border-radius:var(--r2);background:var(--surface2);border:1px solid var(--line)}
.warnbox{background:color-mix(in srgb,var(--bad) 12%,var(--surface2));border-color:color-mix(in srgb,var(--bad) 38%,var(--line));margin-bottom:12px}
.item .tx{flex:1;min-width:0}
.item .tx b{display:block;font-size:13.5px;font-weight:560;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.item .tx span{display:block;font-size:11.5px;color:var(--fg3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
/* A saved email address is a long label next to three or four controls. The text
   is what gives way, never the buttons - they used to be squeezed into each
   other on a 375px phone. */
.item>.btn{flex:0 0 auto}
.item>.ic{flex:0 0 auto}
@media(max-width:400px){
  .item{gap:8px;padding:10px 10px}
  .item>.iconbtn{width:34px;height:34px;border-radius:10px}
  .item>.btn.sm{padding:0 10px;font-size:12.5px}
}
@media(max-width:340px){
  .item{gap:6px}
  .item>.ic{display:none}          /* buys back 30px on an iPhone SE */
  .item>.iconbtn{width:31px;height:31px}
}
.tag{display:inline-block;vertical-align:2px;margin-left:6px;padding:1px 7px;border-radius:999px;font-size:10px;font-weight:600;letter-spacing:.02em;color:var(--ok);background:color-mix(in srgb,var(--ok) 16%,transparent);border:1px solid color-mix(in srgb,var(--ok) 38%,transparent)}
.log{background:#04060a;border:1px solid var(--line);border-radius:var(--r2);padding:10px;
  font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11.5px;color:#6ee7a8;max-height:190px;overflow-y:auto;margin-top:10px}
.log div{white-space:pre-wrap;word-break:break-all;margin:1px 0}
.log .e{color:#ff8080}
.kv2{display:grid;grid-template-columns:auto 1fr;gap:6px 14px;font-size:13px}
.kv2 dt{color:var(--fg3)}
.kv2 dd{text-align:right;font-variant-numeric:tabular-nums;word-break:break-all}

/* ---------------- sheet / toast ---------------- */
.sheet{position:fixed;inset:0;z-index:800;display:none;align-items:flex-end;justify-content:center;
  background:rgba(4,6,10,.66);backdrop-filter:blur(3px)}
.sheet.on{display:flex}
.sheetin{width:100%;max-width:520px;max-height:88vh;overflow-y:auto;background:var(--bg2);
  border:1px solid var(--line);border-radius:20px 20px 0 0;padding:18px;
  padding-bottom:calc(18px + env(safe-area-inset-bottom));animation:up .24s var(--ease)}
@media(min-width:640px){.sheet{align-items:center}.sheetin{border-radius:18px;animation:pop .2s var(--ease)}}
@keyframes up{from{transform:translateY(100%)}to{transform:none}}
@keyframes pop{from{transform:scale(.95);opacity:0}to{transform:none;opacity:1}}
.sheeth{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.sheeth h3{flex:1;font-size:16px;font-weight:620}
.toasts{position:fixed;left:50%;top:calc(12px + env(safe-area-inset-top));transform:translateX(-50%);z-index:900;
  display:flex;flex-direction:column;gap:8px;align-items:center;pointer-events:none;width:max-content;max-width:92vw}
/* reboot takes the USB device away and drops the socket - say so, and show the
   reconnect attempts, rather than looking dead */
.boot{position:fixed;inset:0;z-index:950;display:none;flex-direction:column;align-items:center;justify-content:center;
  gap:14px;background:rgba(5,7,12,.94);backdrop-filter:blur(6px);text-align:center;padding:24px}
.boot.on{display:flex}
.boot .sp{width:46px;height:46px;border-radius:50%;border:3px solid rgba(255,255,255,.12);
  border-top-color:var(--acc);animation:spin .9s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.boot b{font-size:16px;font-weight:620}
.boot span{font-size:13px;color:var(--fg3);max-width:300px}
.boot .bar{width:min(280px,70vw);height:4px;border-radius:2px;background:rgba(255,255,255,.1);overflow:hidden}
.boot .bar i{display:block;height:100%;width:0%;background:var(--acc);transition:width .35s linear}
/* the join progress bar lives in a card, not the boot overlay */
#joinBar{width:100%;height:4px;border-radius:2px;background:rgba(255,255,255,.1);overflow:hidden;margin-top:8px}
#joinBar i{display:block;height:100%;width:0%;background:var(--acc);transition:width .5s linear}
/* the new credentials have to stay on screen - the phone is about to leave the
   network and there is nowhere else to read them from */
#apnewInfo{background:var(--surface);border:1px solid var(--line);border-radius:12px;
  padding:13px 15px;min-width:min(300px,82vw);gap:8px 16px;text-align:left}
#apnewInfo dd{font-weight:600;color:var(--fg);word-break:break-all}
.toast{display:flex;align-items:center;gap:9px;padding:11px 15px;border-radius:12px;font-size:13.5px;
  background:rgba(13,17,25,.97);border:1px solid var(--line);box-shadow:0 10px 30px rgba(0,0,0,.5);
  animation:tin .22s var(--ease)}
.toast.ok{border-color:rgba(34,211,154,.5)}.toast.bad{border-color:rgba(255,90,90,.5)}
@keyframes tin{from{opacity:0;transform:translateY(-10px)}to{opacity:1;transform:none}}

/* ---------------- mobile ---------------- */
.tabbar{display:none}
/* Below 1100px the rail starts collapsed, but that is only a default - the
   collapsed styling itself lives on body.rail-slim so the user's own choice can
   win at any width. RAIL.init() decides which applies. */
@media(max-width:767px){
  .rail{display:none}
  main{padding:12px;padding-bottom:calc(var(--tab) + 12px + var(--sab));
       padding-left:calc(12px + var(--sal));padding-right:calc(12px + var(--sar))}
  .topbar{padding:10px 12px;padding-top:calc(10px + var(--sat));
          padding-left:calc(12px + var(--sal));padding-right:calc(12px + var(--sar))}
  .topbar h1{font-size:15px}
  .topbar .tmark{display:grid;width:26px;height:26px;flex:0 0 26px;border-radius:8px}
  .topbar .tmark .ic{width:17px;height:17px}
  .grid{grid-template-columns:1fr;gap:12px}
  .card{padding:14px;border-radius:12px}
  .tabbar{
    display:flex;position:fixed;left:0;right:0;bottom:0;z-index:500;height:calc(var(--tab) + var(--sab));
    padding-bottom:var(--sab);padding-left:var(--sal);padding-right:var(--sar);
    background:rgba(8,11,17,.94);backdrop-filter:blur(18px);border-top:1px solid var(--line);
  }
  .tabbar button{flex:1 1 0;min-width:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;color:var(--fg3);position:relative}
  .tabbar button i{font-style:normal;font-size:9px;font-weight:560;letter-spacing:.1px;
    max-width:100%;padding:0 2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .tabbar button.on{color:var(--acc)}
  .tabbar button.on::before{content:'';position:absolute;top:0;left:22%;right:22%;height:2px;border-radius:0 0 3px 3px;background:var(--acc)}
  .padwrap.fs{z-index:1000}
}
@media(max-width:359px){
  :root{--sp:10px}
  .tabbar button i{font-size:8.5px}
  .tabbar button .ic{width:19px;height:19px}
  .tiles{grid-template-columns:repeat(auto-fit,minmax(74px,1fr));gap:7px}
  .tile{min-height:70px}
  .tile span{font-size:10px}
  .knobs{grid-template-columns:repeat(auto-fit,minmax(110px,1fr))}
  .pill{height:27px;padding:0 9px;font-size:11px}
  /* At this width the title has nothing left to give, so the count only earns
     its place when it is saying something: more than one dashboard. */
  .pill.conn{display:none}
  .pill.conn.multi{display:inline-flex}
}
/* ---------------- landscape on a phone ----------------
   A phone on its side is wide enough to keep the rail, but only ~400px tall, and
   the width breakpoints above would happily give it a portrait layout. Height is
   what is scarce here, so the vertical chrome shrinks and the trackpad is sized
   from the space that is actually left. */
@media(orientation:landscape) and (max-height:500px){
  .topbar{padding-top:calc(7px + var(--sat));padding-bottom:7px}
  .topbar h1{font-size:15px}
  .topbar .tmark{width:26px;height:26px;flex:0 0 26px;border-radius:8px}
  .topbar .tmark .ic{width:17px;height:17px}
  .brand{padding:2px 0 8px}
  .rail{padding-top:calc(8px + var(--sat));padding-bottom:calc(8px + var(--sab));gap:2px}
  .navbtn{padding:8px 0}
  .linkstat{padding:7px 0}
  main{padding-top:9px}
  .card{padding:12px}
  /* 38vh of 400px is 152px, which is not a trackpad. Take the room from the
     viewport instead, minus the topbar and the card's own padding. */
  #pad{height:clamp(150px,calc(100vh - 150px),300px)}
  .padwrap{--padh:clamp(150px,calc(100vh - 150px),300px)}
  .padhint{display:none}
  .kbd{margin-top:8px}
}
/* ---------------- very large displays ----------------
   A 4K panel running at 100% OS scaling reports 3840 CSS pixels, so a 15px font
   is 15 real pixels on a 32 inch screen and the whole app reads like a stamp.
   zoom on the root is the one lever that scales everything - type, icons, hit
   targets, borders - and Chrome shrinks the initial containing block to match,
   so media queries and vh units simply behave as though the screen were the
   smaller size. Nothing else in this sheet has to know about it.

   Height is in every condition on purpose: a 5120x1440 ultrawide is wide but
   short, and scaling that by width alone would leave no vertical room at all.
   Each step aims for an effective viewport of roughly 1900-2300px wide. */
@media(min-width:2400px) and (min-height:1300px){:root{zoom:1.25}}
@media(min-width:3000px) and (min-height:1600px){:root{zoom:1.5}}
@media(min-width:3800px) and (min-height:2000px){:root{zoom:2}}
@media(min-width:5000px) and (min-height:2600px){:root{zoom:2.5}}
@media(min-width:5900px) and (min-height:3200px){:root{zoom:3}}
@media(min-width:7000px) and (min-height:4000px){:root{zoom:3.5}}
@media(prefers-reduced-motion:reduce){*{animation-duration:.01ms!important;transition-duration:.01ms!important}}
</style>
</head>
<body>

<svg class="sprite" aria-hidden="true"><defs>
<!-- The mark: a pointer that is itself the transmitter. Two arcs break from its
     tip, which is the whole product in one glyph - touch here, cursor there. -->
<symbol id="i-logo" viewBox="0 0 24 24"><path style="stroke-width:2.2" d="M11.67 21.45A8.9 8.9 0 1 1 21.45 11.67"/><path style="fill:currentColor;stroke-width:1.5" d="M8.64 8.64 14.96 20.66 15.7 15.7 20.66 14.96Z"/></symbol>
<symbol id="i-cursor" viewBox="0 0 24 24"><path d="M5 3l5.5 16.5 2.6-6.9 6.9-2.6z"/></symbol>
<symbol id="i-hand" viewBox="0 0 24 24"><path d="M8.6 11.8V6.1a1.7 1.7 0 0 1 3.4 0v4.6"/><path d="M12 10.7V5.2a1.7 1.7 0 0 1 3.4 0v5.5"/><path d="M15.4 11V7.4a1.7 1.7 0 0 1 3.4 0V14a6.6 6.6 0 0 1-6.6 6.6h-.6a6.6 6.6 0 0 1-5.5-3l-2.3-3.5a1.75 1.75 0 0 1 2.9-1.9l1.9 2.6"/></symbol>
<symbol id="i-keyboard" viewBox="0 0 24 24"><rect x="2" y="5" width="20" height="14" rx="2.5"/><path d="M6 9h.01M10 9h.01M14 9h.01M18 9h.01M6 12.5h.01M10 12.5h.01M14 12.5h.01M18 12.5h.01M8 16h8"/></symbol>
<symbol id="i-knob" viewBox="0 0 24 24"><circle cx="12" cy="12" r="6.6"/><path d="M12 5.4v3.4"/><path d="M4.3 19.7 6 18M19.7 19.7 18 18M2.6 12h1.8M19.6 12h1.8"/></symbol>
<symbol id="i-gamepad" viewBox="0 0 24 24"><path d="M7 11h3M8.5 9.5v3M15 11h.01M17.5 13h.01"/><path d="M17.4 6H6.6a4.6 4.6 0 0 0-4.5 3.7l-1 5A4.2 4.2 0 0 0 5.2 19c1.2 0 1.9-.6 2.6-1.4L9.2 16h5.6l1.4 1.6c.7.8 1.4 1.4 2.6 1.4a4.2 4.2 0 0 0 4.1-4.3l-1-5A4.6 4.6 0 0 0 17.4 6Z"/></symbol>
<symbol id="i-power" viewBox="0 0 24 24"><path d="M12 3v9"/><path d="M6.4 6.4a8 8 0 1 0 11.2 0"/></symbol>
<symbol id="i-settings" viewBox="0 0 24 24"><path d="M4 6h10M18 6h2M4 12h2M10 12h10M4 18h7M15 18h5"/><circle cx="16" cy="6" r="2"/><circle cx="8" cy="12" r="2"/><circle cx="13" cy="18" r="2"/></symbol>
<symbol id="i-lock" viewBox="0 0 24 24"><rect x="4" y="10.5" width="16" height="10.5" rx="2.5"/><path d="M8 10.5V7a4 4 0 0 1 8 0v3.6"/></symbol>
<symbol id="i-unlock" viewBox="0 0 24 24"><rect x="4" y="10.5" width="16" height="10.5" rx="2.5"/><path d="M8 10.5V7a4 4 0 0 1 7.5-1.9"/></symbol>
<symbol id="i-mic" viewBox="0 0 24 24"><rect x="9" y="2.5" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7"/></symbol>
<symbol id="i-micoff" viewBox="0 0 24 24"><path d="M9 9v2.5a3 3 0 0 0 4.9 2.3M15 11.4V5.5a3 3 0 0 0-5.6-1.5"/><path d="M5.5 11a6.5 6.5 0 0 0 9.8 5.6M18.5 11v.6M12 17.5V21M8.5 21h7M3 3l18 18"/></symbol>
<symbol id="i-phone" viewBox="0 0 24 24"><path d="M20.6 16.7v2.6a1.9 1.9 0 0 1-2.1 1.9 18.6 18.6 0 0 1-8.1-2.9 18.4 18.4 0 0 1-5.7-5.7A18.6 18.6 0 0 1 1.8 4.5 1.9 1.9 0 0 1 3.7 2.4h2.6a1.9 1.9 0 0 1 1.9 1.7 12 12 0 0 0 .7 2.6 1.9 1.9 0 0 1-.5 2L7.2 10a15 15 0 0 0 5.7 5.7l1.3-1.2a1.9 1.9 0 0 1 2-.5 12 12 0 0 0 2.6.7 1.9 1.9 0 0 1 1.7 1.9Z"/></symbol>
<symbol id="i-phoneoff" viewBox="0 0 24 24"><g transform="translate(12 12) rotate(135) scale(.86) translate(-12 -12)" stroke-width="2.03"><path d="M20.6 16.7v2.6a1.9 1.9 0 0 1-2.1 1.9 18.6 18.6 0 0 1-8.1-2.9 18.4 18.4 0 0 1-5.7-5.7A18.6 18.6 0 0 1 1.8 4.5 1.9 1.9 0 0 1 3.7 2.4h2.6a1.9 1.9 0 0 1 1.9 1.7 12 12 0 0 0 .7 2.6 1.9 1.9 0 0 1-.5 2L7.2 10a15 15 0 0 0 5.7 5.7l1.3-1.2a1.9 1.9 0 0 1 2-.5 12 12 0 0 0 2.6.7 1.9 1.9 0 0 1 1.7 1.9Z"/></g></symbol>
<symbol id="i-monitor" viewBox="0 0 24 24"><rect x="2.5" y="4" width="19" height="12.5" rx="2"/><path d="M8.5 20.5h7M12 16.5v4"/></symbol>
<symbol id="i-vol" viewBox="0 0 24 24"><path d="M11 5 6.5 8.8H3.5v6.4h3L11 19z"/><path d="M15 9.5a3.5 3.5 0 0 1 0 5M17.8 6.8a7.5 7.5 0 0 1 0 10.4"/></symbol>
<symbol id="i-volx" viewBox="0 0 24 24"><path d="M11 5 6.5 8.8H3.5v6.4h3L11 19z"/><path d="m16 10 4.5 4.5M20.5 10 16 14.5"/></symbol>
<symbol id="i-play" viewBox="0 0 24 24"><path d="M7 4.8v14.4l12-7.2z"/></symbol>
<symbol id="i-prev" viewBox="0 0 24 24"><path d="M18 5.5v13L8.5 12zM5.5 5.5v13"/></symbol>
<symbol id="i-next" viewBox="0 0 24 24"><path d="M6 5.5v13L15.5 12zM18.5 5.5v13"/></symbol>
<symbol id="i-stop" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2"/></symbol>
<symbol id="i-sun" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3.4"/><path d="M12 1.6v3.4M12 19v3.4M1.6 12h3.4M19 12h3.4M4.4 4.4l2.4 2.4M17.2 17.2l2.4 2.4M4.4 19.6l2.4-2.4M17.2 6.8l2.4-2.4"/></symbol>
<symbol id="i-sundim" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3.4"/><path d="M12 3.3v1.7M12 19v1.7M3.3 12h1.7M19 12h1.7M5.85 5.85l1.2 1.2M16.95 16.95l1.2 1.2M5.85 18.15l1.2-1.2M16.95 7.05l1.2-1.2"/></symbol>
<symbol id="i-scrollnat" viewBox="0 0 24 24"><path d="M8 20.5V4.5M4.5 8 8 4.5 11.5 8"/><path d="M16 20.5V4.5M12.5 8 16 4.5 19.5 8"/></symbol>
<symbol id="i-scrollstd" viewBox="0 0 24 24"><path d="M8 20.5V4.5M4.5 8 8 4.5 11.5 8"/><path d="M16 4.5v16M12.5 17 16 20.5 19.5 17"/></symbol>
<symbol id="i-max" viewBox="0 0 24 24"><path d="M9 3.5H3.5V9M15 3.5h5.5V9M9 20.5H3.5V15M15 20.5h5.5V15"/></symbol>
<symbol id="i-min" viewBox="0 0 24 24"><path d="M3.5 9H9V3.5M20.5 9H15V3.5M3.5 15H9v5.5M20.5 15H15v5.5"/></symbol>
<symbol id="i-x" viewBox="0 0 24 24"><path d="M5.5 5.5 18.5 18.5M18.5 5.5 5.5 18.5"/></symbol>
<symbol id="i-plus" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></symbol>
<symbol id="i-trash" viewBox="0 0 24 24"><path d="M3.5 6.5h17M8.5 6.5V4.5A1 1 0 0 1 9.5 3.5h5a1 1 0 0 1 1 1v2M6 6.5l1 13a1.5 1.5 0 0 0 1.5 1.4h7A1.5 1.5 0 0 0 17 19.5l1-13M10 10.5v6M14 10.5v6"/></symbol>
<symbol id="i-check" viewBox="0 0 24 24"><path d="m4.5 12.5 5 5 10-11"/></symbol>
<symbol id="i-edit" viewBox="0 0 24 24"><path d="M4 20h4l10.5-10.5a2.8 2.8 0 0 0-4-4L4 16z"/><path d="M13.5 6.5 17.5 10.5"/></symbol>
<symbol id="i-info" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.8h.01"/></symbol>
<symbol id="i-wifi" viewBox="0 0 24 24"><path d="M2.5 9a15 15 0 0 1 19 0M5.8 12.7a10 10 0 0 1 12.4 0M9.2 16.4a5 5 0 0 1 5.6 0M12 20h.01"/></symbol>
<!-- Two devices, side by side rather than overlapping: strokes that cross each
     other turn to mush at the 14px this is actually drawn at. -->
<symbol id="i-devices" viewBox="0 0 24 24"><rect x="2.5" y="6" width="8" height="12.5" rx="1.8"/><rect x="13.5" y="3.5" width="8" height="17" rx="2"/></symbol>
<symbol id="i-shield" viewBox="0 0 24 24"><path d="M12 2.5 20 5.5v6c0 5-3.4 8.9-8 10.5-4.6-1.6-8-5.5-8-10.5v-6z"/><path d="m9 12 2 2 4-4"/></symbol>
<symbol id="i-eye" viewBox="0 0 24 24"><path d="M2.5 12S6.1 5.8 12 5.8 21.5 12 21.5 12 17.9 18.2 12 18.2 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="2.9"/></symbol>
<symbol id="i-key" viewBox="0 0 24 24"><circle cx="7.6" cy="16.4" r="4.1"/><path d="m10.5 13.5 8-8M15.6 8.4l2.4 2.4M18.5 5.5l2.4 2.4"/></symbol>
<symbol id="i-heart" viewBox="0 0 24 24"><path style="fill:currentColor;stroke:none" d="M12 20.7 4.3 13a4.7 4.7 0 0 1 6.6-6.6l1.1 1 1.1-1A4.7 4.7 0 1 1 19.7 13Z"/></symbol>
<symbol id="i-cpu" viewBox="0 0 24 24"><rect x="5.5" y="5.5" width="13" height="13" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/><path d="M9 2.5V5M15 2.5V5M9 19v2.5M15 19v2.5M2.5 9H5M2.5 15H5M19 9h2.5M19 15h2.5"/></symbol>
<symbol id="i-layers" viewBox="0 0 24 24"><path d="m12 2.8 9 4.7-9 4.7-9-4.7z"/><path d="m3 12.5 9 4.7 9-4.7M3 17.2l9 4.7 9-4.7"/></symbol>
<symbol id="i-present" viewBox="0 0 24 24"><rect x="2.5" y="3.5" width="19" height="12" rx="2"/><path d="M12 15.5v3M8.5 21.5 12 18.5l3.5 3M7 11l3-3 2.5 2.5L17 6"/></symbol>
<symbol id="i-clock" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5.3l3.2 2"/></symbol>
<symbol id="i-refresh" viewBox="0 0 24 24"><path d="M20.5 12a8.5 8.5 0 1 1-2.6-6.1"/><path d="M20.5 4.5V10H15"/></symbol>
<symbol id="i-term" viewBox="0 0 24 24"><rect x="2.5" y="4" width="19" height="16" rx="2.5"/><path d="m7 9.5 3 2.5-3 2.5M13 15h4"/></symbol>
<symbol id="i-bolt" viewBox="0 0 24 24"><path d="M13.5 2.5 4.5 13.5h6l-.5 8 9.5-11h-6.5z"/></symbol>
<symbol id="i-up" viewBox="0 0 24 24"><path d="M12 19V5M6 11l6-6 6 6"/></symbol>
<symbol id="i-down" viewBox="0 0 24 24"><path d="M12 5v14M6 13l6 6 6-6"/></symbol>
<symbol id="i-left" viewBox="0 0 24 24"><path d="M19 12H5M11 6l-6 6 6 6"/></symbol>
<symbol id="i-right" viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6"/></symbol>
<symbol id="i-enter" viewBox="0 0 24 24"><path d="M20 5v7.5a3 3 0 0 1-3 3H4.5"/><path d="m9 10.5-4.5 5 4.5 5"/></symbol>
<symbol id="i-bksp" viewBox="0 0 24 24"><path d="M21 5H9.2a2 2 0 0 0-1.5.7l-4.6 5.6a1 1 0 0 0 0 1.4l4.6 5.6a2 2 0 0 0 1.5.7H21a1.5 1.5 0 0 0 1.5-1.5v-11A1.5 1.5 0 0 0 21 5Z"/><path d="m12 10 5 4M17 10l-5 4"/></symbol>
<symbol id="i-search" viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/></symbol>
<symbol id="i-home" viewBox="0 0 24 24"><path d="m3.5 10.5 8.5-7 8.5 7"/><path d="M5.5 9.5V20h13V9.5"/><path d="M10 20v-5.5h4V20"/></symbol>
<symbol id="i-copy" viewBox="0 0 24 24"><rect x="8.5" y="8.5" width="12" height="12" rx="2"/><path d="M4.5 15.5h-.5a1.5 1.5 0 0 1-1.5-1.5V4a1.5 1.5 0 0 1 1.5-1.5h10A1.5 1.5 0 0 1 15.5 4v.5"/></symbol>
<symbol id="i-save" viewBox="0 0 24 24"><path d="M19.5 21H4.5A1.5 1.5 0 0 1 3 19.5v-15A1.5 1.5 0 0 1 4.5 3h11L21 8.5v11A1.5 1.5 0 0 1 19.5 21Z"/><path d="M7.5 3v6h8V3.5M7.5 21v-6h9v6"/></symbol>
<symbol id="i-drag" viewBox="0 0 24 24"><circle cx="9" cy="6" r="1.4"/><circle cx="15" cy="6" r="1.4"/><circle cx="9" cy="12" r="1.4"/><circle cx="15" cy="12" r="1.4"/><circle cx="9" cy="18" r="1.4"/><circle cx="15" cy="18" r="1.4"/></symbol>
<symbol id="i-click" viewBox="0 0 24 24"><path d="M9 3.5v3M4.6 5.6l2 2M3.5 10.5h3M14.5 3.5l-2 2"/><path d="M10 9.5 20.5 13l-4.4 1.6L14.5 19z"/></symbol>
<symbol id="i-scroll" viewBox="0 0 24 24"><rect x="7.5" y="2.5" width="9" height="19" rx="4.5"/><path d="M12 6.5v3"/></symbol>
<symbol id="i-media" viewBox="0 0 24 24"><path d="M9.5 17.5V5.2l11-2.2v12.3"/><circle cx="6.6" cy="17.8" r="2.9"/><circle cx="17.6" cy="15.3" r="2.9"/></symbol>
<symbol id="i-command" viewBox="0 0 24 24"><rect x="9" y="9" width="6" height="6"/><path d="M9 9H6.5a2.5 2.5 0 1 1 2.5-2.5V9zM15 9h2.5a2.5 2.5 0 1 0-2.5-2.5V9zM9 15H6.5a2.5 2.5 0 1 0 2.5 2.5V15zM15 15h2.5a2.5 2.5 0 1 1-2.5 2.5V15z"/></symbol>
<!-- A single mechanical keycap: outer body, raised top face, legend on the face.
     The text is filled rather than stroked, so it stays crisp as it shrinks. -->
<symbol id="i-keycap" viewBox="0 0 24 24"><rect x="3" y="3.5" width="18" height="17" rx="3.2"/><rect x="5.8" y="6.2" width="12.4" height="9.6" rx="2"/><text x="12" y="12.6" text-anchor="middle" font-size="5.1" font-weight="700" fill="currentColor" stroke="none" font-family="-apple-system,'Segoe UI',Roboto,sans-serif">Ctrl</text></symbol>
<symbol id="i-appswitch" viewBox="0 0 24 24"><rect x="2.8" y="2.8" width="13" height="13" rx="2.5"/><path d="M2.8 6.9h13"/><rect x="8.2" y="8.2" width="13" height="13" rx="2.5"/><path d="M8.2 12.3h13"/></symbol>
<symbol id="i-menukey" viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="2.5"/><path d="M8 9h8M8 12.5h8M8 16h5"/></symbol>
</defs></svg>

<div class="app">
  <aside class="rail" id="rail">
    <div class="brand">
      <div class="mark"><svg class="ic"><use href="#i-logo"/></svg></div>
      <div class="txt"><b>HID-Fi</b><span id="brandsub">connecting</span></div>
    </div>
    <div id="navlist"></div>
    <div class="linkstat"><span class="dot" id="ldot"></span><span id="ltext">offline</span></div>
    <button class="railtoggle" id="btnRail" aria-label="Collapse sidebar">
      <svg class="ic"><use href="#i-left"/></svg><span>Collapse</span>
    </button>
  </aside>

  <div class="content">
    <header class="topbar">
      <div class="tmark" aria-hidden="true"><svg class="ic"><use href="#i-logo"/></svg></div>
      <h1 id="vtitle">Trackpad</h1>
      <div class="pill" title="round trip over the socket"><span class="dot" id="tdot"></span><b id="trtt">--</b></div>
      <div class="pill conn" id="connPill" title="dashboards connected to this board"><svg class="ic"><use href="#i-devices"/></svg><b id="connN">--</b></div>
      <button class="iconbtn" id="btnInfo" aria-label="Gestures"><svg class="ic"><use href="#i-info"/></svg></button>
      <button class="iconbtn" id="btnRefresh" aria-label="Refresh"><svg class="ic"><use href="#i-refresh"/></svg></button>
    </header>

    <main id="main">

    <!-- ============ TRACKPAD ============ -->
    <section class="view on" id="v-pad">
      <div class="grid">
        <div class="card pad0 span">
          <div class="padwrap" id="padwrap">
            <div class="padbadge" id="padbadge"></div>
            <button class="fsexit" id="btnFsExitTop"><svg class="ic ic-sm"><use href="#i-min"/></svg>Exit fullscreen</button>
            <div class="padtools">
              <button class="iconbtn" id="btnDeck" aria-label="Show keyboard"><svg class="ic"><use href="#i-keyboard"/></svg></button>
              <button class="iconbtn" id="btnGrab" aria-label="Grab mode"><svg class="ic"><use href="#i-hand"/></svg></button>
              <button class="iconbtn" id="btnFs" aria-label="Fullscreen"><svg class="ic"><use href="#i-max"/></svg></button>
            </div>
            <div id="pad">
              <div class="padhint" id="padhint">
                <i>1 finger move</i><i>tap click</i><i>hold grab</i><i>2 scroll</i><i>3 swipe</i>
              </div>
            </div>
            <div id="padkbd">
              <div class="pkrow">
                <input id="pkText" placeholder="Text to type on the PC" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false">
                <button class="btn sm" id="pkSend">Send</button>
                <button class="btn sm ok" id="pkEnter" aria-label="Send and press Enter"><svg class="ic ic-sm"><use href="#i-enter"/></svg></button>
              </div>
              <div class="kbd" id="kbd2"></div>
            </div>
            <div class="fsbar">
              <button class="btn sm" data-mb="1">Left</button>
              <button class="btn sm" data-mb="2">Right</button>
              <button class="btn sm" id="btnGrab2"><svg class="ic ic-sm"><use href="#i-hand"/></svg>Grab</button>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="ch"><svg class="ic"><use href="#i-click"/></svg><h2>Buttons</h2>
            <button class="iconbtn" id="btnBtnView" aria-label="Switch button layout"><svg class="ic"><use href="#i-cursor"/></svg></button>
          </div>
          <div id="btnsRow">
            <div class="row">
              <button class="btn" data-mb="1">Left</button>
              <button class="btn" data-mb="2">Right</button>
              <button class="btn" data-mb="4">Middle</button>
            </div>
            <div class="row" style="margin-top:9px">
              <button class="btn sm" data-mb2="1">Double click</button>
              <button class="btn sm" id="btnGrab3"><svg class="ic ic-sm"><use href="#i-hand"/></svg>Grab / drag</button>
            </div>
          </div>
          <div id="btnsMouse" style="display:none">
            <div class="mouse">
              <div class="shell"></div>
              <div class="btns">
                <button class="l" data-mb="1">Left</button>
                <button class="r" data-mb="2">Right</button>
              </div>
              <span class="split"></span>
              <button class="m" id="mWheel" aria-label="Scroll wheel - drag to scroll, tap to middle click"><i></i><i></i><i></i><i></i></button>
            </div>
            <div class="row" style="margin-top:10px">
              <button class="btn sm" data-mb2="1">Double click</button>
              <button class="btn sm" id="btnGrab4"><svg class="ic ic-sm"><use href="#i-hand"/></svg>Grab / drag</button>
            </div>
          </div>
          <div class="sep"></div>
          <div class="ch"><svg class="ic"><use href="#i-layers"/></svg><h2>Gestures</h2></div>
          <div class="tiles">
            <button class="tile" data-gs="task_view"><svg class="ic"><use href="#i-layers"/></svg><span>Task view</span></button>
            <button class="tile" data-gs="desktop_left"><svg class="ic"><use href="#i-left"/></svg><span>Desktop left</span></button>
            <button class="tile" data-gs="desktop_right"><svg class="ic"><use href="#i-right"/></svg><span>Desktop right</span></button>
            <button class="tile" data-gs="switch_app"><svg class="ic"><use href="#i-appswitch"/></svg><span>Switch app</span></button>
            <button class="tile" data-gs="back"><svg class="ic"><use href="#i-left"/></svg><span>Back</span></button>
            <button class="tile" data-gs="forward"><svg class="ic"><use href="#i-right"/></svg><span>Forward</span></button>
          </div>
          <p class="hint" style="margin-top:10px">Hold <b>Switch app</b> to keep the host's switcher open, then slide to pick a window - sideways along the row, up and down between rows. Let go and it comes forward.</p>
        </div>

        <div class="card">
          <div class="ch"><svg class="ic"><use href="#i-settings"/></svg><h2>Feel</h2></div>
          <div class="slider"><label>Speed</label><input type="range" id="sSens" min="0.5" max="6" step="0.1"><span class="v" id="vSens"></span></div>
          <div class="slider"><label>Accel</label><input type="range" id="sAcc" min="0" max="3" step="0.1"><span class="v" id="vAcc"></span></div>
          <div class="slider"><label>Scroll</label><input type="range" id="sScr" min="0.3" max="4" step="0.1"><span class="v" id="vScr"></span></div>
          <div class="slider"><label>Glide</label><input type="range" id="sMom" min="0" max="0.97" step="0.01"><span class="v" id="vMom"></span></div>
          <div class="fld" style="margin-bottom:0"><label>Scroll direction</label>
            <div class="seg" id="scrollSeg" style="width:100%">
              <button data-scroll="mac" style="flex:1 1 0"><svg class="ic ic-sm"><use href="#i-scrollnat"/></svg><span>Natural</span></button>
              <button data-scroll="win" style="flex:1 1 0"><svg class="ic ic-sm"><use href="#i-scrollstd"/></svg><span>Classic</span></button>
            </div>
            <p class="hint" style="margin-top:8px">Both arrows the same way means the page follows your fingers. Arrows opposed means it moves against them, like a mouse wheel. macOS defaults to Natural; Windows laptops ship either way, so pick whichever matches the trackpad you already use.</p>
          </div>
          <div class="chips" style="margin-top:12px">
            <button class="chip" id="tTap"><svg class="ic ic-sm"><use href="#i-click"/></svg>Tap to click</button>
            <button class="chip" id="t3D"><svg class="ic ic-sm"><use href="#i-hand"/></svg>Three finger drag</button>
            <button class="chip" id="t3A"><svg class="ic ic-sm"><use href="#i-appswitch"/></svg>Three finger app switch</button>
            <button class="chip" id="tHap"><svg class="ic ic-sm"><use href="#i-bolt"/></svg>Haptics</button>
          </div>
          <p class="hint" style="margin-top:10px">Glide 0 stops the cursor dead when you lift off. Three finger drag replaces the three finger swipes with a drag, which never sends a click first. Turn off Three finger app switch and a sideways three finger swipe changes virtual desktop instead, which is what macOS does.</p>
          <p class="hint" id="hapNote" style="margin-top:8px;display:none"></p>
        </div>
      </div>
    </section>

    <!-- ============ KEYBOARD ============ -->
    <section class="view" id="v-keys">
      <div class="grid">
        <div class="card span">
          <div class="ch"><svg class="ic"><use href="#i-keyboard"/></svg><h2>Type</h2></div>
          <div class="row">
            <input id="typeText" class="fldin" placeholder="Text to type on the PC" style="flex:3 1 200px;min-height:44px;padding:11px 12px;border-radius:10px;background:rgba(0,0,0,.32);border:1px solid var(--line);font-size:16px">
            <button class="btn" style="flex:1 1 90px" id="btnType">Send</button>
            <button class="btn ok" style="flex:1 1 100px" id="btnTypeEnter"><svg class="ic ic-sm"><use href="#i-enter"/></svg>Enter</button>
          </div>
          <div class="kbd" id="kbd"></div>
          <div class="row" style="margin-top:11px">
            <input id="customKeys" placeholder="Custom combo e.g. CTRL+SHIFT+ESC" style="flex:3 1 200px;min-height:44px;padding:11px 12px;border-radius:10px;background:rgba(0,0,0,.32);border:1px solid var(--line);font-size:16px">
            <button class="btn" style="flex:1 1 90px" id="btnCombo">Press</button>
          </div>
        </div>

        <div class="card span">
          <div class="ch"><svg class="ic"><use href="#i-term"/></svg><h2>Macros</h2>
            <button class="iconbtn" id="btnMacroNew" aria-label="New macro"><svg class="ic"><use href="#i-plus"/></svg></button>
          </div>
          <div class="tiles" id="macroTiles"></div>
          <p class="hint" style="margin-top:10px">Stored on the board, so every device that opens this dashboard sees the same macros.</p>
        </div>
      </div>
    </section>

    <!-- ============ SHORTCUTS ============ -->
    <section class="view" id="v-short">
      <div class="grid">
        <div class="card span">
          <div class="ch"><svg class="ic"><use href="#i-shield"/></svg><h2>Passwords</h2>
            <button class="iconbtn" id="btnVaultNew" aria-label="New password"><svg class="ic"><use href="#i-plus"/></svg></button>
          </div>
          <div class="list" id="vaultList"></div>
          <p class="hint" id="vaultHint" style="margin-top:10px"></p>
          <div class="row" style="margin-top:10px">
            <button class="btn sm" id="btnVaultPin">Vault PIN</button>
            <button class="btn sm bad" id="btnVaultWipe">Erase vault</button>
          </div>
        </div>

        <div class="card span">
          <div class="scbar">
            <div class="seg" id="osSeg">
              <button data-os="mac"><span>macOS</span></button>
              <button data-os="win"><span>Windows</span></button>
              <button data-os="linux"><span>Linux</span></button>
            </div>
            <input class="scsearch" id="scq" placeholder="Search shortcuts" autocomplete="off" spellcheck="false">
          </div>
          <p class="hint" id="scNote"></p>
          <div id="scList" style="margin-top:14px"></div>
        </div>
      </div>
    </section>

    <!-- ============ MEDIA ============ -->
    <section class="view" id="v-media">
      <div class="grid">
        <div class="card span">
          <div class="ch"><svg class="ic"><use href="#i-knob"/></svg><h2>Knobs</h2>
            <button class="iconbtn" id="btnKnobNew" aria-label="New knob"><svg class="ic"><use href="#i-plus"/></svg></button>
          </div>
          <div class="knobs" id="knobs"></div>
          <p class="hint" style="margin-top:11px">Turn a knob with your finger. Each detent is one step, with a haptic tick. The board cannot read the PC's current volume, so the dial shows your turn, not an absolute level.</p>
        </div>

        <div class="card">
          <div class="ch"><svg class="ic"><use href="#i-play"/></svg><h2>Playback</h2></div>
          <div class="tiles">
            <button class="tile" data-md="prev"><svg class="ic"><use href="#i-prev"/></svg><span>Previous</span></button>
            <button class="tile" data-md="play_pause"><svg class="ic"><use href="#i-play"/></svg><span>Play / pause</span></button>
            <button class="tile" data-md="next"><svg class="ic"><use href="#i-next"/></svg><span>Next</span></button>
            <button class="tile" data-md="stop"><svg class="ic"><use href="#i-stop"/></svg><span>Stop</span></button>
            <button class="tile" data-md="mute"><svg class="ic"><use href="#i-volx"/></svg><span>Mute</span></button>
            <button class="tile" data-md="brightness_down"><svg class="ic"><use href="#i-sundim"/></svg><span>Dimmer</span></button>
            <button class="tile" data-md="brightness_up"><svg class="ic"><use href="#i-sun"/></svg><span>Brighter</span></button>
            <button class="tile" data-md="search"><svg class="ic"><use href="#i-search"/></svg><span>Search</span></button>
          </div>
        </div>

        <div class="card">
          <div class="ch"><svg class="ic"><use href="#i-bolt"/></svg><h2>Quick actions</h2>
            <button class="iconbtn" id="btnQuickNew" aria-label="New action"><svg class="ic"><use href="#i-plus"/></svg></button>
          </div>
          <div class="tiles" id="quickTiles"></div>
          <p class="hint" style="margin-top:10px">Mic mute, call answer and hang up are not standard USB keys - they differ per laptop and per app. Each button here is editable, so set it to whatever your machine actually uses.</p>
        </div>
      </div>
    </section>

    <!-- ============ GAMEPAD ============ -->
    <section class="view" id="v-game">
      <div class="grid">
        <div class="card span" id="gpCard">
          <div class="ch"><svg class="ic"><use href="#i-gamepad"/></svg><h2>Controller <span class="beta">Beta</span></h2>
            <button class="iconbtn" id="btnGpToggle" aria-label="Toggle gamepad"><svg class="ic"><use href="#i-power"/></svg></button>
          </div>
          <div id="gpWrap">
            <div class="padlayout">
              <div>
                <div class="stick" id="stickL">
                  <div class="base"></div><div class="ring"></div>
                  <div class="cross"><i></i><i></i></div>
                  <div class="cap"></div><div class="lbl">L STICK</div>
                </div>
              </div>
              <div class="dpad">
                <button class="u" data-hat="1"><svg class="ic ic-sm"><use href="#i-up"/></svg></button>
                <button class="l" data-hat="7"><svg class="ic ic-sm"><use href="#i-left"/></svg></button>
                <div class="c"></div>
                <button class="r" data-hat="3"><svg class="ic ic-sm"><use href="#i-right"/></svg></button>
                <button class="d" data-hat="5"><svg class="ic ic-sm"><use href="#i-down"/></svg></button>
              </div>
              <div>
                <div class="stick" id="stickR">
                  <div class="base"></div><div class="ring"></div>
                  <div class="cross"><i></i><i></i></div>
                  <div class="cap"></div><div class="lbl">R STICK</div>
                </div>
              </div>
            </div>
            <div class="sep"></div>
            <div class="padlayout">
              <div class="row">
                <button class="btn sm" data-gb="6">LB</button>
                <button class="btn sm" data-gb="8">LT</button>
              </div>
              <div class="faces">
                <button class="n" data-gb="4">Y</button>
                <button class="w" data-gb="3">X</button>
                <button class="e" data-gb="1">B</button>
                <button class="s" data-gb="0">A</button>
              </div>
              <div class="row">
                <button class="btn sm" data-gb="7">RB</button>
                <button class="btn sm" data-gb="9">RT</button>
              </div>
            </div>
            <div class="row" style="margin-top:14px;max-width:340px;margin-inline:auto">
              <button class="btn sm" data-gb="10">Back</button>
              <button class="btn sm" data-gb="11">Start</button>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- ============ POWER / SESSION ============ -->
    <section class="view" id="v-power">
      <div class="grid">
        <div class="card">
          <div class="ch"><svg class="ic"><use href="#i-unlock"/></svg><h2>Session</h2></div>
          <div class="fld"><label>PC password</label><input type="password" id="unlockPw" autocomplete="off" placeholder="Typed as keystrokes"></div>
          <div class="chips"><button class="chip" id="tCad">Use Ctrl+Alt+Del</button></div>
          <button class="btn pri blk" id="btnUnlock" style="margin-top:12px"><svg class="ic ic-sm"><use href="#i-unlock"/></svg>Unlock PC</button>
          <div class="row" style="margin-top:9px">
            <button class="btn sm bad" id="btnLock"><svg class="ic ic-sm"><use href="#i-lock"/></svg>Lock</button>
            <button class="btn sm" id="btnLockUnlock">Lock then unlock</button>
          </div>
          <div class="sep"></div>
          <div class="ch"><svg class="ic"><use href="#i-shield"/></svg><h2>Saved PCs</h2>
            <button class="iconbtn" id="btnPcNew" aria-label="Save this PC"><svg class="ic"><use href="#i-plus"/></svg></button>
          </div>
          <div class="list" id="pcList"></div>
          <p class="hint" id="pcHint" style="margin-top:10px"></p>
          <div class="sep"></div>
          <div class="row">
            <button class="btn sm" data-sys="sleep">Sleep PC</button>
            <button class="btn sm" data-sys="wake">Wake PC</button>
          </div>
        </div>

        <div class="card">
          <div class="ch"><svg class="ic"><use href="#i-present"/></svg><h2>Presenter</h2></div>
          <div class="row">
            <button class="btn" data-pr="prev"><svg class="ic ic-sm"><use href="#i-left"/></svg>Prev</button>
            <button class="btn" data-pr="next">Next<svg class="ic ic-sm"><use href="#i-right"/></svg></button>
          </div>
          <div class="row" style="margin-top:9px">
            <button class="btn sm ok" data-pr="start">Start</button>
            <button class="btn sm" data-pr="black">Black</button>
            <button class="btn sm" data-pr="white">White</button>
            <button class="btn sm bad" data-pr="end">End</button>
          </div>
          <div class="sep"></div>
          <div class="item">
            <svg class="ic"><use href="#i-clock"/></svg>
            <div class="tx"><b id="presTime">00:00</b><span>Talk timer</span></div>
            <button class="iconbtn" id="btnPresPlay"><svg class="ic"><use href="#i-play"/></svg></button>
            <button class="iconbtn" id="btnPresReset"><svg class="ic"><use href="#i-refresh"/></svg></button>
          </div>
        </div>

        <div class="card">
          <div class="ch"><svg class="ic"><use href="#i-cursor"/></svg><h2>Stay awake</h2></div>
          <div class="item"><svg class="ic"><use href="#i-bolt"/></svg>
            <div class="tx"><b id="wigState">Idle</b><span>Net-zero cursor jiggle</span></div>
          </div>
          <div class="row" style="margin-top:10px">
            <button class="btn sm ok" id="btnWigStart">Start</button>
            <button class="btn sm bad" id="btnWigStop">Stop</button>
          </div>
          <div class="slider"><label>Every</label><input type="range" id="wigInt" min="1" max="60" step="1"><span class="v" id="wigIntV"></span></div>
        </div>
      </div>
    </section>

    <!-- ============ SETTINGS ============ -->
    <section class="view" id="v-setup">
      <div class="grid">
        <div class="card">
          <div class="ch"><svg class="ic"><use href="#i-cpu"/></svg><h2>Computer</h2></div>
          <div class="item"><svg class="ic"><use href="#i-cpu"/></svg>
            <div class="tx"><b id="osName">Detecting&hellip;</b><span id="osSub">Working out whether this is a Mac or a PC</span></div>
          </div>
          <div class="row" id="osSel" style="margin-top:12px">
            <button class="btn sm" data-os-set="auto">Auto</button>
            <button class="btn sm" data-os-set="mac">macOS</button>
            <button class="btn sm" data-os-set="win">Windows</button>
            <button class="btn sm" data-os-set="linux">Linux</button>
          </div>
          <p class="hint" id="osDiag" style="margin-top:8px;font-variant-numeric:tabular-nums"></p>
          <p class="hint" style="margin-top:10px">The board works out whether it is plugged into a Mac or a PC from the way the computer reads its USB descriptors &mdash; it never types anything to find out &mdash; and picks the matching lock shortcut, gestures, app switcher and keyboard. Auto lets it decide and re-checks whenever you move the board to another computer; the others pin it, which you need if it called a Linux box Windows, because it cannot tell those two apart on its own.</p>

          <div class="sep"></div>
          <div class="ch"><svg class="ic"><use href="#i-keyboard"/></svg><h2>Mac setup</h2></div>
          <p class="hint">First time on a Mac there are two one-off steps. macOS asks to allow the accessory &mdash; choose Allow while the Mac is <b>unlocked</b>, or it cannot connect. Then the Keyboard Setup Assistant asks the board to identify its keyboard. Tap these two, in order, when it asks, instead of hunting for keys on your own keyboard:</p>
          <div class="row" style="margin-top:12px">
            <button class="btn sm" id="btnIdZ">1&nbsp;&middot;&nbsp;Key by left Shift</button>
            <button class="btn sm" id="btnIdSlash">2&nbsp;&middot;&nbsp;Key by right Shift</button>
          </div>
          <p class="hint" style="margin-top:10px">The board is a US keyboard, so the answer is always these same two keys. These send exactly what the assistant is waiting for.</p>
        </div>

        <div class="card">
          <div class="ch"><svg class="ic"><use href="#i-wifi"/></svg><h2>Network</h2></div>
          <dl class="kv2" id="netInfo"></dl>
          <div class="item" id="joinBox" style="display:none;margin-top:12px">
            <span class="dot" id="joinDot"></span>
            <div class="tx"><b id="joinTitle">Joining</b><span id="joinMsg" style="white-space:normal"></span></div>
          </div>
          <div class="bar" id="joinBar" style="display:none"><i></i></div>
          <button class="btn sm blk" id="btnScan" style="margin-top:12px">Scan networks</button>
          <div class="list" id="wifiList" style="margin-top:10px"></div>
          <div class="chips" style="margin-top:12px"><button class="chip" id="tStatic">Use a fixed IP</button></div>
          <div id="staticFields" style="display:none">
            <div class="fld"><label>IP address</label><input id="stIp" inputmode="decimal" autocomplete="off" placeholder="192.168.1.50"></div>
            <div class="fld"><label>Gateway (your router)</label><input id="stGw" inputmode="decimal" autocomplete="off" placeholder="192.168.1.1"></div>
            <div class="fld"><label>Subnet mask</label><input id="stMask" inputmode="decimal" autocomplete="off" placeholder="255.255.255.0"></div>
            <div class="fld"><label>DNS (optional)</label><input id="stDns" inputmode="decimal" autocomplete="off" placeholder="same as gateway"></div>
            <p class="hint">Pick an address outside your router's DHCP range, or it may hand the same one to something else later. If the address cannot be reached the board falls back to DHCP by itself and tells you here.</p>
          </div>
          <div class="sep"></div>
          <div class="ch"><svg class="ic"><use href="#i-shield"/></svg><h2>Access point</h2></div>
          <div class="fld"><label>Network name <span id="apBytes" style="float:right;font-weight:500;letter-spacing:0;text-transform:none"></span></label><input id="apSsid" autocomplete="off" data-nofill maxlength="32" placeholder="ESP32-HID-XXXXXX"></div>
          <div class="fld"><label>Password</label><input type="password" id="apPass" autocomplete="off" data-nofill placeholder="leave blank to keep the current password"></div>
          <div class="chips" style="margin-bottom:12px"><button class="chip" id="tApOpen">No password (open network)</button></div>
          <button class="btn ok blk" id="btnAp">Change access point</button>
          <p class="hint" style="margin-top:10px">Leaving the password blank keeps the one already set, so you can rename the network without retyping it. Removing the password is a separate, deliberate choice &mdash; that is what the toggle is for. Emoji and accents work in the name, because a network name is raw bytes; the limit is 32 <b>bytes</b> though, and one emoji uses four. Every board ships with the same password, so anyone who has seen this project knows it. The access point restarts the moment you apply, so this phone will drop and you will have to reconnect.</p>
        </div>

        <div class="card">
          <div class="ch"><svg class="ic"><use href="#i-shield"/></svg><h2>Access PIN</h2></div>
          <div class="item warnbox" id="lanWarn" style="display:none">
            <span class="dot bad"></span>
            <div class="tx"><b>Reachable from your whole network</b><span style="white-space:normal">This board is joined to a home network, so every device on it can reach this page, not just people in radio range. Wireless control is blocked until you set a PIN.</span></div>
          </div>
          <div class="item"><svg class="ic"><use href="#i-lock"/></svg>
            <div class="tx"><b id="authState">Off</b><span>Gates WiFi only, never USB serial</span></div>
          </div>
          <div class="fld" style="margin-bottom:0"><label>New 4 digit PIN</label>
            <div class="pinrow" id="pinSet">
              <input class="pinbox" type="password" inputmode="numeric" pattern="[0-9]*" maxlength="1" autocomplete="off" aria-label="PIN digit 1">
              <input class="pinbox" type="password" inputmode="numeric" pattern="[0-9]*" maxlength="1" autocomplete="off" aria-label="PIN digit 2">
              <input class="pinbox" type="password" inputmode="numeric" pattern="[0-9]*" maxlength="1" autocomplete="off" aria-label="PIN digit 3">
              <input class="pinbox" type="password" inputmode="numeric" pattern="[0-9]*" maxlength="1" autocomplete="off" aria-label="PIN digit 4">
            </div>
          </div>
          <div class="row" style="margin-top:12px">
            <button class="btn sm bad" id="btnPinOff">Turn off</button>
            <button class="btn ok" id="btnPin">Set PIN</button>
          </div>
          <p class="hint" style="margin-top:10px">Without a PIN, anyone in radio range of the access point can type on this PC. Four digits is only 10,000 guesses, so the board locks out for longer and longer after each wrong one, reaching five minutes a try. A USB cable is never locked out, so you can always get back in.</p>
        </div>

        <div class="card">
          <div class="ch"><svg class="ic"><use href="#i-cursor"/></svg><h2>Pointer mode</h2></div>
          <div class="item"><svg class="ic"><use href="#i-cursor"/></svg>
            <div class="tx"><b id="ptrMode">relative</b><span>Switching re-enumerates USB and reboots</span></div>
          </div>
          <div class="row" style="margin-top:10px">
            <button class="btn sm" data-ptr="relative">Relative</button>
            <button class="btn sm" data-ptr="absolute">Absolute</button>
          </div>
        </div>

        <div class="card">
          <div class="ch"><svg class="ic"><use href="#i-bolt"/></svg><h2>Power</h2></div>
          <div class="item"><svg class="ic"><use href="#i-bolt"/></svg>
            <div class="tx"><b id="pwrState">Balanced</b><span id="pwrSub">Radio awake</span></div>
          </div>
          <div class="row" style="margin-top:10px">
            <button class="btn sm" data-pwr="performance">Performance</button>
            <button class="btn sm" data-pwr="balanced">Balanced</button>
            <button class="btn sm" data-pwr="saver">Saver</button>
          </div>
          <div class="chips" style="margin-top:12px"><button class="chip" id="tApOff">Turn the access point off when unused</button></div>
          <p class="hint" style="margin-top:10px">Almost all of this board's power goes to the radio. Keeping it awake is what makes the pointer feel instant, and letting it sleep is what saves power, so the two cannot both be had at once. Balanced keeps it awake while you are using it and lets it sleep once nobody is connected, which costs you nothing you can feel. Saver keeps it asleep always, and you will see the pointer stutter.</p>
          <p class="hint">The access point keeps broadcasting even with nobody on it, so switching it off once the board is on your home network is the only setting here that saves a lot. It also removes a way in. Hold the BOOT button for two seconds to bring it straight back.</p>
        </div>

        <div class="card span">
          <div class="ch"><svg class="ic"><use href="#i-cpu"/></svg><h2>Device</h2></div>
          <dl class="kv2" id="devInfo"></dl>
          <div class="sep"></div>
          <div class="ch"><svg class="ic"><use href="#i-wifi"/></svg><h2>Connected clients</h2>
            <button class="iconbtn" id="btnClients" aria-label="Refresh clients"><svg class="ic"><use href="#i-refresh"/></svg></button>
          </div>
          <div class="list" id="clientList"></div>
          <div class="sep"></div>
          <div class="ch"><svg class="ic"><use href="#i-save"/></svg><h2>Stored on the board</h2>
            <button class="iconbtn" id="btnStorage" aria-label="Refresh storage"><svg class="ic"><use href="#i-refresh"/></svg></button>
          </div>
          <dl class="kv2" id="storeInfo"></dl>
          <p class="hint" style="margin-top:10px">Everything here survives a reboot and a firmware upgrade &mdash; only an erase clears it. No password or PIN is ever read back, by any command; they show as set or not set.</p>
          <div class="row" style="margin-top:12px">
            <button class="btn sm" id="btnTestHid">Test HID</button>
            <button class="btn sm bad" id="btnWifiReset">Reset WiFi</button>
          </div>
          <div class="log" id="log"></div>
        </div>

        <div class="card span colophon">
          <svg class="mark" viewBox="0 0 48 48" aria-hidden="true"><use href="#i-logo"/></svg>
          <div class="name">HID&#8209;Fi</div>
          <div class="blurb">A phone-shaped keyboard, mouse and controller for any PC</div>
          <div class="by">With <svg class="heart" viewBox="0 0 24 24" aria-hidden="true"><use href="#i-heart"/></svg> by Santhosh</div>
          <div class="ver" id="colVer">&mdash;</div>
          <div class="ver">Built and tested on ESP32&#8209;S3 N16R8</div>
        </div>
      </div>
    </section>

    </main>
  </div>
</div>

<nav class="tabbar" id="tabbar"></nav>
<div class="sheet" id="sheet"><div class="sheetin" id="sheetin"></div></div>
<div class="boot" id="boot">
  <div class="sp"></div>
  <b id="bootTitle">Rebooting the board</b>
  <span id="bootMsg">USB is re-enumerating on the PC.</span>
  <div class="bar"><i id="bootBar"></i></div>
  <span id="bootTry">waiting for the board to come back</span>
</div>
<div class="asw" id="asw">
  <h3>Switch app</h3>
  <div class="aswpad">
    <button class="aswbig" id="aswUp" aria-label="Row up"><svg class="ic"><use href="#i-up"/></svg></button>
    <button class="aswbig" id="aswPrev" aria-label="Previous window"><svg class="ic"><use href="#i-left"/></svg></button>
    <div class="aswn" id="aswN">1</div>
    <button class="aswbig" id="aswNext" aria-label="Next window"><svg class="ic"><use href="#i-right"/></svg></button>
    <button class="aswbig" id="aswDown" aria-label="Row down"><svg class="ic"><use href="#i-down"/></svg></button>
  </div>
  <p id="aswHint"></p>
  <div class="row">
    <button class="btn sm" id="aswCancel">Cancel</button>
    <button class="btn pri" id="aswGo">Switch to it</button>
  </div>
</div>
<div class="boot" id="apnew">
  <svg class="ic" style="width:44px;height:44px;color:var(--acc)"><use href="#i-wifi"/></svg>
  <b>Access point changed</b>
  <span>Join the new network in your phone's WiFi settings. This page reconnects on its own once you do.</span>
  <dl class="kv2" id="apnewInfo"></dl>
  <span id="apnewTry">waiting for you to reconnect</span>
  <button class="btn pri" id="apnewReload">Reload now</button>
</div>
<div class="toasts" id="toasts"></div>

<script>
'use strict';
// =====================================================================
//  transport
// =====================================================================
const OP={MOVE:1,BTN:2,ABS:3,PING:4,GPAD:5,CONS:6,KEY:7};
let ws=null,wsUp=false,pingId=1,pings=new Map(),authOK=true,tries=0;
const bMove=new DataView(new ArrayBuffer(7));
const bBtn=new DataView(new ArrayBuffer(4));
const bAbs=new DataView(new ArrayBuffer(5));
const bPing=new DataView(new ArrayBuffer(5));
const bGp=new DataView(new ArrayBuffer(10));
const bCon=new DataView(new ArrayBuffer(3));
// The board answers commands in the order they arrive, one reply each, so this
// queue only stays aligned if EVERY command pushes exactly one slot and EVERY
// reply consumes exactly one - refusals included.
//
// Both halves of that were broken. A command sent without a callback pushed
// nothing but was still answered, and `auth_required` returned without
// consuming a slot. Either one shifts every later reply onto the wrong
// handler for the life of the socket. It showed up as an empty Keys tab while
// the board was holding the entries: the vault's answer was being delivered to
// another card's callback, and no amount of refreshing could fix it.
const waiters=[];
function settle(m){
  if(!waiters.length)return;
  const cb=waiters.shift();
  if(cb){try{cb(m);}catch(e){}}
}
// The scan is a plain GET rather than a socket command, so it has to carry the
// PIN itself. A header, not a query string, so it stays out of browser history
// and any log the request passes through.
function pinHeader(){
  try{const p=sessionStorage.getItem('pin');if(p)return{'X-Auth':p};}catch(e){}
  return {};
}

// Every handler here is bound to *this* socket rather than to whatever `ws`
// happens to point at when it fires, and a second connection is never opened
// while one is alive.
//
// Both mattered. A close arriving from a connection that had already been
// replaced used to mark the transport down, clear the live socket's pending
// callbacks and schedule yet another connect - so a tab left open across a few
// board reboots quietly accumulated connections. The board counted each as a
// separate dashboard, and the pointer stream had to share the phone's radio
// with all of them: fine at rest, hundreds of milliseconds while moving. A
// freshly opened tab was always smooth, which is what gave it away.
function connect(){
  if(ws&&(ws.readyState===0||ws.readyState===1))return;   // CONNECTING or OPEN
  let sock;
  try{sock=new WebSocket('ws://'+location.hostname+':81/');}
  catch(e){link(false);return setTimeout(connect,1500);}
  ws=sock;
  sock.binaryType='arraybuffer';
  const live=()=>ws===sock;
  sock.onopen=()=>{if(!live())return;
    wsUp=true;tries=0;link(true);bootDone();apDone();
    const p=sessionStorage.getItem('pin');if(p)send({cmd:'auth',token:p});
    send({cmd:'key_release_all'});      // clear anything a previous session left held
    refresh();loadCfg();loadMacros();loadPcs();loadVault();};
  sock.onclose=()=>{if(!live())return;
    wsUp=false;waiters.length=0;link(false);aswAbort();tries++;setTimeout(connect,1200);};
  sock.onerror=()=>{try{sock.close();}catch(e){}};
  sock.onmessage=ev=>{
    if(!live())return;
    if(ev.data instanceof ArrayBuffer){
      const d=new DataView(ev.data);
      if(d.getUint8(0)===0x81){const id=d.getUint32(1,true),t=pings.get(id);
        if(t){const r=Math.round(performance.now()-t);pings.delete(id);
          $('trtt').textContent=r+' ms';$('tdot').className='dot '+(r<40?'ok':r<120?'warn':'bad');}}
      return;
    }
    let m;try{m=JSON.parse(ev.data);}catch(e){return;}
    // Pushed by the board rather than answering anything, so it takes no slot.
    if(m.event){if(m.event==='host_os')applyHostOs(m.host_os,m.host_os_source,m.host_os_detected);return;}
    if(m.reply==='status'||m.reply==='pong')applyStatus(m);
    if(m.reply==='auth_required'){authOK=false;askPin();return settle(m);}
    if(m.reply==='lan_locked'){lanLocked();return settle(m);}
    if(m.reply==='auth_ok'){authOK=true;pinAskDone(true);toast('Unlocked','ok');syncAll();}
    if(m.reply==='auth_failed'){authOK=false;sessionStorage.removeItem('pin');
      askPin();pinAskDone(false,m.retry_in?('Wrong PIN. Locked for '+m.retry_in+'s.'):'That PIN was not right.');}
    if(m.reply==='auth_locked'){authOK=false;
      askPin();pinAskDone(false,'Too many wrong PINs. Try again in '+(m.retry_in||0)+'s.');}
    if(m.reply&&m.reply!=='mouse_moved')logline((m.status==='ok'?'ok ':'!! ')+(m.reply||m.message||''),m.status!=='ok');
    settle(m);
  };
}
function link(up){
  $('ldot').className='dot '+(up?'ok':'bad');
  $('ltext').textContent=up?'live':'offline';
  $('brandsub').textContent=up?'connected':'reconnecting';
  if(!up){$('trtt').textContent='--';$('tdot').className='dot';}
}
function send(o,cb){
  if(wsUp){
    waiters.push(cb||null);            // a slot even with no callback, or the queue drifts
    try{ws.send(JSON.stringify(o));return;}catch(e){waiters.pop();}
  }
  const pin=sessionStorage.getItem('pin');
  fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(pin?Object.assign({token:pin},o):o)})
    .then(r=>r.json()).then(d=>{if(d.reply==='auth_required')return askPin();
      if(d.reply==='lan_locked')return lanLocked();
      if(cb)cb(d);})
    .catch(e=>logline('net '+e.message,true));
}
// Shown once per page load, not on every blocked command, or a dashboard that
// polls status would bury the user in sheets.
let lanToldOnce=false;
function lanLocked(){
  if(lanToldOnce)return toast('Set a PIN to use this over your network','bad');
  lanToldOnce=true;
  sheet('A PIN is needed on this network',
    '<p class="hint">On the board\'s own access point an attacker has to be within radio range of you. On your home network every device can reach this page, including anything already compromised and anything a guest brought in. This board types into a logged-in PC, so wireless control stays blocked until a PIN is set.</p>'+
    '<div class="list" style="margin-top:12px">'+
    item('shield','Set a PIN','Open Settings and enter four digits. That is all this needs.')+
    item('wifi','Or use the access point','Connect to the board\'s own network instead. No PIN is required there.')+
    item('cpu','Or use the USB cable','Serial is never gated, so it is always a way back in.')+
    '</div>'+
    '<button class="btn ok blk" id="lan_go" style="margin-top:12px">Take me to Settings</button>',
    ()=>{$('lan_go').onclick=()=>{closeSheet();go('setup');};});
}
// Hot paths: no logging, no reply, and a hard stop if the socket backs up.
// Keys ride the same binary channel as the pointer. As JSON each keystroke cost
// a 1KB parse, a long strcmp walk and a reply frame nobody reads; as three bytes
// it costs none of that, which is what makes fast typing land in order.
function txKey(name,down){
  if(wsUp&&authOK){
    const n=new TextEncoder().encode(name);
    if(n.length<=22){
      const b=new Uint8Array(2+n.length);
      b[0]=OP.KEY;b[1]=down?1:0;b.set(n,2);
      try{ws.send(b.buffer);return;}catch(e){}
    }
  }
  // socket down, not yet authenticated, or an unusually long key name
  send({cmd:down?'key_down':'key_up',key:name});
}
function txMove(dx,dy,w,p){
  if(!wsUp||!authOK||ws.bufferedAmount>2048)return;
  bMove.setUint8(0,OP.MOVE);bMove.setInt16(1,dx,true);bMove.setInt16(3,dy,true);
  bMove.setInt8(5,w);bMove.setInt8(6,p);
  try{ws.send(bMove.buffer);}catch(e){}
}
function txBtn(mask,action,count){
  if(wsUp&&authOK){
    bBtn.setUint8(0,OP.BTN);bBtn.setUint8(1,mask);bBtn.setUint8(2,action);bBtn.setUint8(3,count||1);
    try{ws.send(bBtn.buffer);}catch(e){}buzz(8);return;
  }
  const n={1:'left',2:'right',4:'middle'};
  send(action===2?{cmd:'mouse_click',button:n[mask],count:count||1}
      :{cmd:action===1?'mouse_press':'mouse_release',button:n[mask]});
}
function txAbs(x,y){
  if(!wsUp||!authOK)return send({cmd:'mouse_abs',x:x,y:y});
  bAbs.setUint8(0,OP.ABS);bAbs.setUint16(1,x,true);bAbs.setUint16(3,y,true);
  try{ws.send(bAbs.buffer);}catch(e){}
}
function txGamepad(lx,ly,rx,ry,hat,btns){
  if(!wsUp||!authOK||ws.bufferedAmount>2048)return;
  bGp.setUint8(0,OP.GPAD);bGp.setInt8(1,lx);bGp.setInt8(2,ly);bGp.setInt8(3,rx);bGp.setInt8(4,ry);
  bGp.setUint8(5,hat);bGp.setUint32(6,btns>>>0,true);
  try{ws.send(bGp.buffer);}catch(e){}
}
function txConsumer(usage){
  if(!wsUp||!authOK)return send({cmd:'media',usage:usage});
  bCon.setUint8(0,OP.CONS);bCon.setUint16(1,usage,true);
  try{ws.send(bCon.buffer);}catch(e){}
}
setInterval(()=>{
  if(!wsUp||document.hidden)return;
  const id=pingId++&0xffffffff;pings.set(id,performance.now());
  if(pings.size>8)pings.clear();
  bPing.setUint8(0,OP.PING);bPing.setUint32(1,id,true);
  try{ws.send(bPing.buffer);}catch(e){}
},2000);

// =====================================================================
//  helpers
// =====================================================================
const $=id=>document.getElementById(id);
const ico=(n,c)=>'<svg class="ic'+(c?' '+c:'')+'"><use href="#i-'+n+'"/></svg>';
const esc=s=>{const d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;};
// iOS Safari ignores autocomplete="off". A text field followed by a password
// field reads to it as a login form, so tapping the AutoFill key while joining a
// network silently rewrote the access point's own name and password. Password
// managers skip a field that is readonly, and releasing it on first touch keeps
// the field behaving normally for the person actually typing in it.
function guardAutofill(el){
  if(!el||el.dataset.guarded)return;
  el.dataset.guarded='1';
  el.setAttribute('readonly','');
  const free=()=>el.removeAttribute('readonly');
  ['focus','pointerdown','touchstart'].forEach(ev=>el.addEventListener(ev,free));
}
const clamp=(v,a,b)=>v<a?a:v>b?b:v;
// Android and desktop Chrome have the Vibration API. Safari never implemented it
// on iOS, so there are no haptics there at all through a web page - iOS 17.4 will
// play a system tick when a switch control is toggled, and that is the only hook
// a browser page is given. Best effort, and the Feel panel says which one is live.
let hapticEl=null;
const HAPTIC=(()=>{
  if(navigator.vibrate)return 'vibrate';
  try{
    const i=document.createElement('input');
    if(!('switch' in i))return null;
    i.type='checkbox';i.setAttribute('switch','');
    i.setAttribute('aria-hidden','true');i.tabIndex=-1;
    i.style.cssText='position:fixed;left:-9999px;top:0;width:1px;height:1px;opacity:0;pointer-events:none';
    document.addEventListener('DOMContentLoaded',()=>document.body.appendChild(i));
    if(document.body)document.body.appendChild(i);
    hapticEl=i;
    return 'switch';
  }catch(e){}
  return null;
})();
function buzz(ms){
  if(!S.hap||!HAPTIC)return;
  try{
    if(HAPTIC==='vibrate'){navigator.vibrate(ms);return;}
    if(hapticEl){hapticEl.checked=!hapticEl.checked;hapticEl.dispatchEvent(new Event('change',{bubbles:true}));}
  }catch(e){}
}
function toast(msg,kind){
  const t=document.createElement('div');t.className='toast'+(kind?' '+kind:'');
  t.innerHTML=ico(kind==='bad'?'x':'check','ic-sm')+'<span>'+esc(msg)+'</span>';
  $('toasts').appendChild(t);
  setTimeout(()=>{t.style.opacity='0';t.style.transform='translateY(-8px)';setTimeout(()=>t.remove(),220);},2300);
}
function logline(m,bad){
  const b=$('log');if(!b)return;
  const d=document.createElement('div');if(bad)d.className='e';
  d.textContent=new Date().toLocaleTimeString()+'  '+m;
  b.appendChild(d);b.scrollTop=b.scrollHeight;
  while(b.children.length>50)b.removeChild(b.firstChild);
}
// =====================================================================
//  PIN entry
// =====================================================================
// Four boxes, digits only. The guards are deliberately layered: keydown blocks
// the keystroke, input scrubs whatever still landed (phone keyboards, autofill
// and voice input can all bypass keydown), and paste is handled on its own so
// copying a PIN in fills all four.
function bindPin(rowId,onFull){
  const row=$(rowId), bs=[].slice.call(row.querySelectorAll('.pinbox'));
  const val=()=>bs.map(b=>b.value).join('');
  const paint=()=>bs.forEach(b=>b.classList.toggle('filled',!!b.value));
  bs.forEach((b,i)=>{
    b.addEventListener('keydown',e=>{
      if(e.key==='Backspace'&&!b.value&&i>0){
        bs[i-1].value='';bs[i-1].focus();paint();e.preventDefault();return;
      }
      if(e.key==='ArrowLeft'&&i>0){bs[i-1].focus();e.preventDefault();return;}
      if(e.key==='ArrowRight'&&i<bs.length-1){bs[i+1].focus();e.preventDefault();return;}
      if(e.key==='Enter'){if(onFull&&val().length===bs.length)onFull(val());return;}
      // one printable character that is not a digit never reaches the field
      if(e.key.length===1&&!e.ctrlKey&&!e.metaKey&&!/[0-9]/.test(e.key)){e.preventDefault();return;}
      // maxlength would otherwise swallow a digit typed over a full box
      if(/^[0-9]$/.test(e.key)&&b.value&&!e.ctrlKey&&!e.metaKey)b.value='';
    });
    b.addEventListener('input',()=>{
      const d=b.value.replace(/[^0-9]/g,'');
      b.value=d.slice(-1);                    // keep the last digit if several arrived
      row.classList.remove('bad');
      paint();
      if(b.value&&i<bs.length-1)bs[i+1].focus();
      if(val().length===bs.length&&onFull)onFull(val());
    });
    b.addEventListener('paste',e=>{
      e.preventDefault();
      const d=((e.clipboardData||window.clipboardData).getData('text')||'').replace(/[^0-9]/g,'').slice(0,bs.length);
      if(!d)return;
      bs.forEach((x,j)=>{x.value=d[j]||'';});
      paint();
      bs[Math.min(d.length,bs.length-1)].focus();
      if(d.length===bs.length&&onFull)onFull(d);
    });
    b.addEventListener('focus',()=>setTimeout(()=>b.select(),0));
  });
  return{
    value:val,
    clear(){bs.forEach(b=>{b.value='';});paint();row.classList.remove('bad');},
    focus(){bs[0].focus();},
    reject(){row.classList.add('bad');buzz(30);setTimeout(()=>{this.clear();this.focus();},420);}
  };
}

// The board asks for a PIN when it wants one. Only ever show one prompt.
let pinAsking=false;
function askPin(){
  if(pinAsking)return;
  pinAsking=true;
  sheet('Access PIN',
    '<p class="hint">This board is locked. Enter its 4 digit PIN.</p>'+
    '<div class="pinrow" id="pinAsk">'+
      [0,1,2,3].map(i=>'<input class="pinbox" type="password" inputmode="numeric" pattern="[0-9]*" '+
        'maxlength="1" autocomplete="off" aria-label="PIN digit '+(i+1)+'">').join('')+
    '</div>'+
    '<p class="hint" id="pinAskMsg" style="margin-top:12px"></p>',
    ()=>{
      const p=bindPin('pinAsk',v=>{
        sessionStorage.setItem('pin',v);
        send({cmd:'auth',token:v});
      });
      PIN_ASK=p;p.focus();
    });
}
let PIN_ASK=null;
function pinAskDone(ok,msg){
  if(!pinAsking)return;
  if(ok){pinAsking=false;PIN_ASK=null;closeSheet();return;}
  if(PIN_ASK)PIN_ASK.reject();
  const m=$('pinAskMsg');if(m)m.textContent=msg||'That PIN was not right.';
}

// ---- reboot progress ----
// A reboot drops the socket and re-enumerates USB, so the page would otherwise
// just look frozen. Show it, and count the reconnect attempts.
let booting=false,bootT0=0;
function bootStart(what){
  booting=true;bootT0=Date.now();tries=0;
  $('bootTitle').textContent='Rebooting the board';
  $('bootMsg').textContent=what||'USB is re-enumerating on the PC.';
  $('bootTry').textContent='waiting for the board to come back';
  $('bootBar').style.width='4%';
  $('boot').classList.add('on');
  bootTick();
}
function bootTick(){
  if(!booting)return;
  const secs=(Date.now()-bootT0)/1000;
  $('bootBar').style.width=Math.min(94,8+secs*11)+'%';
  $('bootTry').textContent=tries?('reconnecting, attempt '+tries):'waiting for the board to come back';
  if(secs>25){
    $('bootTitle').textContent='Still waiting';
    $('bootMsg').textContent='If the board does not return, check the USB cable and reload the page.';
  }
  setTimeout(bootTick,700);
}
function bootDone(){
  if(!booting)return;
  booting=false;
  $('bootBar').style.width='100%';
  setTimeout(()=>{$('boot').classList.remove('on');toast('Board is back','ok');},450);
}

// ---- access point changed ----
// Changing the AP drops this phone off the network mid-request, so the reply
// never arrives. Show the new details and wait for the socket to come back.
let apPending=false;
function apStart(ssid,pass){
  apPending=true;tries=0;
  $('apnewInfo').innerHTML=row('Network',ssid)+row('Password',pass||'open, no password');
  $('apnewTry').textContent='waiting for you to reconnect';
  $('apnew').classList.add('on');
  apTick();
}
function apTick(){
  if(!apPending)return;
  $('apnewTry').textContent=tries?('still looking, attempt '+tries):'waiting for you to reconnect';
  setTimeout(apTick,700);
}
function apDone(){
  if(!apPending)return;
  apPending=false;
  $('apnew').classList.remove('on');
  toast('Reconnected','ok');
}
function sheet(title,html,onOpen){
  $('sheetin').innerHTML='<div class="sheeth"><h3>'+esc(title)+'</h3>'+
    '<button class="iconbtn" onclick="closeSheet()">'+ico('x')+'</button></div>'+html;
  $('sheet').classList.add('on');
  if(onOpen)onOpen();
}
function closeSheet(){$('sheet').classList.remove('on');}
$('sheet').addEventListener('click',e=>{if(e.target===$('sheet'))closeSheet();});

// =====================================================================
//  settings (local feel) + layout config (on the board)
// =====================================================================
const S={sens:2.2,acc:1.2,scr:1.5,mom:.9,nat:false,tap:true,hap:true,f3drag:false,f3app:true};
try{Object.assign(S,JSON.parse(localStorage.getItem('feel')||'{}'));}catch(e){}
const saveFeel=()=>{try{localStorage.setItem('feel',JSON.stringify(S));}catch(e){}};

// Host OS globals, declared here (not with the rest of the host-OS block lower
// down) because defCfg() below reads OS the moment CFG is seeded. 'mac'|'win'|'linux'.
let OS='win';               // the computer's OS
let OS_SRC='pending';       // 'auto' | 'manual' | 'pending' | 'undetermined'
let OS_DET='';              // what the board detected, even when OS is pinned by hand
let ASW_MOD='ALT';          // modifier the app switcher holds: ALT on PC, GUI on Mac
let scOsManual=false;       // user tapped a shortcut OS tab, so stop following the host
function osTok(s){return s==='mac'?'mac':s==='linux'?'linux':(s==='windows'||s==='win')?'win':'';}

// The default quick actions differ by computer: a Mac mutes Teams with Cmd, grabs
// a region shot with Cmd+Shift+4, and has Mission Control where Windows has the
// projector menu. Knobs are media-usage codes, which are OS-neutral. These seed a
// fresh board only; once you edit or the board has a saved set, that wins.
function defCfg(){
  const mac=OS==='mac';
  return {
    quick:[
      {n:'Mic mute',i:'micoff',t:'key',v:mac?'GUI+SHIFT+M':'CTRL+SHIFT+M',tog:1},
      {n:'Answer',i:'phone',t:'key',v:'F10'},
      {n:'Hang up',i:'phoneoff',t:'key',v:'F11'},
      mac?{n:'Mission Control',i:'layers',t:'gesture',v:'task_view'}
         :{n:'Display',i:'monitor',t:'key',v:'GUI+P'},
      {n:'Lock PC',i:'lock',t:'cmd',v:'lock'},
      {n:'Screenshot',i:'copy',t:'key',v:mac?'GUI+SHIFT+4':'GUI+SHIFT+S'}
    ],
    knobs:[{n:'Volume',i:'vol',t:'media',up:233,dn:234,step:14,mode:'endless'}]
  };
}
let cfgIsDefault=true;                 // false once the board has a saved set or the user edits
let CFG=defCfg();
function loadCfg(){
  send({cmd:'ui_get'},r=>{
    if(r&&r.data){try{const p=JSON.parse(r.data);if(p&&p.quick&&p.knobs){CFG=p;cfgIsDefault=false;}}catch(e){}}
    if(cfgIsDefault)CFG=defCfg();       // no saved set: seed for whatever OS we know now
    renderQuick();renderKnobs();
  });
}
function saveCfg(){
  cfgIsDefault=false;                   // this is the user's set now, not a per-OS default
  send({cmd:'ui_save',data:JSON.stringify(CFG)},r=>{
    if(r&&r.status==='ok')toast('Saved to board','ok');else toast('Save failed','bad');
  });
}

// =====================================================================
//  navigation
// =====================================================================
// `s` is the tab bar label. Seven tabs across a phone leaves about 50px each,
// so the bar gets its own shorter wording rather than an ellipsis.
const VIEWS=[
  {id:'pad',  t:'Trackpad',  s:'Pad',   i:'cursor'},
  {id:'keys', t:'Keyboard',  s:'Type',  i:'keyboard'},
  {id:'short',t:'Keys',      s:'Keys',  i:'keycap'},
  {id:'media',t:'Media',     s:'Media', i:'media'},
  {id:'game', t:'Gamepad',   s:'Game',  i:'gamepad', beta:1},
  {id:'power',t:'Session',   s:'Power', i:'power'},
  {id:'setup',t:'Settings',  s:'Setup', i:'settings'}
];
let curView='pad';
// =====================================================================
//  sidebar width - a default from the viewport, overridable by the user
// =====================================================================
const RAIL={
  slim:false, manual:false,
  init(){
    try{
      const v=localStorage.getItem('railslim');
      if(v!==null){this.manual=true;this.slim=(v==='1');}
    }catch(e){}
    if(!this.manual)this.slim=innerWidth<1100;
    this.apply();
    // Rotating a phone changes the width by hundreds of pixels. Follow that, but
    // only while the user has not expressed a preference of their own.
    addEventListener('resize',()=>{
      if(this.manual)return;
      const s=innerWidth<1100;
      if(s!==this.slim){this.slim=s;this.apply();}
    });
  },
  toggle(){
    this.slim=!this.slim;this.manual=true;
    try{localStorage.setItem('railslim',this.slim?'1':'0');}catch(e){}
    this.apply();buzz(8);
  },
  apply(){
    document.body.classList.toggle('rail-slim',this.slim);
    const b=$('btnRail');
    if(b)b.setAttribute('aria-label',this.slim?'Expand sidebar':'Collapse sidebar');
  }
};

function buildNav(){
  const beta=v=>v.beta?'<em class="beta">beta</em>':'';
  $('navlist').innerHTML=VIEWS.map(v=>
    '<button class="navbtn" data-v="'+v.id+'">'+ico(v.i)+'<span>'+v.t+beta(v)+'</span></button>').join('');
  $('tabbar').innerHTML=VIEWS.map(v=>
    '<button data-v="'+v.id+'"'+(v.beta?' class="hasbeta"':'')+'>'+ico(v.i)+'<i>'+(v.s||v.t)+'</i></button>').join('');
  document.querySelectorAll('[data-v]').forEach(b=>b.onclick=()=>go(b.dataset.v));
}
function go(id){
  curView=id;
  VIEWS.forEach(v=>$('v-'+v.id).classList.toggle('on',v.id===id));
  document.querySelectorAll('[data-v]').forEach(b=>b.classList.toggle('on',b.dataset.v===id));
  const v=VIEWS.find(x=>x.id===id);
  $('vtitle').textContent=v?v.t:'';
  $('btnInfo').setAttribute('aria-label',(v?v.t:'')+' help');
  $('main').scrollTop=0;
  try{location.hash=id;}catch(e){}
}
window.addEventListener('hashchange',()=>{
  const id=location.hash.replace('#','');
  if(VIEWS.some(v=>v.id===id)&&id!==curView)go(id);
});

// =====================================================================
//  swipe between tabs
// =====================================================================
// Deliberately hard to trigger. A tab switch that fires while you are working
// the pad, dragging something, or scrolling a long list would be far worse than
// no gesture at all, so every rule below is a reason to give up rather than to
// act, and the listeners are passive - this can never swallow a scroll.
(function(){
  // Anything that already interprets a finger opts out entirely. The pad is the
  // whole point of this list: it owns its own gestures and must never share one.
  const KEEP='#padwrap,.kbd,.knob,.mouse,.seg,input,textarea,select,button,a,label,'
            +'[data-k],[data-mb],[data-md],[contenteditable]';
  const DIST=64;      // shorter than this is a stray finger, not a swipe
  const RATIO=2.2;    // and it has to be decisively sideways, not a diagonal
  const MAXMS=600;    // a flick. Anything slower is a drag or a scroll
  const HOLDMS=250;   // sat still first? that is a hold - never a swipe
  const SLOP=10;
  let id=-1,x0=0,y0=0,t0=0,dead=true,moved=false;
  const main=$('main');

  main.addEventListener('pointerdown',e=>{
    if(id!==-1){dead=true;return;}                 // a second finger cancels it
    if(e.pointerType!=='touch'){dead=true;return;} // mice drag, they do not swipe
    if(e.target.closest(KEEP)){dead=true;return;}
    id=e.pointerId;x0=e.clientX;y0=e.clientY;t0=performance.now();
    dead=false;moved=false;
  },{passive:true});

  main.addEventListener('pointermove',e=>{
    if(dead||e.pointerId!==id)return;
    const dx=e.clientX-x0,dy=e.clientY-y0;
    if(moved)return;
    if(Math.abs(dx)<SLOP&&Math.abs(dy)<SLOP)return;
    moved=true;
    // Judge the direction on the first real movement. Committing here means a
    // finger that starts downwards keeps scrolling even if it curves sideways.
    if(performance.now()-t0>HOLDMS||Math.abs(dx)<=Math.abs(dy))dead=true;
  },{passive:true});

  const end=e=>{
    if(e.pointerId!==id)return;
    const dx=e.clientX-x0,dy=e.clientY-y0,dt=performance.now()-t0;
    id=-1;
    if(dead||!moved)return;
    // Never while the pad owns the host: a held button or key would be stranded.
    if(FS.on||dragging||grabLock||held.size||pts.size)return;
    if(dt>MAXMS||Math.abs(dx)<DIST||Math.abs(dx)<RATIO*Math.abs(dy))return;
    const i=VIEWS.findIndex(v=>v.id===curView)+(dx<0?1:-1);
    if(i<0||i>=VIEWS.length)return;   // stop at the ends rather than wrap around
    go(VIEWS[i].id);buzz(12);
  };
  main.addEventListener('pointerup',end,{passive:true});
  main.addEventListener('pointercancel',e=>{if(e.pointerId===id){id=-1;dead=true;}},{passive:true});
})();

// =====================================================================
//  trackpad
// =====================================================================
const pad=$('pad'),padwrap=$('padwrap');
const pts=new Map();
let accX=0,accY=0,scrollPx=0,panPx=0;
let vx=0,vy=0,lastT=0,momRAF=0;
// Scroll carries its own velocity. It used to borrow the pointer's, which is only
// ever written by one-finger moves, so lifting two fingers flung the cursor using
// whatever the last single-finger drag had left behind.
let svx=0,svy=0,scrollRAF=0;
// Where the three-finger slide last stepped the app switcher from.
let aswBaseX=0,aswBaseY=0;
const ASW_STEP=56;
let mode=0,maxPts=0,startX=0,startY=0,fired=false;
let pinchBase=0,pinchAcc=0;
let dragging=false,grabLock=false,lastTapAt=0,holdTimer=0,downPt=null,primaryId=-1,dtapArmed=false;

// Tuned to the platform values these gestures have to coexist with:
//   TAP_MS/TAP_PX  - a tap the OS would accept as a click
//   DTAP_MS        - under GetDoubleClickTime(), 500 ms on Windows by default
//   HOLD_MS        - Android + UIKit long press is 500 ms; 350 feels better for a grab
//   HOLD_PX        - finger jitter on glass, roughly Android's touch slop
//   DRAG_MS/PX     - how long to wait after a second tap before calling it a drag
//   NUDGE_PX       - must exceed SM_CXDOUBLECLK (4 px) - see beginDrag()
const TAP_MS=300,TAP_PX=12,HOLD_MS=350,HOLD_PX=14,DTAP_MS=450,DRAG_MS=250,DRAG_PX=8,NUDGE_PX=6;

const holdRing=document.createElement('div');
holdRing.className='hold';pad.appendChild(holdRing);

function padXY(e){const r=pad.getBoundingClientRect();return{x:e.clientX-r.left,y:e.clientY-r.top};}
function blip(id,x,y){
  let el=$('b'+id);
  if(!el){el=document.createElement('div');el.className='blip';el.id='b'+id;pad.appendChild(el);}
  el.style.transform='translate3d('+x+'px,'+y+'px,0)';
}
function unblip(id){const el=$('b'+id);if(el)el.remove();}
function spread(){const a=[...pts.values()];return a.length<2?0:Math.hypot(a[0].x-a[1].x,a[0].y-a[1].y);}
function setGrab(on){
  grabLock=on;
  if(on&&!dragging){txBtn(1,1,1);dragging=true;}
  if(!on&&dragging){txBtn(1,0,1);dragging=false;}
  pad.classList.toggle('grab',on);
  ['btnGrab','btnGrab2','btnGrab3','btnGrab4'].forEach(id=>{const b=$(id);if(b)b.classList.toggle('on',on);});
  buzz(on?18:8);
  badge();   // the label is state, not something only a touch may refresh
}
// nudge: Windows fuses a click and an immediately following press into a double
// click when both land within SM_CXDOUBLECLK (4 px). On a title bar that arrives
// as WM_NCLBUTTONDBLCLK and maximises the window instead of grabbing it, so shift
// the pointer first and the two stay separate events.
function beginDrag(nudge){
  if(dragging)return;
  if(nudge)txMove(NUDGE_PX,0,0,0);
  txBtn(1,1,1);dragging=true;pad.classList.add('grab');buzz(22);badge();
}
function endDrag(){
  if(!dragging||grabLock)return;
  txBtn(1,0,1);dragging=false;pad.classList.remove('grab');badge();
}
function startHold(x,y){
  clearTimeout(holdTimer);
  holdRing.style.left=x+'px';holdRing.style.top=y+'px';
  holdRing.style.animationDuration=HOLD_MS+'ms';
  holdRing.classList.remove('go');void holdRing.offsetWidth;holdRing.classList.add('go');
  holdTimer=setTimeout(()=>{if(pts.size===1)beginDrag(false);holdRing.classList.remove('go');},HOLD_MS);
}
function cancelHold(){clearTimeout(holdTimer);holdTimer=0;holdRing.classList.remove('go');}

pad.addEventListener('pointerdown',e=>{
  pad.setPointerCapture(e.pointerId);
  const p=padXY(e);
  pts.set(e.pointerId,{x:p.x,y:p.y,x0:p.x,y0:p.y,t0:performance.now()});
  blip(e.pointerId,p.x,p.y);
  pad.classList.add('live');
  if(pts.size>maxPts)maxPts=pts.size;

  if(pts.size===1){
    stopMomentum();stopScrollMomentum();maxPts=1;fired=false;mode=1;primaryId=e.pointerId;
    startX=p.x;startY=p.y;lastT=performance.now();vx=vy=0;downPt=p;
    // A second contact soon after a tap is ambiguous: lift quickly and it is a
    // double click, move or keep holding and it is a drag. Wait and see, the way
    // a precision trackpad does - deciding immediately broke double click.
    if(performance.now()-lastTapAt<DTAP_MS){
      lastTapAt=0;dtapArmed=true;
      clearTimeout(holdTimer);
      holdTimer=setTimeout(()=>{
        if(pts.size===1&&dtapArmed){dtapArmed=false;beginDrag(true);}
      },DRAG_MS);
    }
    else startHold(p.x,p.y);
  }else{
    cancelHold();dtapArmed=false;
    // A gesture is starting, so nothing the pointer was doing may continue.
    stopMomentum();stopScrollMomentum();
    if(pts.size===2){mode=2;pinchBase=spread();pinchAcc=0;}
    else{
      mode=3;
      if(S.f3drag){fired=true;beginDrag(false);}   // macOS-style three finger drag
    }
  }
  badge();e.preventDefault();
},{passive:false});

function onMove(e){
  const p=pts.get(e.pointerId);if(!p)return;
  const l=padXY(e),dx=l.x-p.x,dy=l.y-p.y;
  p.x=l.x;p.y=l.y;blip(e.pointerId,l.x,l.y);
  const now=performance.now(),dt=Math.max(1,now-lastT);lastT=now;

  // maxPts, not pts.size: once two fingers have been down this contact belongs to
  // a gesture, and lifting one of them must not hand the last finger the pointer.
  // Real trackpads gate on contact count the same way - you take your hand off and
  // touch again to point. Without this, the tail of a two-finger scroll flicks the
  // cursor across the screen because the fingers never leave at the same instant.
  if(mode===1&&pts.size===1&&maxPts===1){
    if(Math.hypot(l.x-startX,l.y-startY)>HOLD_PX)cancelHold();
    // moving after the second tap settles it: this is a drag, not a double click
    if(dtapArmed&&Math.hypot(l.x-startX,l.y-startY)>DRAG_PX){
      dtapArmed=false;cancelHold();beginDrag(true);
    }
    const sp=Math.hypot(dx,dy)/dt;
    const g=S.sens*(1+S.acc*Math.min(sp,4));
    accX+=dx*g;accY+=dy*g;
    vx=dx*g/dt;vy=dy*g/dt;
  }else if(mode===3&&S.f3drag){
    if(e.pointerId===primaryId){          // one finger's delta, not the sum of three
      const g=S.sens*(1+S.acc*Math.min(Math.hypot(dx,dy)/dt,4));
      accX+=dx*g;accY+=dy*g;
    }
  }else if(mode===3&&S.f3app){
    // Windows draws the switcher while your fingers are still down and commits
    // when you lift. Deciding only on lift meant choosing a window blind.
    if(e.pointerId===primaryId){
      const sx=l.x-startX, sy=l.y-startY;
      if(!ASW.open&&Math.abs(sx)>44&&Math.abs(sx)>Math.abs(sy)){
        aswOpen();aswBaseX=l.x;aswBaseY=l.y;
      }
      if(ASW.open){
        while(l.x-aswBaseX>=ASW_STEP){aswBaseX+=ASW_STEP;aswStep(1);}
        while(aswBaseX-l.x>=ASW_STEP){aswBaseX-=ASW_STEP;aswStep(-1);}
        while(l.y-aswBaseY>=ASW_STEP){aswBaseY+=ASW_STEP;aswArrow('DOWN');}
        while(aswBaseY-l.y>=ASW_STEP){aswBaseY-=ASW_STEP;aswArrow('UP');}
      }
    }
  }else if(mode===2&&pts.size===2&&maxPts===2){
    const sp=spread();
    if(pinchBase>0&&Math.abs(sp-pinchBase)>26){
      pinchAcc+=sp-pinchBase;pinchBase=sp;
      if(Math.abs(pinchAcc)>32){send({cmd:'gesture',name:pinchAcc>0?'zoom_in':'zoom_out'});pinchAcc=0;buzz(6);}
    }else{
      const sy=dy*S.scr*(S.nat?1:-1), sx=dx*S.scr*(S.nat?-1:1);
      scrollPx+=sy;panPx+=sx;
      svy=sy/dt;svx=sx/dt;
    }
  }
  e.preventDefault();
}
pad.addEventListener('pointerrawupdate' in window?'pointerrawupdate':'pointermove',onMove,{passive:false});
if('onpointerrawupdate' in window)pad.addEventListener('pointermove',e=>e.preventDefault(),{passive:false});

function onUp(e){
  const p=pts.get(e.pointerId);
  pts.delete(e.pointerId);unblip(e.pointerId);
  cancelHold();

  if(pts.size===0&&dragging&&!grabLock){endDrag();fired=true;}

  if(pts.size===0&&p&&!fired){
    const dt=performance.now()-p.t0, moved=Math.hypot(p.x-p.x0,p.y-p.y0);
    if(maxPts===3){
      // The switcher was already open and tracking, so lifting is the choice.
      if(ASW.open){fired=true;aswClose(true);}
      else{
        // This finger's own travel. Measuring the last finger to lift against the
        // first finger's starting point mixed two contacts that sit 80px apart,
        // so a straight three-finger swipe up came out as a sideways one.
        const sx=p.x-p.x0, sy=p.y-p.y0;
        if(Math.hypot(sx,sy)>52){
          fired=true;
          send({cmd:'gesture',name:Math.abs(sx)>Math.abs(sy)?(sx>0?'desktop_right':'desktop_left'):(sy<0?'task_view':'show_desktop')});
          buzz(14);
        }else if(dt<TAP_MS&&moved<14){fired=true;txBtn(4,2,1);}
      }
    }
    else if(maxPts===2){
      if(dt<300&&moved<14){fired=true;txBtn(2,2,1);buzz(10);}
      else scrollGlide();          // coast the scroll, never the pointer
    }
    else if(maxPts===1){
      // lifted quickly after a second tap and never moved: that is a double click
      if(dtapArmed&&dt<TAP_MS&&moved<TAP_PX){fired=true;txBtn(1,2,1);buzz(8);}
      else if(S.tap&&dt<TAP_MS&&moved<TAP_PX){fired=true;txBtn(1,2,1);lastTapAt=performance.now();}
      else glide();
    }
  }
  dtapArmed=false;
  // Alt is held while the switcher is up. If the last finger has gone and nothing
  // above committed it, close it here rather than leave the PC with Alt down.
  if(pts.size===0&&ASW.open)aswClose(true);

  if(pts.size===0){maxPts=0;mode=0;pad.classList.remove('live');}
  else if(dragging&&S.f3drag){
    // fingers come off one at a time - stay in drag until the last one lifts
    if(e.pointerId===primaryId)primaryId=pts.keys().next().value;
  }
  else if(pts.size===1)mode=1;
  else if(pts.size===2){mode=2;pinchBase=spread();}
  badge();e.preventDefault();
}
pad.addEventListener('pointerup',onUp,{passive:false});
pad.addEventListener('pointercancel',onUp,{passive:false});
pad.addEventListener('wheel',e=>{scrollPx+=(e.deltaY>0?1:-1)*24*S.scr*(S.nat?-1:1);e.preventDefault();},{passive:false});
pad.addEventListener('contextmenu',e=>e.preventDefault());

function badge(){
  $('padbadge').textContent=dragging?'grab':pts.size?pts.size+(pts.size>1?' fingers':' finger'):'';
}
function glide(){
  if(S.mom<=0||dragging||grabLock)return;
  if(maxPts!==1)return;            // never fling the pointer after a gesture
  if(Math.hypot(vx,vy)<.35)return;
  cancelAnimationFrame(momRAF);
  const step=()=>{
    vx*=S.mom;vy*=S.mom;
    if(Math.hypot(vx,vy)<.06){momRAF=0;return;}
    accX+=vx*16;accY+=vy*16;
    momRAF=requestAnimationFrame(step);
  };
  momRAF=requestAnimationFrame(step);
}
function stopMomentum(){if(momRAF){cancelAnimationFrame(momRAF);momRAF=0;}vx=vy=0;}
// The same coasting a real trackpad gives a flicked scroll, on the scroll axis.
function scrollGlide(){
  if(S.mom<=0)return;
  if(Math.hypot(svx,svy)<.35)return;
  cancelAnimationFrame(scrollRAF);
  const step=()=>{
    svx*=S.mom;svy*=S.mom;
    if(Math.hypot(svx,svy)<.06){scrollRAF=0;return;}
    scrollPx+=svy*16;panPx+=svx*16;
    scrollRAF=requestAnimationFrame(step);
  };
  scrollRAF=requestAnimationFrame(step);
}
function stopScrollMomentum(){if(scrollRAF){cancelAnimationFrame(scrollRAF);scrollRAF=0;}svx=svy=0;}

// one socket write per frame, worst case
function flush(){
  let dx=Math.round(accX),dy=Math.round(accY);
  accX-=dx;accY-=dy;
  let w=0,p=0;
  if(Math.abs(scrollPx)>=24){w=Math.trunc(scrollPx/24);scrollPx-=w*24;}
  if(Math.abs(panPx)>=42){p=Math.trunc(panPx/42);panPx-=p*42;}
  if(dx||dy||w||p)txMove(clamp(dx,-2000,2000),clamp(dy,-2000,2000),clamp(w,-120,120),clamp(p,-120,120));
  requestAnimationFrame(flush);
}
requestAnimationFrame(flush);

// =====================================================================
//  fullscreen - explicit state machine, survives ESC / system exit / back
// =====================================================================
const FS={
  on:false,pushed:false,home:null,after:null,
  supported(){return !!(padwrap.requestFullscreen||padwrap.webkitRequestFullscreen);},
  enter(){
    if(this.on)return;
    this.on=true;
    // iOS Safari makes any -webkit-overflow-scrolling container a containing
    // block for position:fixed children, and an animating ancestor does the same
    // on every browser. Either one traps the pad inside main, underneath the
    // header. Reparenting to body is the only thing that cannot be defeated by
    // an ancestor, so the pad is moved rather than restyled.
    this.home=padwrap.parentNode;
    this.after=padwrap.nextSibling;
    document.body.appendChild(padwrap);
    padwrap.classList.add('fs');document.body.classList.add('fsmode');
    document.body.style.overflow='hidden';
    if(this.supported()){try{(padwrap.requestFullscreen||padwrap.webkitRequestFullscreen).call(padwrap);}catch(e){}}
    if(!this.pushed){try{history.pushState({fs:1},'');this.pushed=true;}catch(e){}}
    // Fullscreen hands the deck the whole screen, which is usually a different
    // tier. The observer only fires if the width changed, and on a wide page it
    // may not have, so ask for the rebuild directly.
    if($('kbd2'))buildKbdIn('kbd2');
    this.sync();
  },
  exit(fromPop){
    if(!this.on)return;
    this.on=false;padwrap.classList.remove('fs');document.body.classList.remove('fsmode');
    document.body.style.overflow='';
    if(this.home){this.home.insertBefore(padwrap,this.after||null);this.home=null;this.after=null;}
    if(document.fullscreenElement||document.webkitFullscreenElement){
      try{(document.exitFullscreen||document.webkitExitFullscreen).call(document);}catch(e){}
    }
    if(this.pushed&&!fromPop){try{history.back();}catch(e){}}
    this.pushed=false;
    if($('kbd2'))buildKbdIn('kbd2');
    this.sync();
  },
  toggle(){this.on?this.exit():this.enter();},
  sync(){
    const b=$('btnFs');
    b.innerHTML=ico(this.on?'min':'max');
    b.classList.toggle('on',this.on);
  }
};
// the browser can drop out of fullscreen without telling us (ESC, gesture)
['fullscreenchange','webkitfullscreenchange'].forEach(ev=>
  document.addEventListener(ev,()=>{
    const active=!!(document.fullscreenElement||document.webkitFullscreenElement);
    if(!active&&FS.on&&FS.supported())FS.exit();
  }));
window.addEventListener('popstate',()=>{if(FS.on)FS.exit(true);});
// Escape is what anyone on a desktop reaches for, and iOS Safari never enters
// real fullscreen, so its own gesture is not available either.
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&FS.on)FS.exit();});
$('btnFs').onclick=()=>FS.toggle();
$('btnFsExitTop').onclick=()=>FS.exit();

// =====================================================================
//  keyboard deck - the pad card showing keys instead of the trackpad
// =====================================================================
// The dedicated Type tab is untouched. This is the same keyboard in the place
// your hand already is, so a click and the typing that follows it do not cost a
// trip across the app.
const DECK={
  on:false,
  set(on,quiet){
    if(on===this.on&&quiet)return;
    this.on=on;
    // Neither half may leave the host holding something down. Going to keys
    // drops a grabbed mouse button; coming back drops any latched modifier.
    if(on){
      if(grabLock)setGrab(false);
      endDrag();
      cancelHold();
      stopMomentum();
    }else{
      releaseAllHeld();
    }
    padwrap.classList.toggle('kb',on);
    // The deck has no width until it is shown, so the tier it was last built at
    // is stale the moment it appears. ResizeObserver only reports that a frame
    // later, which is one painted frame of the wrong layout - and at a narrow
    // width that means ANSI keys squeezed to 15px. Rebuild now instead.
    if(on&&$('kbd2'))buildKbdIn('kbd2');
    const b=$('btnDeck');
    b.innerHTML=ico(on?'cursor':'keyboard');
    b.setAttribute('aria-label',on?'Show trackpad':'Show keyboard');
    b.classList.toggle('on',on);
    if(!quiet){try{localStorage.setItem('deck',on?'1':'0');}catch(e){}}
  },
  toggle(){this.set(!this.on);buzz(this.on?14:8);}
};
$('btnDeck').onclick=()=>DECK.toggle();
$('pkSend').onclick=()=>doType(false,'pkText');
$('pkEnter').onclick=()=>doType(true,'pkText');
$('pkText').addEventListener('keydown',e=>{
  if(e.key==='Enter'){e.preventDefault();doType(true,'pkText');}
});

// =====================================================================
//  knobs
// =====================================================================
// Two behaviours, because only one of them is honest for volume:
//  endless - a rotary encoder, exactly like the volume knob on a keyboard.
//            It only ever sends up/down ticks, so it cannot drift out of sync
//            with the PC. No end stops, no assumed level. This is the default.
//  ranged  - 0..100 with end stops, for a knob where an absolute value is real.
const SWEEP=270, START=-135;
const RING={};
function tickRing(endless){
  const key=endless?'e':'r';
  if(RING[key])return RING[key];
  const n=endless?36:33, span=endless?360:SWEEP, from=endless?-90:135;
  const step=endless?span/n:span/(n-1);
  let s='';
  for(let i=0;i<n;i++){
    const a=(from+step*i)*Math.PI/180, c=Math.cos(a), y=Math.sin(a);
    s+='<line class="tk" x1="'+(50+42.5*c).toFixed(1)+'" y1="'+(50+42.5*y).toFixed(1)+
       '" x2="'+(50+47.5*c).toFixed(1)+'" y2="'+(50+47.5*y).toFixed(1)+'"/>';
  }
  return RING[key]=s;
}
function renderKnobs(){
  const host=$('knobs');
  host.innerHTML=CFG.knobs.map((k,i)=>{
    const endless=(k.mode||'endless')==='endless';
    return '<div class="knob"><button class="edit" data-kedit="'+i+'">'+ico('edit','ic-sm')+'</button>'+
    '<div class="dial" data-knob="'+i+'">'+
      '<div class="glow"></div>'+
      '<svg class="tks" viewBox="0 0 100 100">'+tickRing(endless)+'</svg>'+
      '<div class="cap"></div>'+
      '<div class="ptr" id="kp'+i+'"><i></i></div>'+
      '<div class="kv"><em id="kt'+i+'"></em></div>'+
      (endless?'':'<span class="mn">MIN</span><span class="mx">MAX</span>')+
    '</div><div class="kn">'+ico(k.i,'ic-sm')+'<span>'+esc(k.n)+'</span></div></div>';
  }).join('')+
    '<button class="knob" id="knobAdd" style="justify-content:center;color:var(--fg3)">'+
    ico('plus','ic-lg')+'<div class="kn">New knob</div></button>';

  host.querySelectorAll('[data-knob]').forEach(el=>bindKnob(el,+el.dataset.knob));
  host.querySelectorAll('[data-kedit]').forEach(b=>b.onclick=e=>{e.stopPropagation();knobEditor(+b.dataset.kedit);});
  const add=$('knobAdd');if(add)add.onclick=()=>knobEditor(-1);
  CFG.knobs.forEach((k,i)=>paintKnob(i,(k.mode||'endless')==='endless'?0:(k.pos===undefined?.5:k.pos)));
}
// endless: ang is absolute degrees, a lit cluster follows the indicator round.
// ranged:  ang is 0..1 of the sweep, lit from MIN up to the indicator.
function paintKnob(i,ang){
  const k=CFG.knobs[i], endless=(k.mode||'endless')==='endless';
  const p=$('kp'+i);if(!p)return;
  p.style.transform='rotate('+(endless?ang:START+SWEEP*ang)+'deg)';
  const tks=p.parentElement.querySelectorAll('.tk');
  if(endless){
    const n=tks.length, at=((Math.round(ang/(360/n))%n)+n)%n;
    for(let t=0;t<n;t++){
      let d=Math.abs(t-at);if(d>n/2)d=n-d;
      const on=d<=2;
      if(on!==tks[t].classList.contains('on'))tks[t].classList.toggle('on',on);
    }
  }else{
    const lit=Math.round(ang*(tks.length-1));
    for(let t=0;t<tks.length;t++){
      const on=t<=lit;
      if(on!==tks[t].classList.contains('on'))tks[t].classList.toggle('on',on);
    }
  }
}
function bindKnob(el,idx){
  const k=CFG.knobs[idx];
  const endless=(k.mode||'endless')==='endless';
  let active=false,lastAng=0,acc=0,steps=0,fade=0,press=0,moved=false;
  let ang=endless?0:(k.pos===undefined?.5:k.pos);
  const angOf=e=>{const r=el.getBoundingClientRect();
    return Math.atan2(e.clientY-(r.top+r.height/2),e.clientX-(r.left+r.width/2))*180/Math.PI;};
  const show=t=>{const n=$('kt'+idx);if(n)n.textContent=t;};

  el.addEventListener('pointerdown',e=>{
    active=true;moved=false;el.classList.add('act');el.setPointerCapture(e.pointerId);
    lastAng=angOf(e);clearTimeout(fade);
    // press and hold without turning = mute, the way a real volume knob clicks in
    press=setTimeout(()=>{if(!moved&&k.t==='media'){toggleMute();show('mute');buzz(24);}},450);
    e.preventDefault();
  },{passive:false});

  el.addEventListener('pointermove',e=>{
    if(!active)return;
    const a=angOf(e);let d=a-lastAng;
    if(d>180)d-=360;if(d<-180)d+=360;
    lastAng=a;
    if(Math.abs(d)>1.5){moved=true;clearTimeout(press);}
    acc+=d;
    if(endless){ang+=d;}
    else{
      const before=ang;
      ang=clamp(ang+d/SWEEP,0,1);
      if(ang===before)acc=0;            // hit an end stop, stop sending
    }
    paintKnob(idx,ang);
    const st=k.step||14;
    while(Math.abs(acc)>=st){
      const dir=acc>0?1:-1;acc-=dir*st;steps+=dir;
      if(k.t==='media')txConsumer(dir>0?k.up:k.dn);
      else send({cmd:'press',keys:dir>0?k.up:k.dn});
      buzz(7);
      show(endless?((steps>0?'+':'')+steps):Math.round(ang*100)+'%');
    }
    e.preventDefault();
  },{passive:false});

  const stop=e=>{
    if(!active)return;
    active=false;el.classList.remove('act');clearTimeout(press);
    if(!endless)k.pos=ang;
    acc=0;
    clearTimeout(fade);
    fade=setTimeout(()=>{show('');steps=0;},1800);
    e.preventDefault();
  };
  el.addEventListener('pointerup',stop,{passive:false});
  el.addEventListener('pointercancel',stop,{passive:false});
}
const MEDIA_USAGES=[['Volume up',233],['Volume down',234],['Mute',226],['Next track',181],['Previous track',182],
  ['Brighter',111],['Dimmer',112],['Play / pause',205],['Stop',183]];
function knobEditor(idx){
  const isNew=idx<0;
  const k=isNew?{n:'New knob',i:'knob',t:'media',up:233,dn:234,step:14,mode:'endless'}:CFG.knobs[idx];
  const opt=(v,l,sel)=>'<option value="'+v+'"'+(sel?' selected':'')+'>'+esc(l)+'</option>';
  sheet(isNew?'New knob':'Edit knob',
    '<div class="fld"><label>Name</label><input id="kn_n" value="'+esc(k.n)+'"></div>'+
    '<div class="fld"><label>Icon</label><select id="kn_i">'+
      ICON_CHOICES.map(n=>opt(n,n,n===k.i)).join('')+'</select></div>'+
    '<div class="fld"><label>Action type</label><select id="kn_t">'+
      opt('media','Media key',k.t==='media')+opt('key','Keyboard combo',k.t==='key')+'</select></div>'+
    '<div class="fld"><label>Turn right</label><select id="kn_up_m">'+
      MEDIA_USAGES.map(m=>opt(m[1],m[0],m[1]==k.up)).join('')+'</select>'+
      '<input id="kn_up_k" style="margin-top:8px" placeholder="e.g. '+(OS==='mac'?'GUI+PLUS':'CTRL+PLUS')+'" value="'+(k.t==='key'?esc(k.up):'')+'"></div>'+
    '<div class="fld"><label>Turn left</label><select id="kn_dn_m">'+
      MEDIA_USAGES.map(m=>opt(m[1],m[0],m[1]==k.dn)).join('')+'</select>'+
      '<input id="kn_dn_k" style="margin-top:8px" placeholder="e.g. '+(OS==='mac'?'GUI+MINUS':'CTRL+MINUS')+'" value="'+(k.t==='key'?esc(k.dn):'')+'"></div>'+
    '<div class="fld"><label>Behaviour</label><select id="kn_m">'+
      opt('endless','Endless - like a keyboard volume knob',(k.mode||'endless')==='endless')+
      opt('ranged','Ranged 0 to 100 with end stops',k.mode==='ranged')+'</select>'+
      '<p class="hint" style="margin-top:6px">Endless only sends up and down ticks, so it can never drift out of step with the PC. Ranged shows a level, but the board cannot read the PC back, so it is only what you set here.</p></div>'+
    '<div class="fld"><label>Degrees per step ('+(k.step||14)+')</label>'+
      '<input type="range" id="kn_s" min="5" max="40" step="1" value="'+(k.step||14)+'"></div>'+
    '<div class="row" style="margin-top:14px">'+
      (isNew?'':'<button class="btn sm bad" id="kn_del">'+ico('trash','ic-sm')+'Delete</button>')+
      '<button class="btn ok" id="kn_ok">'+ico('check','ic-sm')+'Save</button></div>',
    ()=>{
      const syncType=()=>{
        const t=$('kn_t').value;
        $('kn_up_m').style.display=t==='media'?'':'none';
        $('kn_dn_m').style.display=t==='media'?'':'none';
        $('kn_up_k').style.display=t==='key'?'':'none';
        $('kn_dn_k').style.display=t==='key'?'':'none';
      };
      $('kn_t').onchange=syncType;syncType();
      $('kn_ok').onclick=()=>{
        const t=$('kn_t').value;
        const o={n:$('kn_n').value||'Knob',i:$('kn_i').value,t:t,step:+$('kn_s').value,mode:$('kn_m').value,
          up:t==='media'?+$('kn_up_m').value:$('kn_up_k').value,
          dn:t==='media'?+$('kn_dn_m').value:$('kn_dn_k').value};
        if(isNew)CFG.knobs.push(o);else CFG.knobs[idx]=o;
        renderKnobs();saveCfg();closeSheet();
      };
      const del=$('kn_del');
      if(del)del.onclick=()=>{CFG.knobs.splice(idx,1);renderKnobs();saveCfg();closeSheet();};
    });
}

// =====================================================================
//  quick actions
// =====================================================================
const ICON_CHOICES=['bolt','mic','micoff','phone','phoneoff','monitor','vol','volx','sun','sundim',
  'lock','unlock','power','layers','present','search','home','copy','term','clock','knob','media',
  'command','keycap','appswitch','play','shield','cpu'];
function renderQuick(){
  $('quickTiles').innerHTML=CFG.quick.map((q,i)=>
    '<button class="tile" data-q="'+i+'">'+ico(q.i)+'<span>'+esc(q.n)+'</span></button>').join('')+
    '<button class="tile" id="quickAdd" style="color:var(--fg3)">'+ico('plus')+'<span>Add</span></button>';
  $('quickTiles').querySelectorAll('[data-q]').forEach(b=>{
    const i=+b.dataset.q;
    if(CFG.quick[i]&&CFG.quick[i].tog)b.classList.add('cy');
    b.onclick=()=>runQuick(CFG.quick[i],b);
    let t=0;
    b.addEventListener('pointerdown',()=>{t=setTimeout(()=>quickEditor(i),600);});
    ['pointerup','pointerleave','pointercancel'].forEach(ev=>b.addEventListener(ev,()=>clearTimeout(t)));
  });
  $('quickAdd').onclick=()=>quickEditor(-1);
}
function runQuick(q,el){
  if(!q)return;
  if(q.t==='key')send({cmd:'press',keys:q.v});
  else if(q.t==='media')send({cmd:'media',key:q.v});
  else if(q.t==='gesture')send({cmd:'gesture',name:q.v});
  else if(q.t==='cmd')send({cmd:q.v});
  buzz(10);
  if(el){
    el.classList.remove('flash');void el.offsetWidth;el.classList.add('flash');
    if(q.tog)el.classList.toggle('act');   // latched: what we believe we set
  }
}
// The board cannot read the PC back, so mute is tracked as intent. Tap again to undo.
let muted=false;
function toggleMute(){
  muted=!muted;
  txConsumer(226);
  document.querySelectorAll('[data-md="mute"]').forEach(b=>b.classList.toggle('act',muted));
  return muted;
}
// Suggestions shown in the quick-action editor, tuned to the computer: a Mac gets
// Cmd-based mute/screenshot/Spotlight, a PC gets the Windows set.
function keyPresets(){
  return OS==='mac'
    ? ['GUI+SHIFT+M','GUI+SHIFT+4','GUI+SHIFT+5','GUI+SPACE','CTRL+UP','GUI+H','GUI+M','GUI+W','F10','F11','CTRL+GUI+Q','GUI+Q']
    : ['CTRL+SHIFT+M','ALT+A','CTRL+D','F4','F7','F10','F11','GUI+P','GUI+SHIFT+S','GUI+L','CTRL+SHIFT+S','CTRL+SHIFT+H'];
}
function quickEditor(idx){
  const isNew=idx<0;
  const q=isNew?{n:'New action',i:'bolt',t:'key',v:'F1'}:CFG.quick[idx];
  const opt=(v,l,sel)=>'<option value="'+v+'"'+(sel?' selected':'')+'>'+esc(l)+'</option>';
  sheet(isNew?'New action':'Edit action',
    '<div class="fld"><label>Label</label><input id="q_n" value="'+esc(q.n)+'"></div>'+
    '<div class="fld"><label>Icon</label><select id="q_i">'+ICON_CHOICES.map(n=>opt(n,n,n===q.i)).join('')+'</select></div>'+
    '<div class="fld"><label>Type</label><select id="q_t">'+
      opt('key','Keyboard combo',q.t==='key')+opt('media','Media key',q.t==='media')+
      opt('gesture','Gesture',q.t==='gesture')+opt('cmd','Board command',q.t==='cmd')+'</select></div>'+
    '<div class="fld"><label>Value</label><input id="q_v" list="q_presets" value="'+esc(q.v)+'">'+
      '<datalist id="q_presets">'+keyPresets().map(k=>'<option value="'+k+'">').join('')+'</datalist></div>'+
    '<p class="hint">Mic mute and call keys vary by laptop and app. '+(OS==='mac'
      ?'Teams on a Mac uses Cmd+Shift+M, Zoom uses Cmd+Shift+A'
      :'Teams uses CTRL+SHIFT+M, Zoom uses ALT+A')+', and many laptops map them to an F-key.</p>'+
    '<div class="row" style="margin-top:14px">'+
      (isNew?'':'<button class="btn sm bad" id="q_del">'+ico('trash','ic-sm')+'Delete</button>')+
      '<button class="btn sm" id="q_test">Test</button>'+
      '<button class="btn ok" id="q_ok">'+ico('check','ic-sm')+'Save</button></div>',
    ()=>{
      const read=()=>({n:$('q_n').value||'Action',i:$('q_i').value,t:$('q_t').value,v:$('q_v').value});
      $('q_test').onclick=()=>runQuick(read());
      $('q_ok').onclick=()=>{
        const o=read();
        if(isNew)CFG.quick.push(o);else CFG.quick[idx]=o;
        renderQuick();saveCfg();closeSheet();
      };
      const del=$('q_del');
      if(del)del.onclick=()=>{CFG.quick.splice(idx,1);renderQuick();saveCfg();closeSheet();};
    });
}

// =====================================================================
//  gamepad
// =====================================================================
const GP={lx:0,ly:0,rx:0,ry:0,hat:0,btns:0,dirty:false,enabled:false};
function bindStick(el,axis){
  const cap=el.querySelector('.cap');
  let on=false,r=0;
  const set=(dx,dy)=>{
    cap.style.transform='translate3d('+dx+'px,'+dy+'px,0)';
    const nx=Math.round(clamp(dx/r,-1,1)*127), ny=Math.round(clamp(dy/r,-1,1)*127);
    if(axis==='l'){GP.lx=nx;GP.ly=ny;}else{GP.rx=nx;GP.ry=ny;}
    GP.dirty=true;
  };
  el.addEventListener('pointerdown',e=>{
    on=true;el.classList.add('act');el.setPointerCapture(e.pointerId);
    r=el.getBoundingClientRect().width*0.27;
    move(e);e.preventDefault();
  },{passive:false});
  const move=e=>{
    const b=el.getBoundingClientRect();
    let dx=e.clientX-(b.left+b.width/2), dy=e.clientY-(b.top+b.height/2);
    const d=Math.hypot(dx,dy);
    if(d>r){dx=dx/d*r;dy=dy/d*r;}
    set(dx,dy);
  };
  el.addEventListener('pointermove',e=>{if(on){move(e);e.preventDefault();}},{passive:false});
  const up=e=>{
    if(!on)return;on=false;el.classList.remove('act');
    cap.style.transform='translate3d(0,0,0)';set(0,0);buzz(6);e.preventDefault();
  };
  el.addEventListener('pointerup',up,{passive:false});
  el.addEventListener('pointercancel',up,{passive:false});
}
function gpFlush(){
  if(GP.dirty&&GP.enabled){txGamepad(GP.lx,GP.ly,GP.rx,GP.ry,GP.hat,GP.btns);GP.dirty=false;}
  requestAnimationFrame(gpFlush);
}
requestAnimationFrame(gpFlush);
function bindGpButtons(){
  document.querySelectorAll('[data-gb]').forEach(b=>{
    const bit=1<<(+b.dataset.gb);
    const dn=e=>{GP.btns|=bit;GP.dirty=true;b.classList.add('on');buzz(6);e.preventDefault();};
    const up=e=>{GP.btns&=~bit;GP.dirty=true;b.classList.remove('on');e.preventDefault();};
    b.addEventListener('pointerdown',dn,{passive:false});
    b.addEventListener('pointerup',up,{passive:false});
    b.addEventListener('pointercancel',up,{passive:false});
    b.addEventListener('pointerleave',up,{passive:false});
  });
  document.querySelectorAll('[data-hat]').forEach(b=>{
    const v=+b.dataset.hat;
    const dn=e=>{GP.hat=v;GP.dirty=true;b.classList.add('on');buzz(6);e.preventDefault();};
    const up=e=>{GP.hat=0;GP.dirty=true;b.classList.remove('on');e.preventDefault();};
    b.addEventListener('pointerdown',dn,{passive:false});
    b.addEventListener('pointerup',up,{passive:false});
    b.addEventListener('pointercancel',up,{passive:false});
    b.addEventListener('pointerleave',up,{passive:false});
  });
}
function gpSync(){
  const c=$('gpCard');
  c.classList.toggle('gpoff',!GP.enabled);
  let msg=c.querySelector('.gpmsg');
  if(!GP.enabled){
    if(!msg){
      msg=document.createElement('div');msg.className='gpmsg';
      // No wrapper div: .gpmsg is the flex column itself, so the icon, the text
      // and the button share one centre line.
      msg.innerHTML=ico('gamepad','ic-lg')+
        '<p class="hint" style="margin:0;max-width:280px">The controller is a second USB device, off by default so it does not clutter the host. Turning it on reboots the board. <b>This tab is beta and not fully tested yet.</b></p>'+
        '<button class="btn pri" id="gpEnable">Enable controller</button>';
      c.appendChild(msg);
      msg.querySelector('#gpEnable').onclick=()=>{
        send({cmd:'gamepad_enable',on:true,reboot:true});
        bootStart('Adding the controller device.');
      };
    }
  }else if(msg)msg.remove();
  $('btnGpToggle').classList.toggle('on',GP.enabled);
}
$('btnGpToggle').onclick=()=>{
  if(!confirm((GP.enabled?'Disable':'Enable')+' the controller? The board reboots.'))return;
  send({cmd:'gamepad_enable',on:!GP.enabled,reboot:true});
  bootStart((GP.enabled?'Removing':'Adding')+' the controller device.');
};

// =====================================================================
//  keyboard
// =====================================================================
//  keyboard layouts
// =====================================================================
// A physical keyboard does not shrink its keys as the board gets smaller - it
// drops whole blocks. A 60% has no arrows, a 65% adds them, a 75% adds the
// function row, a TKL adds the navigation cluster and a full-size adds the
// numpad. This grows the same way, so a wide screen gets *more keys* rather
// than the same few keys stretched across it.
//
// Every key is [label, name, width, class]. Width is in quarter-units, so 4 is
// one key. A null name is dead space, which real keyboards have and which is
// what makes the function row and the arrow cluster read correctly.
//
// ANSI is exactly 15u wide, so the main block is a 60 column grid and 1u is 4
// columns. Every real key width is then a whole number of columns: 1.25u = 5,
// 1.5u = 6, 1.75u = 7, 2u = 8, 2.25u = 9, 2.75u = 11, 6.25u = 25. That is what
// lets the rows be authentic instead of approximated.
//
// Labels are raw HTML because several are inline SVG, so any symbol that means
// something in HTML is pre-escaped here rather than at render time.
const ANSI_COLS=60;
const GAP=w=>[null,null,w];

// Esc + three groups of four, TKL spacing: 1u + 1u gap + 4u + .5u + 4u + .5u + 4u.
const ANSI_FN=[
 ['Esc','ESC',4],GAP(4),
 ['F1','F1',4],['F2','F2',4],['F3','F3',4],['F4','F4',4],GAP(2),
 ['F5','F5',4],['F6','F6',4],['F7','F7',4],['F8','F8',4],GAP(2),
 ['F9','F9',4],['F10','F10',4],['F11','F11',4],['F12','F12',4]
];
const ANSI_MAIN=[
 [['`','`',4],['1','1',4],['2','2',4],['3','3',4],['4','4',4],['5','5',4],['6','6',4],['7','7',4],['8','8',4],['9','9',4],['0','0',4],['-','-',4],['=','=',4],[ico('bksp','ic-sm'),'BACKSPACE',8]],
 [['Tab','TAB',6],['Q','q',4],['W','w',4],['E','e',4],['R','r',4],['T','t',4],['Y','y',4],['U','u',4],['I','i',4],['O','o',4],['P','p',4],['[','[',4],[']',']',4],['\\','\\',6]],
 [['\u21ea','CAPSLOCK',7],['A','a',4],['S','s',4],['D','d',4],['F','f',4],['G','g',4],['H','h',4],['J','j',4],['K','k',4],['L','l',4],[';',';',4],['&#39;',"'",4],[ico('enter','ic-sm'),'ENTER',9]],
 [['\u21e7','#SHIFT',9,'mod'],['Z','z',4],['X','x',4],['C','c',4],['V','v',4],['B','b',4],['N','n',4],['M','m',4],[',',',',4],['.','.',4],['/','/',4],['\u21e7','#SHIFT',11,'mod']],
 [['Ctrl','#CTRL',5,'mod'],['Win','#GUI',5,'mod'],['Alt','#ALT',5,'mod'],['Space','SPACE',25],['Alt','#ALT',5,'mod'],['Win','#GUI',5,'mod'],[ico('menukey','ic-sm'),'MENU',5],['Ctrl','#CTRL',5,'mod']]
];
// 65% right-hand column: the keys a compact layout reaches through its nav row,
// kept reachable here so moving up a tier never takes a key away. Six rows, to
// line up with the main block's function row plus five - and the row that pairs
// with the function row is the empty one, so hiding it in the deck costs no key.
const NAV_65=[
 [GAP(3)],
 [['Del','DELETE',1],['Home','HOME',1],['End','END',1]],
 [['Ins','INSERT',1],['PgUp','PAGEUP',1],['PgDn','PAGEDOWN',1]],
 [GAP(3)],
 [GAP(1),[ico('up','ic-sm'),'UP',1],GAP(1)],
 [[ico('left','ic-sm'),'LEFT',1],[ico('down','ic-sm'),'DOWN',1],[ico('right','ic-sm'),'RIGHT',1]]
];
// TKL right-hand block: the 3x2 navigation cluster above the same arrow T. Six
// rows, because its top row sits level with the function row exactly as it does
// on a real board - PrtSc above Ins, not beside the number row.
const NAV_TKL=[
 [['PrtSc','PRINTSCREEN',1],['ScrLk','SCROLLLOCK',1],['Pause','PAUSE',1]],
 [['Ins','INSERT',1],['Home','HOME',1],['PgUp','PAGEUP',1]],
 [['Del','DELETE',1],['End','END',1],['PgDn','PAGEDOWN',1]],
 [GAP(3)],
 [GAP(1),[ico('up','ic-sm'),'UP',1],GAP(1)],
 [[ico('left','ic-sm'),'LEFT',1],[ico('down','ic-sm'),'DOWN',1],[ico('right','ic-sm'),'RIGHT',1]]
];
// Numpad, placed explicitly because the tall Plus and Enter and the double
// width Zero cannot be expressed as a list of rows.
// [label, name, column, row, colSpan, rowSpan]
const NUMPAD=[
 ['Num','NUMLOCK',1,1],['/','KPSLASH',2,1],['*','KPSTAR',3,1],['-','KPMINUS',4,1],
 ['7','KP7',1,2],['8','KP8',2,2],['9','KP9',3,2],['+','KPPLUS',4,2,1,2],
 ['4','KP4',1,3],['5','KP5',2,3],['6','KP6',3,3],
 ['1','KP1',1,4],['2','KP2',2,4],['3','KP3',3,4],[ico('enter','ic-sm'),'KPENTER',4,4,1,2],
 ['0','KP0',1,5,2,1],['.','KPDOT',3,5]
];

// ---------------------------------------------------------------------
// Compact layout, for a phone held upright. Twelve columns, and the symbols
// live on a 123 layer because there is no room for them beside the letters.
// The slash still sits immediately left of the right Shift, and Up directly
// above Down, so the muscle memory matches the wider layouts.
const KROWS_ABC=[
 {fn:1,keys:[['Esc','ESC',1],['F1','F1',1],['F2','F2',1],['F3','F3',1],['F4','F4',1],['F5','F5',1],['F6','F6',1],['F7','F7',1],['F8','F8',1],['F9','F9',1],['F10','F10',1],['F11','F11',1],['F12','F12',1]]},
 {num:1,keys:[['1','1',1],['2','2',1],['3','3',1],['4','4',1],['5','5',1],['6','6',1],['7','7',1],['8','8',1],['9','9',1],['0','0',1],['-','-',1],[ico('bksp','ic-sm'),'BACKSPACE',1]]},
 {keys:[['Tab','TAB',1],['Q','q',1],['W','w',1],['E','e',1],['R','r',1],['T','t',1],['Y','y',1],['U','u',1],['I','i',1],['O','o',1],['P','p',1],['\\','\\',1]]},
 {keys:[['\u21ea','CAPSLOCK',1],['A','a',1],['S','s',1],['D','d',1],['F','f',1],['G','g',1],['H','h',1],['J','j',1],['K','k',1],['L','l',1],[';',';',1],[ico('enter','ic-sm'),'ENTER',1]]},
 {keys:[['\u21e7','#SHIFT',1,'mod'],['Z','z',1],['X','x',1],['C','c',1],['V','v',1],['B','b',1],['N','n',1],['M','m',1],['/','/',1],
        ['\u21e7','#SHIFT',1,'mod'],[ico('up','ic-sm'),'UP',1]]},
 {keys:[['123','@sym',1,'mod'],['Ctrl','#CTRL',1,'mod'],['Alt','#ALT',1,'mod'],['Win','#GUI',1,'mod'],['Space','SPACE',5],
        [ico('left','ic-sm'),'LEFT',1],[ico('down','ic-sm'),'DOWN',1],[ico('right','ic-sm'),'RIGHT',1]]},
 {nav:1,keys:[['Home','HOME',1],['End','END',1],['PgUp','PAGEUP',1],['PgDn','PAGEDOWN',1],['Del','DELETE',1]]}
];
// Copy, paste, undo and the app switcher are Command based on a Mac, so unlike
// every other key in the tables these cannot be baked in - they are appended to
// the nav row at build time, which buildKbd() reruns whenever the OS changes.
const NAV_COMBOS=()=>OS==='mac'
 ? [['\u2318C','GUI+C',1],['\u2318V','GUI+V',1],['\u2318Z','GUI+Z',1],['\u2318Tab','GUI+TAB',1]]
 : [['Ctrl+C','CTRL+C',1],['Ctrl+V','CTRL+V',1],['Ctrl+Z','CTRL+Z',1],['Alt+Tab','ALT+TAB',1]];
// Every printable ASCII symbol is reachable here. Nothing outside ASCII is
// offered, because the board types through a US HID map - a character it cannot
// represent would silently do nothing, which is worse than not showing it.
// Same twelve column geometry as the letter layer, so Backspace, Enter, the
// right Shift, the slash and the arrows never move under the thumb.
const KROWS_SYM=[
 {fn:1,keys:[['Esc','ESC',1],['F1','F1',1],['F2','F2',1],['F3','F3',1],['F4','F4',1],['F5','F5',1],['F6','F6',1],['F7','F7',1],['F8','F8',1],['F9','F9',1],['F10','F10',1],['F11','F11',1],['F12','F12',1]]},
 {num:1,keys:[['1','1',1],['2','2',1],['3','3',1],['4','4',1],['5','5',1],['6','6',1],['7','7',1],['8','8',1],['9','9',1],['0','0',1],['=','=',1],[ico('bksp','ic-sm'),'BACKSPACE',1]]},
 {keys:[['Tab','TAB',1],['!','!',1],['@','@',1],['#','#',1],['$','$',1],['%','%',1],['^','^',1],['&amp;','&',1],['*','*',1],['(','(',1],[')',')',1],['|','|',1]]},
 {keys:[['_','_',1],['+','+',1],['[','[',1],[']',']',1],['{','{',1],['}','}',1],['&lt;','<',1],['&gt;','>',1],['`','`',1],['~','~',1],[':',':',1],[ico('enter','ic-sm'),'ENTER',1]]},
 {keys:[[';',';',1],['&#39;',"'",1],['&quot;','"',1],[',',',',1],['.','.',1],['?','?',1],['-','-',1],['\\','\\',1],['/','/',1],
        ['\u21e7','#SHIFT',1,'mod'],[ico('up','ic-sm'),'UP',1]]},
 {keys:[['ABC','@abc',1,'mod'],['Ctrl','#CTRL',1,'mod'],['Alt','#ALT',1,'mod'],['Win','#GUI',1,'mod'],['Space','SPACE',5],
        [ico('left','ic-sm'),'LEFT',1],[ico('down','ic-sm'),'DOWN',1],[ico('right','ic-sm'),'RIGHT',1]]},
 {nav:1,keys:[['Home','HOME',1],['End','END',1],['PgUp','PAGEUP',1],['PgDn','PAGEDOWN',1],['Del','DELETE',1]]}
];

// Which layout the container is wide enough for. Measured on the keyboard, not
// the window, because the deck inside the trackpad card is far narrower than
// the page it sits on.
const KB_TIERS=[['full',1300],['tkl',1010],['ansi',500],['compact',0]];
function kbTier(w){for(const t of KB_TIERS)if(w>=t[1])return t[0];return 'compact';}
let KLAYER='abc';
const mods=new Set();
const held=new Set();
function keyDown(k){if(held.has(k))return;held.add(k);txKey(k,1);}
function keyUp(k){if(!held.has(k))return;held.delete(k);txKey(k,0);}
// Anything still held has to be let go before the keys under the fingers change,
// or the host is left with a modifier down and no button to lift it.
function releaseAllHeld(){
  [...held].forEach(k=>keyUp(k));
  mods.clear();
  document.querySelectorAll('.kbd .mod.on').forEach(x=>x.classList.remove('on'));
}
// The same key exists on both keyboards, so a modifier latched on one has to
// light up on the other - they drive a single host, not one each.
function syncMods(){
  document.querySelectorAll('.kbd [data-k]').forEach(b=>{
    const raw=b.dataset.k;
    if(raw[0]!=='#')return;
    b.classList.toggle('on',mods.has(raw.slice(1)));
  });
}
// Built into any number of containers. KLAYER is shared deliberately: switching
// to symbols on the deck should not leave the Type tab on letters.
const KBDS=['kbd','kbd2'];
const kbTierOf={};          // hostId -> tier last rendered, so a resize is cheap

// A modifier's cap is relabelled per computer OS - Alt/Opt, Win/Cmd/Super - so a
// Mac user is not hunting for a key called Win. Everything else keeps the label
// baked into the row. The double quote has to be escaped for the attribute
// itself, not just for display, or data-k=""" truncates and that key silently
// stops working.
function kBtn(k){
  if(!k||k[1]===null)return '<span class="kgap" style="grid-column:span '+(k?k[2]:1)+'"></span>';
  return '<button class="'+(k[3]||'')+'" style="grid-column:span '+k[2]+'"'+
         ' data-k="'+esc(k[1]).replace(/"/g,'&quot;')+'">'+(modLabel(k[1])||k[0])+'</button>';
}
const kRow=(keys,cls)=>'<div class="krow'+(cls?' '+cls:'')+'">'+keys.map(kBtn).join('')+'</div>';

function kbBlocks(tier){
  if(tier==='compact'){
    const rows=KLAYER==='abc'?KROWS_ABC:KROWS_SYM;
    return '<div class="kblock kmain c12">'+
      rows.map(r=>kRow(r.nav?r.keys.concat(NAV_COMBOS()):r.keys,
                       r.fn?'fn':r.nav?'nav':r.num?'num':'')).join('')+'</div>';
  }
  // The function row is never optional above compact: Esc lives nowhere else, and
  // a tier that drops it would take a key away from a *wider* screen.
  let main='<div class="kblock kmain ansi">'+kRow(ANSI_FN,'fn');
  // The digits row is tagged rather than counted: a positional selector silently
  // moved onto the letters when the function row stopped being the first child.
  main+=ANSI_MAIN.map((r,i)=>kRow(r,i===0?'num':'')).join('')+'</div>';

  // The right-hand column always ends with the arrow T, so its bottom row lines
  // up with the main block's bottom row however many clusters sit above it.
  // Its top row is tagged fn for the same reason the numpad's spacer is: the
  // deck hides the function row, and a block that keeps six rows while the main
  // block shows five divides the same height differently and stops lining up.
  const tkl=(tier==='tkl'||tier==='full');
  const right='<div class="kblock kside c3">'+
    (tkl?NAV_TKL:NAV_65).map((r,i)=>kRow(r,i===0?'fn':'')).join('')+'</div>';

  let num='';
  if(tier==='full'){
    num='<div class="kblock knum">'+kRow([GAP(4)],'fn')+'<div class="kpad">'+
      NUMPAD.map(k=>'<button style="grid-column:'+k[2]+(k[4]?' / span '+k[4]:'')+
        ';grid-row:'+k[3]+(k[5]?' / span '+k[5]:'')+'"'+
        ' data-k="'+esc(k[1]).replace(/"/g,'&quot;')+'">'+k[0]+'</button>').join('')+
      '</div></div>';
  }
  return main+right+num;
}

function kbTierFor(host){
  const w=host.clientWidth||host.getBoundingClientRect().width;
  let t=kbTier(w);
  // The deck is locked to the trackpad card's height, so in a card it cannot
  // afford a function row or a nav cluster however wide the page is. Fullscreen
  // is the exception: there it has the whole screen, and capping it would be the
  // "big screen, few keys" problem this tiering exists to remove.
  const pw=document.getElementById('padwrap');
  const fs=pw&&pw.classList.contains('fs');
  if(host.id==='kbd2'&&t!=='compact'&&!fs)t='ansi';
  return t;
}

function buildKbd(){KBDS.forEach(id=>{if($(id))buildKbdIn(id);});}

// Re-render only when the tier actually changes. A resize that does not cross a
// threshold costs one width read, which matters because rebuilding drops every
// listener and would fight a key held down at the time.
function kbWatch(){
  if(!window.ResizeObserver)return;
  KBDS.forEach(id=>{
    const h=$(id); if(!h||h._kro)return;
    h._kro=new ResizeObserver(()=>{ if(kbTierFor(h)!==kbTierOf[id])buildKbdIn(id); });
    h._kro.observe(h);
  });
}

function buildKbdIn(hostId){
  const host=$(hostId);
  const tier=kbTierFor(host);
  kbTierOf[hostId]=tier;
  host.className='kbd t-'+tier;
  host.innerHTML=kbBlocks(tier);

  // Real down/up per key rather than a click, so two fingers can chord: hold Alt
  // with one and strike Tab with another, exactly like a physical keyboard.
  // A quick tap on a modifier still latches it, for one-handed use.
  host.querySelectorAll('[data-k]').forEach(b=>{
    const raw=b.dataset.k;
    // Layer switches are not keys - they must not send anything to the host.
    if(raw[0]==='@'){
      b.onclick=()=>{releaseAllHeld();KLAYER=raw.slice(1);buildKbd();buzz(8);};
      return;
    }
    const isMod=raw[0]==='#', key=isMod?raw.slice(1):raw;
    let t0=0,usedWith=false;

    b.addEventListener('pointerdown',e=>{
      b.setPointerCapture(e.pointerId);
      t0=performance.now();usedWith=false;
      if(isMod&&mods.has(key)){          // tap a latched modifier again to drop it
        mods.delete(key);keyUp(key);b.classList.remove('dn');syncMods();buzz(5);e.preventDefault();return;
      }
      if(!isMod)mods.forEach(()=>{usedWith=true;});
      keyDown(key);b.classList.add('dn');buzz(isMod?5:8);
      e.preventDefault();
    },{passive:false});

    const up=e=>{
      b.classList.remove('dn');
      if(isMod){
        // short tap with nothing struck while held -> latch it for the next key
        if(performance.now()-t0<350&&!usedWith){mods.add(key);}
        else{mods.delete(key);keyUp(key);}
        syncMods();
      }else{
        keyUp(key);
        mods.forEach(m=>{keyUp(m);});          // latched modifiers fire once
        mods.clear();
        syncMods();
      }
      e.preventDefault();
    };
    b.addEventListener('pointerup',up,{passive:false});
    b.addEventListener('pointercancel',up,{passive:false});
  });
  // A rebuild across a tier change throws away the old caps, so the latched
  // modifiers have to be re-lit on the new ones.
  syncMods();
}

// =====================================================================
//  shortcuts
// =====================================================================
// Columns are [label, macOS, Windows, Linux]. An empty column means that OS has
// no equivalent and the row is hidden for it rather than faked.
//
// Every combo here resolves to a real HID key. pressKeyCombo() upper-cases the
// string, splits on '+', looks each token up in mapKeyName(), and falls back to
// pressing a single character. So GUI+. and CTRL+- work, but a literal '+' can
// never appear as a token - that is why PLUS and EQUALS exist as names.
const SC=[
['Essentials','copy',[
['Copy','GUI+C','CTRL+C','CTRL+C'],
['Cut','GUI+X','CTRL+X','CTRL+X'],
['Paste','GUI+V','CTRL+V','CTRL+V'],
['Paste without formatting','GUI+SHIFT+V','CTRL+SHIFT+V','CTRL+SHIFT+V'],
['Undo','GUI+Z','CTRL+Z','CTRL+Z'],
['Redo','GUI+SHIFT+Z','CTRL+Y','CTRL+SHIFT+Z'],
['Select all','GUI+A','CTRL+A','CTRL+A'],
['Save','GUI+S','CTRL+S','CTRL+S'],
['Save as','GUI+SHIFT+S','CTRL+SHIFT+S','CTRL+SHIFT+S'],
['New','GUI+N','CTRL+N','CTRL+N'],
['Open','GUI+O','CTRL+O','CTRL+O'],
['Print','GUI+P','CTRL+P','CTRL+P'],
['Find','GUI+F','CTRL+F','CTRL+F'],
['Find next','GUI+G','F3','CTRL+G'],
['Find previous','GUI+SHIFT+G','SHIFT+F3','CTRL+SHIFT+G'],
['Replace','GUI+ALT+F','CTRL+H','CTRL+H'],
['Close window','GUI+W','CTRL+W','CTRL+W'],
['Quit application','GUI+Q','ALT+F4','CTRL+Q'],
['Preferences','GUI+,','CTRL+,','CTRL+,'],
['Bold','GUI+B','CTRL+B','CTRL+B'],
['Italic','GUI+I','CTRL+I','CTRL+I'],
['Underline','GUI+U','CTRL+U','CTRL+U']
]],
['Moving and selecting text','keyboard',[
['Word left','ALT+LEFT','CTRL+LEFT','CTRL+LEFT'],
['Word right','ALT+RIGHT','CTRL+RIGHT','CTRL+RIGHT'],
['Start of line','GUI+LEFT','HOME','HOME'],
['End of line','GUI+RIGHT','END','END'],
['Start of document','GUI+UP','CTRL+HOME','CTRL+HOME'],
['End of document','GUI+DOWN','CTRL+END','CTRL+END'],
['Select word left','ALT+SHIFT+LEFT','CTRL+SHIFT+LEFT','CTRL+SHIFT+LEFT'],
['Select word right','ALT+SHIFT+RIGHT','CTRL+SHIFT+RIGHT','CTRL+SHIFT+RIGHT'],
['Select to start of line','GUI+SHIFT+LEFT','SHIFT+HOME','SHIFT+HOME'],
['Select to end of line','GUI+SHIFT+RIGHT','SHIFT+END','SHIFT+END'],
['Select to start of document','GUI+SHIFT+UP','CTRL+SHIFT+HOME','CTRL+SHIFT+HOME'],
['Select to end of document','GUI+SHIFT+DOWN','CTRL+SHIFT+END','CTRL+SHIFT+END'],
['Delete word left','ALT+BACKSPACE','CTRL+BACKSPACE','CTRL+BACKSPACE'],
['Delete word right','ALT+DELETE','CTRL+DELETE','CTRL+DELETE'],
['Delete to start of line','GUI+BACKSPACE','',''],
['Page up','PAGEUP','PAGEUP','PAGEUP'],
['Page down','PAGEDOWN','PAGEDOWN','PAGEDOWN']
]],
['Windows and desktops','appswitch',[
['Switch app','GUI+TAB','ALT+TAB','ALT+TAB'],
['Switch app backwards','GUI+SHIFT+TAB','ALT+SHIFT+TAB','ALT+SHIFT+TAB'],
['Next window in this app','GUI+`','','ALT+`'],
['Mission Control / Task view','CTRL+UP','GUI+TAB','GUI'],
['Application windows','CTRL+DOWN','',''],
['Show desktop','F11','GUI+D','GUI+D'],
['Minimise window','GUI+M','GUI+DOWN','GUI+H'],
['Maximise / full screen','CTRL+GUI+F','GUI+UP','GUI+UP'],
['Snap window left','','GUI+LEFT','GUI+LEFT'],
['Snap window right','','GUI+RIGHT','GUI+RIGHT'],
['Next desktop','CTRL+RIGHT','CTRL+GUI+RIGHT','CTRL+ALT+RIGHT'],
['Previous desktop','CTRL+LEFT','CTRL+GUI+LEFT','CTRL+ALT+LEFT'],
['New desktop','','CTRL+GUI+D',''],
['Close desktop','','CTRL+GUI+F4',''],
['Close the active window','GUI+W','ALT+F4','ALT+F4']
]],
['System','cpu',[
['Lock the screen','CTRL+GUI+Q','GUI+L','GUI+L'],
['Search','GUI+SPACE','GUI+S','GUI'],
['Start menu / launcher','','GUI','GUI'],
['Settings','','GUI+I',''],
['File manager','','GUI+E','GUI+E'],
['Run dialog','','GUI+R','ALT+F2'],
['Terminal','','','CTRL+ALT+T'],
['Force quit / Task Manager','GUI+ALT+ESC','CTRL+SHIFT+ESC',''],
['Security screen','','CTRL+ALT+DELETE','CTRL+ALT+DELETE'],
['Quick link menu','','GUI+X',''],
['Clipboard history','','GUI+V',''],
['Emoji picker','CTRL+GUI+SPACE','GUI+.','CTRL+.'],
['Project to a second screen','','GUI+P',''],
['System information','','GUI+PAUSE',''],
['Dictation','','GUI+H',''],
['Screen reader','','CTRL+GUI+ENTER',''],
['Magnifier zoom in','GUI+ALT+EQUALS','GUI+PLUS',''],
['Magnifier zoom out','GUI+ALT+MINUS','GUI+MINUS',''],
['Context menu','','MENU','MENU'],
['Context menu (no Menu key)','','SHIFT+F10','SHIFT+F10']
]],
['Screenshots','monitor',[
['Whole screen','GUI+SHIFT+3','PRINTSCREEN','PRINTSCREEN'],
['Selected area','GUI+SHIFT+4','GUI+SHIFT+S','SHIFT+PRINTSCREEN'],
['Active window','','ALT+PRINTSCREEN','ALT+PRINTSCREEN'],
['Screenshot toolbar','GUI+SHIFT+5','',''],
['Start screen recording','','GUI+ALT+R','']
]],
['Browser','search',[
['New tab','GUI+T','CTRL+T','CTRL+T'],
['Close tab','GUI+W','CTRL+W','CTRL+W'],
['Reopen closed tab','GUI+SHIFT+T','CTRL+SHIFT+T','CTRL+SHIFT+T'],
['Next tab','CTRL+TAB','CTRL+TAB','CTRL+TAB'],
['Previous tab','CTRL+SHIFT+TAB','CTRL+SHIFT+TAB','CTRL+SHIFT+TAB'],
['First tab','GUI+1','CTRL+1','CTRL+1'],
['Last tab','GUI+9','CTRL+9','CTRL+9'],
['New window','GUI+N','CTRL+N','CTRL+N'],
['Private window','GUI+SHIFT+N','CTRL+SHIFT+N','CTRL+SHIFT+N'],
['Address bar','GUI+L','CTRL+L','CTRL+L'],
['Reload','GUI+R','CTRL+R','CTRL+R'],
['Reload ignoring cache','GUI+SHIFT+R','CTRL+SHIFT+R','CTRL+SHIFT+R'],
['Back','GUI+LEFT','ALT+LEFT','ALT+LEFT'],
['Forward','GUI+RIGHT','ALT+RIGHT','ALT+RIGHT'],
['Home page','GUI+SHIFT+H','ALT+HOME','ALT+HOME'],
['Bookmark this page','GUI+D','CTRL+D','CTRL+D'],
['Bookmark manager','GUI+ALT+B','CTRL+SHIFT+O','CTRL+SHIFT+O'],
['History','GUI+Y','CTRL+H','CTRL+H'],
['Downloads','GUI+SHIFT+J','CTRL+J','CTRL+J'],
['Developer tools','GUI+ALT+I','F12','F12'],
['Inspect element','GUI+SHIFT+C','CTRL+SHIFT+C','CTRL+SHIFT+C'],
['JavaScript console','GUI+ALT+J','CTRL+SHIFT+J','CTRL+SHIFT+J'],
['View page source','GUI+ALT+U','CTRL+U','CTRL+U'],
['Zoom in','GUI+EQUALS','CTRL+EQUALS','CTRL+EQUALS'],
['Zoom out','GUI+MINUS','CTRL+MINUS','CTRL+MINUS'],
['Reset zoom','GUI+0','CTRL+0','CTRL+0'],
['Full screen','CTRL+GUI+F','F11','F11'],
['Clear browsing data','GUI+SHIFT+DELETE','CTRL+SHIFT+DELETE','CTRL+SHIFT+DELETE']
]],
['Files','home',[
['New folder','GUI+SHIFT+N','CTRL+SHIFT+N','CTRL+SHIFT+N'],
['Rename','ENTER','F2','F2'],
['Open the selected item','GUI+DOWN','ENTER','ENTER'],
['Go up a folder','GUI+UP','ALT+UP','ALT+UP'],
['Back','GUI+LEFT','ALT+LEFT','ALT+LEFT'],
['Forward','GUI+RIGHT','ALT+RIGHT','ALT+RIGHT'],
['Move to trash','GUI+BACKSPACE','DELETE','DELETE'],
['Delete permanently','GUI+ALT+BACKSPACE','SHIFT+DELETE','SHIFT+DELETE'],
['Get info / properties','GUI+I','ALT+ENTER','CTRL+I'],
['Show hidden files','GUI+SHIFT+.','CTRL+H','CTRL+H'],
['Search in this folder','GUI+F','CTRL+F','CTRL+F'],
['Copy the path','GUI+ALT+C','CTRL+SHIFT+C','CTRL+SHIFT+C'],
['Duplicate','GUI+D','',''],
['New window','GUI+N','CTRL+N','CTRL+N'],
['Select all','GUI+A','CTRL+A','CTRL+A']
]]
];
const SC_COL={mac:1,win:2,linux:3};
const SC_NAME={mac:'macOS',win:'Windows',linux:'Linux'};
// The same physical key is called something different on each platform, so the
// caps are relabelled rather than showing the wire name.
const SC_MOD={
  GUI:{mac:'Cmd',win:'Win',linux:'Super'},
  ALT:{mac:'Option',win:'Alt',linux:'Alt'},
  CTRL:{mac:'Ctrl',win:'Ctrl',linux:'Ctrl'},
  SHIFT:{mac:'Shift',win:'Shift',linux:'Shift'}
};
const SC_KEY={ENTER:'Enter',ESC:'Esc',TAB:'Tab',SPACE:'Space',BACKSPACE:'Backspace',DELETE:'Delete',
  HOME:'Home',END:'End',PAGEUP:'PgUp',PAGEDOWN:'PgDn',UP:'Up',DOWN:'Down',LEFT:'Left',RIGHT:'Right',
  PRINTSCREEN:'PrtSc',PAUSE:'Pause',MENU:'Menu',PLUS:'+',MINUS:'-',EQUALS:'='};
// ---------------------------------------------------------------------
//  host OS  (the computer, not the phone)
// ---------------------------------------------------------------------
// The board tells us what it is plugged into by watching how the host enumerates
// it, and reports it as host_os in every status. That one fact drives three
// things on this side: which modifier the app switcher holds, how the modifier
// keys are labelled, and - unless you are browsing another - which shortcuts
// show. The firmware owns the same value for the lock shortcut and the gestures,
// so the two never disagree. SC_OS below is the *shortcuts* filter and defaults
// to OS; tapping a shortcut OS tab pins it (scOsManual) so browsing is not
// fought.
// (OS, OS_SRC, ASW_MOD, scOsManual and osTok are declared earlier, above defCfg,
//  because defCfg() reads OS while seeding CFG near the top of the script.)
// Short cap labels per computer OS. Unicode technical symbols like the ones
// already used on the keyboard, never emoji (see AGENTS.md).
// All three are single glyphs, which is what lets the modifier row survive a
// twelve column grid: "Win" needed 24px in a 22px key on a 360px phone and was
// clipped, while the glyph fits any width. Mac uses its own key symbols, which
// is both what a Mac user reads and narrow enough never to clip.
const KB_MODLABEL={
  '#CTRL' :{mac:'⌃',win:'Ctrl',linux:'Ctrl'},   // ⌃
  '#ALT'  :{mac:'⌥',win:'Alt', linux:'Alt'},     // ⌥
  '#GUI'  :{mac:'⌘',win:'⊞', linux:'◆'},  // ⌘  /  ⊞ (Windows)  /  ◆ (Super)
  '#SHIFT':{mac:'⇧',win:'⇧',linux:'⇧'} // ⇧
};
function modLabel(dataK){const m=KB_MODLABEL[dataK];return m?(m[OS]||m.win):null;}
function applyHostOs(os,src,det){
  if(src)OS_SRC=src;
  if(det!==undefined)OS_DET=osTok(det);
  const n=osTok(os);
  if(!n||n===OS){paintOsUi();return;}   // 'unknown'/'pending' -> keep what we have
  OS=n;
  ASW_MOD=(n==='mac')?'GUI':'ALT';
  if(!scOsManual){SC_OS=n;if(typeof renderSC==='function')renderSC();}
  if(typeof buildKbd==='function')buildKbd();
  // Re-seed the quick actions for the new OS, but only while they are still the
  // untouched defaults - never overwrite a set the board saved or the user edited.
  if(typeof cfgIsDefault!=='undefined'&&cfgIsDefault&&typeof defCfg==='function'){
    CFG=defCfg(); if(typeof renderQuick==='function'){renderQuick();renderKnobs();}
  }
  paintGestureLabels();
  paintOsUi();
}
// The trackpad gesture tiles carry the same command on every OS, but their names
// do not: a Space is a Desktop on Windows, Mission Control is Task View. Relabel
// them for the detected computer so the tile says what will actually happen.
const GS_LABEL={
  task_view:   {mac:'Mission Control',win:'Task view',   linux:'Activities'},
  desktop_left:{mac:'Space left',      win:'Desktop left', linux:'Workspace left'},
  desktop_right:{mac:'Space right',    win:'Desktop right',linux:'Workspace right'}
};
function paintGestureLabels(){
  document.querySelectorAll('[data-gs]').forEach(b=>{
    const m=GS_LABEL[b.dataset.gs]; if(!m)return;
    const sp=b.querySelector('span'); if(sp)sp.textContent=m[OS]||m.win;
  });
}
// Reflects the current OS and its source in the Settings selector. Safe to call
// before the Settings markup exists, since an early status can arrive first.
function paintOsUi(){
  const sel=document.getElementById('osSel');
  if(sel)sel.querySelectorAll('[data-os-set]').forEach(b=>
    b.classList.toggle('on', b.dataset.osSet===(OS_SRC==='manual'?OS:'auto')));
  const name={mac:'macOS',win:'Windows',linux:'Linux'}[OS]||'Unknown';
  const nm=document.getElementById('osName');
  if(nm)nm.textContent=OS_SRC==='pending'?'Detecting…'
    :OS_SRC==='undetermined'?'Not identified':name;
  // Ctrl+Alt+Del is Windows-only; on a Mac it does nothing and would break the
  // unlock flow, so the option is hidden and cleared there.
  const cad=document.getElementById('tCad');
  if(cad){cad.style.display=(OS==='mac')?'none':'';if(OS==='mac')cad.classList.remove('on');}
  const sub=document.getElementById('osSub');
  if(!sub)return;
  // A pin set on one computer follows the board to the next one - which is how a
  // board set up on a Mac ends up holding Cmd on a PC and opening the Start menu.
  // Say that out loud instead of letting it look like detection failing.
  const detName={mac:'macOS',win:'Windows',linux:'Linux'}[OS_DET];
  sub.textContent =
      (OS_SRC==='manual'&&detName&&OS_DET!==OS)
        ? 'Set by hand. This computer looks like '+detName+' \u2014 tap Auto to follow it.'
    : OS_SRC==='manual'       ? 'Set by hand'
    : OS_SRC==='auto'         ? 'Detected while this computer connected'
    : OS_SRC==='undetermined' ? 'This computer did not identify itself \u2014 pick one above'
    :                           'Working out whether this is a Mac or a PC';
}

let SC_OS='win';
try{
  const saved=localStorage.getItem('scos');
  if(saved&&SC_COL[saved]){SC_OS=saved;scOsManual=true;}   // a pinned browse choice
  else{
    // Only a first guess from the phone, until the board reports the real
    // computer OS through host_os and applyHostOs() takes over.
    const ua=navigator.userAgent||'';
    SC_OS=/Mac|iPhone|iPad|iPod/.test(ua)?'mac'
         :(/Linux|X11/.test(ua)&&!/Android/.test(ua))?'linux':'win';
  }
}catch(e){}
// Which groups are open. Essentials starts open so the screen is not a wall of
// closed headings on a first visit.
let SC_OPEN={0:1};
try{const o=JSON.parse(localStorage.getItem('scopen')||'null');if(o)SC_OPEN=o;}catch(e){}
const saveSCOpen=()=>{try{localStorage.setItem('scopen',JSON.stringify(SC_OPEN));}catch(e){}};

function caps(combo){
  return '<span class="keys">'+combo.split('+').map((t,i)=>{
    const k=t.toUpperCase(), m=SC_MOD[k];
    const lbl=m?m[SC_OS]:(SC_KEY[k]||k);
    return (i?'<span class="kjoin">+</span>':'')+
           '<span class="kcap'+(m?' m':'')+'">'+esc(lbl)+'</span>';
  }).join('')+'</span>';
}
function renderSC(){
  const q=($('scq').value||'').trim().toLowerCase(), col=SC_COL[SC_OS];
  let h='', n=0;
  SC.forEach((g,i)=>{
    const rows=g[2].filter(r=>r[col]&&
      (!q||r[0].toLowerCase().indexOf(q)>=0||r[col].toLowerCase().indexOf(q)>=0));
    if(!rows.length)return;
    n+=rows.length;
    // A search only shows what matched, so collapsing those away would hide the
    // answer. Open them for the duration without disturbing the saved state.
    const open=q?true:!!SC_OPEN[i];
    h+='<div class="scg'+(open?' open':'')+'">'+
      '<button class="scgh" data-gi="'+i+'" aria-expanded="'+open+'">'+ico(g[1])+
        '<h2>'+esc(g[0])+'</h2><span class="cnt">'+rows.length+'</span>'+
        ico('down','ic-sm chev')+'</button>'+
      '<div class="scgrid">'+
      rows.map(r=>'<button class="sck" data-sc="'+esc(r[col])+'">'+
        '<b>'+esc(r[0])+'</b>'+caps(r[col])+'</button>').join('')+
      '</div></div>';
  });
  $('scList').innerHTML=h||'<p class="hint">Nothing matches that search.</p>';
  $('scList').querySelectorAll('[data-sc]').forEach(b=>b.onclick=()=>{
    send({cmd:'press',keys:b.dataset.sc});buzz(10);
    b.classList.remove('flash');void b.offsetWidth;b.classList.add('flash');
  });
  // Toggle the class rather than re-render, so the page does not jump back to
  // the top every time a group is opened.
  $('scList').querySelectorAll('[data-gi]').forEach(hd=>hd.onclick=()=>{
    const g=hd.parentElement, on=!g.classList.contains('open');
    g.classList.toggle('open',on);
    hd.setAttribute('aria-expanded',on);
    SC_OPEN[hd.dataset.gi]=on?1:0;saveSCOpen();buzz(6);
  });
  $('scNote').textContent=n+' '+SC_NAME[SC_OS]+' shortcut'+(n===1?'':'s')+
    '. Tap a heading to open it, then tap a shortcut to press it on the PC.';
  document.querySelectorAll('[data-os]').forEach(b=>b.classList.toggle('on',b.dataset.os===SC_OS));
}

// =====================================================================
//  app switcher
// =====================================================================
// Alt+Tab is not one keystroke. It is a modifier held down while Tab is struck
// against it, and releasing the modifier is what actually raises the window.
// Sending ALT+TAB as a combo can therefore only ever toggle between the last two
// windows. This holds Alt open instead, so the host keeps its own switcher on
// screen and we just feed it Tabs.
const ASW={open:false,step:1,idle:0};
function aswHint(latched){
  $('aswHint').textContent=latched
    ? 'Tap the arrows to move through the windows, then Switch to it.'
    : 'Keep holding and slide in any direction. Let go to switch.';
}
function aswTab(dir){
  if(dir<0){keyDown('SHIFT');keyDown('TAB');keyUp('TAB');keyUp('SHIFT');}
  else{keyDown('TAB');keyUp('TAB');}
}
function aswOpen(){
  if(ASW.open)return;
  ASW.open=true;ASW.step=1;
  keyDown(ASW_MOD);          // Alt+Tab on a PC, Cmd+Tab on a Mac
  aswTab(1);
  $('aswN').textContent='1';
  $('asw').classList.add('on');
  aswHint(false);buzz(20);aswAlive();
}
function aswStep(dir){
  if(!ASW.open)return;
  aswTab(dir);
  ASW.step=Math.max(1,ASW.step+dir);
  $('aswN').textContent=ASW.step;
  buzz(7);aswAlive();
}
// Rows are moved with the arrow keys, not Tab, so they do not disturb the step
// count - that is tracking position along the row.
function aswArrow(k){
  if(!ASW.open)return;
  keyDown(k);keyUp(k);
  buzz(7);aswAlive();
}
// Alt held forever would be a dead PC, so the switcher commits itself if it is
// left alone.
function aswAlive(){clearTimeout(ASW.idle);ASW.idle=setTimeout(()=>aswClose(true),15000);}
function aswClose(commit){
  if(!ASW.open)return;
  clearTimeout(ASW.idle);
  if(!commit){keyDown('ESC');keyUp('ESC');}
  keyUp('TAB');keyUp('SHIFT');keyUp(ASW_MOD);keyUp('ALT');keyUp('GUI');
  ASW.open=false;
  $('asw').classList.remove('on');
  buzz(commit?14:6);
}
// The socket went away, so the board has already released everything for us.
// Just drop our own idea of what is held.
function aswAbort(){
  if(!ASW.open)return;
  clearTimeout(ASW.idle);ASW.open=false;
  $('asw').classList.remove('on');
}
function bindAppSwitch(b){
  const STEP_PX=56;
  let t=0,x0=0,y0=0,ax=0,ay=0,moved=0,down=false;
  b.addEventListener('pointerdown',e=>{
    down=true;x0=e.clientX;y0=e.clientY;ax=0;ay=0;moved=0;
    b.setPointerCapture(e.pointerId);
    clearTimeout(t);t=setTimeout(()=>{if(down)aswOpen();},HOLD_MS);
    e.preventDefault();
  },{passive:false});
  b.addEventListener('pointermove',e=>{
    if(!down)return;
    const dx=e.clientX-x0, dy=e.clientY-y0;
    moved=Math.max(moved,Math.hypot(dx,dy));
    if(!ASW.open){if(moved>HOLD_PX)clearTimeout(t);return;}
    const wx=Math.trunc(dx/STEP_PX), wy=Math.trunc(dy/STEP_PX);
    while(ax<wx){ax++;aswStep(1);}
    while(ax>wx){ax--;aswStep(-1);}
    while(ay<wy){ay++;aswArrow('DOWN');}
    while(ay>wy){ay--;aswArrow('UP');}
    e.preventDefault();
  },{passive:false});
  const up=e=>{
    if(!down)return;
    down=false;clearTimeout(t);
    // Slid while holding: they picked a window, so let go of Alt and raise it.
    // Held without sliding: keep it up and latched so it can be tapped instead,
    // because one thumb cannot both hold this and reach the arrows.
    if(ASW.open){if(moved>DRAG_PX)aswClose(true);else aswHint(true);}
    else{send({cmd:'gesture',name:'switch_app'});buzz(9);}
    e.preventDefault();
  };
  b.addEventListener('pointerup',up,{passive:false});
  b.addEventListener('pointercancel',e=>{
    if(!down)return;
    down=false;clearTimeout(t);
    if(ASW.open)aswHint(true);
  },{passive:false});
}

// =====================================================================
//  macros
// =====================================================================
let MACROS=[];
function loadMacros(){
  send({cmd:'macro_list'},r=>{MACROS=(r&&r.macros)||[];renderMacros();});
}
function renderMacros(){
  const host=$('macroTiles');
  let h='';
  for(let i=0;i<8;i++){
    const m=MACROS.find(x=>x.slot===i);
    h+='<button class="tile'+(m?'':' ')+'" data-ms="'+i+'"'+(m?'':' style="opacity:.45"')+'>'+
       ico(m?'term':'plus')+'<span>'+esc(m?m.name:'Slot '+i)+'</span></button>';
  }
  host.innerHTML=h;
  host.querySelectorAll('[data-ms]').forEach(b=>{
    const i=+b.dataset.ms, m=MACROS.find(x=>x.slot===i);
    b.onclick=()=>{if(m){send({cmd:'macro_run',slot:i});buzz(12);toast('Running '+m.name);}else macroEditor(i);};
    let t=0;
    b.addEventListener('pointerdown',()=>{t=setTimeout(()=>macroEditor(i),600);});
    ['pointerup','pointerleave','pointercancel'].forEach(ev=>b.addEventListener(ev,()=>clearTimeout(t)));
  });
}
function macroEditor(slot){
  send({cmd:'macro_get',slot:slot},r=>{
    const name=(r&&r.name)||'', steps=(r&&r.steps)?r.steps.join('\n'):'';
    sheet('Macro slot '+slot,
      '<div class="fld"><label>Name</label><input id="m_n" value="'+esc(name)+'"></div>'+
      '<div class="fld"><label>Steps, one per line</label><textarea id="m_s" spellcheck="false">'+esc(steps)+'</textarea></div>'+
      '<p class="hint">Verbs: <b>key</b> COMBO &middot; <b>text</b> literal &middot; <b>delay</b> ms &middot; <b>click</b> left|right|middle</p>'+
      '<div class="row" style="margin-top:14px">'+
        '<button class="btn sm bad" id="m_del">'+ico('trash','ic-sm')+'Delete</button>'+
        '<button class="btn ok" id="m_ok">'+ico('check','ic-sm')+'Save</button></div>',
      ()=>{
        $('m_ok').onclick=()=>{
          const lines=$('m_s').value.split('\n').map(s=>s.trim()).filter(Boolean);
          if(!lines.length)return toast('No steps','bad');
          send({cmd:'macro_save',slot:slot,name:$('m_n').value||('Slot '+slot),steps:lines},()=>{
            toast('Macro saved','ok');loadMacros();closeSheet();});
        };
        $('m_del').onclick=()=>send({cmd:'macro_delete',slot:slot},()=>{loadMacros();closeSheet();});
      });
  });
}

// =====================================================================
//  password vault
// =====================================================================
// Secrets the board types for you. The board never sends one back except to the
// one request that asks for it and proves the PIN in the same breath, so this
// page holds nothing: every action asks again, including typing. That is on
// purpose - the keystrokes ARE the secret, and the board cannot see which
// window they land in.
let VAULT=[],V_AUTH=false,V_PINSET=false;
const V_KIND={password:'Password',pin:'PIN'};
function loadVault(){
  send({cmd:'vault_list'},r=>{
    VAULT=(r&&r.entries)||[];
    V_AUTH=!!(r&&r.auth_set);
    V_PINSET=!!(r&&r.vault_pin_set);
    renderVault();
  });
}
// The PIN prompt that guards getting a secret OUT - reading one or typing one.
// Four boxes rather than a text field, because it is the same act as unlocking
// the board and should look like it. Nothing is remembered between two of
// these, so there is no window during which the vault is open.
function vaultAsk(title,note,run){
  sheet(title,
    '<p class="hint">'+note+'</p>'+
    '<p class="hint" style="margin-top:10px">'+(V_PINSET?'Vault PIN':'Access PIN')+'</p>'+
    '<div class="pinrow" id="vpinRow">'+
      [0,1,2,3].map(i=>'<input class="pinbox" type="password" inputmode="numeric" pattern="[0-9]*" '+
        'maxlength="1" autocomplete="off" aria-label="PIN digit '+(i+1)+'">').join('')+
    '</div>'+
    '<p class="hint" id="vpinMsg" style="margin-top:10px;color:var(--bad)"></p>',
    ()=>{
      const p=bindPin('vpinRow',v=>{
        run(v,r=>{
          const m=$('vpinMsg');
          if(m)m.textContent=(r&&r.message)||'That PIN was not right.'+
            (r&&r.retry_in?' Try again in '+r.retry_in+'s.':'');
          p.reject();
        });
      });
      p.focus();
    });
}
function renderVault(){
  $('vaultList').innerHTML=VAULT.length
    ? VAULT.map(v=>'<div class="item">'+ico('key')+
        '<div class="tx"><b>'+esc(v.label)+'</b><span>'+(V_KIND[v.type]||'Password')+'</span></div>'+
        '<button class="btn sm ok" data-vtype="'+v.slot+'">Type</button>'+
        '<button class="iconbtn" data-vshow="'+v.slot+'" aria-label="Show">'+ico('eye','ic-sm')+'</button>'+
        '<button class="iconbtn" data-ved="'+v.slot+'" aria-label="Edit">'+ico('edit','ic-sm')+'</button>'+
        '</div>').join('')
    : '<p class="hint">Nothing saved yet.</p>';

  $('vaultList').querySelectorAll('[data-vtype]').forEach(b=>b.onclick=()=>{
    const v=VAULT.find(x=>x.slot===+b.dataset.vtype); if(!v)return;
    const what=v.type==='pin'?'PIN':'password';
    vaultAsk('Type on the computer',
      'This types the '+what+' saved for <b>'+esc(v.label)+'</b> wherever the cursor is on the computer. '+
      'Put the cursor in the right box first \u2014 the board cannot see where it is typing.',
      (pin,fail)=>send({cmd:'vault_type',slot:v.slot,pin:pin},r=>{
        if(r&&r.reply==='vault_typed'){closeSheet();toast('Typed on the computer','ok');buzz(12);}
        else fail(r);
      }));
  });

  $('vaultList').querySelectorAll('[data-vshow]').forEach(b=>b.onclick=()=>{
    const v=VAULT.find(x=>x.slot===+b.dataset.vshow); if(!v)return;
    const what=v.type==='pin'?'PIN':'password';
    vaultAsk('Show this '+what,
      'This shows the '+what+' saved for <b>'+esc(v.label)+'</b> on this screen.',
      (pin,fail)=>send({cmd:'vault_get',slot:v.slot,pin:pin},r=>{
        if(r&&r.reply==='vault_secret')showSecret(r); else fail(r);
      }));
  });

  $('vaultList').querySelectorAll('[data-ved]').forEach(b=>b.onclick=()=>{
    vaultEditor(VAULT.find(x=>x.slot===+b.dataset.ved));
  });

  $('btnVaultPin').textContent=V_PINSET?'Change vault PIN':'Add a vault PIN';
  $('btnVaultWipe').style.display=VAULT.length?'':'none';
  $('vaultHint').innerHTML=V_AUTH
    ? (V_PINSET
        ? 'Reading or typing one asks for the <b>vault PIN</b>, every time. Saving and editing do not \u2014 nothing there gives a stored secret back.'
        : 'Reading or typing one asks for the <b>access PIN</b>, every time. Give the vault a PIN of its own so unlocking the dashboard is not the same as unlocking your passwords.')
    : 'Set an access PIN under Settings before storing anything. Without one, anybody in radio range could have these typed out.';
}
// A reveal is deliberately plain: shown once, in this sheet, and gone when it is
// closed. Nothing is written to storage and nothing is copied for you.
function showSecret(r){
  sheet(esc(r.label),
    '<p class="hint">'+(V_KIND[r.type]||'Password')+', stored on the board.</p>'+
    '<div class="fld" style="margin-top:12px"><label>Secret</label>'+
      '<input id="vshow" readonly value="'+esc(r.secret)+'" '+
      'style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:17px"></div>'+
    '<p class="hint">Close this when you are done. It is not kept anywhere on this page.</p>'+
    '<button class="btn blk" id="vshowClose" style="margin-top:12px">'+ico('check','ic-sm')+'Done</button>',
    ()=>{ $('vshowClose').onclick=closeSheet; });
}
// No argument adds; passing an entry edits it. Neither asks for the vault PIN:
// saving cannot disclose anything, and the stored secret is never sent here to
// be put back in the field - leaving it empty keeps what is on the board.
function vaultEditor(edit){
  if(!V_AUTH)return toast('Set an access PIN first','bad');
  const kind=edit?(edit.type==='pin'?'pin':'password'):'password';
  const opt=(v,t)=>'<button class="btn sm'+(v===kind?' on':'')+'" data-vk="'+v+'">'+t+'</button>';
  sheet(edit?'Edit stored secret':'Store a password',
    '<div class="fld"><label>What it is for</label>'+
      '<input id="v_l" placeholder="Netflix, work email\u2026" autocomplete="off" value="'+esc(edit?edit.label:'')+'"></div>'+
    '<div class="fld"><label>Kind</label><div class="row" id="v_k">'+opt('password','Password')+opt('pin','PIN')+'</div></div>'+
    '<div class="fld"><label id="v_slab">'+(edit?'New password':'Password')+'</label>'+
      '<input type="password" id="v_s" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="'+
      (edit?'Leave empty to keep the stored one':'Any length')+'"></div>'+
    '<p class="hint" id="v_msg" style="color:var(--bad)"></p>'+
    '<p class="hint">Flash on this board is not encrypted, so anyone who physically takes it can read what is stored here. The PIN is what stops someone in radio range using it.</p>'+
    '<button class="btn ok blk" id="v_ok" style="margin-top:12px">'+ico('check','ic-sm')+'Save</button>'+
    (edit?'<button class="btn bad blk" id="v_del" style="margin-top:8px">'+ico('trash','ic-sm')+'Delete this</button>':''),
    ()=>{
      let k=kind;
      const f=$('v_s');
      // A PIN is digits. Say so with the keypad, and enforce it as it is typed
      // rather than letting the board reject it after the fact.
      const paintKind=()=>{
        const pin=k==='pin';
        f.setAttribute('inputmode',pin?'numeric':'text');
        f.placeholder=edit?('Leave empty to keep the stored one'):(pin?'Digits only, any length':'Any length');
        $('v_slab').textContent=(edit?'New ':'')+(pin?'PIN':'Password');
        if(pin)f.value=f.value.replace(/[^0-9]/g,'');
      };
      f.addEventListener('input',()=>{if(k==='pin')f.value=f.value.replace(/[^0-9]/g,'');});
      $('v_k').querySelectorAll('[data-vk]').forEach(b=>b.onclick=()=>{
        k=b.dataset.vk;
        $('v_k').querySelectorAll('[data-vk]').forEach(x=>x.classList.toggle('on',x===b));
        paintKind();
      });
      paintKind();
      $('v_ok').onclick=()=>{
        const l=$('v_l').value.trim(), s=f.value;
        if(!l)return toast('Give it a name','bad');
        if(!s&&!edit)return toast(k==='pin'?'Enter the PIN':'Enter the password','bad');
        if(s&&k==='pin'&&!/^[0-9]+$/.test(s))return toast('A PIN can only contain digits','bad');
        const m={cmd:'vault_save',label:l,type:k};
        if(s)m.secret=s;
        if(edit)m.slot=edit.slot;
        send(m,r=>{
          if(r&&r.reply==='vault_saved'){toast(edit?'Updated':'Stored on the board','ok');closeSheet();syncAll();}
          else{const e=$('v_msg');if(e)e.textContent=(r&&r.message)||'Save failed';}
        });
      };
      // Deleting cannot be undone and the board cannot show you what you are
      // about to lose, so it asks before it happens.
      if(edit)$('v_del').onclick=()=>{
        if(!confirm('Delete "'+edit.label+'"?\n\nThe stored '+(edit.type==='pin'?'PIN':'password')+
                    ' will be erased from the board. This cannot be undone.'))return;
        send({cmd:'vault_delete',slot:edit.slot},r=>{
          if(r&&r.reply==='vault_deleted'){toast('Deleted');closeSheet();syncAll();}
          else{const e=$('v_msg');if(e)e.textContent=(r&&r.message)||'Could not delete it';}
        });
      };
    });
}
// Giving the vault its own PIN is the browser behaviour: the thing that unlocks
// the dashboard stops being the thing that unlocks the secrets.
//
// Changing it needs the current one. Resetting a forgotten one needs the access
// PIN - and erases the vault, because otherwise the access PIN would quietly be
// a way to read everything and the second PIN would be decorative.
function vaultPinEditor(){
  if(!V_AUTH)return toast('Set an access PIN first','bad');
  const rows=(id,label)=>
    '<p class="hint" style="margin-top:12px">'+label+'</p>'+
    '<div class="pinrow" id="'+id+'">'+
      [0,1,2,3].map(i=>'<input class="pinbox" type="password" inputmode="numeric" pattern="[0-9]*" '+
        'maxlength="1" autocomplete="off" aria-label="'+label+' digit '+(i+1)+'">').join('')+
    '</div>';
  const mode=V_PINSET?'change':'add';
  sheet(V_PINSET?'Change vault PIN':'Add a vault PIN',
    '<p class="hint">'+(V_PINSET
      ? 'The vault has its own four digit PIN. Enter it to change it, or leave the new one empty to go back to using the access PIN.'
      : 'Right now the access PIN opens your stored passwords. Give the vault a PIN of its own and unlocking the dashboard stops being the same thing.')+'</p>'+
    rows('vpCur',V_PINSET?'Current vault PIN':'Access PIN')+
    rows('vpNew',V_PINSET?'New vault PIN (or leave empty to remove)':'New vault PIN')+
    '<p class="hint" id="vp_msg" style="margin-top:10px;color:var(--bad)"></p>'+
    '<button class="btn ok blk" id="vp_ok" style="margin-top:12px">'+ico('shield','ic-sm')+'Save</button>'+
    (V_PINSET?'<button class="btn bad blk" id="vp_forgot" style="margin-top:8px">'+ico('trash','ic-sm')+'Forgotten it?</button>':''),
    ()=>{
      const cur=bindPin('vpCur'), nw=bindPin('vpNew');
      const fail=r=>{const e=$('vp_msg');if(e)e.textContent=(r&&r.message)||'Could not change it';cur.reject();};
      $('vp_ok').onclick=()=>{
        const c=cur.value(), n=nw.value();
        if(c.length!==4)return toast('Enter all four digits','bad');
        if(n.length&&n.length!==4)return toast('A vault PIN is four digits','bad');
        const m={cmd:'vault_pin',new_pin:n};
        mode==='change'?m.pin=c:m.access=c;
        send(m,r=>{
          if(r&&r.reply==='vault_pin_set'){
            toast(n?'Vault PIN saved':'Vault PIN removed','ok');closeSheet();syncAll();
          }else fail(r);
        });
      };
      // The only way past a forgotten vault PIN, and it costs what it guarded.
      if(V_PINSET)$('vp_forgot').onclick=()=>{
        if(!confirm('Reset the vault PIN with your access PIN?\n\n'+
                    'Every stored password will be erased. That is the only way to '+
                    'reset a PIN you cannot produce \u2014 otherwise the access PIN would be '+
                    'a way to read them.'))return;
        vaultAskAccess();
      };
    });
}
// Reset path: proves the access PIN, sets a new vault PIN, and takes the vault
// with it. Split out so the confirmation above is read before the PIN is asked.
function vaultAskAccess(){
  sheet('Reset vault PIN',
    '<p class="hint">Enter the <b>access PIN</b> to set a new vault PIN. Everything stored in the vault will be erased.</p>'+
    '<p class="hint" style="margin-top:12px">Access PIN</p>'+
    '<div class="pinrow" id="vrAcc">'+
      [0,1,2,3].map(i=>'<input class="pinbox" type="password" inputmode="numeric" pattern="[0-9]*" '+
        'maxlength="1" autocomplete="off" aria-label="Access PIN digit '+(i+1)+'">').join('')+
    '</div>'+
    '<p class="hint" style="margin-top:12px">New vault PIN (or leave empty to remove it)</p>'+
    '<div class="pinrow" id="vrNew">'+
      [0,1,2,3].map(i=>'<input class="pinbox" type="password" inputmode="numeric" pattern="[0-9]*" '+
        'maxlength="1" autocomplete="off" aria-label="New vault PIN digit '+(i+1)+'">').join('')+
    '</div>'+
    '<p class="hint" id="vr_msg" style="margin-top:10px;color:var(--bad)"></p>'+
    '<button class="btn bad blk" id="vr_ok" style="margin-top:12px">'+ico('trash','ic-sm')+'Reset and erase</button>',
    ()=>{
      const acc=bindPin('vrAcc'), nw=bindPin('vrNew');
      acc.focus();
      $('vr_ok').onclick=()=>{
        const a=acc.value(), n=nw.value();
        if(a.length!==4)return toast('Enter all four digits','bad');
        if(n.length&&n.length!==4)return toast('A vault PIN is four digits','bad');
        send({cmd:'vault_pin',access:a,new_pin:n},r=>{
          if(r&&r.reply==='vault_pin_set'){
            toast('Vault reset','ok');closeSheet();syncAll();
          }else{
            const e=$('vr_msg');if(e)e.textContent=(r&&r.message)||'Wrong access PIN';acc.reject();
          }
        });
      };
    });
}
// Ungated on the board, because destroying secrets reveals none of them and it
// is the only way back from a forgotten vault PIN. Guarded here by two prompts.
function vaultWipe(){
  if(!VAULT.length)return toast('Nothing stored');
  if(!confirm('Erase the whole vault?\n\nAll '+VAULT.length+' stored '+
              (VAULT.length===1?'secret':'secrets')+' will be destroyed. This cannot be undone.'))return;
  if(!confirm('Really erase everything?\n\nThis is the last warning.'))return;
  send({cmd:'vault_wipe'},()=>{toast('Vault erased');syncAll();});
}

// =====================================================================
//  saved PCs
// =====================================================================
// Names come back from the board, passwords never do. Unlocking sends the slot
// number and the board looks the password up itself, so the secret only ever
// travels once - when you save it. Each slot also carries the OS of that
// computer, because the board may be plugged into a different one by the time
// the slot is used and the two login windows want different keystrokes.
let PCS=[],PC_AUTH=false;
const PC_OSN={mac:'macOS',windows:'Windows',linux:'Linux'};
function loadPcs(){
  send({cmd:'pc_list'},r=>{
    PCS=(r&&r.profiles)||[];PC_AUTH=!!(r&&r.auth_set);
    renderPcs();
  });
}
function renderPcs(){
  $('pcList').innerHTML=PCS.length
    ? PCS.map(p=>'<div class="item">'+ico('cpu')+
        '<div class="tx"><b>'+esc(p.name)+'</b><span>'+
          (PC_OSN[p.os]||'Follows this computer')+'</span></div>'+
        '<button class="btn sm ok" data-pcgo="'+p.slot+'">Unlock</button>'+
        '<button class="iconbtn" data-pced="'+p.slot+'" aria-label="Edit">'+ico('edit','ic-sm')+'</button>'+
        '<button class="iconbtn" data-pcdel="'+p.slot+'" aria-label="Forget">'+ico('trash','ic-sm')+'</button>'+
        '</div>').join('')
    : '<p class="hint">Nothing saved yet.</p>';
  $('pcList').querySelectorAll('[data-pcgo]').forEach(b=>b.onclick=()=>{
    send({cmd:'unlock',profile:+b.dataset.pcgo,ctrl_alt_del:$('tCad').classList.contains('on')},r=>{
      toast(r&&r.reply==='unlock_done'?'Unlock sent':'Already unlocked','ok');refresh();});
    buzz(12);
  });
  $('pcList').querySelectorAll('[data-pced]').forEach(b=>b.onclick=()=>{
    pcEditor(PCS.find(p=>p.slot===+b.dataset.pced));
  });
  $('pcList').querySelectorAll('[data-pcdel]').forEach(b=>b.onclick=()=>{
    const p=PCS.find(q=>q.slot===+b.dataset.pcdel);
    if(!confirm('Forget "'+((p&&p.name)||'this PC')+'"?\n\nThe password stored on the board will be deleted. This cannot be undone.'))return;
    send({cmd:'pc_delete',slot:+b.dataset.pcdel},()=>{toast('Forgotten');syncAll();});
  });
  $('pcHint').textContent=PC_AUTH
    ? 'Stored on the board, so every phone sees them. A saved password is never sent back to this page - unlocking just names the slot.'
    : 'Set an access PIN under Settings before saving a password. Without one, anybody in radio range could unlock this PC with a single request.';
}
// No argument saves a new PC; passing a profile edits that slot. Editing never
// asks for the password again - the board keeps the stored one when the field
// is left empty, because this page has no way to show it back.
function pcEditor(edit){
  if(!PC_AUTH)return toast('Set an access PIN first','bad');
  const osNow=(OS==='mac'?'mac':OS==='linux'?'linux':'windows');
  const cur=edit?(edit.os&&PC_OSN[edit.os]?edit.os:'auto'):osNow;
  const opt=(v,t)=>'<button class="btn sm'+(v===cur?' on':'')+'" data-pcos="'+v+'">'+t+'</button>';
  sheet(edit?'Edit saved PC':'Save this PC',
    '<div class="fld"><label>Name</label><input id="pc_n" placeholder="Work laptop" autocomplete="off" value="'+esc(edit?edit.name:'')+'"></div>'+
    '<div class="fld"><label>This computer is</label><div class="row" id="pc_os">'+
      opt('windows','Windows')+opt('mac','macOS')+opt('linux','Linux')+opt('auto','Auto')+
    '</div><p class="hint">Its login screen is woken differently on each, and the board may be plugged into another computer by the time you use this. Auto follows whatever is attached.</p></div>'+
    '<div class="fld"><label>PC password</label><input type="password" id="pc_p" autocomplete="off" placeholder="'+(edit?'Leave empty to keep the saved one':'')+'" value="'+esc(edit?'':$('unlockPw').value)+'"></div>'+
    '<p class="hint">The board stores this in flash, which is not encrypted. Anyone who takes the board can read it, so treat it like a written-down password. The access PIN is what stops someone in radio range using it.</p>'+
    '<button class="btn ok blk" id="pc_ok" style="margin-top:14px">'+ico('check','ic-sm')+'Save</button>',
    ()=>{
      let os=cur;
      $('pc_os').querySelectorAll('[data-pcos]').forEach(b=>b.onclick=()=>{
        os=b.dataset.pcos;
        $('pc_os').querySelectorAll('[data-pcos]').forEach(x=>x.classList.toggle('on',x===b));
      });
      $('pc_ok').onclick=()=>{
        const n=$('pc_n').value.trim(), p=$('pc_p').value;
        if(!n)return toast('A name is needed','bad');
        if(!p&&!edit)return toast('Name and password are both needed','bad');
        const m={cmd:'pc_save',name:n,os:os};
        if(p)m.password=p;
        if(edit)m.slot=edit.slot;
        send(m,r=>{
          if(r&&r.status==='ok'){toast(edit?'Updated':'Saved on the board','ok');$('unlockPw').value='';closeSheet();syncAll();}
          else toast((r&&r.message)||'Save failed','bad');
        });
      };
    });
}

// =====================================================================
//  status
// =====================================================================
// Everything the board stores is shown in more than one place, so nothing may
// depend on the user pressing Refresh. Anything that changes state on the board
// calls syncAll(); the status poll below catches changes made from another
// phone, and applyStatus() rebuilds whatever a change to the PIN affects.
let AUTH_SET=null;
function syncAll(){refresh();loadMacros();loadPcs();loadVault();}
function refresh(){
  if(wsUp)send({cmd:'status'});
  else fetch('/api/status').then(r=>r.json()).then(applyStatus).catch(()=>{});
}
function applyStatus(d){
  if(!d)return;
  if(d.host_os)applyHostOs(d.host_os,d.host_os_source,d.host_os_detected);  // The evidence behind the verdict, in the UI rather than only on the serial
  // port: a detection argument is settled by reading these, not by guessing at
  // what a given host does during enumeration.
  const od=$('osDiag');
  if(od&&d.detect_phase!==undefined){
    od.textContent='Signals seen: string reads \u00d7'+(d.usb_str_reqs|0)+
      ' (re-reads \u00d7'+(d.usb_str_rereads|0)+')'+
      ' \u00b7 SET_IDLE \u00d7'+(d.usb_set_idle|0)+
      ' \u00b7 lock-key reports \u00d7'+(d.usb_led_reports|0)+
      ' \u00b7 control requests \u00d7'+(d.usb_ctrl_reqs|0)+
      ' \u00b7 '+d.detect_phase+' \u2192 '+(d.host_os_detected||'?')+
      (d.usb_str_seq?' \u00b7 indices '+d.usb_str_seq:'');
  }
  if(d.pointer_mode)$('ptrMode').textContent=d.pointer_mode;
  // How many dashboards the board is serving. Polled, so a second phone opening
  // this page shows up here on its own - and so does a tab that was left open
  // somewhere and is still holding a socket.
  if(d.ws_clients!==undefined){
    const n=d.ws_clients|0, ap=d.ap_clients|0;
    $('connN').textContent=n;
    const pill=$('connPill');
    pill.classList.toggle('multi',n>1);
    pill.title=(n===1?'1 dashboard':n+' dashboards')+' connected to this board'+
      (ap?' \u00b7 '+ap+' device'+(ap===1?'':'s')+' on its access point':'')+
      (n>1?' \u2014 they share the link, so the pointer can feel slower':'');
  }
  // The access PIN decides what half the dashboard is allowed to offer, and it
  // can be turned on from Settings or from another phone entirely. Notice it
  // changing and rebuild whatever depended on it, rather than leaving the other
  // tabs showing a stale "set a PIN first".
  if(d.auth_set!==undefined){
    $('authState').textContent=d.auth_set?'On':'Off';
    if(AUTH_SET!==null&&AUTH_SET!==!!d.auth_set){loadPcs();loadVault();}
    AUTH_SET=!!d.auth_set;
  }
  // Being reachable from a whole network instead of only from radio range is a
  // real change in exposure, and it is invisible unless we say so.
  if(d.lan_exposed!==undefined)$('lanWarn').style.display=d.lan_exposed?'':'none';
  if(d.power_mode)paintPower(d);
  if(d.gamepad!==undefined&&d.gamepad!==GP.enabled){GP.enabled=d.gamepad;gpSync();}
  if(d.wiggle_mode!==undefined)$('wigState').textContent=d.wiggle_active?('Active - '+d.wiggle_mode):'Idle';
  if(d.firmware){
    $('brandsub').textContent=d.firmware.replace('hid_fi_','');
    $('colVer').textContent=d.firmware.replace('hid_fi_','')+(d.chip?' \u00b7 '+d.chip:'');
  }
  if(d.ap_ssid!==undefined){
    if(!$('apSsid').value)$('apSsid').value=d.ap_ssid;
    // RSSI only means something to people who already know what it means, so it
    // is shown as words with the number kept for anyone debugging.
    const sig=r=>r>=-55?'strong':r>=-67?'good':r>=-75?'weak':'very weak';
    // The board has one radio, so the access point and the home network are always
    // on the same channel. Saying which one decided it explains why the access
    // point moves when you join a network.
    const chan=d.channel
      ? (d.channel+' \u00b7 2.4 GHz'+(d.wifi_connected?', set by '+d.wifi_ssid:''))
      : '-';
    $('netInfo').innerHTML=
      row('Access point',d.ap_running===false?'off to save power':d.ap_ssid)+row('AP address',d.ap_ip)+
      row('mDNS',d.mdns?d.mdns+'.local':'-')+
      row('Home network',d.wifi_connected
        ? (d.wifi_ssid+'  '+sig(d.wifi_rssi)+' ('+d.wifi_rssi+' dBm)')
        : (d.sta_phase==='failed'?'join failed':'not connected'))+
      (d.wifi_connected?row('Address on it',d.wifi_ip+(d.sta_ip_mode==='static'?'  fixed':d.sta_ip_mode==='dhcp_fallback'?'  from router':'')):'')+
      row('Channel',chan)+
      (d.mac?row('MAC (home network)',d.mac):'')+
      (d.ap_mac?row('MAC (access point)',d.ap_mac):'')+
      row('AP clients',d.ap_clients)+row('Socket clients',d.ws_clients);
    $('devInfo').innerHTML=
      row('Firmware',d.firmware)+row('Uptime',upt(d.uptime_sec))+
      row('Free heap',bytes(d.free_heap)+(d.heap_floor?'  (lowest '+bytes(d.heap_floor)+')':''))+
      row('PSRAM',bytes(d.free_psram)+' / '+bytes(d.total_psram))+
      row('HID',d.hid_ready?'ready':'not ready')+
      row('USB',d.usb_mounted===false?'not connected':(d.usb_suspended?'host asleep':'awake'))+
      row('Pointer',d.pointer_mode)+
      row('PC state',d.pc_state)+row('Commands',d.cmd_count+' total, '+d.wifi_cmd_count+' wireless');
  }
}
// The mode is what you asked for; radio_awake is what is actually happening,
// which is the honest answer to "why does it feel slow right now".
function paintPower(d){
  if(d.power_mode||d.mode)$('pwrState').textContent=({performance:'Performance',balanced:'Balanced',saver:'Saver'})[d.power_mode||d.mode]||'Balanced';
  const bits=[d.radio_awake===false?'Radio sleeping':'Radio awake'];
  if(d.ap_running===false)bits.push('access point off, hold BOOT to restore');
  $('pwrSub').textContent=bits.join(' \u00b7 ');
  document.querySelectorAll('[data-pwr]').forEach(b=>b.classList.toggle('on',b.dataset.pwr===(d.power_mode||d.mode)));
  if(d.ap_auto_off!==undefined)$('tApOff').classList.toggle('on',!!d.ap_auto_off);
}
const row=(k,v)=>'<dt>'+esc(k)+'</dt><dd>'+esc(v)+'</dd>';
const upt=s=>s<60?s+'s':s<3600?Math.floor(s/60)+'m '+(s%60)+'s':Math.floor(s/3600)+'h '+Math.floor((s%3600)/60)+'m';
const bytes=b=>b>1048576?(b/1048576).toFixed(1)+' MB':b>1024?(b/1024).toFixed(0)+' KB':b+' B';

// =====================================================================
//  help - one sheet per view, so the info button is never generic
// =====================================================================
const HELP={
 pad:['Trackpad gestures',[
  ['cursor','One finger','Move the pointer.'],
  ['click','Tap','Left click.'],
  ['hand','Press and hold, then move','The reliable grab. A ring fills, the pad turns green, and the button is held down - use this for a window title bar or the hand tool in an image viewer.'],
  ['drag','Tap, then press again','Lift straight away and it is a double click. Move or keep holding and it becomes a drag instead.'],
  ['scroll','Two fingers','Scroll vertically and horizontally. Pinch to zoom.'],
  ['click','Two finger tap','Right click.'],
  ['layers','Three fingers','Slide sideways and the app switcher opens while your fingers are still down, stepping window by window - lift when the one you want is highlighted, exactly like a Windows touchpad. Up is task view, down shows the desktop. Tap for middle click.'],
  ['appswitch','Hold Switch app','Holds Alt down so the host keeps its switcher open. Slide to pick a window, let go to raise it.'],
  ['keyboard','Keyboard button','Flips this card between the trackpad and a keyboard, so you can click and then type without leaving the page. Grab is released for you on the way in, and any held key on the way out.'],
  ['layers','Swipe sideways','A quick flick across empty space moves to the next or previous tab. It is ignored on the pad, on a key, during a hold, and on anything slower than a flick.']
 ],'For long drags use Grab in the pad toolbar - it holds the button down until you tap it again. If you drag windows a lot, turn on Three finger drag under Feel: it never sends a click first, so nothing can be misread as a double click.'],

 keys:['Keyboard',[
  ['keyboard','Type field','Sends the text as keystrokes. Enter sends it and presses Return at the end.'],
  ['hand','Hold a modifier','Hold Ctrl, Alt, Shift or Win with one finger and strike another key with a second finger, exactly like a physical keyboard.'],
  ['click','Tap a modifier','A quick tap latches it for the next key only, for one-handed use. Tap it again to drop it.'],
  ['layers','123 and ABC','The key on the left of the bottom row swaps letters for symbols. Every printable ASCII character is on one of the two layers, and digits are on both.'],
  ['cursor','Also on the trackpad','The same keyboard sits on the Trackpad tab behind the keyboard button, for when you need a few keystrokes without leaving the pointer. Both share one state, so a modifier latched on either applies to both.'],
  ['term','Custom combo','Type something like CTRL+SHIFT+ESC and press it.'],
  ['save','Macros','Eight slots stored on the board, so every phone sees the same ones. Long press a slot to edit. Verbs are key, text, delay and click.']
 ],'Anything held is released if this page closes or the board loses the socket, so a modifier cannot get stuck down on the PC.'],

 short:['Shortcuts',[
  ['command','Pick your OS','macOS, Windows and Linux use different keys. The choice is remembered on this phone.'],
  ['search','Search','Filters on the name or on the keys themselves. Matching groups open automatically.'],
  ['layers','Groups','Tap a heading to open or close it. Whatever you leave open is remembered.'],
  ['bolt','Tap to send','Tapping a shortcut presses it on the PC straight away.']
 ],'Only combos this board can actually send are listed. Where an operating system has no equivalent, the shortcut is hidden for it rather than shown as a button that would do nothing.'],

 media:['Media and knobs',[
  ['knob','Turn a knob','Drag around the dial. Each detent is one step, with a haptic tick.'],
  ['volx','Press and hold a knob','Holding without turning mutes, the way a real volume knob clicks in.'],
  ['edit','Edit anything','The pencil on a knob, or a long press on a quick action, opens its editor. Changes are saved on the board.'],
  ['play','Playback','Standard USB media keys, understood by Windows, macOS and Linux with no driver.'],
  ['bolt','Quick actions','Mic mute and call keys differ per laptop and per app, so every button here is editable.']
 ],'The board cannot read the PC back, so a knob shows the turn you just made, not the level the machine is actually at. Mute is tracked as intent - tap it again to undo.'],

 game:['Controller',[
  ['gamepad','Enable it first','The controller is a second USB device, off by default so it does not clutter the host. Turning it on reboots the board.'],
  ['cursor','Sticks','Drag inside a stick. It springs back to centre when you let go.'],
  ['layers','Buttons and D-pad','Press and hold works, so you can hold a trigger and move a stick at the same time.']
 ],'It reports as a standard gamepad, so anything that reads XInput or DirectInput will see it.'],

 power:['Session',[
  ['unlock','Unlock','Types your PC password as keystrokes. Nothing is typed until you ask for it.'],
  ['shield','Saved PCs','A password saved here lives on the board, so any phone can unlock that machine without retyping it. It is never sent back to this page.'],
  ['lock','Lock','Sends Win+L on a PC, Ctrl+Cmd+Q on a Mac. Lock then unlock does both, three seconds apart.'],
  ['present','Presenter','Arrow keys and F5, plus B and W for a black or white slide in PowerPoint.'],
  ['clock','Talk timer','Runs in this browser only. It never touches the PC.'],
  ['bolt','Stay awake','Nudges the cursor and puts it straight back, so the net movement is zero and nothing drifts across the screen.']
 ],'A sleeping PC has the USB bus suspended and reads nothing the board sends, so Wake asks the bus to resume first. That only works if the PC is set to allow this device to wake it. Saving a PC password needs an access PIN, because flash is not encrypted and anyone in radio range could otherwise use it.'],

 setup:['Settings',[
  ['wifi','Network','The board always runs its own access point and can join your home network at the same time. It is never one or the other, so a failed join can never lock you out.'],
  ['cpu','Fixed IP','Optional. Pick an address outside the router DHCP range. If it cannot be used the board falls back to DHCP by itself and says so.'],
  ['shield','Access point','Change the default password. Every board ships with the same one, so it protects nothing until you do. Removing the password is a separate toggle, never just an empty field.'],
  ['wifi','Two MAC addresses','One for the home network, which is what a router DHCP list or MAC filter shows, and one for the access point, which is what a phone sees. Same radio, different addresses.'],
  ['layers','Connected clients','Everything joined to the access point, with signal and address. Names are not shown because a WiFi client never has to give one, and phones randomise their address as well.'],
  ['save','Stored on the board','What is in permanent memory and how full it is. Passwords and PINs only ever report as set or not set.'],
  ['shield','Access PIN','Four digits, gating wireless clients only. USB serial is never gated, so you can always recover over the cable.'],
  ['lock','On a home network','Radio range is a limit. A network is not. Once the board joins one, every device on that network can reach it, so wireless control is refused until a PIN is set.'],
  ['lock','Wrong PINs','The board locks out for longer after each wrong guess, up to five minutes a try, so 10,000 combinations cannot be walked through.'],
  ['shield','Where your WiFi password goes','Type it while you are on the board\'s own access point and WPA2 covers it the whole way - nothing else is in between. There is no HTTPS here, because a board this size cannot hold a certificate anyone could trust, so over your home network the same form travels in the clear and anyone able to watch that network could read it. Join from the access point.'],
  ['bolt','Power','The radio is where the power goes, and keeping it awake is what makes the pointer feel instant. Balanced keeps it awake while you are connected and lets it sleep when you are not.'],
  ['cursor','Pointer mode','Relative moves by deltas like a mouse. Absolute jumps to a coordinate. Switching re-enumerates USB, so the board reboots.'],
  ['cpu','Test HID','Types HID_OK on the PC, which proves the USB keyboard is alive.']
 ],'A join can only fail for a handful of reasons: the board is 2.4 GHz only and cannot see a 5 GHz network at all, and anything else is almost always the password. Reset WiFi clears only the saved home network - the access point name and password stay exactly as they are. If power saving has switched the access point off, holding BOOT for two seconds always brings it back.']
};
function showHelp(){
  const h=HELP[curView];
  if(!h)return;
  sheet(h[0],'<div class="list">'+h[1].map(r=>item(r[0],r[1],r[2])).join('')+'</div>'+
    (h[2]?'<p class="hint" style="margin-top:12px">'+esc(h[2])+'</p>':''));
}

// =====================================================================
//  wiring
// =====================================================================
function bindAll(){
  document.querySelectorAll('[data-mb]').forEach(b=>b.onclick=()=>txBtn(+b.dataset.mb,2,1));
  document.querySelectorAll('[data-mb2]').forEach(b=>b.onclick=()=>txBtn(+b.dataset.mb2,2,2));
  document.querySelectorAll('[data-gs]').forEach(b=>{
    // Switch app is the one gesture with a held state, so it gets its own binding
    // and keeps the plain tap as a straight there-and-back Alt+Tab.
    if(b.dataset.gs==='switch_app')return bindAppSwitch(b);
    b.onclick=()=>{send({cmd:'gesture',name:b.dataset.gs});buzz(9);};
  });
  $('aswPrev').onclick=()=>aswStep(-1);
  $('aswNext').onclick=()=>aswStep(1);
  $('aswUp').onclick=()=>aswArrow('UP');
  $('aswDown').onclick=()=>aswArrow('DOWN');
  $('aswGo').onclick=()=>aswClose(true);
  $('aswCancel').onclick=()=>aswClose(false);

  document.querySelectorAll('[data-os]').forEach(b=>b.onclick=()=>{
    SC_OS=b.dataset.os;
    scOsManual=true;                 // browsing another OS, so stop following the host
    try{localStorage.setItem('scos',SC_OS);}catch(e){}
    renderSC();buzz(8);toast(SC_NAME[SC_OS]+' shortcuts');
  });
  // The computer's OS lives on the board, so this drives set_host_os and the
  // reply (or the pushed host_os event) reskins everything through applyHostOs.
  document.querySelectorAll('#osSel [data-os-set]').forEach(b=>b.onclick=()=>{
    send({cmd:'set_host_os',os:b.dataset.osSet},r=>{if(r&&r.host_os)applyHostOs(r.host_os,r.host_os_source,r.host_os_detected);});
    buzz(8);
    toast(b.dataset.osSet==='auto'?'Detecting the computer':SC_NAME[b.dataset.osSet]||'Set');
  });
  // The Keyboard Setup Assistant asks for the key beside each Shift; the board is
  // a US keyboard so those are always Z and /. These answer it with one tap.
  if($('btnIdZ'))$('btnIdZ').onclick=()=>{send({cmd:'press',keys:'z'});buzz(10);toast('Sent Z');};
  if($('btnIdSlash'))$('btnIdSlash').onclick=()=>{send({cmd:'press',keys:'/'});buzz(10);toast('Sent /');};
  $('scq').oninput=renderSC;
  document.querySelectorAll('[data-md]').forEach(b=>b.onclick=()=>{
    if(b.dataset.md==='mute'){toggleMute();buzz(10);return;}
    send({cmd:'media',key:b.dataset.md});buzz(9);
    b.classList.remove('flash');void b.offsetWidth;b.classList.add('flash');
  });
  document.querySelectorAll('[data-sys]').forEach(b=>b.onclick=()=>{
    if(!confirm('Send '+b.dataset.sys+' to the PC?'))return;
    send({cmd:'system',action:b.dataset.sys},r=>{
      if(r&&r.action==='wake'&&r.was_suspended&&!r.remote_wakeup){
        toast('The PC is not set to allow USB wake','bad');
        sheet('Wake did not work',
          '<p class="hint">The host had the USB bus suspended and refused the resume, which means this device is not armed as a wake source. Nothing the board sends can wake it until that is changed.</p>'+
          '<div class="list" style="margin-top:12px">'+
          item('cpu','On the PC','Device Manager, find the HID keyboard under Human Interface Devices, Power Management tab, tick Allow this device to wake the computer.')+
          item('bolt','In the BIOS','Enable wake on USB. It is often called USB Wake Support or Wake on USB.')+
          item('power','Modern Standby','Some laptops only wake from the power button by design, whatever the USB settings say.')+
          '</div>');
      }else if(r&&r.action==='wake'){toast('Wake sent','ok');}
      refresh();
    });
  });
  document.querySelectorAll('[data-pr]').forEach(b=>b.onclick=()=>{send({cmd:'presenter',action:b.dataset.pr});buzz(8);});
  document.querySelectorAll('[data-ptr]').forEach(b=>b.onclick=()=>{
    if(!confirm('Switch pointer to '+b.dataset.ptr+'? The board reboots.'))return;
    send({cmd:'pointer_mode',mode:b.dataset.ptr,reboot:true});
    bootStart('Switching the pointer to '+b.dataset.ptr+'. Windows will install the new device.');});

  // macOS = content follows your fingers, Windows = classic wheel direction.
  // It flips both axes, not just vertical.
  document.querySelectorAll('[data-scroll]').forEach(b=>b.onclick=()=>{
    S.nat=(b.dataset.scroll==='mac');saveFeel();syncScroll();buzz(8);
    toast(S.nat?'macOS scrolling':'Windows scrolling');
  });

  ['btnGrab','btnGrab2','btnGrab3','btnGrab4'].forEach(id=>{const b=$(id);if(b)b.onclick=()=>setGrab(!grabLock);});
  bindWheel($('mWheel'));

  // Two ways to show the same three buttons: a plain row, or the shape of a mouse.
  const setBtnView=m=>{
    $('btnsRow').style.display=m?'none':'';
    $('btnsMouse').style.display=m?'':'none';
    $('btnBtnView').classList.toggle('on',m);
    try{localStorage.setItem('btnview',m?'mouse':'row');}catch(e){}
  };
  let btnMouse=false;
  try{btnMouse=localStorage.getItem('btnview')==='mouse';}catch(e){}
  setBtnView(btnMouse);
  $('btnBtnView').onclick=()=>{btnMouse=!btnMouse;setBtnView(btnMouse);buzz(8);};
  $('btnRefresh').onclick=()=>{syncAll();toast('Refreshed');};
  $('btnInfo').onclick=showHelp;

  $('btnType').onclick=()=>doType(false);
  $('btnTypeEnter').onclick=()=>doType(true);
  // The phone's own Go key should do the obvious thing rather than nothing.
  $('typeText').addEventListener('keydown',e=>{
    if(e.key==='Enter'){e.preventDefault();doType(true);}
  });
  $('customKeys').addEventListener('keydown',e=>{
    if(e.key==='Enter'){e.preventDefault();$('btnCombo').click();}
  });
  $('btnCombo').onclick=()=>{const v=$('customKeys').value;if(v)send({cmd:'press',keys:v});};
  $('btnMacroNew').onclick=()=>{
    for(let i=0;i<8;i++)if(!MACROS.find(m=>m.slot===i))return macroEditor(i);
    toast('All 8 slots used','bad');
  };
  $('btnKnobNew').onclick=()=>knobEditor(-1);
  $('btnQuickNew').onclick=()=>quickEditor(-1);
  $('btnPcNew').onclick=pcEditor;
  $('btnVaultNew').onclick=()=>vaultEditor();
  $('btnVaultPin').onclick=vaultPinEditor;
  $('btnVaultWipe').onclick=vaultWipe;

  $('btnUnlock').onclick=()=>{
    const pw=$('unlockPw').value;if(!pw)return toast('Enter the password','bad');
    // The board cannot see the real lock state, so if it wrongly believes the PC
    // is already unlocked it would refuse. A tap here is a deliberate ask, so on
    // that reply we offer to type anyway rather than dead-end.
    const go=force=>send({cmd:'unlock',password:pw,ctrl_alt_del:$('tCad').classList.contains('on'),force:!!force},r=>{
      if(r&&r.reply==='already_unlocked'){
        if(confirm('The board thinks this computer is already unlocked. Type the password anyway?'))go(true);
        return;
      }
      toast(r&&r.reply==='unlock_done'?'Unlock sent':'Done','ok');refresh();
    });
    go(false);
  };
  $('btnLock').onclick=()=>send({cmd:'lock'},()=>{toast('Locked','ok');refresh();});
  $('btnLockUnlock').onclick=()=>{
    const pw=$('unlockPw').value;if(!pw)return toast('Enter the password','bad');
    send({cmd:'lock',force:true});toast('Locking, unlocking in 3s');
    setTimeout(()=>send({cmd:'unlock',password:pw,ctrl_alt_del:$('tCad').classList.contains('on'),force:true},
      ()=>{toast('Done','ok');refresh();}),3000);
  };
  $('tCad').onclick=()=>$('tCad').classList.toggle('on');

  $('btnWigStart').onclick=()=>send({cmd:'wiggle',mode:'interval',amplitude:5,
    interval_ms:(+$('wigInt').value||10)*1000,force:true},()=>{toast('Wiggle on','ok');refresh();});
  $('btnWigStop').onclick=()=>send({cmd:'wiggle_stop'},()=>{toast('Wiggle off');refresh();});
  $('wigInt').oninput=()=>$('wigIntV').textContent=$('wigInt').value+'s';
  $('wigInt').value=10;$('wigIntV').textContent='10s';

  $('btnScan').onclick=scanWifi;
  document.querySelectorAll('[data-pwr]').forEach(b=>b.onclick=()=>{
    if(b.dataset.pwr==='saver'&&!confirm('Saver keeps the radio asleep, so the pointer will stutter. Use it anyway?'))return;
    send({cmd:'power_mode',mode:b.dataset.pwr},r=>{if(r&&r.status==='ok'){paintPower(r);toast('Power: '+r.mode,'ok');}});
    buzz(8);
  });
  $('tApOff').onclick=()=>{
    const on=!$('tApOff').classList.contains('on');
    if(on&&!confirm('Switch the access point off once the board is on your home network and nobody has used it for ten minutes?\n\nHold BOOT for two seconds to bring it back.'))return;
    send({cmd:'power_mode',ap_auto_off:on},r=>{if(r&&r.status==='ok')paintPower(r);});
  };
  $('tStatic').onclick=()=>{
    const on=!$('tStatic').classList.contains('on');
    $('tStatic').classList.toggle('on',on);
    $('staticFields').style.display=on?'':'none';
  };
  $('btnAp').onclick=()=>{
    const s=$('apSsid').value.trim(), p=$('apPass').value;
    const open=$('tApOpen').classList.contains('on');
    if(!s)return toast('A network name is required','bad');
    // 802.11 limits the SSID to 32 BYTES. Emoji are four bytes each, so the
    // character count on screen is not the number that matters.
    const bytes=new TextEncoder().encode(s).length;
    if(bytes>32)return toast('Name is '+bytes+' bytes, the limit is 32','bad');
    if(/[\x00-\x1f\x7f]/.test(s))return toast('That name contains characters a network cannot carry','bad');
    if(!open&&p.length&&p.length<8)return toast('Password needs 8 characters or more','bad');
    if(new TextEncoder().encode(p).length>63)return toast('Password can be at most 63 bytes','bad');
    if(open&&!confirm('Remove the password from "'+s+'"?\n\nAnyone nearby will be able to join and type on this PC.'))return;
    if(!confirm('Change the access point to "'+s+'"? This phone will drop off the network straight away.'))return;
    send({cmd:'ap_configure',ssid:s,password:open?'':p,open:open},r=>{
      if(r&&r.status==='error')toast(r.message||'Rejected','bad');
    });
    apStart(s,open?'':p);
    $('apPass').value='';
  };
  $('tApOpen').onclick=()=>{
    const on=!$('tApOpen').classList.contains('on');
    $('tApOpen').classList.toggle('on',on);
    $('apPass').disabled=on;
    $('apPass').placeholder=on?'not used - the network will be open'
                              :'leave blank to keep the current password';
  };
  $('btnClients').onclick=loadClients;
  $('btnStorage').onclick=loadStorage;
  // Live byte count, because "how long is my name" has a surprising answer once
  // an emoji is in it.
  const apCount=()=>{
    const v=$('apSsid').value, b=new TextEncoder().encode(v).length;
    const el=$('apBytes');
    if(!el)return;
    el.textContent=b+' / 32 bytes';
    el.style.color=b>32?'var(--bad)':b>26?'var(--warn)':'var(--fg3)';
  };
  $('apSsid').addEventListener('input',apCount);apCount();
  $('apnewReload').onclick=()=>location.reload();
  const pinSet=bindPin('pinSet');
  // The board erases the vault when the access PIN is replaced by someone who
  // could not produce the old one. This page usually can, so it sends it and
  // the user keeps their passwords through an ordinary PIN change.
  const applyPin=v=>send({cmd:'set_auth',token:v,old:sessionStorage.getItem('pin')||''},r=>{
    if(r&&r.status==='ok'){
      v?sessionStorage.setItem('pin',v):sessionStorage.removeItem('pin');
      toast(v?'PIN set':'PIN turned off','ok');
      if(r.vault_wiped)toast('Stored passwords were erased with the old PIN','bad');
      pinSet.clear();
      syncAll();
    }else{toast((r&&r.message)||'Failed','bad');pinSet.reject();}
  });
  $('btnPin').onclick=()=>{
    const v=pinSet.value();
    if(v.length!==4){toast('Enter all four digits','bad');pinSet.reject();return;}
    applyPin(v);
  };
  // Without a PIN the vault guards nothing, so the board will not keep it. Say
  // that before it happens rather than in a toast afterwards.
  $('btnPinOff').onclick=()=>{
    if(!confirm('Turn the PIN off? Anyone in radio range could then type on this PC.'))return;
    if(VAULT.length&&!confirm('Your '+VAULT.length+' stored '+
        (VAULT.length===1?'password':'passwords')+' will also be erased.\n\n'+
        'Without a PIN there is nothing guarding them, so the board does not keep them.'))return;
    applyPin('');
  };
  $('btnTestHid').onclick=()=>send({cmd:'test_hid'});
  $('btnWifiReset').onclick=()=>{if(confirm('Clear saved home-network credentials?'))send({cmd:'wifi_disconnect'});};

  $('btnPresPlay').onclick=()=>presToggle();
  $('btnPresReset').onclick=()=>{presT=0;presStop();renderPres();};

  const bind=(id,vid,key,fmt)=>{
    const el=$(id);el.value=S[key];$(vid).textContent=fmt(S[key]);
    el.oninput=()=>{S[key]=parseFloat(el.value);$(vid).textContent=fmt(S[key]);saveFeel();};
  };
  bind('sSens','vSens','sens',v=>v.toFixed(1));
  bind('sAcc','vAcc','acc',v=>v.toFixed(1));
  bind('sScr','vScr','scr',v=>v.toFixed(1));
  bind('sMom','vMom','mom',v=>v<=0?'off':v.toFixed(2));
  [['tTap','tap'],['t3D','f3drag'],['t3A','f3app'],['tHap','hap']].forEach(([id,k])=>{
    const el=$(id);el.classList.toggle('on',!!S[k]);
    el.onclick=()=>{S[k]=!S[k];el.classList.toggle('on',S[k]);saveFeel();if(k==='hap'&&S[k])buzz(18);};
  });
  // Saying nothing would look like a broken switch on every iPhone.
  if(!HAPTIC){
    $('tHap').classList.add('off');
    $('hapNote').style.display='';
    $('hapNote').textContent='Haptics are not available in this browser. iOS Safari has never '
      +'supported the web vibration API, so no page served from the board can buzz your phone. '
      +'Android Chrome and Firefox can. Everything else works exactly the same either way.';
  }else if(HAPTIC==='switch'){
    $('hapNote').style.display='';
    $('hapNote').textContent='Haptics here use the system tick iOS plays for a switch control, '
      +'which is the only one a web page is allowed. It is lighter than the buzz on Android.';
  }
  syncScroll();
}
function syncScroll(){
  document.querySelectorAll('[data-scroll]').forEach(b=>
    b.classList.toggle('on',(b.dataset.scroll==='mac')===!!S.nat));
}
// =====================================================================
//  scroll wheel on the mouse graphic
// =====================================================================
// A real wheel gives you a detent every few degrees, so this counts pixels into
// notches rather than streaming a continuous delta - that is what makes it feel
// like a wheel instead of a slider. A drag that never reaches one notch is a
// middle click, which is what the button used to be.
const WHEEL_PX=16;
function bindWheel(el){
  if(!el)return;
  let y0=0, acc=0, notches=0, t0=0;
  el.addEventListener('pointerdown',e=>{
    el.setPointerCapture(e.pointerId);
    y0=e.clientY; acc=0; notches=0; t0=performance.now();
    el.classList.add('spin');
    e.preventDefault();
  },{passive:false});

  el.addEventListener('pointermove',e=>{
    if(!t0)return;
    acc+=e.clientY-y0; y0=e.clientY;
    while(Math.abs(acc)>=WHEEL_PX){
      const dir=acc>0?-1:1;                    // drag down scrolls down
      acc-=Math.sign(acc)*WHEEL_PX;
      notches++;
      txMove(0,0,S.nat?-dir:dir,0);
      buzz(4);
    }
    e.preventDefault();
  },{passive:false});

  const up=e=>{
    if(!t0)return;
    el.classList.remove('spin');
    // never moved far enough to turn the wheel -> it was a press
    if(notches===0&&performance.now()-t0<600){txBtn(4,2,1);buzz(9);}
    t0=0;
    e.preventDefault();
  };
  el.addEventListener('pointerup',up,{passive:false});
  el.addEventListener('pointercancel',up,{passive:false});
}

// =====================================================================
//  connected clients + what is stored on the board
// =====================================================================
function loadClients(){
  const l=$('clientList');
  l.innerHTML='<p class="hint">Asking the board...</p>';
  send({cmd:'clients'},r=>{
    if(!r||r.status!=='ok'){l.innerHTML='<p class="hint">Could not read the client list.</p>';return;}
    if(!r.clients||!r.clients.length){
      l.innerHTML='<p class="hint">Nothing is connected to the board\u2019s access point right now. If you are reading this over your home network, you are not on the AP.</p>';
      return;
    }
    l.innerHTML=r.clients.map(c=>
      '<div class="item">'+ico('wifi')+
      '<div class="tx"><b>'+esc(c.ip||'no address yet')+
      (c.dashboard?' <span class="tag">dashboard open</span>':'')+
      '</b><span>'+esc(c.mac)+
      (c.quality?' &middot; '+esc(c.quality):'')+'</span></div></div>').join('')+
      (r.note?'<p class="hint" style="margin-top:10px">'+esc(r.note)+'</p>':'');
  });
}
function loadStorage(){
  const l=$('storeInfo');
  l.innerHTML=row('Reading','...');
  send({cmd:'storage_info'},r=>{
    if(!r||r.status!=='ok'){l.innerHTML=row('Storage','could not be read');return;}
    const yn=v=>v?'yes':'no';
    const n=r.nvs||{}, c=r.hid_cfg||{}, w=r.wifi_cfg||{};
    l.innerHTML=
      (n.total_entries?row('Flash entries used',n.used_entries+' of '+n.total_entries+'  ('+n.percent_used+'%)'):'')+
      (n.namespaces?row('Namespaces',n.namespaces):'')+
      row('Pointer mode',c.pointer_mode||'-')+
      row('Gamepad',yn(c.gamepad))+
      row('Access PIN',yn(c.pin_set))+
      row('Power mode',c.power_mode||'-')+
      row('AP auto off',yn(c.ap_auto_off))+
      row('Dashboard layout',(c.ui_bytes||0)+' bytes')+
      row('Home network',w.sta_ssid?w.sta_ssid:'none saved')+
      row('Its password',yn(w.sta_pass_set))+
      row('Fixed IP',w.static_ip?w.static_ip+' via '+(w.static_gw||'?'):'not set')+
      row('AP name',w.ap_ssid?w.ap_ssid:'default')+
      row('AP password',w.ap_open?'none - open network':yn(w.ap_pass_set));
  });
}
const item=(i,t,d)=>'<div class="item">'+ico(i)+'<div class="tx"><b>'+esc(t)+'</b><span style="white-space:normal">'+esc(d)+'</span></div></div>';
function doType(withEnter,srcId){
  const el=$(srcId||'typeText');
  const v=el.value;
  // An empty box with Enter pressed is not a mistake - it is someone who typed
  // with the on-screen keys and now wants to submit. Send a bare Return.
  if(!v){
    if(withEnter){send({cmd:'press',keys:'ENTER'});buzz(8);return;}
    return toast('Type something first','bad');
  }
  send({cmd:'type',text:v,enter:withEnter});
  el.value='';
  buzz(8);
}
// The board starts the scan and returns straight away, then we ask again until
// results appear. It used to block the whole board for two to four seconds.
function scanWifi(){
  const l=$('wifiList');
  l.innerHTML='<p class="hint">Looking for networks nearby...</p>';
  let tries=0;
  // A scan is a snapshot. Networks come and go, and the one you are waiting for
  // may simply not have been up yet, so the result always carries a way to ask
  // again rather than making you hunt back up the page for the button.
  const again=n=>'<div class="scanfoot">'
    +'<span>'+(n===null?'':n+(n===1?' network':' networks')+' &middot; ')+'just now</span>'
    +'<button class="btn sm" id="btnRescan">Scan again</button></div>';
  const wire=()=>{const b=$('btnRescan');if(b)b.onclick=()=>{buzz(8);scanWifi();};};
  const paint=d=>{
    if(!d.networks||!d.networks.length){
      l.innerHTML='<p class="hint">No networks found. Only 2.4 GHz networks can be seen by this board, and a hidden network never appears in a scan - it still joins if you know its name.</p>'+again(0);
      return wire();
    }
    l.innerHTML=d.networks.map(n=>
      '<button class="item" data-ssid="'+esc(n.ssid)+'">'+ico('wifi')+
      '<div class="tx"><b>'+esc(n.ssid)+'</b><span>'+esc(n.quality||'')+' &middot; '+esc(n.encryption)+' &middot; ch '+n.channel+'</span></div>'+
      ico('right','ic-sm')+'</button>').join('')+again(d.networks.length);
    l.querySelectorAll('[data-ssid]').forEach(b=>b.onclick=()=>joinWifi(b.dataset.ssid));
    wire();
  };
  const poll=()=>fetch('/api/scan',{headers:pinHeader()}).then(r=>r.json()).then(d=>{
    if(d.busy){l.innerHTML='<p class="hint">'+esc(d.message||'The board is busy.')+'</p>'+again(null);return wire();}
    if(d.scanning){
      if(++tries>15){l.innerHTML='<p class="hint">The scan is taking longer than expected.</p>'+again(null);return wire();}
      return setTimeout(poll,600);
    }
    paint(d);
  }).catch(()=>{l.innerHTML='<p class="hint">Scan failed. Check you are still connected to the board.</p>'+again(null);wire();});
  poll();
}
function joinWifi(ssid){
  sheet('Join '+ssid,
    '<div class="fld"><label>Password</label>'+
      '<div class="pwrow"><input type="password" id="w_p" autocomplete="off" data-nofill>'+
      '<button type="button" class="btn sm" id="w_eye">Show</button></div></div>'+
    (($('tStatic').classList.contains('on'))
      ? '<p class="hint">Requesting the fixed address you entered. If the router will not give it, the board falls back to DHCP and says so.</p>'
      : '<p class="hint">The router will assign an address. Turn on <b>Use a fixed IP</b> first if you want a specific one.</p>')+
    '<p class="hint">'+(location.hostname==='192.168.4.1'
      ? 'You are on the board\'s own access point, so WPA2 covers this password the whole way to it.'
      : 'You are not on the board\'s access point. There is no HTTPS here, so this password will cross your network in the clear - join from the access point instead if that network is shared.')+'</p>'+
    '<button class="btn ok blk" id="w_go" style="margin-top:12px">Connect</button>',
    ()=>{
    guardAutofill($('w_p'));
    $('w_eye').onclick=()=>{
      const p=$('w_p');
      const show=p.type==='password';
      p.type=show?'text':'password';
      $('w_eye').textContent=show?'Hide':'Show';
    };
    $('w_go').onclick=()=>{
      const o={cmd:'wifi_configure',ssid:ssid,password:$('w_p').value};
      if($('tStatic').classList.contains('on')){
        o.ip=$('stIp').value.trim();o.gateway=$('stGw').value.trim();
        o.subnet=$('stMask').value.trim();o.dns=$('stDns').value.trim();
        if(!o.ip||!o.gateway)return toast('A fixed IP needs an address and a gateway','bad');
      }
      closeSheet();
      // The board answers immediately and joins in the background, so from here
      // we just poll and narrate. Nothing blocks and the AP never drops.
      send(o,r=>{if(r&&r.status==='error')toast(r.message||'Rejected','bad');});
      joinWatch(ssid);
    };});
}

// ---- live join feedback ----
// The board is never unreachable during a join because its own access point
// stays up throughout, so we can keep asking it what is happening.
let joinPoll=0;
function joinWatch(ssid){
  clearInterval(joinPoll);
  $('joinBox').style.display='';
  $('joinBar').style.display='';
  $('joinDot').className='dot warn';
  $('joinTitle').textContent='Joining '+ssid;
  $('joinMsg').textContent='Asking the router for a place on the network.';
  joinPoll=setInterval(()=>send({cmd:'wifi_status'},joinPaint),700);
}
function joinPaint(r){
  if(!r)return;
  const p=r.sta_phase, bar=$('joinBar').firstElementChild;
  if(p==='connecting'){
    $('joinDot').className='dot warn';
    $('joinTitle').textContent='Joining '+(r.sta_target||'');
    $('joinMsg').textContent=r.want_ip?('Requesting '+r.want_ip+'.'):'Waiting for an address from the router.';
    bar.style.width='60%';
    return;
  }
  clearInterval(joinPoll);joinPoll=0;
  bar.style.width='100%';
  if(p==='online'){
    $('joinDot').className='dot ok';
    $('joinTitle').textContent='Connected as '+r.sta_ip;
    const how={static:'Fixed address, exactly as asked.',
               dhcp_fallback:'The fixed address did not work, so this one came from the router.',
               dhcp:'Address assigned by the router.'}[r.sta_ip_mode]||'';
    $('joinMsg').textContent=(r.sta_error?r.sta_error+' ':'')+how+
      ' The board is still running its own access point as well, so both ways in work.';
    toast('Connected as '+r.sta_ip,'ok');
    refresh();
  }else if(p==='failed'){
    $('joinDot').className='dot bad';
    $('joinTitle').textContent='Could not join';
    $('joinMsg').textContent=(r.sta_error||'The router did not accept the connection.')+
      ' The access point is unaffected, so you have not lost the dashboard.';
    toast('Join failed','bad');
  }else{
    $('joinBox').style.display='none';$('joinBar').style.display='none';
  }
}
let presT=0,presTimer=0;
function presToggle(){presTimer?presStop():presStart();}
function presStart(){presTimer=setInterval(()=>{presT++;renderPres();},1000);$('btnPresPlay').innerHTML=ico('stop');}
function presStop(){clearInterval(presTimer);presTimer=0;$('btnPresPlay').innerHTML=ico('play');}
function renderPres(){
  $('presTime').textContent=String(Math.floor(presT/60)).padStart(2,'0')+':'+String(presT%60).padStart(2,'0');
}

// =====================================================================
//  boot
// =====================================================================
buildNav();buildKbd();kbWatch();bindAll();bindGpButtons();
RAIL.init();$('btnRail').onclick=()=>RAIL.toggle();
document.querySelectorAll('[data-nofill]').forEach(guardAutofill);
DECK.set((()=>{try{return localStorage.getItem('deck')==='1';}catch(e){return false;}})(),true);
loadClients();loadStorage();
bindStick($('stickL'),'l');bindStick($('stickR'),'r');
renderQuick();renderKnobs();renderMacros();renderSC();gpSync();FS.sync();paintOsUi();paintGestureLabels();
go(VIEWS.some(v=>v.id===location.hash.replace('#',''))?location.hash.replace('#',''):'pad');
connect();
setInterval(()=>{if(wsUp&&!document.hidden)send({cmd:'status'});},8000);
// iOS Safari ignores user-scalable=no, so pinch has to be refused as it happens.
// All three events matter: blocking only the first still lets a pinch already in
// flight carry on.
['gesturestart','gesturechange','gestureend'].forEach(ev=>
  document.addEventListener(ev,e=>e.preventDefault(),{passive:false}));

// And if the page ends up zoomed anyway, say so and offer the way out, rather
// than leaving someone hunting for a piece of text to pinch back from.
const ZOOM={el:null};
function zoomWatch(){
  const vv=window.visualViewport;
  if(!vv)return;
  const check=()=>{
    const zoomed=vv.scale>1.05;
    if(zoomed&&!ZOOM.el){
      const b=document.createElement('button');
      b.className='zoomout';
      b.textContent='Zoomed in - tap to reset';
      b.onclick=()=>{
        // Rewriting the tag is the only lever a page has over the visual
        // viewport, and Safari only acts on a change, so it goes out and back.
        const m=document.querySelector('meta[name=viewport]');
        const keep=m.content;
        m.content='width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover';
        setTimeout(()=>{m.content=keep;},60);
        buzz(8);
      };
      document.body.appendChild(b);
      ZOOM.el=b;
    }else if(!zoomed&&ZOOM.el){
      ZOOM.el.remove();ZOOM.el=null;
    }
  };
  vv.addEventListener('resize',check);
  vv.addEventListener('scroll',check);
  check();
}
zoomWatch();
document.addEventListener('visibilitychange',()=>{
  if(document.hidden)aswClose(true);   // never leave the PC with Alt stuck down
  else refresh();
});
</script>
</body>
</html>
)rawliteral";
