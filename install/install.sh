#!/bin/sh
# Copyright (C) 2026 Abdelmonem Awad <eg2@live.com>. BSD 2-Clause License.
#
# Install the Link Health plugin from a copy of this repository on the firewall:
#   sh install/install.sh
# Safe to run again after an update: the settings in config.xml and the counter
# baseline under /var/db/linkhealth are kept, so the next cycle still has something
# to compare against.

set -e
HERE=$(cd "$(dirname "$0")/.." && pwd)
CONFIGPHP=/usr/local/opnsense/mvc/app/config/config.php

# plugin files: src/ maps to /usr/local
cp -R "${HERE}/src/" /usr/local/

# the collector runs from cron and the load test runs itself as a detached worker, so
# every script in the directory needs the bit; the data files beside them are only read
find /usr/local/opnsense/scripts/linkhealth -type f -name '*.py' -exec chmod 755 {} +
find /usr/local/opnsense/scripts/linkhealth -type f ! -name '*.py' -exec chmod 644 {} +
chmod 644 /usr/local/etc/cron.d/linkhealth
# No daily self-update since 2026-10-20: a release is installed from XGS: Updates
# (its Plugins tab, in os-xgs-npu) or by hand with self-update.py. An older
# install's cron entry goes.
rm -f /usr/local/etc/cron.d/linkhealth-update
chmod 644 /usr/local/opnsense/service/conf/actions.d/actions_linkhealth.conf
chmod 644 /usr/local/opnsense/www/js/widgets/LinkHealth.js
chmod 644 /usr/local/opnsense/www/js/widgets/Metadata/LinkHealth.xml

# the counter baseline, the status file, the alert state and the history live here. 0750
# keeps the directory in step with the 0640 of status.json, which lists the addresses
# reachable behind each port. install -d also repairs the mode on a second run.
install -d -o root -g wheel -m 0750 /var/db/linkhealth

# everything that has to parse is checked while only files have been touched: a syntax
# error must stop the install here rather than after configd has been told to reload it.
# php -l writes the parse error to its own stdout, so it cannot simply be discarded - the
# install would then stop without ever saying which file was wrong.
for f in $(find "${HERE}/src" -name '*.php'); do
    REPORT=$(php -l "$f" 2>&1) || { echo "${REPORT}"; exit 1; }
done
# the model, the form, the ACL, the menu and the widget metadata are all XML, read by the
# GUI through the caches dropped below; one malformed file there takes the page with it
for f in $(find "${HERE}/src" -name '*.xml'); do
    python3 -c 'import sys, xml.etree.ElementTree as ET; ET.parse(sys.argv[1])' "$f" ||
        { echo "malformed XML: $f"; exit 1; }
done
# And one rule about the widget metadata that well-formed XML does not cover: a comment
# inside <translations> reaches gettext() as a value, two of them reach it as an array,
# and the TypeError empties every dashboard in the GUI with nothing in any log to say
# which file did it. Checked here because this script is what puts the file in place.
if [ -f "${HERE}/tools/check-widget-metadata.py" ]; then
    python3 "${HERE}/tools/check-widget-metadata.py" "${HERE}" || exit 1
fi
for f in $(find /usr/local/opnsense/scripts/linkhealth -name '*.py'); do
    python3 -m py_compile "$f"
done
# the driver counter map and the chassis table are data the collector cannot start
# without, and a broken one would otherwise only surface a minute later in the log
for f in $(find /usr/local/opnsense/scripts/linkhealth -name '*.json'); do
    python3 -c 'import json, sys; json.load(open(sys.argv[1], encoding="utf-8"))' "$f" ||
        { echo "malformed JSON: $f"; exit 1; }
done

# Where the page goes in the menu: XGS when os-xgs-npu is installed, Interfaces when it is not.
# OPNsense's menu file takes no condition, so place-menu.sh writes it from menu/ and says which.
chmod 755 /usr/local/opnsense/scripts/linkhealth/place-menu.sh
MENUPLACE=$(/usr/local/opnsense/scripts/linkhealth/place-menu.sh)

