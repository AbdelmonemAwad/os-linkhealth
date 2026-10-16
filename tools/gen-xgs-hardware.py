#!/usr/bin/env python3
# Copyright (C) 2026 Abdelmonem Awad <eg2@live.com>. BSD 2-Clause License.
"""Generate chassis.json and faceplates.json entries for every Sophos XGS board, and the FleXi
module table, from the vendor's own board files.

    tools/gen-xgs-hardware.py <xgs-bsp/data directory> [--check]

THE SOURCE. The vendor's GPL board support package carries one file per assembly in
xgs-bsp/data: a board's file names every front port as it is printed, its kind of socket and its
speed, and the expansion bays it has; a module's file is a template of the same, filled in under
the bay's letter when the module is fitted. Those are facts about the hardware - counts, printed
labels, socket kinds - and they are what this reads. Nothing is copied from a picture.

WHAT IT WRITES. Into chassis.json, before the hand-made entries: one entry per board, matched on
the assembly number in the board's own serial (smbios.planar.serial carries "AMDA0203-0001" and the
like), so a model is recognised exactly and before any looser match. Into faceplates.json: the
same models' drawings - one row per kind of socket and speed, then one row per expansion bay - and
"modules", every module the vendor describes, which the bay rows are filled from at run time.

Every generated entry carries "generated", and is replaced, never edited, on the next run. The
hand-made entries - the XGS 3300, the XGS 136 and the XG 330, which were checked port by port on
the machine - are left exactly as they are, and their boards are not generated again.

WHERE A PORT GETS ITS NAME ON THE PAGE. Only where a driver presents the board's ports: the
OCTEON TX boards (AMDA0202, the XGS 2100 to 3300) through os-xgs-npu's octep, the ARMADA desktops
(AMDA0201, the XGS 126 and 136) through its npuep. Those generated entries take the hand-made
entry's unit-to-label groups, because the same driver numbers the same board the same way. Every
other board gets its drawing and its name, and its sockets stay "absent" until something presents
them.

--check changes nothing and exits 1 if the files are not what this would write.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(HERE, 'src', 'opnsense', 'scripts', 'linkhealth')
CHASSIS = os.path.join(SCRIPTS, 'chassis.json')
FACEPLATES = os.path.join(SCRIPTS, 'faceplates.json')

# Boards checked port by port on the machine; their hand-made entries stand.
HAND_MADE = {'AMDA0202-0004': 'sophos-xgs-3300', 'AMDA0201-0003': 'sophos-xgs-136'}
# Which hand-made entry's groups a generated board takes: the same driver on the same board.
GROUPS_FROM = {'AMDA0202': 'sophos-xgs-3300', 'AMDA0201': 'sophos-xgs-136'}
# Files that are not a product: test fixtures, an evaluation build, a placeholder.
SKIP = re.compile(r'testing|dummy|EVT|mockup', re.I)
TYPES = {'RJ45': 'rj45', 'SFP': 'sfp', 'SFP+': 'sfp+', 'SFP28': 'sfp28', 'QSFP': 'qsfp',
         'QSFP28': 'qsfp28'}


def read(path):
    with open(path, encoding='utf-8', errors='replace') as handle:
        return handle.read()


def description(text):
    m = re.search(r'^#\s*(.+?)\s*$', text, re.M)
    return m.group(1) if m else ''


def product(desc):
    """'1US XG S3300' -> 'XGS 3300'; 'Desktop XGS 136w' -> 'XGS 136w'."""
    rest = re.sub(r'^(1US|1UL|2Ub?|Desktop)\s+', '', desc)
    rest = re.sub(r'^XG S(\d)', r'XGS \1', rest)
    return re.sub(r'\s+', ' ', rest).strip()


def label(raw):
    return 'Port%s' % raw if raw.isdigit() else raw


def parse_board(text):
    keys = dict(re.findall(r'^(npu0\.[^=\s]+)=(.*?)\s*$', text, re.M))
    ports = []
    for key, value in keys.items():
        m = re.match(r'npu0\.phy(\d+)\.label$', key)
        if not m:
            continue
        phy = m.group(1)
        eth = keys.get('npu0.phy%s.eth' % phy, '')
        ports.append({
            'label': label(value),
            'type': TYPES.get(keys.get('npu0.phy%s.type' % phy, ''), 'unknown'),
            'speed': keys.get('npu0.eth%s.init_speed' % eth, ''),
            'poe': ('npu0.phy%s.poe' % phy) in keys,
            'bypass': any(k.startswith('npu0.phy%s.ftw' % phy) for k in keys),
        })
    slots = sorted({m.group(1) for m in (re.match(r'npu0\.slot([A-Z])\.presdet$', k) for k in keys) if m})
    return ports, slots


def parse_module(text):
    """A template: each port's Bphy block (label, socket), then its Ceth block (speed)."""
    ports = []
    for line in text.splitlines():
        m = re.match(r'Bphy%d\.label=F%c(\d+)', line)
        if m:
            ports.append({'n': int(m.group(1)), 'type': 'unknown', 'speed': ''})
            continue
        m = re.match(r'Bphy%d\.type=A(\S+)', line)
        if m and ports:
            ports[-1]['type'] = TYPES.get(m.group(1), 'unknown')
            continue
        m = re.match(r'Ceth%d\.init_speed=A(\S+)', line)
        if m and ports and not ports[-1]['speed']:
            ports[-1]['speed'] = m.group(1)
    return sorted(ports, key=lambda p: p['n'])


