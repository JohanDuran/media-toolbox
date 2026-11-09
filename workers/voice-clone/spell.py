"""Spell numbers out before voice cloning.

Voice-clone models misread digits: in the 2026-11 bench Chatterbox said "7.32%" as "seven thirty-two percent" and
Fish S2 Pro turned "$9,800" into "nine thousand". With the numbers written as words, every take was word-perfect.

  $43,300      -> forty-three thousand, three hundred dollars
  $9.99        -> nine dollars and ninety-nine cents
  $1.5B        -> one point five billion dollars
  7.32%        -> seven point three two percent
  3rd          -> third
  2026         -> twenty twenty-six (4-digit numbers 1100-2099 read as years)
  12,500 / 3.5 -> twelve thousand, five hundred / three point five
"""

from __future__ import annotations

import re

from num2words import num2words

NUM = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
CURRENCY = {"$": ("dollar", "dollars", "cent", "cents"), "€": ("euro", "euros", "cent", "cents"),
            "£": ("pound", "pounds", "penny", "pence")}
SCALE = {"k": "thousand", "thousand": "thousand", "m": "million", "mm": "million", "million": "million",
         "b": "billion", "bn": "billion", "billion": "billion", "t": "trillion", "tn": "trillion", "trillion": "trillion"}
_SCALE_RE = r"(?:\s?(k|m|mm|bn|b|tn|t)\b|\s(thousand|million|billion|trillion)\b)"


def number(s: str) -> str:
    """'43,300' -> 'forty-three thousand, three hundred'; '7.32' -> 'seven point three two'."""
    whole, _, frac = s.replace(",", "").partition(".")
    words = num2words(int(whole or 0))
    if frac:
        words += " point " + " ".join(num2words(int(d)) for d in frac)
    return words


def _money(m: re.Match) -> str:
    one, many, cent, cents = CURRENCY[m.group(1)]
    amount, scale = m.group(2), (m.group(3) or m.group(4) or "").lower()
    if scale:
        return f"{number(amount)} {SCALE[scale]} {many}"
    whole, _, frac = amount.replace(",", "").partition(".")
    w = int(whole or 0)
    out = f"{num2words(w)} {one if w == 1 else many}"
    if frac and len(frac) == 2 and int(frac):
        c = int(frac)
        out += f" and {num2words(c)} {cent if c == 1 else cents}"
    elif frac and len(frac) != 2:  # e.g. $0.005: read it as a decimal
        out = f"{number(amount)} {many}"
    return out


def spell_numbers(text: str) -> str:
    text = re.sub(rf"([$€£])\s?({NUM}){_SCALE_RE}?", _money, text, flags=re.I)
    text = re.sub(rf"({NUM})\s?%", lambda m: f"{number(m.group(1))} percent", text)
    text = re.sub(r"\b(\d+)(st|nd|rd|th)\b", lambda m: num2words(int(m.group(1)), to="ordinal"), text)
    text = re.sub(r"(?<![\d.,])(1[1-9]\d\d|20\d\d)(?!\d|[.,]\d)", lambda m: num2words(int(m.group(1)), to="year"), text)
    text = re.sub(rf"(?<![\w.]){NUM}(?![\w])", lambda m: number(m.group(0)), text)
    return text


if __name__ == "__main__":  # quick self-check: python spell.py
    cases = {
        "about $43,300.": "about forty-three thousand, three hundred dollars.",
        "at 7.32%,": "at seven point three two percent,",
        "$9.99": "nine dollars and ninety-nine cents",
        "$1 bill": "one dollar bill",
        "$1.5B deal": "one point five billion dollars deal",
        "$2 million": "two million dollars",
        "the 3rd time": "the third time",
        "in 2026": "in twenty twenty-six",
        "In 2026, the": "In twenty twenty-six, the",
        "by 2030.": "by twenty thirty.",
        "about $2,400 a year": "about two thousand, four hundred dollars a year",
        "2,026 cars": "two thousand and twenty-six cars",
        "12,500 people": "twelve thousand, five hundred people",
        "3.5 years": "three point five years",
    }
    bad = {k: (spell_numbers(k), v) for k, v in cases.items() if spell_numbers(k) != v}
    for k, (got, want) in bad.items():
        print(f"FAIL {k!r}: got {got!r}, want {want!r}")
    print("ok" if not bad else f"{len(bad)} failed")
