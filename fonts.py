"""65 услуби шрифт. Ҳамаро ин ҷо тағйир додан мумкин аст."""
import string

UP, LOW, DIG = string.ascii_uppercase, string.ascii_lowercase, string.digits

# --- Транслитератсияи тоҷикӣ (кириллик -> лотинӣ) ---
_TJ_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "ғ": "gh", "д": "d", "е": "e",
    "ё": "yo", "ж": "j", "з": "z", "и": "i", "ӣ": "i", "й": "y", "к": "k",
    "қ": "q", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ӯ": "u", "ф": "f", "х": "kh", "ҳ": "h",
    "ч": "ch", "ҷ": "j", "ш": "sh", "ъ": "'", "э": "e", "ю": "yu", "я": "ya",
    "ц": "ts", "щ": "shch", "ы": "i", "ь": "",
}


def translit(text: str) -> str:
    out = []
    for ch in text:
        low = ch.lower()
        if low in _TJ_LAT:
            r = _TJ_LAT[low]
            if ch != low and r:
                r = r[0].upper() + r[1:]
            out.append(r)
        else:
            out.append(ch)
    return "".join(out)


# --- Ҷадвалҳои Unicode (латинӣ) ---
def _build(upper_start, lower_start, digit_start=None, exceptions=None):
    m = {}
    for i, c in enumerate(UP):
        m[c] = chr(upper_start + i)
    for i, c in enumerate(LOW):
        m[c] = chr(lower_start + i)
    if digit_start:
        for i, c in enumerate(DIG):
            m[c] = chr(digit_start + i)
    if exceptions:
        m.update(exceptions)
    return m


BOLD = _build(0x1D400, 0x1D41A, 0x1D7CE)
ITALIC = _build(0x1D434, 0x1D44E, None, {"h": "\u210E"})
BOLD_ITALIC = _build(0x1D468, 0x1D482, 0x1D7CE)
SCRIPT = _build(
    0x1D49C, 0x1D4B6, None,
    {"B": "\u212C", "E": "\u2130", "F": "\u2131", "H": "\u210B", "I": "\u2110",
     "L": "\u2112", "M": "\u2133", "R": "\u211B", "e": "\u212F", "g": "\u210A", "o": "\u2134"},
)
BOLD_SCRIPT = _build(0x1D4D0, 0x1D4EA, 0x1D7CE)
FRAKTUR = _build(
    0x1D504, 0x1D51E, None,
    {"C": "\u212D", "H": "\u210C", "I": "\u2111", "R": "\u211C", "Z": "\u2128"},
)
DOUBLE = _build(
    0x1D538, 0x1D552, 0x1D7D8,
    {"C": "\u2102", "H": "\u210D", "N": "\u2115", "P": "\u2119", "Q": "\u211A",
     "R": "\u211D", "Z": "\u2124"},
)
MONO = _build(0x1D670, 0x1D68A, 0x1D7F6)
SANS_BOLD = _build(0x1D5D4, 0x1D5EE, 0x1D7EC)
SANS_BOLD_ITALIC = _build(0x1D63C, 0x1D656, 0x1D7EC)

_SC_SRC = "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ"
assert len(_SC_SRC) == 26
SMALL_CAPS = dict(zip(LOW, _SC_SRC))

# --- Услубҳои кириллик ---
_STYLISH_LOWER = {"а": "ɑ", "е": "є", "э": "є", "о": "ө", "у": "ү", "и": "і"}
STYLISH = dict(_STYLISH_LOWER)
STYLISH.update({k.upper(): v.upper() for k, v in _STYLISH_LOWER.items()})

_COPTIC_CODES = {
    "а": 0x2C81, "б": 0x2C83, "в": 0x2C83, "г": 0x2C85, "ғ": 0x2C85, "д": 0x2C87,
    "е": 0x2C89, "ё": 0x2C89, "э": 0x2C89, "з": 0x2C8D, "ж": 0x2C8D, "и": 0x2C93,
    "ӣ": 0x2C93, "й": 0x2C93, "к": 0x2C95, "қ": 0x2C95, "л": 0x2C97, "м": 0x2C99,
    "н": 0x2C9B, "о": 0x2C9F, "п": 0x2CA1, "р": 0x2CA3, "с": 0x2CA5, "т": 0x2CA7,
    "у": 0x2CA9, "ӯ": 0x2CA9, "ф": 0x2CAB, "х": 0x2CAD, "ш": 0x03E3, "ҳ": 0x03E9,
    "ҷ": 0x03ED,
}
COPTIC = {}
for _k, _v in _COPTIC_CODES.items():
    COPTIC[_k] = chr(_v)
    COPTIC[_k.upper()] = chr(_v).upper()


