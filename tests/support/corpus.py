"""The text every typing test uses, and the expected result computed independently.

expect() is deliberately written from the specification, not from the firmware:
Python's own UTF-8 decoder finds the ill-formed runs (one per maximal subpart,
as Unicode recommends), and FOLD is a separate copy of the folding table. When
the firmware and this file disagree, one of them is wrong - that is the point.
"""
import codecs
import random

SENT = "\x00"  # stands in for an ill-formed run; input text can never contain NUL
codecs.register_error("hidfi_sentinel", lambda e: (SENT, e.end))

FOLD = {}
for c in "            　":
    FOLD[c] = " "
for c in "‘’‚‛′ʼ´":
    FOLD[c] = "'"
for c in "“”„‟″«»":
    FOLD[c] = '"'
for c in "‐‑‒–—―−":
    FOLD[c] = "-"
FOLD["…"] = "..."
for c in "​‌‍⁠﻿":
    FOLD[c] = ""


def expect(raw: bytes):
    """(text with Unicode entry off, skipped off, text with entry on, skipped on)."""
    s = raw.decode("utf-8", "hidfi_sentinel")
    off = on = ""
    off_sk = on_sk = 0
    for ch in s:
        if ch == SENT:
            off_sk += 1
            on_sk += 1
        elif ord(ch) < 0x80:
            off += ch
            on += ch
        else:
            on += ch
            if ch in FOLD:
                off += FOLD[ch]
            else:
                off_sk += 1
    return off, off_sk, on, on_sk


# Real text in the scripts and emoji people type. Each entry is one test case.
CORPUS = {
    "tamil": "வணக்கம் உலகம் — தமிழ் ஒரு அழகான மொழி",
    "hindi": "नमस्ते दुनिया, क्षत्रिय ज्ञान",
    "telugu": "తెలుగు భాష శ్రీ",
    "kannada": "ಕನ್ನಡ ನಮಸ್ಕಾರ",
    "malayalam": "മലയാളം നന്ദി",
    "bengali": "বাংলা ভাষা",
    "gujarati": "ગુજરાતી",
    "punjabi": "ਪੰਜਾਬੀ",
    "arabic": "مرحبا بالعالم ١٢٣",
    "hebrew": "שלום עולם",
    "urdu": "اردو زبان",
    "chinese": "你好，世界！中文输入测试。",
    "chinese_ext_b": "𠀀𠜎𪚥",
    "japanese": "こんにちは、カタカナ、漢字。ｱｲｳ",
    "korean": "안녕하세요 한국어",
    "korean_jamo": "각",
    "thai": "สวัสดีครับ",
    "russian": "Привет, мир! Ёё",
    "greek": "Γειά σου Κόσμε",
    "vietnamese_nfc": "Tiếng Việt có dấu",
    "vietnamese_nfd": "Tiếng Việt",
    "amharic": "ሰላም ልዑል",
    "georgian": "გამარჯობა",
    "armenian": "Բարեւ",
    "sinhala": "ආයුබෝවන්",
    "tibetan": "བོད་ཡིག",
    "mongolian": "ᠮᠣᠩᠭᠣᠯ",
    "latin_accents": "café naïve façade Ångström São Paulo Øresund ß œ",
    "turkish": "İstanbul ığüşöç",
    "polish": "Zażółć gęślą jaźń",
    "smart_punct": "“Hello,” she said — it’s ‘Laya’… 2–3",
    "spaces": "a b c d　e",
    "invisible": "a​b‍c﻿d⁠e",
    "currency": "₹100 €5 £3 ¥7 ₿ ¢",
    "math": "∑ √2 ≈ 1.41 ≠ ∞ ± × ÷ ° µ π",
    "arrows_box": "← → ↑ ↓ ⇒ ┌─┐ █",
    "emoji_basic": "😀😂🥲🫠👍🔥✅❤",
    "emoji_vs16": "❤️ ☺️ ✌️",
    "emoji_skin": "👍🏽 👋🏿 🧑🏻‍💻",
    "emoji_zwj_family": "👨‍👩‍👧‍👦",
    "emoji_flags": "🇮🇳🇺🇸🇯🇵🏴󠁧󠁢󠁳󠁣󠁴󠁿",
    "emoji_keycap": "1️⃣ #️⃣",
    "emoji_newest": "🫨🫎🪿🐦‍🔥",
    "mixed_sentence": "Meeting at 3pm 📅 with Priya — “bring the ₹500” ✔",
    "plane1_math": "𝐀𝐁𝐂 𝔸𝔹",
    "plane14_tags": "\U000e0041\U000e007f",
    "pua": "\U000f0000\U0010fffd",
    "noncharacters": "﷐￾￿\U0010ffff",
    "replacement": "�",
    "bmp_edges": "\u0080߿ࠀ퟿￯",
    "combining_only": "́̈",
    "rtl_marks": "‏‎‮",
    "ascii_only": "Hello, World! 123 ~`@#$%^&*()_+-=[]{}|;:'\",.<>/?",
}

# Byte sequences that are not valid UTF-8. A board must never turn these into keys.
INVALID = {
    "lone_cont": b"a\x80b", "two_cont": b"\x80\xbf", "overlong_slash": b"\xc0\xaf", "overlong_c1": b"\xc1\xbf",
    "overlong_3": b"\xe0\x80\xaf", "overlong_4": b"\xf0\x80\x80\xaf", "surrogate_hi": b"\xed\xa0\x80",
    "surrogate_lo": b"\xed\xbf\xbf", "surrogate_pair_cesu": b"\xed\xa0\xbd\xed\xb8\x80",
    "past_10ffff": b"\xf4\x90\x80\x80", "f5": b"\xf5\x80\x80\x80", "ff": b"x\xffy",
    "trunc2": b"ok\xc3", "trunc3": b"ok\xe2\x80", "trunc4": b"ok\xf0\x9f\x98", "trunc_then_ascii": b"\xe2\x80Z",
    "lead_lead": b"\xe2\xe2\x80\x99", "mixed_bad": b"\xe2\x80\x99\x80\xf0\x9f\x98\x80\xc3",
}

# Not valid in any host text field - macOS drops U+FFFE/U+FFFF on input - so the
# end-to-end tests leave them out; the unit tier still covers them.
HOST_UNTYPEABLE = {"noncharacters"}


def mixes(n=3000, seed=7):
    """Random interleavings of valid text, ASCII and garbage bytes - fixed seed, reproducible."""
    rnd = random.Random(seed)
    pool = "".join(CORPUS.values())
    out = {}
    for i in range(n):
        parts = []
        for _ in range(rnd.randint(1, 8)):
            r = rnd.random()
            if r < 0.6:
                parts.append("".join(rnd.choice(pool) for _ in range(rnd.randint(1, 6))).encode())
            elif r < 0.8:
                parts.append(bytes(rnd.randint(0x20, 0x7E) for _ in range(rnd.randint(1, 5))))
            else:
                parts.append(bytes(rnd.randint(0x80, 0xFF) for _ in range(rnd.randint(1, 3))))
        out[f"mix{i}"] = b"".join(parts)
    return out


def cases_file(path, include_mixes=True):
    """Write the bench's input: label, input hex, expected-off hex, skipped, expected-on hex, skipped."""
    rows = []
    items = {k: v.encode() for k, v in CORPUS.items()}
    items.update(INVALID)
    if include_mixes:
        items.update(mixes())
    for label, raw in items.items():
        off, osk, on, nsk = expect(raw)
        rows.append(f"{label} {raw.hex()} {off.encode().hex() or '-'} {osk} {on.encode().hex() or '-'} {nsk}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return len(rows)
