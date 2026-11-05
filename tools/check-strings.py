#!/usr/local/bin/python3
#-
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Abdelmonem Awad <eg2@live.com>
#
"""Every string the page asks the catalogue for, against every string the catalogue holds.

A msgid that reaches lang._() without being in source.json is shown in English on an
otherwise Arabic page, and nothing in the build would have said so.

The mail is checked here too, and for the same reason. alerts.py reads the same
catalogue through gettext, which means its sentences can be wrong in two new ways: a
msgid it asks for that nobody translated, and a sentence it shares with the port grid
that the page has since reworded. The second is the one worth a build failure - an
alert and the page disagreeing about the same port is a bug, and it is invisible until
somebody with an Arabic GUI reads an alert about a port the page is describing
differently in the next window.

The Arabic is in opnsense-arabic's catalogue since 2026-10-22 (tools/arabic_catalogue.py), and a
catalogued string with no Arabic there fails - except on a contributor's pull request and on main,
where its Arabic may follow later and it is a warning (ARABIC_LATER, below).
"""
import glob
import json
import os
import re
import sys

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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arabic_catalogue                                                  # noqa: E402

# A contributor who does not write Arabic leaves it out (CONTRIBUTING.md). On a pull request from a
# fork and on main, checks.yml sets ARABIC_LATER=1: a string without Arabic is then a warning on the
# pull request and not a failure, and opnsense-arabic's "Strings waiting for Arabic" lists it until
# its Arabic is there. A pull request from a branch of this repository still fails on one.
LATER = os.environ.get('ARABIC_LATER') == '1'


def later(keys):
    """Each string without Arabic as a warning GitHub shows on the pull request, and in its summary."""
    if not LATER:
        return
    for key in keys:
        print('::warning title=Arabic to follow::' +
              key.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A'))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if keys and summary:
        with open(summary, 'a', encoding='utf-8') as handle:
            handle.write('### Strings waiting for Arabic\n\n' + ''.join('- %s\n' % key for key in keys))


try:
    ar = arabic_catalogue.catalogue()
except (OSError, ValueError) as exc:
    print("opnsense-arabic's catalogue cannot be read: %s" % exc)
    sys.exit(1)

# The mail's msgids come from alerts.py itself rather than from a copy kept here, so
# there is no second list to forget. It imports nothing outside the standard library,
# which is why this can import it on a build runner with no OPNsense on it.
mail, shared = set(), set()
scripts = os.path.dirname(os.path.dirname(os.path.dirname(name)))   # .../<plugin>/i18n/ui -> .../<plugin>
if os.path.exists(os.path.join(scripts, 'alerts.py')):
    sys.path.insert(0, scripts)
    import alerts                                                        # noqa: E402
    mail = set(alerts.mail_strings())
    shared = set(alerts.page_strings())

absent = sorted(used - src)
stale = sorted(src - used - mail)
missing = sorted(k for k in src if not ar.get(k))
uncatalogued = sorted(mail - src)
drifted = sorted(shared - used)

print('in templates: %d | in the mail: %d | catalogued: %d | translated in opnsense-arabic: %d'
      % (len(used), len(mail), len(src), len(src) - len(missing)))
print('used but not catalogued: %d' % len(absent))
for key in absent[:25]:
    print('   + ' + key[:100])
print('catalogued but unused: %d' % len(stale))
for key in stale[:25]:
    print('   - ' + key[:100])
print('catalogued but with no Arabic in opnsense-arabic: %d' % len(missing))
for key in missing[:25]:
    print('   ? ' + key[:100])
later(missing)

print('asked for by the mail but not catalogued: %d' % len(uncatalogued))
for key in uncatalogued:
    print('   + ' + key[:100])
print('the mail says this and the page no longer does: %d' % len(drifted))
for key in drifted:
    print('   ! ' + key[:100])

# The two faults that make a message lie are fatal, and so is a catalogued string with no
# Arabic - a step of the workflow enforced that one until 2026-10-22 - unless ARABIC_LATER is
# set, where it is a warning. The other two counts are reported and not enforced, as they
# always were: the form XML and the PHP say plenty that no .volt asks for, and that is not a bug.
sys.exit(1 if uncatalogued or drifted or (missing and not LATER) else 0)
