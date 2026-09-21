#!/usr/local/bin/python3
"""
    Copyright (c) 2026 Abdelmonem Awad <eg2@live.com>
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

    --------------------------------------------------------------------------

    Asks lldpd what is at the other end of each cable, when lldpd is there.

    The plugin already knows which addresses it has SEEN behind a port, from
    the bridge's address table. That answers "who talks through here", which is
    not the same question as "what is plugged in here" - a switch with twenty
    hosts behind it shows twenty addresses and never names itself.

    LLDP answers the second question properly: the neighbour introduces itself
    by name and says which of ITS ports the cable is in. "SFP+2 -> netgear-sw
    port xg10" is worth more than eight IP addresses when you are standing at
    the rack.

    We do not implement LLDP. If os-lldpd is installed we read it; if it is
    not, this returns nothing and the bridge table remains the answer. There is
    no dependency either way.

    NOT VERIFIED ON THE MACHINE THIS WAS WRITTEN FOR: lldpd is not installed
    there, so the parsing below follows lldpctl's documented JSON and is
    written to survive the shapes that documentation has used over the years -
    interfaces as a list or as an object keyed by name, and values as bare
    strings, as {"value": ...} wrappers, or as single-element lists of either.
    Anything it cannot read becomes an absent neighbour, never an exception.
"""

import json
import os
import subprocess

LLDPCTL = '/usr/local/sbin/lldpctl'


def available():
    return os.path.exists(LLDPCTL)


def _text(value):
    """Pull a plain string out of whatever shape lldpctl used this time."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ('value', 'name', 'id', 'descr'):
            if key in value:
                return _text(value[key])
        return ''
    if isinstance(value, list):
        for item in value:
            found = _text(item)
            if found:
                return found
    return ''


def _interfaces(document):
    """The per-interface entries, whichever way this version nests them."""
    section = (document or {}).get('lldp')
    if isinstance(section, list):
        section = section[0] if section else {}
    if not isinstance(section, dict):
        return []

    entries = section.get('interface')
    if isinstance(entries, dict):
        # keyed by interface name: {"igb0": {...}}
        return [dict(body, name=name) for name, body in entries.items()
                if isinstance(body, dict)]
    if isinstance(entries, list):
        out = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if 'name' in entry:
                out.append(entry)
            else:
                # some versions wrap each interface in its own single-key object
                for name, body in entry.items():
                    if isinstance(body, dict):
                        out.append(dict(body, name=name))
        return out
    return []


def by_interface(timeout=5):
    """{interface: {'chassis': name, 'port': id, 'descr': text}} or {} when absent."""
    if not available():
        return {}
    try:
        result = subprocess.run([LLDPCTL, '-f', 'json'],
                                capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0 or not result.stdout.strip():
            return {}
        document = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}

    found = {}
    for entry in _interfaces(document):
        name = _text(entry.get('name'))
        if not name:
            continue
        chassis = entry.get('chassis') or {}
        if isinstance(chassis, dict) and len(chassis) == 1 and 'id' not in chassis:
            # {"netgear-sw": {...}} - the neighbour's name is the key
            only = next(iter(chassis.items()))
            chassis = dict(only[1], name=only[0]) if isinstance(only[1], dict) else {'name': only[0]}
        port = entry.get('port') or {}
        neighbour = {
            'chassis': _text(chassis.get('name')) or _text(chassis.get('id')),
            'port': _text(port.get('id')),
            'descr': _text(port.get('descr')),
        }
        if any(neighbour.values()):
            found[name] = neighbour
    return found
