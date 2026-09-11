"""Extracts the dashboard from web_ui.h and checks it is well formed.

The dashboard lives inside a PROGMEM raw string literal, so a syntax error in it
is invisible to the compiler -- it builds fine and the board serves a blank white
page. This pulls the HTML out, runs node --check over the script, and verifies
that every id the script reaches for actually exists in the markup.
"""
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

    out_html.unlink(missing_ok=True)
    out_js.unlink(missing_ok=True)

    print("=" * 60)
    print("  all checks passed" if not failed else f"  {failed} check(s) failed")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
