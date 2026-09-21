#!/usr/local/bin/python3

"""
Copyright (C) 2026 Abdelmonem Awad <eg2@live.com>
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright
   notice, this list of conditions and the following disclaimer in the
   documentation and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.

Add the Link Health GUI strings (i18n/ui/<locale>.json) to the OPNsense gettext
catalog of every installed GUI language. Strings the catalog already translates are left
alone, so running it again is harmless. Core updates replace the catalogs, which is why
this also runs at boot. Prints the number of strings added; exit code 0 either way.
"""

import glob
import json
import os
import struct
import sys

LOCALE_DIR = '/usr/local/share/locale'
UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'i18n', 'ui')


def read_mo(path):
    """msgid bytes -> msgstr bytes (plural and context entries kept as raw bytes)"""
    data = open(path, 'rb').read()
    magic = struct.unpack('<I', data[:4])[0]
    endian = '<' if magic == 0x950412de else '>'
    _, count, orig_off, trans_off = struct.unpack(endian + '4I', data[4:20])
    entries = {}
    for i in range(count):
        olen, opos = struct.unpack(endian + '2I', data[orig_off + 8 * i:orig_off + 8 * i + 8])
        tlen, tpos = struct.unpack(endian + '2I', data[trans_off + 8 * i:trans_off + 8 * i + 8])
        entries[data[opos:opos + olen]] = data[tpos:tpos + tlen]
    return entries


def write_mo(path, entries):
    """standard little-endian .mo without a hash table (gettext then uses binary search)"""
    keys = sorted(entries)
    count = len(keys)
    orig_off = 28
    trans_off = orig_off + 8 * count
    data_off = trans_off + 8 * count
    ids = strs = b''
    orig_tab, trans_tab = [], []
    for k in keys:
        orig_tab.append((len(k), len(ids)))
        ids += k + b'\0'
    for k in keys:
        trans_tab.append((len(entries[k]), len(strs)))
        strs += entries[k] + b'\0'
    out = struct.pack('<7I', 0x950412de, 0, count, orig_off, trans_off, 0, data_off)
    for length, pos in orig_tab:
        out += struct.pack('<2I', length, data_off + pos)
    for length, pos in trans_tab:
        out += struct.pack('<2I', length, data_off + len(ids) + pos)
    out += ids + strs
    # A temporary name of our own. os-netreport ships the same merge and runs it
    # from its own boot hook; two plugins writing one temp path would trade
    # halves of a catalogue the moment they ever ran together.
    tmp = path + '.linkhealth.tmp'
    with open(tmp, 'wb') as fh:
        fh.write(out)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


OWNED = '/var/db/linkhealth/i18n-owned.json'


def owned():
    """The msgids this plugin has written into the catalogues itself.

    A catalogue holds core's translations and ours in one file, and the rule
    "never touch a string that is already translated" is what keeps this from
    rewording the whole GUI: 26 of this plugin's strings are core's words -
    Port, Settings, Save, Cancel - and core's Arabic must win every time.

    But that rule also froze OUR OWN strings: correcting a word here would
    never reach the catalogue, because the catalogue already had the old one.
    So the script remembers what it wrote. Anything in this list is ours to
    correct; anything else is left exactly as it was found.
    """
    try:
        with open(OWNED, 'r') as handle:
            return {locale: set(ids) for locale, ids in json.load(handle).items()}
    except (OSError, ValueError):
        return {}


def remember(state):
    try:
        os.makedirs(os.path.dirname(OWNED), mode=0o750, exist_ok=True)
        with open(OWNED, 'w') as handle:
            json.dump({locale: sorted(ids) for locale, ids in state.items()}, handle)
        os.chmod(OWNED, 0o640)
    except OSError:
        pass          # the merge itself succeeded; the bookkeeping is a nicety


def main():
    force = '--force' in sys.argv
    state = owned()
    added_total = 0
    for src in sorted(glob.glob(os.path.join(UI_DIR, '*_*.json'))):
        locale = os.path.basename(src)[:-5]
        mo = os.path.join(LOCALE_DIR, locale, 'LC_MESSAGES', 'OPNsense.mo')
        if not os.path.exists(mo):
            continue
        try:
            strings = json.load(open(src, encoding='utf-8'))
            entries = read_mo(mo)
        except Exception as exc:
            print(f'{locale}: skipped ({exc})', file=sys.stderr)
            continue
        mine = state.setdefault(locale, set())
        added = 0
        for msgid, msgstr in strings.items():
            if not msgstr:
                continue
            key = msgid.encode('utf-8')
            current = entries.get(key)
            if not current:
                entries[key] = msgstr.encode('utf-8')
                mine.add(msgid)
                added += 1
            elif force and msgid in mine and current != msgstr.encode('utf-8'):
                entries[key] = msgstr.encode('utf-8')
                added += 1
        if added:
            write_mo(mo, entries)
            read_mo(mo)          # sanity check: the result must parse
        added_total += added
    remember(state)
    print(added_total)


if __name__ == '__main__':
    main()
