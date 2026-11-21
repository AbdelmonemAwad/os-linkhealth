#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Abdelmonem Awad <eg2@live.com>
"""The ARMADA boards' unit-to-label groups and the XGS 87 drawing, checked without a firewall.

    python3 tools/test-faceplate.py [--svg <file>]

- the groups name npup1..npup5 on an XGS 87 Port1..Port4 and F1, as os-xgs-npu does, and the
  other ARMADA boards as npuep's front-port table numbers them;
- the faceplate join gives each of those sockets its port's state, and none comes back absent,
  while a power inlet or a reset hole is drawn and never joined;
- the page's draw_bay, run in node where node is installed, sets the copper block in two lines
  beside F1 (without node the same is checked on the data);
- the generator, run on board files written here by hand, keeps the hand-made XGS 87 drawing and
  still writes the board's chassis entry and its groups.

--svg writes the drawing node made to a file, to be looked at."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, 'src', 'opnsense', 'scripts', 'linkhealth')
VIEW = os.path.join(ROOT, 'src', 'opnsense', 'mvc', 'app', 'views', 'OPNsense', 'LinkHealth', 'index.volt')
failures = 0


def check(name, got, want):
    global failures
    ok = got == want
    failures += 0 if ok else 1
    print('%s %s%s' % ('ok  ' if ok else 'FAIL', name, '' if ok else ' - got %r, want %r' % (got, want)))


# Two modules the plugin imports exist only on a Unix; stand-ins let this run anywhere.
for missing in ('syslog', 'fcntl'):
    try:
        __import__(missing)
    except ImportError:
        stub = types.ModuleType(missing)
        stub.LOG_NOTICE = stub.LOG_WARNING = stub.LOG_ERR = stub.LOG_DAEMON = 0
        stub.syslog = stub.openlog = stub.flock = lambda *a, **k: None
        stub.LOCK_EX = stub.LOCK_NB = stub.LOCK_UN = 0
        sys.modules[missing] = stub
sys.path.insert(0, SCRIPTS)
import collector    # noqa: E402
import linkhealth   # noqa: E402

with open(os.path.join(SCRIPTS, 'chassis.json'), encoding='utf-8') as handle:
    CHASSIS = json.load(handle)['models']
with open(os.path.join(SCRIPTS, 'faceplates.json'), encoding='utf-8') as handle:
    FACES = json.load(handle)


def labels(table, units):
    return [collector.label_for('npup', unit, CHASSIS[table])['label'] for unit in units]


# (a) the groups, as npuep numbers each board
EIGHTY_SEVEN = ['Port1', 'Port2', 'Port3', 'Port4', 'F1']
check('XGS 87: npup1..npup5', labels('sophos-xgs-87', range(1, 6)), EIGHTY_SEVEN)
check('XGS 87w: npup1..npup5', labels('sophos-xgs-87w', range(1, 6)), EIGHTY_SEVEN)
check('XGS 87: no npup6', labels('sophos-xgs-87', [6]), [None])
ONE_O_SEVEN = ['Port%d' % n for n in range(1, 9)] + ['F1']
for table in ('sophos-xgs-107', 'sophos-xgs-107w', 'sophos-xgs-116', 'sophos-xgs-116w'):
    check('%s: npup1..npup9' % table, labels(table, range(1, 10)), ONE_O_SEVEN)
check('XGS 138: npup1..npup8', labels('sophos-xgs-138', range(1, 9)),
      ['Port1', 'Port2', 'Port3', 'Port4', 'Port5', 'Port6', 'F1', 'F2'])
record = collector.label_for('npup', 5, CHASSIS['sophos-xgs-87'])
check('XGS 87: F1 lit through the coprocessor', (record['identify'], record['identify_led']),
      ('coprocessor', True))
check('XGS 136 groups unchanged', labels('sophos-xgs-136', [1, 12, 13, 14]), ['Port1', 'Port12', 'F1', 'F2'])

# (b) the join: what the XGS 87 drawing makes of its five ports, two up and three not
STATES = {1: 'ok', 2: 'ok', 3: 'down', 4: 'down', 5: 'disabled'}
ports = []
for unit, state in STATES.items():
    placed = collector.label_for('npup', unit, CHASSIS['sophos-xgs-87'])
    ports.append({'if': 'npup%d' % unit, 'label': placed['label'], 'bay': placed['bay'], 'name': '',
                  'verdict': {'state': state}, 'window': {'error_ppm': 0},
                  'link': {'speed_mbps': 1000 if state == 'ok' else 0}, 'serves': [], 'caps': []})
linkhealth.current = lambda: {'chassis': {'table': 'sophos-xgs-87'}, 'ports': ports, 'generated': 0}
linkhealth.identify.can_flicker = lambda port: False
doc = linkhealth.faceplate()
check('XGS 87: a drawing', (doc['available'], doc['orientation'], doc['confirmed']), (True, 'rear', False))
items = [item for row in doc['rows'] for item in row['items']]
joined = {item['label']: (item['if'], item['state']) for item in items if item.get('join') is not False}
check('XGS 87: each socket joined to its port', joined, {
    'F1': ('npup5', 'disabled'), 'Port1': ('npup1', 'ok'), 'Port2': ('npup2', 'ok'),
    'Port3': ('npup3', 'down'), 'Port4': ('npup4', 'down')})
fixtures = [item for item in items if item.get('join') is False]
check('XGS 87: drawn and not joined', [(i['label'], 'state' in i, 'if' in i) for i in fixtures],
      [('DC IN 1', False, False), ('COM', False, False), ('USB', False, False), ('Reset', False, False)])
check('XGS 87: printed texts', [i['display'] for i in items if i['label'].startswith('Port')],
      ['1/LAN', '2/WAN', '3/DMZ', '4'])

# (c) the two lines, on the data: each `below` item joins the column before it
columns = []
for item in FACES['models']['sophos-xgs-87']['rows'][0]['items']:
    if item.get('below') and columns:
        columns[-1].append(item['label'])
    else:
        columns.append([item['label']])
check('XGS 87: columns as on the back', columns,
      [['DC IN 1'], ['COM', 'USB'], ['F1'], ['Port1', 'Port2'], ['Port3', 'Port4'], ['Reset']])
for name in ('dc', 'reset', 'console', 'usb', 'sfp', 'rj45'):
    check('port type %s has a hint' % name, name in FACES['port_types'], True)

# (c) the two lines, as the page draws them
NODE = shutil.which('node')
SHIM = r"""
const SVG_NS = 'svg';
function Node(name) { this.name = name; this.attrs = {}; this.kids = []; this.textContent = ''; }
Node.prototype.setAttribute = function (k, v) { this.attrs[k] = v; };
Node.prototype.appendChild = function (n) { this.kids.push(n); return n; };
Node.prototype.xml = function () {
    const esc = (t) => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
    const a = Object.keys(this.attrs).map((k) => ' ' + k + '="' + esc(this.attrs[k]) + '"').join('');
    return '<' + this.name + a + '>' + esc(this.textContent) + this.kids.map((n) => n.xml()).join('') +
        '</' + this.name + '>';
};
const document = {createElementNS: (ns, name) => new Node(name)};
const verdict_text = {};
const $ = {
    each: (o, f) => { if (Array.isArray(o)) { o.forEach((v, i) => f(i, v)); } else { Object.keys(o || {}).forEach((k) => f(k, o[k])); } },
    extend: Object.assign
};
"""
if NODE is None:
    print('skip draw_bay in node: node is not installed here')
else:
    with open(VIEW, encoding='utf-8') as handle:
        page = handle.read()
    start = page.index('        const FP_PAD = ')
    end = page.index('        /* What the drawing is made of, as one string.')
    code = page[start:end]
    code = code.replace('const SVG_NS =', 'const SVG_NS_PAGE =')
    layout = FACES['models']['sophos-xgs-87']['rows'][0]
    script = SHIM + code + (
        '\nconst row = %s;\nconst types = %s;\nconst svg = draw_bay(row, 0, types);\n'
        "svg.attrs.xmlns = 'http://www.w3.org/2000/svg';\n"
        'process.stdout.write(svg.xml());\n') % (json.dumps(layout), json.dumps(FACES['port_types']))
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'draw.js')
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(script)
        run = subprocess.run([NODE, path], capture_output=True, text=True, encoding='utf-8')
    check('draw_bay runs in node', (run.returncode, run.stderr.strip()), (0, ''))
    svg = run.stdout
    spots = {}
    for match in re.finditer(r'<g class="([^"]*)"(?: data-key="([^"]*)")? transform="translate\(([-\d.]+),([-\d.]+)\)">'
                             r'.*?<text[^>]*>([^<]*)</text>', svg):
        spots[match.group(5)] = (match.group(1), float(match.group(3)), float(match.group(4)))
    check('drawn texts', sorted(spots), sorted(['DC IN 1', 'COM', 'USB', 'F1', '1/LAN', '2/WAN', '3/DMZ',
                                                '4', 'Reset']))
    if len(spots) == 9:
        check('2/WAN under 1/LAN', (spots['2/WAN'][1] == spots['1/LAN'][1], spots['2/WAN'][2] > spots['1/LAN'][2]),
              (True, True))
        check('4 under 3/DMZ', (spots['4'][1] == spots['3/DMZ'][1], spots['4'][2] > spots['3/DMZ'][2]), (True, True))
        check('USB under COM', spots['USB'][2] > spots['COM'][2], True)
        order = sorted(['DC IN 1', 'COM', 'F1', '1/LAN', '3/DMZ', 'Reset'], key=lambda t: spots[t][1])
        check('left to right', order, ['DC IN 1', 'COM', 'F1', '1/LAN', '3/DMZ', 'Reset'])
        check('ports are sockets, the rest fixtures',
              sorted(t for t, s in spots.items() if 'lh-fp-socket' in s[0]),
              sorted(['F1', '1/LAN', '2/WAN', '3/DMZ', '4']))
        check('sockets keyed by their place in the row', [s for s in re.findall(r'data-key="([^"]*)"', svg)],
              ['r0i3', 'r0i4', 'r0i5', 'r0i6', 'r0i7'])
    if '--svg' in sys.argv:
        with open(sys.argv[sys.argv.index('--svg') + 1], 'w', encoding='utf-8') as handle:
            handle.write(svg)

# (d) the generator keeps the hand-made drawing, on board files written here
spec = importlib.util.spec_from_file_location('gen', os.path.join(HERE, 'gen-xgs-hardware.py'))
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)
BOARD = ''.join('npu0.phy%d.label=%s\nnpu0.phy%d.eth=%d\nnpu0.phy%d.type=%s\nnpu0.eth%d.init_speed=1G\n'
                % (n, label, n, n, n, kind, n)
                for n, (label, kind) in enumerate([('1', 'RJ45'), ('2', 'RJ45'), ('3', 'RJ45'), ('4', 'RJ45'),
                                                   ('F1', 'SFP')]))
with tempfile.TemporaryDirectory() as tmp:
    for name, desc in (('AMDA0200-0001R03.txt', 'Desktop XGS 87'), ('AMDA0200-0002R03.txt', 'Desktop XGS 87w')):
        with open(os.path.join(tmp, name), 'w', encoding='utf-8') as handle:
            handle.write('# %s\n%s' % (desc, BOARD))
    chassis, faces = gen.generate(tmp)
drawn = faces['models']['sophos-xgs-87']
check('regenerated: the XGS 87 drawing stands', drawn, FACES['models']['sophos-xgs-87'])
check('regenerated: the XGS 87 chassis entry is generated',
      bool(chassis['models']['sophos-xgs-87'].get('generated')), True)
check('regenerated: its groups', [(g['units'], g['label']) for g in chassis['models']['sophos-xgs-87']['groups']],
      [([1, 4], 'Port%d'), ([5, 5], 'F%d')])
check('regenerated: the XGS 87w drawing is generated', bool(faces['models']['sophos-xgs-87w'].get('generated')), True)

print('%d failure(s)' % failures if failures else 'all passed')
sys.exit(1 if failures else 0)
