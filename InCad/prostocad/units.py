"""Millimetres or inches, as in the old program: a file has its units from New on, everything inside stays in mm, only
what people read and type goes through here. A plain number is in the file's units, also inside a formula; "2 in" and
"10 mm" are taken in any file. Settings ▸ Dimensions ▸ Inches: four decimals, or fractions down to 1/32."""
import math
import re

INCH = 25.4
WORDS = {"mm": 1.0, "in": INCH, '"': INCH}
FINEST = 32  # the finest fraction shown: 1/32
SIXTEENTH = 1 / 16  # the step in inches, as in the old program

now = "mm"  # the open file's units
fractions = False  # Settings: inches as 1 1/2, else 1.5000

_COMMON = re.compile(r'(?<![\w.])(?:(\d+)\s+)?(\d+)/(\d+)(?:\s*(mm|in|")(?![A-Za-z0-9_]))?')
_NUMBER = re.compile(r'(?<![\w.])(\d+(?:[.,]\d*)?|[.,]\d+)\s*(mm|in|")(?![A-Za-z0-9_])')


def word():
    return now


def k():
    """Millimetres in one unit of the file."""
    return WORDS[now]


def shown(mm):
    return mm / k()


def to_mm(v):
    return v * k()


def edit(v):
    """A number in the file's units as a box holds it to be changed: never rounded to a fraction."""
    return "%.2f" % v if now == "mm" else "%.4f" % v


def number(v):
    """A number in the file's units as people read it."""
    if now == "mm" or not fractions:
        return edit(v)
    n = round(abs(v) * FINEST)
    if n == 0:
        return edit(v)  # too small for 1/32: a fraction would say nothing
    whole, rest = divmod(n, FINEST)
    tail = ""
    if rest:
        common = math.gcd(rest, FINEST)
        tail = "%d/%d" % (rest // common, FINEST // common)
    return ("-" if v < 0 else "") + " ".join(x for x in (str(whole) if whole else "", tail) if x)


def length(mm):
    """A length for a message: "12.50 mm" or "0.5000 in"."""
    return "%s %s" % (number(shown(mm)), now)


def short(mm, places=2):
    """A length in a sentence, no zeros at the end: "80 mm", "0.5 in"."""
    v = shown(mm)
    word_ = number(v) if now == "in" and fractions else "%g" % round(v, places if now == "mm" else 4)
    return "%s %s" % (word_, now)


def literals(text):
    """The text with every number that has its own unit, and every 1 3/16, as a plain number in the file's units:
    ready for a formula. A plain number stays what it is."""
    def common(m):
        whole, top, bottom, unit = m.groups()
        if int(bottom) == 0:
            return m.group(0)
        body = "(%s%s/%s)" % (whole + "+" if whole else "", top, bottom)
        return "(%s*%.10f)" % (body, WORDS[unit] / k()) if unit else body

    def plain(m):
        return "(%.10f)" % (float(m.group(1).replace(",", ".")) * WORDS[m.group(2)] / k())

    return _NUMBER.sub(plain, _COMMON.sub(common, text))


def step(mm_step):
    """A window's ↑ ↓ step: in inches 1/16, as in the old program; a fine one (print clearance) stays fine."""
    return mm_step if now == "mm" else (SIXTEENTH if mm_step >= .5 else .002)


def wheel(look):
    """Settings' Wheel Step: 0.1 and 0.01 in mm, 1/16 and 0.01 in inches."""
    return look if now == "mm" else (SIXTEENTH if look >= .1 else .01)
