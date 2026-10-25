#!/bin/sh
# Copyright (C) 2026 Abdelmonem Awad <eg2@live.com>. BSD 2-Clause License.
#
# Run by pkg(8) after the files of os-linkhealth are in place, on install and on upgrade
# alike. tools/make-package.sh inlines it into the package manifest.
#
# Three of the blocks below - the configd restart, run_migrations and rc.configure_plugins
# POST_INSTALL - are, line for line, what an OPNsense plugin runs: os-smart's post-install
# is those three and nothing else, and os-vnstat's is those three followed by a template
# reload, which this plugin has no service templates to need. The rest is this plugin's own.
#
# Nothing here may fail the install. By the time pkg runs this every file is already on
# disk, so refusing now would leave the package installed anyway; each step that can fail
# therefore says what the machine will be missing and the script carries on.
#
# Four things install/install.sh does are deliberately NOT here:
#
#   the chmod lines - pkg has already set every mode and owner from the plist, and it
#   recorded what it set. Setting them again from a script lets the disk and pkg's record
#   of the disk drift apart, and pkg check -s then reports this package as altered on a
#   machine where nothing is wrong.
#
#   the php -l / XML / JSON / py_compile checks - they belong to the source tree, before
#   a package exists, and .github/workflows/checks.yml already runs all of them on every
#   change. By the time this script runs the files are installed; a check that fails here
#   cannot undo anything, it can only make pkg print an error after the fact.
#
#   rm -f of opnsense_menu_cache.xml and opnsense_acl_cache.json - rc.configure_plugins
#   POST_INSTALL below calls system_cache_flush(), which invalidates the ACL cache and the
#   menu cache and, unlike those two rm lines, also clears the model caches and the
#   compiled Volt templates. The official call does strictly more.
#
#   cp -R src/ /usr/local/ - that is what the package itself is.

# configd reads actions_linkhealth.conf once, at start.
if [ -f /usr/local/etc/rc.d/configd ]; then
	/usr/local/etc/rc.d/configd restart
fi

# The counter baseline, the status file, the alert state and the history live here. 0750
# matches the 0640 of status.json, which lists the addresses reachable behind each port.
# install -d also repairs the mode and the owner on an upgrade instead of leaving whatever
# it found. It is checked, because a collector with nowhere to put its baseline measures
# nothing and the page it fills stays empty for ever without ever saying why.
if ! install -d -o root -g wheel -m 0750 /var/db/linkhealth; then
	echo "/var/db/linkhealth could not be created: the collector has nowhere to keep"
	echo "its baseline, so every cycle starts from nothing and the page stays empty"
fi

if [ -f /usr/local/opnsense/mvc/script/run_migrations.php ]; then
	/usr/local/opnsense/mvc/script/run_migrations.php OPNsense/LinkHealth
fi

# Put the plugin in config.xml's system/firmware/plugins list, which is where OPNsense
# keeps the plugins it considers managed; without it System > Firmware > Plugins shows
# this as an installed package that nothing configured. The GUI's own install path runs
# this line itself, after pkg - scripts/firmware/install.sh does - but a bare pkg add
# does not, so the package carries it. register.php reads the version marker the package
# ships at /usr/local/opnsense/version/linkhealth and refuses anything whose name does not
# start with os-, so it cannot register the wrong thing. It is idempotent. Guarded because
# it is core's file, not ours, and it has moved before.
if [ -x /usr/local/opnsense/scripts/firmware/register.php ]; then
	/usr/local/opnsense/scripts/firmware/register.php install os-linkhealth > /dev/null 2>&1 || true
fi

# Where the page goes in the menu: XGS when os-xgs-npu is installed, Interfaces when it is not.
# OPNsense's menu file takes no condition, so it is not one of the package's files: place-menu.sh
# writes it, before the reload below drops the menu cache.
MENUPLACE=
if [ -x /usr/local/opnsense/scripts/linkhealth/place-menu.sh ]; then
	MENUPLACE=$(/usr/local/opnsense/scripts/linkhealth/place-menu.sh) ||
	    echo "the menu entry could not be written: run /usr/local/opnsense/scripts/linkhealth/place-menu.sh"
fi

if [ -f /usr/local/etc/rc.configure_plugins ]; then
	echo "Reloading plugin configuration"
	/usr/local/etc/rc.configure_plugins POST_INSTALL
fi

# The page's and the mail's Arabic is in opnsense-arabic's catalogue since 2026-10-22: nothing
# is merged from here, and the merge's record from an earlier version goes.
rm -f /var/db/linkhealth/i18n-owned.json

# Take the first sample now instead of a minute from now: "dry" measures and stores the
# baseline without mailing anything, so the first run from cron already has something to
# compare against and whoever just installed this does not open an empty page. It only
# reads counters, and a failure is not a reason to fail an install that has succeeded.
if [ -x /usr/local/opnsense/scripts/linkhealth/linkhealth.py ]; then
	/usr/local/opnsense/scripts/linkhealth/linkhealth.py collect dry > /dev/null 2>&1 ||
	    echo "the first sample could not be taken; the next cycle will try again"
fi

if [ "${MENUPLACE}" = xgs ]; then
	echo "Link Health installed: the page is at XGS > Link Health"
else
	echo "Link Health installed: the page is at Interfaces > Link Health"
fi
echo "the widget is at Lobby > Dashboard, under Add widget > Link Health"
