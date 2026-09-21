#!/bin/sh
# Copyright (C) 2026 Abdelmonem Awad <eg2@live.com>. BSD 2-Clause License.
#
# Build an installable OPNsense plugin package from this repository.
#
#   sh tools/make-package.sh [-o OUTDIR] [-c CATEGORY]
#
# It runs on the firewall, because the thing it builds is a FreeBSD package and pkg(8)
# is the only program that can write one. It does not need root and it installs nothing:
# everything happens in a temporary directory that is removed on the way out, and the
# result is a single .pkg file you are told the path and the SHA256 of.
#
# What it has to supply by hand, and why. The Makefile carries PLUGIN_NAME,
# PLUGIN_VERSION, PLUGIN_COMMENT, PLUGIN_MAINTAINER and PLUGIN_DEPENDS, and that is all
# it carries; the rest of a package manifest is filled in by ../../Mk/plugins.mk inside
# the OPNsense plugins tree, which this repository is not in yet. So this script supplies
# the origin (opnsense/os-<name>, the path the plugin will have in that tree), the prefix
# (/usr/local), the licence (BSD2CLAUSE, single), the website, the category, and the
# product_* annotations that make OPNsense treat the package as one of its plugins rather
# than as an unrelated program that happens to be installed.
#
# It also writes the file the hand installation never wrote: /usr/local/opnsense/version/
# <name>. Every OPNsense plugin ships one - it is in the file list of os-smart, os-vnstat
# and every other - and opnsense-version -c os-<name>, which is how the framework answers
# "is this plugin installed", reads that file and nothing else.

set -e

OUTDIR=
CATEGORY=

usage()
{
	echo "usage: sh tools/make-package.sh [-o OUTDIR] [-c CATEGORY]" >&2
	echo "  -o  where to leave the .pkg file (default: the current directory)" >&2
	echo "  -c  pkg category (default: the PLUGIN_CATEGORY line in the Makefile," >&2
	echo "      or misc; it is metadata only - see the note further down)" >&2
	exit 2
}

while getopts "o:c:h" opt; do
	case "${opt}" in
	o)	OUTDIR=${OPTARG} ;;
	c)	CATEGORY=${OPTARG} ;;
	*)	usage ;;
	esac
done

# Refuse before touching anything, and say what to do instead. A package built anywhere
# but on FreeBSD would be wrong in ways that only show up at install time.
if [ "$(uname -s)" != "FreeBSD" ]; then
	echo "make-package.sh builds a FreeBSD package, so it has to run on FreeBSD." >&2
	echo "This is $(uname -s). Copy this repository to the firewall and run it there:" >&2
	echo "  scp -r . root@firewall:/root/os-plugin && ssh root@firewall" >&2
	echo "  cd /root/os-plugin && sh tools/make-package.sh -o /root" >&2
	exit 1
fi

if ! command -v pkg > /dev/null 2>&1; then
	echo "pkg(8) is not in PATH, and it is the program that writes the package." >&2
	exit 1
fi

ROOT=$(cd "$(dirname "$0")/.." && pwd)

if [ ! -f "${ROOT}/Makefile" ] || [ ! -d "${ROOT}/src" ]; then
	echo "${ROOT} does not look like a plugin repository: no Makefile, or no src/." >&2
	exit 1
fi

# One value out of the Makefile. Accepts =, ?= and +=, and trims the tabs the OPNsense
# plugin Makefiles line their values up with.
mk_get()
{
	sed -n "s/^$1[[:space:]]*[?+]*=[[:space:]]*//p" "${ROOT}/Makefile" |
	    sed -e '1!d' -e 's/[[:space:]]*$//'
}

PLUGIN_NAME=$(mk_get PLUGIN_NAME)
PLUGIN_VERSION=$(mk_get PLUGIN_VERSION)
PLUGIN_COMMENT=$(mk_get PLUGIN_COMMENT)
PLUGIN_MAINTAINER=$(mk_get PLUGIN_MAINTAINER)
PLUGIN_DEPENDS=$(mk_get PLUGIN_DEPENDS)
PLUGIN_WWW=$(mk_get PLUGIN_WWW)

for var in PLUGIN_NAME PLUGIN_VERSION PLUGIN_COMMENT PLUGIN_MAINTAINER; do
	eval "value=\${${var}}"
	if [ -z "${value}" ]; then
		echo "${var} is missing from ${ROOT}/Makefile; cannot build a package." >&2
		exit 1
	fi
done

PKGNAME=os-${PLUGIN_NAME}
ORIGIN=opnsense/${PKGNAME}
PREFIX=/usr/local
: "${PLUGIN_WWW:=https://github.com/AbdelmonemAwad/${PKGNAME}}"

