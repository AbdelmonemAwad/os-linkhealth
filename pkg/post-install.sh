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

if [ -f /usr/local/etc/rc.configure_plugins ]; then
	echo "Reloading plugin configuration"
	/usr/local/etc/rc.configure_plugins POST_INSTALL
fi

# The GUI strings are merged into the gettext catalogues of every installed language.
# This is the same thing /usr/local/etc/rc.syshook.d/start/62-linkhealth does at every
# boot - the catalogues belong to the opnsense-lang package, which replaces them on its
# own schedule, independent of the core version - so doing it now takes no new liberty
# with the machine; it only brings the first run forward from the next reboot to now. It
# does mean pkg check -s opnsense-lang names the altered catalogues afterwards, exactly as
# it does after the hand installation; post-deinstall.sh says why that is not a fault.
#
# stderr is deliberately not discarded: when the merge cannot read a catalogue it names
# it, and an install is the one moment somebody is watching. The exit status is kept
# beside the count rather than folded into it, so that a merge which wrote some strings
# and then died still gets the webgui restart those strings need, and so that a failure
# is reported as a failure instead of as "0 added".
if [ -x /usr/local/opnsense/scripts/linkhealth/merge_ui_translations.py ]; then
	ADDED=$(/usr/local/opnsense/scripts/linkhealth/merge_ui_translations.py)
	STATUS=$?
	if [ "${STATUS}" -eq 0 ]; then
		echo "translated strings added: ${ADDED:-0}"
	elif [ -n "${ADDED}" ]; then
		echo "the GUI string merge added ${ADDED} and then failed (exit ${STATUS});"
		echo "the next boot runs the same merge again"
	else
		echo "the GUI strings were not merged (the merge exited ${STATUS}): the page"
		echo "keeps the words it has until the next boot, which runs the same merge"
	fi
	if [ -n "${ADDED}" ] && [ "${ADDED}" != "0" ]; then
		# A restart takes away every logged-in session's php worker, so a page that
		# is open goes quiet for a moment. Say so: this is the one line of output
		# that explains why the GUI somebody is watching just stopped answering.
		echo "restarting the web GUI so that it reads the strings just added"
		# absolute, because a pkg script does not necessarily inherit a PATH with
		# /usr/local/sbin in it - which is why os-vnstat's own post-install calls
		# /usr/local/sbin/configctl by its full path too
		[ -f /usr/local/sbin/configctl ] &&
		    /usr/local/sbin/configctl webgui restart > /dev/null 2>&1 || true
	fi
fi

# Take the first sample now instead of a minute from now: "dry" measures and stores the
# baseline without mailing anything, so the first run from cron already has something to
# compare against and whoever just installed this does not open an empty page. It only
# reads counters, and a failure is not a reason to fail an install that has succeeded.
if [ -x /usr/local/opnsense/scripts/linkhealth/linkhealth.py ]; then
	/usr/local/opnsense/scripts/linkhealth/linkhealth.py collect dry > /dev/null 2>&1 ||
	    echo "the first sample could not be taken; the next cycle will try again"
fi

echo "Link Health installed: the page is at Interfaces > Link Health"
echo "the widget is at Lobby > Dashboard, under Add widget > Link Health"
