"""15 услуби шрифт. Ҳамаро ин ҷо тағйир додан мумкин аст."""
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
# Шрифти 1 (Stylish): а -> ɑ, л -> ʌ, е -> є, рақамҳо ғафс (bold)
_STYLISH_LOWER = {"а": "ɑ", "л": "ʌ", "е": "є", "э": "є", "и": "і"}
STYLISH = dict(_STYLISH_LOWER)
STYLISH.update({k.upper(): v.upper() for k, v in _STYLISH_LOWER.items()})
for _i, _d in enumerate(DIG):
    STYLISH[_d] = chr(0x1D7CE + _i)

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
]
_BY_ID = {f[0]: f for f in FONTS}

SAMPLE = "Салом алейкум 123"


def font_name(font_id: int) -> str:
    return _BY_ID[font_id][1] if font_id in _BY_ID else "—"


def apply_font(font_id: int, text: str) -> str:
    if font_id not in _BY_ID:
        return text
    return _BY_ID[font_id][2](text)