# The category. In the OPNsense plugins tree it is the directory the plugin sits in -
# net/vnstat becomes categories ["net"], sysutils/smart becomes ["sysutils"] - so until
# this repository is in that tree there is nothing to read it from. It is metadata: the
# GUI's plugin list never looks at it (System > Firmware > Plugins reads name, version,
# comment, size, arch, licence, repository and origin), and neither does pkg add. Pick
# the one the plugin would have upstream and it will be right when it gets there.
if [ -z "${CATEGORY}" ]; then
	CATEGORY=$(mk_get PLUGIN_CATEGORY)
fi
: "${CATEGORY:=misc}"

if [ -z "${OUTDIR}" ]; then
	OUTDIR=$(pwd)
fi
mkdir -p "${OUTDIR}"
OUTDIR=$(cd "${OUTDIR}" && pwd)

WORK=$(mktemp -d -t "${PKGNAME}") || {
	echo "could not make a temporary directory" >&2
	exit 1
}
trap 'rm -rf "${WORK}"' EXIT HUP INT TERM

STAGE=${WORK}/stage
META=${WORK}/meta
mkdir -p "${STAGE}${PREFIX}" "${META}"

# ---------------------------------------------------------------- staging and modes
#
# git in this repository records no executable bits - every file is 100644 - so the mode
# cannot be copied from the checkout, and install/install.sh does not copy it either: it
# names the files that need the bit and chmods them. The rule below is that same decision
# made from the file itself rather than from a list that can fall behind: a file whose
# first two bytes are #! is run, and gets 0755; everything else is read, and gets 0644.
# On this repository that selects exactly the set install/install.sh names.
#
# Ownership is written into the plist rather than applied with chown, so that the build
# does not need root and pkg still installs the files owned by root:wheel.

find "${ROOT}/src" -type f | sed "s|^${ROOT}/src/||" | LC_ALL=C sort > "${WORK}/files"

if [ ! -s "${WORK}/files" ]; then
	echo "src/ is empty; there is nothing to package." >&2
	exit 1
fi

: > "${WORK}/data"
: > "${WORK}/exec"

while read -r rel; do
	from=${ROOT}/src/${rel}
	to=${STAGE}${PREFIX}/${rel}
	mkdir -p "$(dirname "${to}")"
	cp -p "${from}" "${to}"
	if [ "$(head -c 2 "${from}")" = "#!" ]; then
		chmod 0755 "${to}"
		echo "${PREFIX}/${rel}" >> "${WORK}/exec"
	else
		chmod 0644 "${to}"
		echo "${PREFIX}/${rel}" >> "${WORK}/data"
	fi
done < "${WORK}/files"

# ------------------------------------------------------------------- version marker
#
# The same JSON every OPNsense plugin ships at /usr/local/opnsense/version/<name>. It is
# a packaged file, not something a script writes afterwards, which is why removing the
# package removes it too and the framework stops claiming the plugin is there.
#
# product_hash is the commit this was built from, so that a package somebody sends you
# can be traced back to a tree. product_tier is 4 on purpose: the GUI forces tier 4 for
# anything that did not come from a repository it trusts, and this did not.

HASH=unknown
if command -v git > /dev/null 2>&1 && [ -d "${ROOT}/.git" ]; then
	HASH=$(git -C "${ROOT}" rev-parse --short HEAD 2> /dev/null) || HASH=unknown
fi

PRODUCT_ABI=$(opnsense-version -a 2> /dev/null) || PRODUCT_ABI=
PRODUCT_ARCH=$(uname -p)
: "${PRODUCT_ABI:=unknown}"

VERSIONFILE=${STAGE}${PREFIX}/opnsense/version/${PLUGIN_NAME}
mkdir -p "$(dirname "${VERSIONFILE}")"
cat > "${VERSIONFILE}" << MARKER
{
    "product_abi": "${PRODUCT_ABI}",
    "product_arch": "${PRODUCT_ARCH}",
    "product_conflicts": "${PKGNAME}-devel",
    "product_email": "${PLUGIN_MAINTAINER}",
    "product_hash": "${HASH}",
    "product_id": "${PKGNAME}",
    "product_name": "${PLUGIN_NAME}",
    "product_tier": "4",
    "product_version": "${PLUGIN_VERSION}",
    "product_website": "${PLUGIN_WWW}"
}
MARKER
chmod 0644 "${VERSIONFILE}"
echo "${PREFIX}/opnsense/version/${PLUGIN_NAME}" >> "${WORK}/data"

LC_ALL=C sort -o "${WORK}/data" "${WORK}/data"

# --------------------------------------------------------------------------- plist
{
	echo "@owner root"
	echo "@group wheel"
	echo "@mode 0644"
	cat "${WORK}/data"
	if [ -s "${WORK}/exec" ]; then
		echo "@mode 0755"
		cat "${WORK}/exec"
	fi
} > "${WORK}/plist"

# ------------------------------------------------------------------------ manifest
#
# UCL, with heredoc strings for everything that has newlines in it, so that no text from
# pkg-descr or from pkg/*.sh has to be escaped on its way in. The terminators are chosen
# to be things that cannot appear in the files themselves.

