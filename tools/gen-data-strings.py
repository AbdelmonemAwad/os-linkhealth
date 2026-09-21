#!/usr/local/bin/python3
"""Regenerate the page's map of translatable DATA strings.

The page translates through lang._() like every other part of OPNsense, and lang._()
resolves at template-compile time against a literal. That is fine for the page's own
sentences and impossible for text that lives in a data file, so index.volt carries a map
from the English in the data files to the same English passed through lang._(). Every
entry has to be listed, which means a note added to chassis.json or faceplates.json comes
out in English on an otherwise Arabic page - and nothing says so.

This closes that hole: the map is built from the data files rather than remembered, and
any string it finds that the catalogue does not know is reported by name. Run it after
touching any data file; the installer runs it too.

    tools/gen-data-strings.py [--check]

--check changes nothing and exits 1 if the map or the catalogue is out of date, which is
what a build should run.
"""
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(HERE, 'src', 'opnsense', 'scripts', 'linkhealth')
VIEW = os.path.join(HERE, 'src', 'opnsense', 'mvc', 'app', 'views',
                    'OPNsense', 'LinkHealth', 'index.volt')

BEGIN = '        const lh_data_text = {'
END = '        };'

# The fields in the data files that end up in front of a reader. A field not listed here
# is either an identifier the page never prints (driver, units, verify) or a name that is
# not ours to translate (a vendor's model string).
TEXT_FIELDS = ('bay', 'note', 'identify_led_note', 'confirmed_note', 'hint')


def harvest():
    """Every reader-facing string in the data files, in a stable order."""
    found = []
    seen = set()

    def walk(node):
        if isinstance(node, dict):
            for field in TEXT_FIELDS:
                value = node.get(field)
                if isinstance(value, str) and value.strip() and value not in seen:
                    seen.add(value)
                    found.append(value)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for name in sorted(glob.glob(os.path.join(SCRIPTS, '*.json'))):
        if os.path.basename(name) in ('thresholds.json',):
            continue
        if os.sep + 'i18n' + os.sep in name:
            continue
        with open(name, encoding='utf-8') as handle:
            walk(json.load(handle))
    return found


def as_js(value):
    """A JS single-quoted literal, and the Volt call that translates it.

    Volt ends a single-quoted string at the first unescaped quote, so an apostrophe has to
    arrive as backslash-apostrophe in BOTH places - and as exactly one backslash. Two is
    what broke this page once already: Volt read the first as an escaped backslash and the
    string ended in the middle of a sentence.
    """
    escaped = value.replace(chr(92), chr(92) * 2).replace(chr(39), chr(92) + chr(39))
    return escaped


def build_block(strings):
    lines = [
        '        /* Translatable text that lives in the DATA files, not in this page: bay names,',
        '           the notes beside them, and the sentence a chassis table uses to say that its',
        '           sockets have no light. lang._() resolves against a literal at compile time, so',
        '           each one is listed as its own key - and lh_data_text is GENERATED, by',
        '           tools/gen-data-strings.py, because a note added to chassis.json and forgotten',
        '           here comes out in English on an Arabic page with nothing to say so. */',
        BEGIN,
    ]
    for value in strings:
        literal = as_js(value)
        entry = "            '%s': \"{{ lang._('%s') }}\"," % (literal, literal)
        if len(entry) > 100:
            lines.append("            '%s':" % literal)
            lines.append("                \"{{ lang._('%s') }}\"," % literal)
        else:
            lines.append(entry)
    if len(lines) > 7:
        lines[-1] = lines[-1].rstrip(',')
    lines.append(END)
    return '\n'.join(lines)


def replace_block(text, block):
    start = text.index(BEGIN)
    # back up over the comment that introduces it, if one is there
    head = text.rfind('\n        /*', 0, start)
    if head != -1 and 'lh_data_text' in text[head:start]:
        start = head + 1
    finish = text.index('\n' + END, text.index(BEGIN)) + len('\n' + END)
    return text[:start] + block + text[finish:]


def main():
    check = '--check' in sys.argv
    strings = harvest()
    block = build_block(strings)

    with open(VIEW, encoding='utf-8') as handle:
        text = handle.read()
    updated = replace_block(text, block)

    catalogue = os.path.join(SCRIPTS, 'i18n', 'ui')
    with open(os.path.join(catalogue, 'source.json'), encoding='utf-8') as handle:
        source = json.load(handle)
    with open(os.path.join(catalogue, 'ar_SA.json'), encoding='utf-8') as handle:
        arabic = json.load(handle)

    missing = [s for s in strings if s not in source]
    untranslated = [s for s in strings if s not in arabic]

    if check:
        problems = 0
        if updated != text:
            print('the data-string map in index.volt is out of date')
            problems += 1
        for value in missing:
            print('not in source.json: %s' % value[:80])
            problems += 1
        for value in untranslated:
            print('no Arabic: %s' % value[:80])
            problems += 1
        if problems == 0:
            print('data strings: %d, all mapped and translated' % len(strings))
        return 1 if problems else 0

    if updated != text:
        with open(VIEW, 'w', encoding='utf-8') as handle:
            handle.write(updated)
        print('map rewritten: %d data strings' % len(strings))
    else:
        print('map already current: %d data strings' % len(strings))

    for value in missing:
        source.append(value)
    if missing:
        with open(os.path.join(catalogue, 'source.json'), 'w', encoding='utf-8') as handle:
            json.dump(source, handle, ensure_ascii=False, indent=1)
        print('added to source.json: %d' % len(missing))
    for value in untranslated:
        print('NEEDS ARABIC: %s' % value[:90])
    return 0


if __name__ == '__main__':
    sys.exit(main())