def _map(text: str, table: dict) -> str:
    return "".join(table.get(c, c) for c in text)


def _latin(table: dict):
    return lambda text: _map(translit(text), table)


def _fullwidth(text: str) -> str:
    out = []
    for ch in translit(text):
        if ch == " ":
            out.append("\u3000")
        elif 0x21 <= ord(ch) <= 0x7E:
            out.append(chr(ord(ch) + 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


# ====================== 50 шрифти иловагӣ ======================
def _az(dst: str) -> dict:
    assert len(dst) == 26, dst
    m = {}
    for i in range(26):
        m[UP[i]] = dst[i]
        m[LOW[i]] = dst[i]
    return m


def _digits(dst: str) -> dict:
    assert len(dst) == 10, dst
    return {DIG[i]: dst[i] for i in range(10)}


def _seq(start: int, n: int = 26) -> str:
    return "".join(chr(start + i) for i in range(n))


BOLD_FRAKTUR = _build(0x1D56C, 0x1D586)
SANS = _build(0x1D5A0, 0x1D5BA, 0x1D7E2)
SANS_ITALIC = _build(0x1D608, 0x1D622, 0x1D7E2)

CIRCLED = _build(0x24B6, 0x24D0)
CIRCLED.update(_digits("\u24EA" + _seq(0x2460, 9)))
NEG_CIRCLED = _az(_seq(0x1F150))
NEG_CIRCLED.update(_digits("\u24FF" + _seq(0x2776, 9)))
SQUARED = _az(_seq(0x1F130))
NEG_SQUARED = _az(_seq(0x1F170))
PAREN = _az(_seq(0x249C))
PAREN.update(_digits("0" + _seq(0x2474, 9)))

SUPER = _az("ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖᑫʳˢᵗᵘᵛʷˣʸᶻ")
SUPER.update(_digits("⁰¹²³⁴⁵⁶⁷⁸⁹"))
UPSIDE = _az("ɐqɔpǝɟƃɥᴉɾʞlɯuodbɹsʇnʌʍxʎz")
UPSIDE.update(_digits("0ƖᄅƐㄣϛ9ㄥ86"))
CANADIAN = _az("ᗩᗷᑕᗪᗴᖴǤᕼᎥᒍᛕᒪᗰᑎᗝᑭᑫᖇᔕ丅ᑌᐯᗯ᙭Ƴ乙")
CURRENCY = _az("₳฿₵ĐɆ₣₲ⱧłJ₭Ⱡ₥₦Ø₱QɌ₴₮ɄV₩ӾɎⱫ")
GREEK = _az("αβςδεƒγηιϳκℓмνορϙгστυѵωχүζ")


def _marks(mark: str, base=None):
    """Ба ҳар ҳарф аломати часпак (combining) илова мекунад."""
    def f(text: str) -> str:
        if base:
            text = base(text)
        return "".join(c if c.isspace() else c + mark for c in text)
    return f


def _join(sep: str, word_sep: str = " "):
    def f(text: str) -> str:
        return word_sep.join(sep.join(w) for w in text.split(" "))
    return f


def _wrap(prefix: str, suffix: str = ""):
    return lambda t: f"{prefix}{t}{suffix}"


def _vapor(text: str) -> str:
    return "\u3000".join(" ".join(_fullwidth(w)) for w in text.split(" "))


_ABOVE = ["\u030d", "\u030e", "\u0304", "\u0305", "\u033f", "\u0311", "\u0306", "\u0310", "\u0352"]
_BELOW = ["\u0316", "\u0317", "\u0318", "\u0319", "\u031c", "\u031d", "\u031e", "\u031f", "\u0320"]


def _glitch(text: str) -> str:
    out = []
    for i, c in enumerate(text):
        out.append(c if c.isspace() else c + _ABOVE[i % 9] + _BELOW[(i * 2) % 9])
    return "".join(out)


# (id, номи шрифт, функсия) — тартиб ба видео мувофиқ аст
FONTS = [
    (1, "Stylish", lambda t: _map(t, STYLISH)),
    (2, "Coptic", lambda t: _map(t, COPTIC)),
    (3, "Mono Tj", _latin(MONO)),
    (4, "Sans Bold Italic", _latin(SANS_BOLD_ITALIC)),
    (5, "Small caps", lambda t: _map(translit(t).lower(), SMALL_CAPS)),
    (6, "Fraktur", _latin(FRAKTUR)),
    (7, "Double-struck", _latin(DOUBLE)),
    (8, "Script", _latin(SCRIPT)),
    (9, "Monospace", lambda t: _map(t, MONO)),
    (10, "Bold", _latin(BOLD)),
    (11, "Italic", _latin(ITALIC)),
    (12, "Bold Italic", _latin(BOLD_ITALIC)),
    (13, "Bold Script", _latin(BOLD_SCRIPT)),
    (14, "Fullwidth", _fullwidth),
    (15, "Sans Bold", _latin(SANS_BOLD)),
    # ----- 50 шрифти нав -----
    (16, "Bold Fraktur", _latin(BOLD_FRAKTUR)),
    (17, "Sans", _latin(SANS)),
    (18, "Sans Italic", _latin(SANS_ITALIC)),
    (19, "Circled", _latin(CIRCLED)),
    (20, "Negative Circled", _latin(NEG_CIRCLED)),
    (21, "Squared", _latin(SQUARED)),
    (22, "Negative Squared", _latin(NEG_SQUARED)),
    (23, "Parenthesized", _latin(PAREN)),
    (24, "Superscript", _latin(SUPER)),
    (25, "Upside Down", lambda t: _map(translit(t), UPSIDE)[::-1]),
    (26, "Underline", _marks("\u0332")),
    (27, "Double Underline", _marks("\u0333")),
    (28, "Strikethrough", _marks("\u0336")),
    (29, "Slash Through", _marks("\u0338")),
    (30, "Overline", _marks("\u0305")),
    (31, "Dot Below", _marks("\u0323")),
    (32, "Dot Above", _marks("\u0307")),
    (33, "Tilde Above", _marks("\u0303")),
    (34, "X Above", _marks("\u033d")),
    (35, "Tilde Below", _marks("\u0330")),
    (36, "Ring Above", _marks("\u030a")),
    (37, "Cross Below", _marks("\u0353")),
    (38, "Glitch", _glitch),
    (39, "Canadian", _latin(CANADIAN)),
    (40, "Currency", _latin(CURRENCY)),
    (41, "Greek", lambda t: _map(translit(t).lower(), GREEK)),
    (42, "Vaporwave", _vapor),
    (43, "Spaced", _join(" ", "   ")),
    (44, "Dotted", _join("·", " ")),
    (45, "Slashed", _join("/", " ")),
    (46, "Stars", _wrap("★ ", " ★")),
    (47, "Diamonds", _wrap("✦ ", " ✦")),
    (48, "Hearts", _wrap("♥ ", " ♥")),
    (49, "Flowers", _wrap("❖ ", " ❖")),
    (50, "Brackets", _wrap("『", "』")),
    (51, "Lenticular", _wrap("【", "】")),
    (52, "Tortoise", _wrap("〖", "〗")),
    (53, "Guillemets", _wrap("«", "»")),
    (54, "Arrow", _wrap("╰┈➤ ")),
    (55, "Sparkle", _wrap("☆彡 ", " 彡☆")),
    (56, "Dots", _wrap("·.·´¯`·.·• ", " •·.·´¯`·.·")),
    (57, "Bars", _wrap("▁▂▃ ", " ▃▂▁")),
    (58, "Waves", _wrap("≋ ", " ≋")),
    (59, "Sparkles", _wrap("⋆｡°✩ ", " ✩°｡⋆")),
    (60, "Crown", _wrap("♛ ", " ♛")),
    (61, "Quote", _wrap("❝ ", " ❞")),
    (62, "Shade", _wrap("░▒▓ ", " ▓▒░")),
    (63, "Bold Underline", _marks("\u0332", _latin(BOLD))),
    (64, "Fraktur Strike", _marks("\u0336", _latin(FRAKTUR))),
    (65, "Reversed", lambda t: t[::-1]),
]
_BY_ID = {f[0]: f for f in FONTS}

SAMPLE = "Салом алейкум 123"


def font_name(font_id: int) -> str:
    return _BY_ID[font_id][1] if font_id in _BY_ID else "—"


def apply_font(font_id: int, text: str) -> str:
    if font_id not in _BY_ID:
        return text
    return _BY_ID[font_id][2](text)
