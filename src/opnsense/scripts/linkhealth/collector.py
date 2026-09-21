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

    Reads the state of every physical port on the firewall.

    Four commands cover the whole machine, and they are issued once each rather
    than once per port: a per-port loop costs three times as much for the same
    answer.

        ifconfig -vm          link, media, the supported-media ladder and the
                              transceiver block. -v and -m are disjoint: -v
                              alone prints no ladder, -m alone prints no
                              transceiver, so both flags are mandatory.
        netstat -i -b -n -W   the driver's own error totals, for every
                              interface including those whose driver counts
                              nothing else. The --libxo json form is NOT used:
                              it emits two keys named "dropped-packets" in one
                              object, and a standard parser silently loses one.
        sysctl dev.<driver>   the per-cause hardware counters, one call per
                              driver tree that is actually present.
        ifconfig <bridge> addr  which host sits behind which port. On a bridged
                              firewall arp(8) reports every host as "on
                              bridge0"; the per-port truth lives only here.

    Nothing in this module decides anything. It reports what the machine says;
    verdict.py judges it.
"""

import json
import os
import re
import subprocess

import neighbours

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_XML = '/conf/config.xml'

# "10Gbase-SR" -> 10000, "1000baseT" -> 1000, "2500Base-T" -> 2500
_MEDIA_SPEED = re.compile(r'^(\d+)\s*(G|M)?base', re.IGNORECASE)


def _run(args):
    """Run a command and return its stdout, or '' if it failed.

    A missing optional tool must never take the whole sweep down with it, so
    failures are swallowed here and show up as absent data instead.
    """
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=30)
        return res.stdout if res.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError):
        return ''


def load_json(name):
    with open(os.path.join(HERE, name), 'r') as handle:
        return json.load(handle)


def media_speed(media):
    """Megabits per second for a media string, or 0 when it carries no speed."""
    if not media:
        return 0
    match = _MEDIA_SPEED.match(media.strip())
    if not match:
        return 0
    value = int(match.group(1))
    unit = (match.group(2) or 'M').upper()
    return value * 1000 if unit == 'G' else value


def parse_ifconfig(text):
    """Split `ifconfig -vm` into one record per interface.

    The format is stable: an interface starts at column 0, its attributes are
    indented, and the supported-media ladder is a further indented block under
    its own header.
    """
    interfaces = {}
    current = None
    in_media_list = False

    for line in text.splitlines():
        if not line.strip():
            continue

        if not line[0].isspace():
            name = line.split(':', 1)[0]
            flags = ''
            match = re.search(r'flags=[0-9a-fx]+<([^>]*)>', line)
            if match:
                flags = match.group(1)
            mtu = 0
            match = re.search(r'\bmtu (\d+)', line)
            if match:
                mtu = int(match.group(1))
            current = {
                'if': name,
                'flags': flags.split(',') if flags else [],
                'mtu': mtu,
                'media': '',
                'media_raw': '',
                'status': '',
                'supported_media': [],
                'driver': '',
                'description': '',
                'ether': '',
                'optics': None,
            }
            interfaces[name] = current
            in_media_list = False
            continue

        if current is None:
            continue

        stripped = line.strip()

        # the ladder is the only multi-line block; it ends at the next attribute
        if in_media_list:
            if stripped.startswith('media '):
                entry = stripped[len('media '):].strip()
                if entry and entry != 'autoselect':
                    current['supported_media'].append(entry)
                continue
            in_media_list = False

        if stripped == 'supported media:':
            in_media_list = True
        elif stripped.startswith('description: '):
            current['description'] = stripped[len('description: '):].strip()
        elif stripped.startswith('ether '):
            current['ether'] = stripped.split()[1].lower()
        elif stripped.startswith('media: '):
            current['media_raw'] = stripped[len('media: '):].strip()
            # "Ethernet autoselect (10Gbase-SR <full-duplex,rxpause,txpause>)"
            match = re.search(r'\(([^)<]+)', current['media_raw'])
            current['media'] = match.group(1).strip() if match else ''
        elif stripped.startswith('status: '):
            current['status'] = stripped[len('status: '):].strip()
        elif stripped.startswith('drivername: '):
            current['driver'] = stripped[len('drivername: '):].strip()
        elif stripped.startswith('plugged: '):
            optics = current['optics'] or {}
            optics['present'] = True
            optics['type'] = stripped[len('plugged: '):].strip()
            current['optics'] = optics
        elif stripped.startswith('vendor: '):
            optics = current['optics'] or {'present': True}
            # "vendor: FINISAR CORP. PN: FTLX8571D3BCL-FC SN: ABC1234 DATE: 2018-08-07"
            for key, pattern in (
                ('vendor', r'vendor:\s*(.*?)\s+PN:'),
                ('pn', r'PN:\s*(\S+)'),
                ('sn', r'SN:\s*(\S+)'),
                ('date', r'DATE:\s*(\S+)'),
            ):
                match = re.search(pattern, stripped)
                if match:
                    optics[key] = match.group(1).strip()
            current['optics'] = optics
        elif stripped.startswith('module temperature: '):
            optics = current['optics'] or {'present': True}
            match = re.search(r'temperature:\s*([-\d.]+)\s*C', stripped)
            if match:
                optics['temp_c'] = float(match.group(1))
            match = re.search(r'voltage:\s*([\d.]+)\s*Volts', stripped)
            if match:
                optics['vcc_v'] = float(match.group(1))
            current['optics'] = optics
        elif stripped.startswith('lane '):
            optics = current['optics'] or {'present': True}
            match = re.search(r'RX power:.*?\(([-\d.]+)\s*dBm\)', stripped)
            if match:
                optics['rx_dbm'] = float(match.group(1))
            match = re.search(r'TX power:.*?\(([-\d.]+)\s*dBm\)', stripped)
            if match:
                optics['tx_dbm'] = float(match.group(1))
            match = re.search(r'TX bias:\s*([\d.]+)\s*mA', stripped)
            if match:
                optics['tx_bias_ma'] = float(match.group(1))
            current['optics'] = optics

    return interfaces


def parse_netstat(text):
    """Per-interface totals from `netstat -i -b -n -W`.

    Only the link-level rows carry counters; the per-address rows that follow
    them repeat the name with no numbers and must be skipped, or an interface
    ends up with its counters overwritten by blanks.
    """
    stats = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 11 or fields[0] == 'Name':
            continue
        if not fields[2].startswith('<Link'):
            continue
        try:
            stats[fields[0]] = {
                'rx_frames': int(fields[4]),
                'rx_errors': int(fields[5]),
                'rx_drops': int(fields[6]),
                'rx_bytes': int(fields[7]),
                'tx_frames': int(fields[8]),
                'tx_errors': int(fields[9]),
                'tx_bytes': int(fields[10]),
            }
        except (ValueError, IndexError):
            continue
    return stats


def read_sysctl_tree(driver):
    """Every counter under dev.<driver> as {unit: {relative name: int}}."""
    text = _run(['/sbin/sysctl', '-e', 'dev.%s' % driver])
    tree = {}
    prefix = 'dev.%s.' % driver
    for line in text.splitlines():
        if '=' not in line or not line.startswith(prefix):
            continue
        oid, _, value = line.partition('=')
        rest = oid[len(prefix):]
        unit, _, leaf = rest.partition('.')
        if not unit.isdigit() or not leaf:
            continue
        try:
            tree.setdefault(int(unit), {})[leaf] = int(value)
        except ValueError:
            continue  # strings such as %desc
    return tree


def read_descriptions():
    """Interface -> (friendly name, config key) taken from the configuration.

    Deliberately not from the kernel's interface description: after a rename
    the kernel keeps the old string until the interface is reconfigured, and on
    the machine this was written for the two are currently swapped relative to
    the configuration. The configuration is what the operator edited, so the
    configuration is what we show.
    """
    names = {}
    try:
        import xml.etree.ElementTree as ET
        root = ET.parse(CONFIG_XML).getroot()
        section = root.find('./interfaces')
        if section is None:
            return names
        for node in section:
            device = node.findtext('if')
            if device:
                names[device] = (node.findtext('descr') or '', node.tag)
    except Exception:
        pass
    return names


def read_bridge_map(interfaces):
    """Which MAC addresses have been seen behind which port."""
    behind = {}
    for name in interfaces:
        if not name.startswith('bridge'):
            continue
        for line in _run(['/sbin/ifconfig', name, 'addr']).splitlines():
            fields = line.split()
            if len(fields) >= 3 and ':' in fields[0]:
                behind.setdefault(fields[2], set()).add(fields[0].lower())
    return behind


def read_arp():
    """MAC -> IP, so a port can be described by the host behind it."""
    hosts = {}
    for line in _run(['/usr/sbin/arp', '-an']).splitlines():
        match = re.search(r'\((\d+\.\d+\.\d+\.\d+)\) at ([0-9a-f:]{17})', line)
        if match:
            hosts.setdefault(match.group(2).lower(), match.group(1))
    return hosts


def read_pci():
    """Where each port physically sits: PCI bus, and the card it belongs to.

    This is the only independent witness to how the ports are grouped. The
    silk-screened labels are not readable anywhere - SMBIOS type 41, the one
    firmware table that could carry them, describes only the onboard video and
    a single onboard LAN on the machine this was written for, and none of its
    twenty ports. So a model table is the only way to print "PortA3" instead of
    "igb2", and this reader exists to check that table against the hardware
    before its labels are trusted.
    """
    layout = {}
    for line in _run(['/usr/sbin/pciconf', '-l']).splitlines():
        match = re.match(r'^([a-z]+\d+)@pci(\d+):(\d+):(\d+):(\d+):\s+(.*)$', line)
        if not match:
            continue
        name, _domain, bus, device, function, rest = match.groups()
        entry = {'bus': int(bus), 'device': int(device), 'function': int(function)}
        for key in ('subvendor', 'subdevice', 'vendor', 'device_id'):
            found = re.search(r'(?:^| )%s=(0x[0-9a-f]+)' % key.replace('device_id', 'device'), rest)
            if found:
                entry[key] = found.group(1)
        layout[name] = entry
    return layout


def read_chassis(models):
    """Identify the appliance from SMBIOS and pick its port-label table."""
    values = {}
    for line in _run(['/bin/kenv']).splitlines():
        if line.startswith('smbios.'):
            key, _, value = line.partition('=')
            values[key.strip()] = value.strip().strip('"')

    fields = {
        'maker': values.get('smbios.planar.maker') or values.get('smbios.system.maker', ''),
        'product': values.get('smbios.planar.product') or values.get('smbios.system.product', ''),
        'version': values.get('smbios.planar.version') or values.get('smbios.system.version', ''),
    }

    chosen, table = 'generic', models.get('generic', {})
    for key, model in models.items():
        if key == 'generic':
            continue
        criteria = model.get('match') or {}
        if not criteria:
            continue
        if all(re.search(pattern, fields.get(field, '') or '') for field, pattern in criteria.items()):
            chosen, table = key, model
            break

    return {
        'vendor': fields['maker'] or 'unknown',
        'model': (' '.join(x for x in (fields['product'], fields['version']) if x)).strip() or 'unknown',
        'display': table.get('display', 'unrecognised hardware'),
        'table': chosen,
        'verified': bool(table.get('verified')),
        'hint': table.get('hint', ''),
        'source': 'smbios',
    }, table


def label_for(driver, unit, table, chip='', pci=None):
    """What the chassis table says about one port - if the hardware agrees.

    A model table is a claim about a machine we cannot see. Before its labels
    are printed, each group states what the hardware under it should look like,
    and that claim is checked against the chip the driver reports and against
    the PCI address. A FleXi bay holding a different module, or a model that
    shares an SMBIOS string with a cousin that is wired differently, fails the
    check and the port simply keeps its interface name.

    A wrong label is worse than no label: an alert that says PortA3 while
    meaning another socket sends somebody to the wrong cable.

    This returns a record rather than a name, because a group states more about
    its sockets than what is printed beside them. `identify_led: false` is how a
    model says that its cage lights are not wired to the controller, and it
    passes the same verify gate as everything else here - a table cannot make
    claims about a card that is not the card it describes.
    """
    blank = {'label': None, 'bay': '', 'note': '', 'refused': '',
             'identify_led': None, 'identify_led_note': ''}

    for group in table.get('groups') or []:
        if group.get('driver') != driver:
            continue
        first, last = group.get('units', [0, -1])
        if not (first <= unit <= last):
            continue

        verify = group.get('verify') or {}
        expected_chip = verify.get('chip')
        if expected_chip and expected_chip.lower() not in (chip or '').lower():
            return dict(blank, refused='the card here is "%s", not the %s this model should have' % (
                chip or 'unknown', expected_chip))

        expected_sub = verify.get('subdevice')
        if expected_sub and (pci or {}).get('subdevice') not in (expected_sub if isinstance(expected_sub, list) else [expected_sub]):
            return dict(blank, refused='this card identifies itself as %s, which is not what this model ships' % (
                (pci or {}).get('subdevice') or 'unknown'))

        number = group.get('start', 1) + (unit - first)
        return {'label': group['label'] % number,
                'bay': group.get('bay', ''),
                'note': group.get('note', ''),
                'refused': '',
                'identify_led': group.get('identify_led'),
                'identify_led_note': group.get('identify_led_note', '')}
    return dict(blank)


def classify(name, driver, drivers):
    """Decide whether a port is ours to judge, and which counter map it uses."""
    base = re.match(r'^([a-z_]+)', driver or name)
    base = base.group(1) if base else ''

    for prefix in drivers['virtual']['prefixes']:
        if name.startswith(prefix):
            return None, 'virtual'

    for key, family in drivers['families'].items():
        if base in family['drivers']:
            return key, 'physical'

    if base in drivers['hypervisor']['drivers']:
        return None, 'hypervisor'

    return None, 'physical'


def collect():
    """One complete sweep. Returns the raw picture, with no judgement in it."""
    drivers = load_json('drivers.json')
    chassis_models = load_json('chassis.json')['models']

    interfaces = parse_ifconfig(_run(['/sbin/ifconfig', '-vm']))
    totals = parse_netstat(_run(['/usr/bin/netstat', '-i', '-b', '-n', '-W']))
    descriptions = read_descriptions()
    behind = read_bridge_map(interfaces)
    hosts = read_arp()
    chassis, table = read_chassis(chassis_models)
    pci = read_pci()
    # who introduces themselves on each cable, when lldpd is installed to ask
    lldp = neighbours.by_interface()

    trees = {}
    ports = []

    for name, data in sorted(interfaces.items()):
        driver = data['driver'] or name
        family, kind = classify(name, driver, drivers)
        if kind == 'virtual':
            continue

        match = re.match(r'^([a-z_]+)(\d+)$', driver)
        driver_base, unit = (match.group(1), int(match.group(2))) if match else (driver, 0)

        counters = {}
        counter_meta = {}
        if family:
            spec = drivers['families'][family]
            if driver_base not in trees:
                trees[driver_base] = read_sysctl_tree(driver_base)
            node = spec['stat_node']
            values = trees[driver_base].get(unit, {})
            for leaf, meta in spec['counters'].items():
                key = '%s.%s' % (node, leaf)
                if key in values:
                    counters[leaf] = values[key]
                    counter_meta[leaf] = meta
            for role in ('rx_frames', 'tx_frames', 'rx_bytes', 'tx_bytes'):
                leaf = spec.get(role)
                if leaf and '%s.%s' % (node, leaf) in values:
                    counters['_%s' % role] = values['%s.%s' % (node, leaf)]

        caps = ['LINK', 'NETSTAT']
        # led(4) registers one node per port on the drivers that support it, and
        # that is the first half of the test. The second half cannot be asked of
        # any interface: whether a light is actually wired to the pin the driver
        # drives. On the reference appliance the copper ports blink and every SFP
        # cage takes the write and stays dark, so a chassis group that has been
        # looked at can withdraw this below - see label_for().
        if os.path.exists('/dev/led/%s' % driver):
            caps.append('IDENTIFY_LED')
        if data['supported_media']:
            caps.append('MEDIA_LADDER')
        if counters:
            caps.append('COUNTERS')
        optics = data['optics']
        if optics and optics.get('present'):
            caps.append('OPTICS_INVENTORY')
            if 'rx_dbm' in optics or 'temp_c' in optics:
                caps.append('OPTICS_DOM')

        speed = media_speed(data['media'])
        ladder = [media_speed(entry) for entry in data['supported_media']]
        max_speed = max(ladder) if ladder else speed
        up = 'UP' in data['flags']
        active = data['status'] == 'active'

        chip = _run(['/sbin/sysctl', '-n', 'dev.%s.%d.%%desc' % (driver_base, unit)]).strip()
        chassis = label_for(driver_base, unit, table, chip, pci.get(name))
        label, bay, note, refused = (chassis['label'], chassis['bay'],
                                     chassis['note'], chassis['refused'])

        # The node existing only means the driver offered one. Whether a light is
        # wired to the other end of it is a fact about the board, and the only
        # machine that can answer is the one in front of you - so where a verified
        # chassis group has been looked at and says the light does not reach these
        # sockets, the capability goes and the reason stays.
        identify_note = ''
        if chassis['identify_led'] is False and 'IDENTIFY_LED' in caps:
            caps.remove('IDENTIFY_LED')
            identify_note = chassis['identify_led_note']

        friendly, confkey = descriptions.get(name, ('', ''))

        serves = []
        for mac in sorted(behind.get(name, [])):
            address = hosts.get(mac)
            if address:
                serves.append(address)

        ports.append({
            'if': name,
            'label': label or name,
            'labelled': label is not None,
            'name': friendly,
            'confkey': confkey,
            'bay': bay,
            'bay_note': note,
            'identify_note': identify_note,
            'driver': driver_base,
            'unit': unit,
            'family': family or '',
            'kind': kind,
            'chip': chip,
            'pci': pci.get(name, {}),
            'label_source': 'chassis' if label else 'interface',
            'label_refused': refused,
            'mtu': data['mtu'],
            'mac': data['ether'],
            'caps': caps,
            'serves': serves[:8],
            'neighbour': lldp.get(name, {}),
            'link': {
                'state': 'active' if active else ('down' if up else 'disabled'),
                'admin_up': up,
                'media': data['media'],
                'media_raw': data['media_raw'],
                'duplex': 'full' if 'full-duplex' in data['media_raw'] else
                          ('half' if 'half-duplex' in data['media_raw'] else ''),
                'speed_mbps': speed,
                'max_speed_mbps': max_speed,
                'supported_media': data['supported_media'],
                'downshift': bool(active and speed and max_speed and speed < max_speed),
            },
            'totals': totals.get(name, {}),
            'counters': counters,
            'counter_meta': counter_meta,
            'optics': optics or {'present': False},
        })

    return {'chassis': chassis, 'ports': ports}


def boottime():
    """Seconds since the epoch at which the kernel started.

    The baseline is anchored to this so that a reboot is recognised even when
    the counters happen to climb past the values they had before it.
    """
    text = _run(['/sbin/sysctl', '-n', 'kern.boottime'])
    match = re.search(r'sec\s*=\s*(\d+)', text)
    return int(match.group(1)) if match else 0
