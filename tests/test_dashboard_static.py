"""Extracts the dashboard from web_ui.h and checks it is well formed.

The dashboard lives inside a PROGMEM raw string literal, so a syntax error in it
is invisible to the compiler -- it builds fine and the board serves a blank white
page. This pulls the HTML out, runs node --check over the script, and verifies
that every id the script reaches for actually exists in the markup.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
UI = HERE.parent / "hid_fi" / "web_ui.h"


def extract_html(text):
    start = text.index("R\"rawliteral(")
    start = text.index("\n", start) + 1
    end = text.index(")rawliteral\"", start)
    return text[start:end]


def kbd_layouts(js):
    """Evaluate the keyboard layout tables in node and return their geometry.

    Parsed by running them rather than by regex, because the labels contain
    ico(...) calls with their own commas and brackets. Every table is one
    contiguous slab of source, so it is taken whole.
    """
    i = js.index("const ANSI_COLS=")
    j = js.index("const KB_TIERS=", i)
    j = js.index("\n", j)

    prog = (
        "function ico(n,c){return 'I';}\n"
        + js[i:j]
        + "\n"
        "const w=k=>(k&&k[2])||0, nm=k=>(k&&k[1])||null;\n"
        "function row(keys){let c=1,out=[];keys.forEach(k=>{const n=nm(k);\n"
        "  if(n!==null)out.push([n,c,w(k)]); c+=w(k);});\n"
        "  return {cols:c-1,keys:out};}\n"
        "function block(rows){return rows.map(row);}\n"
        "console.log(JSON.stringify({\n"
        "  cols:ANSI_COLS,\n"
        "  compact:{abc:KROWS_ABC.map(r=>Object.assign(row(r.keys),{fn:!!r.fn,nav:!!r.nav})),\n"
        "           sym:KROWS_SYM.map(r=>Object.assign(row(r.keys),{fn:!!r.fn,nav:!!r.nav}))},\n"
        "  ansiFn:row(ANSI_FN), ansi:block(ANSI_MAIN),\n"
        "  arrows:block(NAV_65), nav:block(NAV_TKL),\n"
        "  numpad:NUMPAD.map(k=>[k[1],k[2],k[3],k[4]||1,k[5]||1]),\n"
        "  tiers:KB_TIERS\n"
        "}));"
    )
    out = HERE / "_kbd_probe.js"
    out.write_text(prog, encoding="utf-8")
    try:
        r = subprocess.run(["node", str(out)], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            print("  node said:", (r.stderr or "").strip().splitlines()[:3])
            return None
        return json.loads(r.stdout)
    finally:
        out.unlink(missing_ok=True)


def firmware_key_names():
    """Every key name mapKeyName() will resolve.

    A name missing from it does not error -- press() simply does nothing, which
    is the project's classic silent failure. So the layouts are checked against
    the firmware rather than against a second copy of the list.
    """
    ino = (HERE.parent / "hid_fi" / "hid_fi.ino").read_text(encoding="utf-8", errors="replace")
    # The definition, not the prototype -- the prototype is declared far earlier
    # and slicing from it picks up setup() instead, which names no keys at all.
    m = re.search(r"uint8_t mapKeyName\([^)]*\)\s*\{", ino)
    if not m:
        return set()
    return set(re.findall(r'name\s*==\s*"([^"]+)"', ino[m.end():ino.index("\n}", m.end())]))


def check_key_names(layouts, check):
    allow = firmware_key_names()
    check("firmware key allowlist parsed", len(allow) > 40, f"{len(allow)} names")

    names = set()

    def collect(rows):
        for r in rows:
            for n, _c, _w in r["keys"]:
                names.add(n)

    collect(layouts["compact"]["abc"])
    collect(layouts["compact"]["sym"])
    collect([layouts["ansiFn"]])
    collect(layouts["ansi"])
    collect(layouts["arrows"])
    collect(layouts["nav"])
    names.update(k[0] for k in layouts["numpad"])

    unresolved = []
    for n in sorted(names):
        if n.startswith("@"):
            continue                       # layer switch, never sent to the host
        raw = n[1:] if n.startswith("#") else n
        for tok in raw.split("+"):         # Ctrl+C style rows resolve per token
            if not tok:
                continue
            if tok.upper() in allow or len(tok) == 1:
                continue
            unresolved.append(n)
            break
    check("every key resolves in the firmware", not unresolved, ", ".join(unresolved))


def check_keyboard(html, js, check):
    """The keyboard must grow by adding blocks, and the arrows must form a real
    inverted T rather than a flat line.

    Up has to sit directly above Down, which only holds if the rows share a grid.
    Both were broken before: the grid was ten columns wide and the arrows were
    laid out LEFT UP DOWN RIGHT in a row.
    """
    m12 = re.search(r"\.kmain\.c12 \.krow\{grid-template-columns:repeat\((\d+),", html)
    m60 = re.search(r"\.kmain\.ansi \.krow\{grid-template-columns:repeat\((\d+),", html)
    check("compact grid declared", m12 is not None, m12.group(1) if m12 else "")
    check("ANSI grid declared", m60 is not None, m60.group(1) if m60 else "")
    if not (m12 and m60):
        return
    c12, c60 = int(m12.group(1)), int(m60.group(1))

    try:
        g = kbd_layouts(js)
    except (ValueError, FileNotFoundError):
        print("  [SKIP] node not available, keyboard geometry not verified")
        return
    if g is None:
        check("keyboard layouts evaluate", False)
        return

    check("ANSI grid matches the layout tables", c60 == g["cols"], f"{c60} vs {g['cols']}")

    # --- ANSI block: every row is exactly 15u, which is what makes it ANSI ---
    bad = [r["cols"] for r in g["ansi"] if r["cols"] != c60]
    check(f"every ANSI row spans {c60} columns (15u)", not bad,
          ", ".join(str(b) for b in bad))
    check("ANSI function row spans the same 15u", g["ansiFn"]["cols"] == c60,
          str(g["ansiFn"]["cols"]))

    # --- right-hand blocks are three columns wide ---
    for label, rows in (("arrow", g["arrows"]), ("nav", g["nav"])):
        wrong = [r["cols"] for r in rows if r["cols"] != 3]
        check(f"{label} cluster rows span 3 columns", not wrong,
              ", ".join(str(x) for x in wrong))

    # --- numpad cells must tile 4x5 without overlapping ---
    used = {}
    clash = []
    for name, col, row, cs, rs in g["numpad"]:
        for dc in range(cs):
            for dr in range(rs):
                cell = (col + dc, row + dr)
                if cell in used:
                    clash.append(f"{name} over {used[cell]} at {cell}")
                used[cell] = name
    check("numpad keys do not overlap", not clash, "; ".join(clash))
    check("numpad fills a 4x5 block", len(used) == 20, f"{len(used)} cells")

    # --- compact layout: the inverted T and the slash ---
    for layer in ("abc", "sym"):
        rows = g["compact"][layer]
        body = [r for r in rows if not r["fn"] and not r["nav"]]
        over = [r["cols"] for r in body if r["cols"] > c12]
        check(f"compact {layer}: no row exceeds {c12} columns", not over,
              ", ".join(str(b) for b in over))

        pos = {}
        for r in body:
            for name, col, _w in r["keys"]:
                pos.setdefault(name, col)
        if {"UP", "DOWN", "LEFT", "RIGHT"} <= set(pos):
            check(f"compact {layer}: Up sits directly above Down",
                  pos["UP"] == pos["DOWN"], f"{pos['UP']} vs {pos['DOWN']}")
            check(f"compact {layer}: Left and Right flank Down",
                  pos["LEFT"] == pos["DOWN"] - 1 and pos["RIGHT"] == pos["DOWN"] + 1,
                  f"{pos['LEFT']}/{pos['DOWN']}/{pos['RIGHT']}")
        else:
            check(f"compact {layer} has all four arrows", False)

        shifts = [c for r in body for n, c, _w in r["keys"] if n == "#SHIFT"]
        check(f"compact {layer}: slash immediately left of the right Shift",
              "/" in pos and shifts and pos["/"] == max(shifts) - 1,
              f"slash {pos.get('/')}, shift {max(shifts) if shifts else None}")

    # The ANSI shift row is the real thing: slash then the right Shift.
    shift_row = g["ansi"][3]
    seq = [n for n, _c, _w in shift_row["keys"]]
    check("ANSI shift row is Z X C V B N M , . / then Shift",
          seq == ["#SHIFT", "z", "x", "c", "v", "b", "n", "m", ",", ".", "/", "#SHIFT"],
          " ".join(seq))

    # --- tiers must be ordered widest first, or kbTier picks the wrong one ---
    widths = [t[1] for t in g["tiers"]]
    check("tiers are ordered widest first", widths == sorted(widths, reverse=True),
          str(widths))
    check("there is a zero-width fallback tier", widths[-1] == 0, str(widths[-1]))

    check_key_names(g, check)


def main():
    src = UI.read_text(encoding="utf-8")
    html = extract_html(src)
    out_html = HERE / "_ui_preview.html"
    out_html.write_text(html, encoding="utf-8")

    failed = 0

    def check(label, ok, detail=""):
        nonlocal failed
        print(f"  [{'OK ' if ok else 'FAIL'}] {label}{(': ' + detail) if detail else ''}")
        if not ok:
            failed += 1

    print("=" * 60)
    print("  Dashboard static checks")
    print("=" * 60)

    check("HTML extracted", len(html) > 10000, f"{len(html)} bytes")
    check("no unresolved template markers", "){rawliteral" not in html)

    # --- script syntax ---
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    check("exactly one script block", len(scripts) == 1, str(len(scripts)))
    js = "\n".join(scripts)
    out_js = HERE / "_ui_preview.js"
    out_js.write_text(js, encoding="utf-8")

    try:
        r = subprocess.run(["node", "--check", str(out_js)],
                           capture_output=True, text=True, timeout=60)
        check("node --check passes", r.returncode == 0,
              (r.stderr or r.stdout).strip().splitlines()[0] if r.returncode else "")
    except FileNotFoundError:
        print("  [SKIP] node not available, script syntax not verified")

    # --- every $('id') must exist in the markup ---
    ids_in_html = set(re.findall(r'id="([^"]+)"', html))
    ids_used = set(re.findall(r"\$\('([A-Za-z0-9_]+)'\)", js))
    missing = sorted(ids_used - ids_in_html)
    check("every $('id') exists in the markup", not missing, ", ".join(missing))

    # --- every <use href="#i-x"> must have a matching <symbol id="i-x"> ---
    # Icon names built at runtime (ico(name) inside a template) cannot be checked
    # statically, so only literal references are compared.
    symbols = set(re.findall(r'<symbol id="(i-[^"]+)"', html))
    used_static = set(re.findall(r'href="#(i-[a-z0-9]+)"', html))
    used_js = {"i-" + m for m in re.findall(r"ico\('([a-z0-9]+)'", js)}
    missing_icons = sorted((used_static | used_js) - symbols)
    check("every literal icon reference resolves", not missing_icons, ", ".join(missing_icons))

    # --- no emoji anywhere, per the project's own rule ---
    emoji = re.findall(r"[\U0001F000-\U0001FAFF\u2600-\u27BF]", html)
    check("no emoji in the dashboard", not emoji, "".join(sorted(set(emoji))))

    # --- text selection is off globally, but inputs stay usable ---
    check("body text selection disabled", "user-select:none" in html)
    check("inputs re-enable selection",
          re.search(r"input[^{]*\{[^}]*user-select:text", html) is not None
          or "input,textarea" in html and "user-select:text" in html)

    # --- keyboard geometry ---
    check_keyboard(html, js, check)

    out_html.unlink(missing_ok=True)
    out_js.unlink(missing_ok=True)

    print("=" * 60)
    print("  all checks passed" if not failed else f"  {failed} check(s) failed")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
