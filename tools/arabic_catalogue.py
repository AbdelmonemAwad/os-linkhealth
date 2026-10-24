#!/usr/local/bin/python3
#-
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Abdelmonem Awad <eg2@live.com>
#
"""The Arabic of this plugin's strings, read from opnsense-arabic's catalogue.

Since 2026-10-22 (opnsense-arabic issue 5) the Arabic of every plugin's GUI is in one place,
opnsense-arabic's ar_SA.po, and this plugin ships none of its own. The checks read it from a
checkout of that repository when ARABIC_PO names its ar_SA.po; on an appliance, from the compiled
catalogue opnsense-arabic installed, which is the Arabic the page is actually shown in; else from
its main branch on GitHub - so a string added here needs its Arabic there first.

The appliance's own copy comes before the network because the installer runs the check: an
appliance's first boot from an image has no network yet, and the fetch printed a traceback there
(an XGS 126's first boot, 2026-10-24).

    catalogue() -> {msgid: msgstr}, the entries without a context
"""
import gettext
import os
import re
import urllib.request

ARABIC_PO = os.environ.get('ARABIC_PO', '')
# where opnsense-arabic's install.sh keeps the compiled catalogue, and where it puts it for the GUI
ARABIC_MO = ('/usr/local/share/opnsense-arabic/OPNsense.mo',
             '/usr/local/share/locale/ar_SA/LC_MESSAGES/OPNsense.mo')
ARABIC_URL = 'https://raw.githubusercontent.com/AbdelmonemAwad/opnsense-arabic/main/ar_SA.po'


def installed():
    """The installed catalogue's entries without a context, or None when there is none."""
    for path in ARABIC_MO:
        if os.path.isfile(path):
            with open(path, 'rb') as handle:
                found = gettext.GNUTranslations(handle)._catalog
            return {k: v for k, v in found.items()
                    if isinstance(k, str) and k and '\x04' not in k and isinstance(v, str)}
    return None


def unquote(s):
    return re.sub(r'\\(.)', lambda m: {'n': '\n', 't': '\t'}.get(m.group(1), m.group(1)), s)


def catalogue():
    if ARABIC_PO:
        with open(ARABIC_PO, encoding='utf-8') as handle:
            text = handle.read()
    else:
        found = installed()
        if found is not None:
            return found
        with urllib.request.urlopen(ARABIC_URL, timeout=20) as response:
            text = response.read().decode('utf-8')
    entries, cur, key = {}, {}, None

    def flush():
        if 'msgid' in cur and 'msgstr' in cur and 'msgctxt' not in cur:
            entries[cur['msgid']] = cur['msgstr']

    for line in text.splitlines():
        line = line.strip()
        m = re.match(r'^(msgctxt|msgid|msgid_plural|msgstr|msgstr\[\d+\]) "(.*)"$', line)
        if m:
            word = m.group(1)
            if word in ('msgctxt', 'msgid') and 'msgstr' in cur:
                flush()
                cur = {}
            key = 'msgstr' if word == 'msgstr[0]' else (None if word.startswith('msgstr[') else word)
            if key:
                cur[key] = unquote(m.group(2))
        elif line.startswith('"'):
            if key:
                cur[key] += unquote(line[1:-1])
        else:
            key = None
            if not line and 'msgstr' in cur:
                flush()
                cur = {}
    flush()
    return entries