def natural(text):
    return [int(t) if t.isdigit() else t for t in re.split(r'(\d+)', text)]


def rows_for(ports):
    """One row per kind of socket and speed, in the order the kinds first appear."""
    order, rows = [], {}
    for p in sorted(ports, key=lambda p: natural(p['label'])):
        kind = p['type'].upper().replace('RJ45', 'RJ45')
        bay = '%s, %s' % (kind, p['speed']) if p['speed'] else kind
        if p['poe']:
            bay += ', PoE'
        if bay not in rows:
            order.append(bay)
            rows[bay] = []
        rows[bay].append({'label': p['label'], 'type': p['type']})
    return [{'bay': bay, 'items': rows[bay]} for bay in order]


def slug(name):
    return 'sophos-' + re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


def build(datadir):
    boards, modules = {}, {}
    for fname in sorted(os.listdir(datadir)):
        m = re.match(r'(AMDA\d{4})-(\d{4})([ab]?)R(\d+)\.txt$', fname)
        if not m:
            continue
        text = read(os.path.join(datadir, fname))
        desc = description(text)
        if SKIP.search(desc):
            continue
        assembly = '%s-%s' % (m.group(1), m.group(2))
        if desc.startswith(('phymod', 'nicmod')):
            modules[assembly] = {'display': re.sub(r'^(phymod|nicmod)\s+', '', desc),
                                 'ports': parse_module(text), 'generated': 'xgs-bsp/data/' + fname}
            continue
        if not text.lstrip().startswith('#') and 'npu0.' not in text:
            continue
        ports, slots = parse_board(text)
        if not ports:
            continue
        entry = boards.setdefault(assembly, {'names': [], 'ports': ports, 'slots': slots,
                                             'rev': -1, 'file': fname})
        name = product(desc)
        if name not in entry['names']:
            entry['names'].append(name)
        if int(m.group(4)) > entry['rev']:          # the newest revision's ports
            entry.update(ports=ports, slots=slots, rev=int(m.group(4)), file=fname)
    return boards, modules


def generate(datadir):
    chassis = json.loads(read(CHASSIS))
    faces = json.loads(read(FACEPLATES))
    boards, modules = build(datadir)

    hand = {k: v for k, v in chassis['models'].items() if not v.get('generated')}
    hand_faces = {k: v for k, v in faces['models'].items() if not v.get('generated')}
    generated, gfaces = {}, {}
    for assembly in sorted(boards):
        if assembly in HAND_MADE:
            continue
        b = boards[assembly]
        name = ' / '.join(b['names'])
        key = slug(name)
        if key in generated:
            # Two assemblies sold under one name (the XGS 6500 is AMDA0204-0002 and -0099):
            # both are kept, each matched on its own number.
            key = '%s-%s' % (key, assembly.lower())
        entry = {
            'display': 'Sophos %s' % name,
            'verified': False,
            'generated': 'xgs-bsp/data/' + b['file'],
            'match': {'maker': '^Sophos$', 'serial': assembly},
        }
        donor = GROUPS_FROM.get(assembly.split('-')[0])
        if donor and donor in hand:
            labels = {p['label'] for p in b['ports']}
            groups = []
            for g in hand[donor].get('groups', []):
                first, last = g['units']
                start = g.get('start', 0)
                names = {(g['label'] % (start + i)) if '%' in g['label'] else g['label']
                         for i in range(last - first + 1)}
                if g['driver'] in ('oxp', 'npup') and not names <= labels:
                    continue          # a port this board does not have
                groups.append(g)
            entry['groups'] = groups
        generated[key] = entry
        gfaces[key] = {'display': 'Sophos %s' % name, 'confirmed': False,
                       'generated': 'xgs-bsp/data/' + b['file'], 'orientation': 'front',
                       'rows': rows_for(b['ports']), 'bays': b['slots']}

    # The hand-made entries' bays, which the drawing had no row for before modules were read.
    for key, assembly in (('sophos-xgs-3300', 'AMDA0202-0004'),):
        if key in hand_faces and assembly in boards:
            hand_faces[key]['bays'] = boards[assembly]['slots']

    # Generated boards first: an exact assembly match must win over a looser hand-made one.
    models = {}
    models.update(generated)
    models.update(hand)
    chassis['models'] = models
    fmodels = {}
    fmodels.update(gfaces)
    fmodels.update(hand_faces)
    faces['models'] = fmodels
    faces['modules'] = {k: modules[k] for k in sorted(modules)}
    for kind, hint in (('sfp28', {'width': 44, 'height': 24, 'shape': 'cage'}),
                       ('qsfp28', {'width': 54, 'height': 26, 'shape': 'cage'})):
        faces['port_types'].setdefault(kind, hint)
    return chassis, faces


def dump(obj):
    return json.dumps(obj, indent=2, ensure_ascii=False) + '\n'


def main(argv):
    if len(argv) < 2:
        print(__doc__.split('\n\n')[1])
        return 64
    chassis, faces = generate(argv[1])
    out = {CHASSIS: dump(chassis), FACEPLATES: dump(faces)}
    if '--check' in argv:
        stale = [os.path.basename(p) for p, text in out.items() if read(p) != text]
        print('out of date: %s' % ', '.join(stale) if stale else 'generated entries current')
        return 1 if stale else 0
    for path, text in out.items():
        with open(path, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
    print('%d boards, %d modules' % (sum(1 for v in chassis['models'].values() if v.get('generated')),
                                     len(faces['modules'])))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