# reload configd so it picks up the new actions
service configd restart > /dev/null

# The menu and the ACL map are cached on disk for an hour each, so until they are dropped
# the new page is missing from the menu and its ACL tag is unknown to the user manager.
# Those caches are not in /tmp: they sit in the framework's own tempDir, which is
# /var/lib/php/tmp on 26.7, so ask config.php where it is instead of writing down a path
# that has already moved once.
PHPTMP=$(php -r "echo (include '${CONFIGPHP}')->application->tempDir;" 2>/dev/null) || PHPTMP=''
PHPTMP=${PHPTMP:-/var/lib/php/tmp}
rm -f "${PHPTMP}/opnsense_menu_cache.xml" "${PHPTMP}/opnsense_acl_cache.json"

# Text that lives in the data files reaches the page through a generated map. If the
# map has fallen behind a note somebody added, say so here rather than letting an
# English sentence appear on an Arabic page with nothing to explain it. Not fatal:
# a blemish is not a reason to refuse to install a working plugin.
if [ -f "${HERE}/tools/gen-data-strings.py" ]; then
    python3 "${HERE}/tools/gen-data-strings.py" --check || \
        echo "run tools/gen-data-strings.py and translate what it names"
fi

# The page's and the mail's Arabic is in opnsense-arabic's catalogue since 2026-10-22, so this
# plugin merges nothing into it any more. What an earlier install put here for that goes: the
# copy of the Arabic, its merge, the boot hook and the pkg trigger that ran it, and its record.
rm -f /usr/local/opnsense/scripts/linkhealth/i18n/ui/ar_SA.json     /usr/local/opnsense/scripts/linkhealth/i18n/ui/part-a.json     /usr/local/opnsense/scripts/linkhealth/i18n/ui/part-b.json     /usr/local/opnsense/scripts/linkhealth/i18n/ui/part-c.json     /usr/local/opnsense/scripts/linkhealth/i18n/ui/part-d.json     /usr/local/opnsense/scripts/linkhealth/i18n/ui/glossary.json     /usr/local/opnsense/scripts/linkhealth/merge_ui_translations.py     /usr/local/etc/rc.syshook.d/start/62-linkhealth     /usr/local/share/pkg/triggers/os-linkhealth.ucl     /var/db/linkhealth/i18n-owned.json
configctl webgui restart > /dev/null 2>&1 || true

# What this install is, for the self-update: the commit, from a git checkout, or from the
# .source-commit stamp that an xgs-image-builder image or the self-update leaves in the tree.
# Without either it is recorded as unknown, and the self-update takes the latest release once.
REV=; RDATE=; RSRC=unknown
if [ -e "${HERE}/.git" ] && REV=$(git -C "${HERE}" rev-parse HEAD 2>/dev/null); then
    RDATE=$(git -C "${HERE}" log -1 --format=%cI 2>/dev/null); RSRC=git
elif [ -s "${HERE}/.source-commit" ]; then
    read -r REV RDATE _ < "${HERE}/.source-commit"; RSRC=stamp
else
    REV=
fi
case ${REV} in *[!0-9a-f]*) REV=; RDATE=; RSRC=unknown ;; esac
install -d -m 0755 /usr/local/share/os-linkhealth
printf '{"commit": "%s", "date": "%s", "source": "%s", "installed_at": %s}\n' \
    "${REV}" "${RDATE}" "${RSRC}" "$(date +%s)" > /usr/local/share/os-linkhealth/installed.json

# take the first sample now instead of a minute from now: "dry" measures and stores the
# baseline without mailing anything, so the first run from cron already has something to
# compare against and whoever just ran this does not open an empty page
if ! /usr/local/opnsense/scripts/linkhealth/linkhealth.py collect dry > /dev/null 2>&1; then
    echo "the first sample could not be taken; the next cycle will try again"
fi

if [ "${MENUPLACE}" = xgs ]; then
    echo "Link Health installed: XGS > Link Health"
else
    echo "Link Health installed: Interfaces > Link Health"
fi
echo "the widget is at Lobby > Dashboard, under Add widget > Link Health"
