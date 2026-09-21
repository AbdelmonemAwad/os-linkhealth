#!/bin/sh
# Copyright (C) 2026 Abdelmonem Awad <eg2@live.com>. BSD 2-Clause License.
#
# Remove the Link Health plugin files. The settings stay in config.xml and the state
# stays in /var/db/linkhealth, so a reinstall keeps the per-port history and the
# counter baseline instead of spending its first window measuring nothing.

rm -f /usr/local/etc/cron.d/linkhealth
rm -f /usr/local/etc/rc.syshook.d/start/62-linkhealth
rm -f /usr/local/opnsense/service/conf/actions.d/actions_linkhealth.conf
rm -f /usr/local/opnsense/www/js/widgets/LinkHealth.js
rm -f /usr/local/opnsense/www/js/widgets/Metadata/LinkHealth.xml
rm -rf /usr/local/opnsense/scripts/linkhealth
rm -rf /usr/local/opnsense/mvc/app/models/OPNsense/LinkHealth
rm -rf /usr/local/opnsense/mvc/app/controllers/OPNsense/LinkHealth
rm -rf /usr/local/opnsense/mvc/app/views/OPNsense/LinkHealth

# node_exporter reads whatever text files it finds, so this one would be scraped for
# ever, frozen at the last cycle, long after the collector that wrote it is gone
rm -f /var/tmp/node_exporter/linkhealth.prom

service configd restart > /dev/null

# the menu and the ACL map are cached on disk for an hour each, so the menu would keep a
# dead entry until then. They live in the framework's own tempDir, which is
# /var/lib/php/tmp on 26.7 and not /tmp, so ask config.php where it is.
CONFIGPHP=/usr/local/opnsense/mvc/app/config/config.php
PHPTMP=$(php -r "echo (include '${CONFIGPHP}')->application->tempDir;" 2>/dev/null)
PHPTMP=${PHPTMP:-/var/lib/php/tmp}
rm -f "${PHPTMP}/opnsense_menu_cache.xml" "${PHPTMP}/opnsense_acl_cache.json"

# the GUI strings merged into the gettext catalogs are left where they are: they are
# only ever read by msgid, and the next core update replaces those catalogs anyway
echo "Link Health removed, settings in config.xml and state in /var/db/linkhealth kept"
