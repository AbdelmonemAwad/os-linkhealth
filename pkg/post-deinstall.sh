#!/bin/sh
# Copyright (C) 2026 Abdelmonem Awad <eg2@live.com>. BSD 2-Clause License.
#
# Run by pkg(8) after the files of os-linkhealth have been removed, and only on a real
# removal. An upgrade does not come through here: pkg deletes the old version in
# pkg_add_cleanup_old(), which runs the old package's PRE-deinstall and nothing else -
# PKG_SCRIPT_POST_DEINSTALL appears nowhere in libpkg/pkg_add.c - and the new version's
# post-install runs instead. So everything here may assume the plugin is going for good.
# Anything moved from here into a pre-deinstall later must test PKG_UPGRADE, which pkg
# sets to "true" for exactly that case, or it will fire in the middle of every upgrade.
#
# pkg has already removed every file the package owns. It has not yet removed the
# directories: pkg_delete() runs this script between pkg_delete_files() and
# pkg_delete_dirs(), so the plugin's now-empty directories are still on disk while these
# lines run and are gone a moment later. Nothing here depends on that, and none of
# install/uninstall.sh's rm lines are repeated. What is left is what pkg cannot know
# about: the files this plugin writes outside its own package, the entry it made in
# config.xml, and the GUI's caches.

# node_exporter reads whatever text files it finds in this directory, so this one would be
# scraped for ever, frozen at the last cycle, long after the collector that wrote it is gone.
rm -f /var/tmp/node_exporter/linkhealth.prom

# The identify and load-test locks. /etc/rc.d/cleanvar empties /var/run at every boot, which
# is exactly why these are easy to forget: a firewall that is not rebooted keeps them for
# months. They are empty files and hold nothing once the process that flock()ed them is gone.
# A blink still in flight is deliberately not interrupted and needs no help here: identify.py
# writes "0" back to the /dev/led node in its own finally, so the LED goes back to the driver
# even though the script it is running from was deleted underneath it a moment ago.
rm -f /var/run/linkhealth-identify.lock /var/run/linkhealth-identify.lock.stop
rm -f /var/run/linkhealth-test.lock

# Take the plugin back out of config.xml's system/firmware/plugins. Left in, it would be a
# plugin OPNsense believes it is managing and cannot find, and that list is what a plugin
# restore after a firmware upgrade works from. register.php only unregisters a name whose
# marker under /usr/local/opnsense/version has gone, which is why this belongs here and not
# in a pre-deinstall: that marker is one of the files pkg has just removed. The GUI's own
# remove.sh runs the same line after pkg remove, and running it twice changes nothing.
# Its output is kept rather than sent to /dev/null: on the remove path register.php prints
# nothing when it works, so anything it does print is worth reading, and a config.xml that
# could not be written is the one thing in this script that should be said out loud.
if [ -x /usr/local/opnsense/scripts/firmware/register.php ]; then
	/usr/local/opnsense/scripts/firmware/register.php remove os-linkhealth ||
	    echo "could not take os-linkhealth out of the plugin list in config.xml;" \
	        "System > Firmware > Plugins will keep showing it until the next plugin sync"
fi

# actions_linkhealth.conf is gone; configd is still holding it. The generated deinstall
# script of an official plugin does not do this - upstream restarts configd on install only -
# so it is here on purpose: until configd is restarted, configctl still answers for actions
# whose scripts are no longer on disk. It is safe in the middle of a removal driven from the
# GUI, because configd's stop kills processes running configd.py and nothing else, and the
# pkg transaction is not one of them.
if [ -f /usr/local/etc/rc.d/configd ]; then
	/usr/local/etc/rc.d/configd restart
fi

# system_cache_flush(): without it the menu keeps a dead entry and the user manager keeps
# an ACL tag pointing at a page that is not there, for up to an hour each. This block is
# word for word the +POST_DEINSTALL that opnsense/plugins generates for a plugin with
# models, and is kept identical to it on purpose.
if [ -f /usr/local/etc/rc.configure_plugins ]; then
	echo "Reloading plugin configuration"
	/usr/local/etc/rc.configure_plugins POST_DEINSTALL
fi

# Two things are kept on purpose, and both are named here rather than left for somebody to
# find later:
#
#   /var/db/linkhealth - the counter baseline and the per-port history. A reinstall that
#   still has them starts judging immediately instead of spending its first window
#   measuring nothing.  Remove it with:  rm -rf /var/db/linkhealth
#
#   the strings this plugin merged into a GUI gettext catalogue. It has only ever written to
#   one. merge_ui_translations.py works from the i18n/ui/<locale>.json files the plugin
#   ships, and it ships ar_SA alone, so on a machine with no Arabic catalogue none of this
#   happened at all - the merge skips a language whose OPNsense.mo is not there. Where it did
#   happen, ar_SA/LC_MESSAGES/OPNsense.mo is owned by no package on the machine: the
#   catalogues opnsense-lang ships are cs_CZ through zh_TW and ar_SA is not among them, so
#   pkg check will not name it and nothing will replace it on its own either. Those msgids
#   stay until that catalogue is installed again from the opnsense-arabic project it came
#   from. Nothing asks for them any more, so they are never looked up; what they cost is
#   their own size. Exactly what was added, per locale, is in a file this script keeps -
#   /var/db/linkhealth/i18n-owned.json - so it can be undone later. It is not undone here:
#   the script that writes .mo files was deleted a moment ago, which makes this a job for a
#   pre-deinstall, and there is no pre-deinstall today.
echo "Link Health removed; its settings in config.xml and its state in /var/db/linkhealth are kept"
