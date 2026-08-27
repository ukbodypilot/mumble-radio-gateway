"""Reading and writing values in `gateway_config.txt`.

Both halves live here so they cannot drift. `Config.load_config()` in
radio_gateway.py parses with `parse_config_value`; `_save_config` in
web_server.py serialises with `format_config_value`. When one side learns a new
rule the other has to agree, and a round trip has to be lossless — a config the
web form saves must read back as the same value it was given.

The bug that produced this module: `PACKET_APRS_SYMBOL` could not be set at
all. Its default '/#' -- the standard APRS digipeater symbol -- read back as
'/', because the reader treated every '#' as an inline comment and stripped
surrounding quotes BEFORE the comment split, so quoting could not protect it
either. Setting the key did something other than what it said, silently, and
'/' is itself a legal APRS table selector so nothing downstream complained.
"""


def parse_config_value(raw):
    """Return the value half of a config line, with any inline comment removed.

    Three rules, applied in this order:

    1. A QUOTED value is literal. Everything between the opening quote and the
       matching closing quote is the value, and anything after it is discarded.
       This is the only way to write a value that itself ends in a comment
       character, or that contains ' #'.
    2. Otherwise '#' begins a comment only when it is at the start of the value
       or preceded by whitespace. `foo # note` is a comment; `/#` is not.
    3. Text inside {braces} is exempt from rule 2 -- smart-announce prompts use
       '#' inside a brace expression.
    """
    value = raw.strip()

    # -- Rule 1: quoted values are literal --
    if len(value) >= 2 and value[0] in ('"', "'"):
        quote = value[0]
        close = value.find(quote, 1)
        if close != -1:
            return value[1:close]
        # Unterminated quote -- fall through and treat it as unquoted rather
        # than swallowing the rest of the line.

    # -- Rules 2 + 3: a comment starts at the beginning or after whitespace,
    #    and never inside braces --
    depth = 0
    for i, ch in enumerate(value):
        if ch == '{':
            depth += 1
        elif ch == '}':
            if depth:
                depth -= 1
        elif ch == '#' and depth == 0 and (i == 0 or value[i - 1].isspace()):
            value = value[:i]
            break

    return value.strip()


def format_config_value(val):
    """Render a value for `KEY = <value>`, quoting only when it must be.

    The test for "must be" is the reader itself: if `parse_config_value` would
    not give the value back unchanged, it needs quoting. That keeps this honest
    as the rules evolve -- there is no second copy of them to forget to update
    -- and leaves the other ~400 keys in the file unquoted and readable.

    A value containing both quote characters cannot be represented; it is
    returned unquoted rather than silently mangled, since that is the case a
    human needs to see and no config key here produces it.
    """
    text = str(val)
    if parse_config_value(text) == text:
        return text
    for quote in ('"', "'"):
        if quote not in text:
            return f'{quote}{text}{quote}'
    return text
