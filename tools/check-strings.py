#!/usr/local/bin/python3
"""Every string the page asks the catalogue for, against every string the catalogue holds.

A msgid that reaches lang._() without being in source.json is shown in English on an
otherwise Arabic page, and nothing in the build would have said so.
"""
import glob
import json
import re

BS = chr(92)
Q = chr(39)

# lang._('...') with backslash escapes inside, the way Volt writes them
PATTERN = re.compile("lang" + BS + "._" + BS + "(" + BS + "s*" + Q
                     + "((?:[^" + Q + BS + BS + "]|" + BS + BS + ".)*)" + Q
                     + BS + "s*" + BS + ")")

used = set()
for path in sorted(glob.glob('src/opnsense/mvc/app/views/OPNsense/*/*.volt')):
    for raw in PATTERN.findall(open(path, encoding='utf-8').read()):
        used.add(raw.replace(BS + Q, Q).replace(BS + BS, BS))

base = 'src/opnsense/scripts/'
name = sorted(glob.glob(base + '*/i18n/ui/source.json'))[0]
src = set(json.load(open(name, encoding='utf-8')))
ar = json.load(open(name.replace('source.json', 'ar_SA.json'), encoding='utf-8'))

absent = sorted(used - src)
stale = sorted(src - used)
missing = sorted(k for k in src if k not in ar)

print('in templates: %d | catalogued: %d | translated: %d' % (len(used), len(src), len(ar)))
print('used but not catalogued: %d' % len(absent))
for key in absent[:25]:
    print('   + ' + key[:100])
print('catalogued but unused: %d' % len(stale))
for key in stale[:25]:
    print('   - ' + key[:100])
print('catalogued but untranslated: %d' % len(missing))
for key in missing[:25]:
    print('   ? ' + key[:100])