manifest_script()
{
	# $1 = key in the scripts block, $2 = file to inline; prints nothing if absent
	[ -f "${ROOT}/pkg/$2" ] || return 0
	echo "    $1 = <<PKGSCRIPT_$1"
	cat "${ROOT}/pkg/$2"
	echo "PKGSCRIPT_$1"
}

{
	echo "name = \"${PKGNAME}\";"
	echo "version = \"${PLUGIN_VERSION}\";"
	echo "origin = \"${ORIGIN}\";"
	echo "comment = \"${PLUGIN_COMMENT}\";"
	echo "maintainer = \"${PLUGIN_MAINTAINER}\";"
	echo "www = \"${PLUGIN_WWW}\";"
	echo "prefix = \"${PREFIX}\";"
	echo "categories = [ \"${CATEGORY}\" ];"
	echo "licenselogic = \"single\";"
	echo "licenses = [ \"BSD2CLAUSE\" ];"

	echo "desc = <<PKGDESCR"
	if [ -f "${ROOT}/pkg-descr" ]; then
		cat "${ROOT}/pkg-descr"
	else
		echo "${PLUGIN_COMMENT}"
	fi
	echo "PKGDESCR"

	# Dependencies. pkg wants an origin and a version beside the name, and the only
	# honest source for those on this machine is pkg itself: what is installed first,
	# then the repository catalogue as it was last fetched (-U, so that this does not
	# try to update it and does not need root). If neither knows the package, stop -
	# a manifest with a guessed dependency version installs and then misbehaves.
	if [ -n "${PLUGIN_DEPENDS}" ]; then
		echo "deps {"
		for dep in ${PLUGIN_DEPENDS}; do
			dorigin=$(pkg query %o "${dep}" 2> /dev/null) || dorigin=
			dversion=$(pkg query %v "${dep}" 2> /dev/null) || dversion=
			if [ -z "${dorigin}" ]; then
				dorigin=$(pkg rquery -U %o "${dep}" 2> /dev/null | sed -e '1!d') || dorigin=
				dversion=$(pkg rquery -U %v "${dep}" 2> /dev/null | sed -e '1!d') || dversion=
			fi
			if [ -z "${dorigin}" ] || [ -z "${dversion}" ]; then
				echo "PLUGIN_DEPENDS names ${dep}, which pkg does not know." >&2
				echo "It is neither installed nor in the repository catalogue," >&2
				echo "so its origin and version cannot be filled in. Fix that" >&2
				echo "first: pkg update, or install ${dep}." >&2
				exit 1
			fi
			echo "    ${dep} { origin = \"${dorigin}\"; version = \"${dversion}\"; }"
		done
		echo "}"
	fi

	# The annotations OPNsense reads. They are the same set the version marker carries;
	# pkg annotate shows them on the installed package, and firmware health and the
	# plugin list use them to tell an OPNsense plugin from any other package.
	echo "annotations {"
	echo "    product_abi = \"${PRODUCT_ABI}\";"
	echo "    product_arch = \"${PRODUCT_ARCH}\";"
	echo "    product_conflicts = \"${PKGNAME}-devel\";"
	echo "    product_email = \"${PLUGIN_MAINTAINER}\";"
	echo "    product_hash = \"${HASH}\";"
	echo "    product_id = \"${PKGNAME}\";"
	echo "    product_name = \"${PLUGIN_NAME}\";"
	echo "    product_tier = \"4\";"
	echo "    product_version = \"${PLUGIN_VERSION}\";"
	echo "    product_website = \"${PLUGIN_WWW}\";"
	echo "}"

	echo "scripts {"
	manifest_script pre-install pre-install.sh
	manifest_script post-install post-install.sh
	manifest_script pre-deinstall pre-deinstall.sh
	manifest_script post-deinstall post-deinstall.sh
	echo "}"
} > "${META}/+MANIFEST"

# ----------------------------------------------------------------------- build it
pkg create -m "${META}" -p "${WORK}/plist" -r "${STAGE}" -o "${OUTDIR}"

RESULT=${OUTDIR}/${PKGNAME}-${PLUGIN_VERSION}.pkg
if [ ! -f "${RESULT}" ]; then
	# pkg names the file itself; find it rather than insist on the name
	RESULT=$(ls -t "${OUTDIR}/${PKGNAME}-${PLUGIN_VERSION}".* 2> /dev/null | sed -e '1!d')
fi
if [ ! -f "${RESULT}" ]; then
	echo "pkg create reported success but no package appeared in ${OUTDIR}." >&2
	exit 1
fi

echo
echo "${RESULT}"
echo "SHA256 $(sha256 -q "${RESULT}")"
echo
echo "check it before installing it:"
echo "  pkg info -F ${RESULT}"
echo "  pkg info -lF ${RESULT}"
echo "install it with:"
echo "  pkg add ${RESULT}"
