"""Advent faces and glyph rows.

Adding a paper changes this module only. The repair logic lives elsewhere.
A glyph row still wins over either list.
"""

# These Adv names are text. A small one keeps the character the map claims.
# A glyph row still overrides that.
_TEXT_FONTS = frozenset(
    {
        # Biology 2014, Genetics 2008, ONE 2014. Letters and punctuation.
        "AdvP4041BD",
        # Biology 2014 and ONE 2014. Digits and punctuation, and letters in ONE.
        "AdvP497E2",
        # Biology 2014 and ONE 2014. Letters.
        "AdvP497E3",
        # Client 062. The hyphen in "Extant-only".
        "AdvTimes",
        # Client 062. The digits in "Biology 34".
        "AdvPSHN-MI",
        # Client 074. Bold italic letters.
        "AdvOT45bf69de.BI",
        # Client 135. Bold italic letters.
        "AdvTT3978f921.BI",
        # Client 170. Bold italic letters.
        "AdvOTd72d62f3.BI",
        # Biology 2014, Genetics 2008, ONE 2014. Body text. The currency
        # glyph is a circled times and has its own row.
        "AdvP403A40",
        "AdvP41461E",
    }
)

# None drops the glyph. Each comment names the paper where that glyph is used.
# Costa 2013 is Environ Sci Pollut Res 20:5887-5912 and is not a fixture, and
# neither is NAR 2007. Client numbers are the training PDFs, also not fixtures.
# Tests re-check the glyphs in the CC BY fixtures. Other rows are not copied
# into tests. A rule test may use one of them as its example.
_ROWS: dict[tuple[str, str], str | None] = {
    # Biology 2014, Genetics 2008.
    ("AdvP7DA6", "numbersign"): "\u2264",
    # Biology 2014, Genetics 2008, ONE 2014.
    ("AdvP7DA6", "comma"): "<",
    # Biology 2014, Genetics 2008, ONE 2014.
    ("AdvP7DA6", "period"): ">",
    # Biology 2014, Genetics 2008, ONE 2014.
    ("AdvP7DA6", "two"): "\u2212",
    # ONE 2014. The drawing is the dot in "Kg·day".
    ("AdvP7DA6", "question"): "\u00b7",
    # Biology 2014, Genetics 2008, ONE 2014.
    ("AdvP3D3256", "six"): "\u00b1",
    # ONE 2014. The drawing is the closing quote after "mean ±".
    ("AdvP3D3256", "hyphen"): "\u201d",
    # Biology 2014, Genetics 2008, ONE 2014.
    ("AdvPi1", "six"): "\u00d7",
    # ONE 2014. The drawing is the degree sign in "17°C".
    ("AdvP4072C", "six"): "\u00b0",
    # Genetics 2008, ONE 2014.
    ("AdvP44E6F4", "u"): "\u00b0",
    # ONE 2014.
    ("AdvP4C9F3E", "plusminus"): "\u00b1",
    # Biology 2014, Genetics 2008, ONE 2014, NAR 2007.
    ("AdvP697C", "plus"): "+",
    # ONE 2014.
    ("AdvP697C", "m"): "\u00b5",
    # NAR 2007. The drawing is beta in "β cells".
    ("AdvP697C", "b"): "\u03b2",
    # NAR 2007. The drawing is the copyright sign before the year.
    ("AdvP697C", "C211"): "\u00a9",
    # Biology 2014, Genetics 2008.
    ("AdvP7DB7", "comma"): "\u223c",
    # ONE 2014. The drawing is the radical in "arcsin √x".
    ("AdvP7DB7", "exclam"): "\u221a",
    # Biology 2014, ONE 2014.
    ("AdvP40798", "asciitilde"): "=",
    # Biology 2014, ONE 2014.
    ("AdvP40798", "braceleft"): "\u2212",
    # Biology 2014, ONE 2014.
    ("AdvP40798", "z"): "+",
    # Biology 2014.
    ("AdvP40798", "quotedbl"): "\u00b6",
    # ONE 2014.
    ("AdvP40798", "bar"): "\u00d7",
    # Biology 2014, ONE 2014.
    ("AdvP7CAC", "R"): "\u2192",
    # Biology 2014, Genetics 2008, ONE 2014.
    ("AdvT377", "C223"): "\u00a9",
    # Biology 2014, Genetics 2008.
    ("AdvP3F206A", "period"): "\u2609",
    # Biology 2014, Genetics 2008. The drawing is the circled cross that marks
    # a current address, beside the author and again before the note.
    ("AdvP41461E", "currency"): "\u2297",
    ("AdvP403A40", "currency"): "\u2297",
    # ONE 2014.
    ("AdvP3EAA99", "a"): "\u03b1",
    # Genetics 2008.
    ("AdvP3EAA99", "b"): "\u03b2",
    # Genetics 2008, ONE 2014.
    ("AdvP3EAA99", "c"): "\u03b3",
    # Biology 2014.
    ("AdvP3EAA99", "d"): "\u03b4",
    # Genetics 2008.
    ("AdvP3EAA99", "m"): "\u00b5",
    # Genetics 2008.
    ("AdvP3EAA99", "n"): "\u03bd",
    # Biology 2014.
    ("AdvP3EAA99", "x"): "\u03c7",
    # Biology 2014.
    ("AdvP43B38B", "m"): "\u00b5",
    # Biology 2014.
    ("AdvP43B38B", "p"): "\u03c0",
    # ONE 2014.
    ("AdvP43A417", "a"): "\u03b1",
    # ONE 2014.
    ("AdvP43A417", "b"): "\u03b2",
    # Genetics 2008.
    ("AdvP43A417", "m"): "\u00b5",
    # Genetics 2008.
    ("AdvP43A417", "n"): "\u03bd",
    # Biology 2014, Costa 2013.
    ("AdvP4C4E74", "p"): "\u221a",
    # Costa 2013. Not a fixture.
    ("AdvP4C4E74", "onequarter"): "=",
    # Costa 2013. Not a fixture.
    ("AdvP4C4E74", "thorn"): "+",
    # Costa 2013. Not a fixture.
    ("AdvP4C4E74", "eth"): "(",
    # Costa 2013. Not a fixture.
    ("AdvP4C4E74", "Thorn"): ")",
    # NAR 2007. Not a fixture. The drawing is the prime in "D′".
    ("AdvP4C4E74", "zero"): "\u2032",
    # Costa 2013. Not a fixture. The drawing is "(" in the moment formula.
    ("AdvP4C4E46", "C0"): "(",
    # Costa 2013. Not a fixture. The drawing is ")" in the moment formula.
    ("AdvP4C4E46", "C1"): ")",
    # Costa 2013. Not a fixture. The drawing is the radical before "Area".
    ("AdvP4C4E46", "q"): "\u221a",
    # Biology 2014, Costa 2013. Dropped. This is the bar over a square root.
    ("AdvP4C4E46", "ffi"): None,
    # Costa 2013. Not a fixture. The drawing is a bullet, not an ampersand.
    ("AdvP0005", "ampersand"): "\u2022",
    # ONE 2014, Costa 2013.
    ("AdvP4C4E51", "equal"): "/",
    # ONE 2014, Costa 2013.
    ("AdvP4C4E59", "percent"): "%",
    # ONE 2014. The drawing is the dot over the z in "Strzeżek".
    ("AdvP4C4E59", "underscore"): "\u0307",
    # ONE 2014. The drawing is the dot in "O₂⁻·".
    ("AdvP7CA8", "N"): "\u00b7",
    # Costa 2013. Not a fixture. The drawing is the copyright sign.
    ("AdvPi3", "numbersign"): "\u00a9",
    # Costa 2013. Not a fixture. The drawing is the corresponding-author envelope.
    ("AdvTT4ff65459", "asterisk"): "\u2709",
    # Client 008. The drawing is the en dash in a page range.
    ("AdvPS44A44B", "e"): "\u2013",
    # Client 001 and 008. The drawing is the copyright sign.
    ("AdvPSSym", "C211"): "\u00a9",
    # Client 062. The drawing is the copyright sign.
    ("AdvPSSym", "ordfeminine"): "\u00a9",
    # Client 001 and 109. The drawing is the author asterisk.
    ("AdvCORRESAST", "arrowdblup"): "*",
    # Client 008. The drawing is the corresponding-author asterisk.
    ("AdvP4C4E59", "asterisk"): "*",
    # Client 008. The drawing is the tilde in "~50 mm".
    ("AdvPS3FDD77", "w"): "\u223c",
    # Client 008. The drawing is alpha.
    ("AdvPS3F4C13", "a"): "\u03b1",
    # Client 062. The drawing is capital delta.
    ("AdvPS3F4C13", "D"): "\u0394",
    # Client 062. The drawing is mu.
    ("AdvPS3F4C13", "m"): "\u03bc",
    # Client 062. The drawing is sigma.
    ("AdvPS3F4C13", "s"): "\u03c3",
    # Client 008. The drawing is greater-than.
    ("AdvP4C4E51", "greater"): ">",
    # Client 008 and 063. The drawing is less-than.
    ("AdvP4C4E51", "less"): "<",
    # Client 048. The drawing is a dot on the baseline.
    ("AdvP4C4E51", "colon"): ".",
    # Client 008. The drawing is plus-minus.
    ("AdvP4C4E74", "C6"): "\u00b1",
    # Client 008. The drawing is the dagger after a table note.
    ("AdvP4C4E74", "y"): "\u2020",
    # Client 062. The drawing is the minus before a negative number.
    ("AdvP4C4E74", "C0"): "\u2212",
    # Client 048. The drawing is the multiplication sign.
    ("AdvP4C4E74", "C2"): "\u00d7",
    # Client 048, 113, and 146. The drawing is an asterisk.
    ("AdvP4C4E74", "C3"): "*",
    # Client 135 and 146. The drawing is the degree sign.
    ("AdvP4C4E74", "C14"): "\u00b0",
    # Client 135. The drawing is a bullet.
    ("AdvP4C4E74", "C15"): "\u2022",
    # Client 063. The drawing is a tilde.
    ("AdvP4C4E74", "C24"): "\u223c",
    # Client 048. The drawing is the vertical bar in the formula.
    ("AdvP4C4E74", "j"): "|",
    # Client 048. The drawing is the summation sign.
    ("AdvP4C4E46", "P"): "\u2211",
    # Client 062, 063, and 146. The acute follows its letter.
    ("AdvP4C4E59", "C19"): "\u0301",
    # Client 062. The drawing is a list bullet.
    ("AdvPSMPi6", "d"): "\u2022",
    # Client 062. The drawing is a filled star.
    ("AdvPS480274", "plus"): "\u2605",
    # Client 062. The drawing is a hollow circle.
    ("AdvPS480274", "B"): "\u25cb",
    # Client 063. The drawing is the registered sign.
    ("AdvP80516", "V"): "\u00ae",
    # Client 113. The drawing is the multiplication sign.
    ("AdvTT9f665322", "multiply"): "\u00d7",
    # Client 146. The drawing is beta.
    ("AdvPSMP13", "b"): "\u03b2",
    # Client 063. The drawing is capital gamma.
    ("AdvPSMP13", "C"): "\u0393",
    # Client 063. The drawing is mu.
    ("AdvPSMP13", "l"): "\u03bc",
    # Client 146. The drawing is the minus before a negative number.
    ("AdvP7DED", "two"): "\u2212",
    # Client 146. The drawing is the equals sign in "n = 51".
    ("AdvP7DED", "five"): "=",
    # Client 147. The drawing is the degree sign in "n°".
    ("AdvOT310e2d14", "degree"): "\u00b0",
    # Client 063 and 146. The two dots of ä and ö are stored before the letter.
    ("AdvP4C4E59", "Euro"): None,
    # Client 063 and 146. The circumflex of â is stored before the letter.
    ("AdvP4C4E59", "asciicircum"): None,
    # Client 063 and 146. The tilde of ñ is stored before the letter.
    ("AdvP4C4E59", "asciitilde"): None,
    # Client 063. The grave of à is stored before the letter.
    ("AdvP4C4E59", "C18"): None,
    # Client 146. The caron of š is stored before the letter.
    ("AdvP4C4E59", "C20"): None,
    # Client 001. The cedilla of ş is stored before the letter.
    ("AdvSPSASORT", "C223"): None,
    # Client 048. The boxes are empty.
    ("AdvP4C4E46", "C18"): None,
    ("AdvP4C4E46", "C19"): None,
}

# A face with a row is a symbol face, unless it is on the text list.
# An unlisted glyph in one of these faces becomes U+FFFD at any size.
# The size cutoff applies only to an Adv name that is in neither list.
_SYMBOL_FONTS = frozenset(base for base, _name in _ROWS if base not in _TEXT_FONTS)
