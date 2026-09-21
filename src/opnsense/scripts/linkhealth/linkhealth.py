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

    Link Health - the command every other part of the plugin goes through.

        linkhealth.py collect          one sweep; this is what cron calls
        linkhealth.py status           the current picture, as JSON
        linkhealth.py detail <if>      one port, with its last test
        linkhealth.py test <if> [ip]   start a load test, return at once
        linkhealth.py runtest <if>     the load test itself (started above)
        linkhealth.py testresult <if>  how that test ended
        linkhealth.py faceplate        the front panel, joined to live state
        linkhealth.py identify <if>    blink that port's LED, return at once
        linkhealth.py flicker <if>     beat that port's activity light, return at once
        linkhealth.py stopidentify     stop the blinking or the beat early
        linkhealth.py testmail         prove the mail path works
        linkhealth.py check            exit non-zero if any port is failing,
                                       for people who want Monit watching too
"""

import json
import os
import sys
import syslog
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import alerts        # noqa: E402
import collector     # noqa: E402
import exporters     # noqa: E402
import flaps         # noqa: E402
import identify      # noqa: E402
import loadtest      # noqa: E402
import state         # noqa: E402
import verdict as verdicts  # noqa: E402

CONFIG = '/conf/config.xml'
MODEL = './OPNsense/LinkHealth'


def log(message, level=syslog.LOG_NOTICE):
    syslog.openlog('linkhealth', syslog.LOG_PID, syslog.LOG_DAEMON)
    syslog.syslog(level, message)
    syslog.closelog()


def settings(thresholds):
    """Plugin settings, with the shipped defaults behind every one of them.

    Written so that a fresh installation works before anybody opens the
    settings page: an absent field is not an error, it is the default.
    """
    values = {
        'enabled': True,
        'poll_interval': 60,
        'min_frames': thresholds['min_frames'],
        'ppm_watch': thresholds['error_ppm']['watch'],
        'ppm_warn': thresholds['error_ppm']['warn'],
        'ppm_fail': thresholds['error_ppm']['fail'],
        'flap_window': thresholds['flaps']['window_seconds'],
        'flap_warn': thresholds['flaps']['warn'],
        'flap_fail': thresholds['flaps']['fail'],
        'windows_before_alert': thresholds['windows_before_alert'],
        'cooldown_hours': thresholds['alerting']['cooldown_hours'],
        'send_recovery': thresholds['alerting']['send_recovery'],
        'recipients': '',
        'node_exporter': True,
        'test_count': thresholds['load_test']['count'],
        'test_payload': thresholds['load_test']['payload'],
        'test_timeout': thresholds['load_test']['timeout_seconds'],
        'ports': {},
    }

    try:
        root = ET.parse(CONFIG).getroot()
    except Exception:
        return values, None

    general = root.find(MODEL + '/general')
    if general is not None:
        def text(name, default=None):
            found = general.findtext(name)
            return found if found not in (None, '') else default

        values['enabled'] = text('enabled', '1') == '1'
        values['send_recovery'] = text('recovery_mail', '1') == '1'
        values['node_exporter'] = text('node_exporter', '1') == '1'
        # No address, no mail. The firewall's Monit credentials are used to
        # send, but never its recipient: nobody should be surprised by post
        # from a plugin they have not finished configuring.
        values['recipients'] = text('alert_address', '') or ''

        for key, name in (
            ('poll_interval', 'poll_interval'),
            ('min_frames', 'min_frames'),
            ('ppm_watch', 'ppm_watch'),
            ('ppm_warn', 'ppm_warn'),
            ('ppm_fail', 'ppm_fail'),
            ('flap_window', 'flap_window'),
            ('cooldown_hours', 'cooldown_hours'),
            ('test_count', 'test_count'),
            ('test_payload', 'test_payload'),
            ('test_timeout', 'test_timeout'),
        ):
            raw = text(name)
            if raw is not None:
                try:
                    values[key] = int(raw)
                except ValueError:
                    pass

        # One knob in the GUI, two thresholds underneath: the settings page
        # asks how many link changes are too many, and a link that is doing it
        # three times as often as that is past warning about.
        raw = text('flap_threshold')
        if raw is not None:
            try:
                values['flap_warn'] = int(raw)
                values['flap_fail'] = max(int(raw) * 3, int(raw) + 1)
            except ValueError:
                pass

    section = root.find(MODEL + '/ports')
    if section is not None:
        for node in section:
            device = node.findtext('interface')
            if not device:
                continue
            values['ports'][device] = {
                'label': node.findtext('label') or '',
                'watch': (node.findtext('enabled') or '1') == '1',
                'neighbour': node.findtext('neighbour') or '',
            }

    return values, root


def sweep(now=None, quiet=False):
    """One complete cycle: measure, compare, judge, store, tell."""
    now = int(now or time.time())
    thresholds = collector.load_json('thresholds.json')
    options, root = settings(thresholds)

    thresholds['min_frames'] = options['min_frames']
    thresholds['error_ppm'] = {'watch': options['ppm_watch'],
                               'warn': options['ppm_warn'],
                               'fail': options['ppm_fail']}
    thresholds['flaps'].update({'window_seconds': options['flap_window'],
                                'warn': options['flap_warn'],
                                'fail': options['flap_fail']})

    snapshot = collector.collect()
    boot = collector.boottime()
    baseline = state.load_baseline(boot)
    previous_ports = (baseline or {}).get('ports', {})
    window = now - (baseline or {}).get('taken', now) or 0

    events = flaps.recent(thresholds['flaps']['window_seconds'],
                          thresholds['flaps']['debounce_seconds'], now)

    # What each port has gathered since it was last judged. A busy port clears
    # the bar inside one window and is judged every minute as before; a quiet
    # one keeps adding until it has enough to say something true.
    gathered = state.load_accumulator()
    gathered_ports = dict(gathered.get('ports') or {})
    max_age = int(thresholds.get('max_accumulate_seconds', 3600))

    ports = []
    new_baseline = {}

    for port in snapshot['ports']:
        device = port['if']
        override = options['ports'].get(device, {})
        if override.get('label'):
            port['label'] = override['label']
            port['labelled'] = True

        sample = dict(port['counters'])
        totals = port.get('totals') or {}
        sample['totals_rx_frames'] = totals.get('rx_frames', 0)
        sample['totals_rx_errors'] = totals.get('rx_errors', 0)
        sample['totals_tx_errors'] = totals.get('tx_errors', 0)
        new_baseline[device] = {'counters': sample, 'optics': port.get('optics')}

        deltas, usable = state.diff_counters((previous_ports.get(device) or {}).get('counters'), sample)
        if '_rx_frames' not in deltas and 'totals_rx_frames' in deltas:
            deltas['_rx_frames'] = deltas['totals_rx_frames']

        window_frames = deltas.get('_rx_frames', 0) if usable else 0
        entry, total_frames, age, ready = state.accumulate(
            gathered_ports.get(device), deltas if usable else {}, window_frames,
            now, thresholds['min_frames'], max_age)

        # The verdict is computed on everything gathered, not on the last
        # minute alone, so the numbers the GUI shows are the numbers it judged.
        judged = dict(entry.get('counters') or {})
        judged['_rx_frames'] = total_frames
        judged['totals_rx_frames'] = total_frames

        if not override.get('watch', True):
            judgement = {'state': 'disabled', 'reasons': [
                {'code': 'not_watched', 'severity': 'info',
                 'text': 'this port is not being watched', 'params': {}}]}
            gathered_ports[device] = entry
        elif not usable:
            judgement = verdicts.judge(port, deltas, usable, events.get(device),
                                       thresholds, window)
            gathered_ports[device] = state.reset_accumulator_entry(now)
        elif ready:
            judgement = verdicts.judge(port, judged, True, events.get(device),
                                       thresholds, age)
            swapped = verdicts.module_changed(port, (previous_ports.get(device) or {}).get('optics'))
            if swapped:
                judgement['reasons'].append(swapped)
                if verdicts.ORDER.get(swapped['severity'], 0) > verdicts.ORDER.get(judgement['state'], 0):
                    judgement['state'] = swapped['severity']
            gathered_ports[device] = state.reset_accumulator_entry(now)
        else:
            # Not enough yet, and honest about it: this is a port still being
            # measured, not a port that has been found healthy.
            judgement = {'state': 'idle', 'reasons': [{
                'code': 'gathering', 'severity': 'info',
                'text': 'measuring: %s of %s frames so far, over %d min'
                        % (f'{total_frames:,}', f"{thresholds['min_frames']:,}", max(1, age // 60)),
                'params': {'frames': total_frames, 'needed': thresholds['min_frames'],
                           'seconds': age}}]}
            gathered_ports[device] = entry
        frames = judged.get('_rx_frames', 0)
        errors = judgement.get('errors', 0)
        ports.append({
            'if': device,
            'label': port['label'],
            'labelled': port['labelled'],
            'name': port['name'],
            'confkey': port['confkey'],
            'bay': port['bay'],
            'bay_note': port['bay_note'],
            'identify_note': port.get('identify_note', ''),
            'driver': port['driver'],
            'chip': port['chip'],
            'pci': port.get('pci', {}),
            'label_source': 'override' if override.get('label') else port.get('label_source', 'interface'),
            'label_refused': port.get('label_refused', ''),
            'kind': port['kind'],
            'caps': port['caps'],
            'mtu': port['mtu'],
            'serves': port['serves'],
            'neighbour': port.get('neighbour', {}),
            'link': port['link'],
            'totals': totals,
            'window': {
                'seconds': age,
                'rx_frames': frames,
                'rx_errors': errors,
                'error_ppm': judgement.get('ppm', 0),
                'ready': ready,
                'counters': {name: value for name, value in (entry.get('counters') or {}).items()
                             if value and not name.startswith(('_', 'totals_'))},
            },
            'last_minute': {
                'seconds': window,
                'rx_frames': window_frames,
                'counters': {name: value for name, value in deltas.items()
                             if value and not name.startswith(('_', 'totals_'))},
            },
            'counter_meta': port['counter_meta'],
            'optics': port['optics'],
            'flaps': events.get(device, {'count': 0, 'last': None, 'events': []}),
            'verdict': judgement,
            'last_test': loadtest.last_result(device),
        })

    ports.sort(key=lambda item: (-verdicts.ORDER.get(item['verdict']['state'], 0), item['label']))

    status = {
        'generated': now,
        'boottime': boot,
        'window_seconds': window,
        'chassis': snapshot['chassis'],
        'ports': ports,
    }

    state.save_accumulator({'ports': gathered_ports, 'updated': now})
    state.save_baseline(boot, new_baseline, now)
    state.write(state.STATUS, status)
    state.append_history(status, now)

    if options['node_exporter']:
        exporters.write(status)

    # Nothing is mailed until there is somebody to mail, and never on the
    # first window after a reboot - there is nothing to compare against yet.
    if options['recipients'] and baseline is not None and not quiet:
        notify(status, options, root)

    return status


def notify(status, options, root):
    """Send at most one message, about everything that changed."""
    faults, recoveries, memory = alerts.decide(status, state.load_alerts(), options)
    state.save_alerts(memory)
    if not faults and not recoveries:
        return

    try:
        mail = alerts.mail_settings(root)
        recipients = [address.strip() for address in options['recipients'].split(',') if address.strip()]
        if not recipients:
            log('ports changed state but no recipient is configured', syslog.LOG_WARNING)
            return
        subject, body_html, body_text = alerts.compose(
            status, faults, recoveries, alerts.system_language())
        alerts.send(mail, recipients, subject, body_html, body_text)
        log('mailed %d fault(s) and %d recovery(ies) to %s'
            % (len(faults), len(recoveries), ', '.join(recipients)))
    except Exception as failure:
        log('could not send mail: %s' % failure, syslog.LOG_ERR)


def faceplate():
    """The front of the appliance, joined to what each socket is doing.

    The layout knows only bays and printed labels; the live state is matched on
    that pair. No interface name appears in the layout file, so a drawing that
    is wrong about where a socket sits cannot be wrong about which port an
    alert belongs to.
    """
    status = current()
    layouts = collector.load_json('faceplates.json')
    table = (status.get('chassis') or {}).get('table', 'generic')
    model = (layouts.get('models') or {}).get(table)

    by_label = {}
    for port in status.get('ports', []):
        by_label[(port.get('bay', ''), port.get('label', ''))] = port

    if not model:
        return {'available': False,
                'reason': 'no faceplate drawing for this model yet',
                'chassis': status.get('chassis'),
                'ports': [{'label': p['label'], 'if': p['if'],
                           'state': p['verdict']['state']} for p in status.get('ports', [])]}

    rows = []
    for row in model.get('rows', []):
        items = []
        for item in row.get('items', []):
            port = by_label.get((row.get('bay', ''), item['label']))
            if port is None:
                # fall back to the label alone: a bay may have been renamed by
                # hand without the drawing following it
                port = next((p for p in status.get('ports', [])
                             if p.get('label') == item['label']), None)
            items.append({
                'label': item['label'],
                'type': item.get('type', 'unknown'),
                'if': port['if'] if port else None,
                'name': port.get('name') if port else '',
                'state': port['verdict']['state'] if port else 'absent',
                'error_ppm': (port.get('window') or {}).get('error_ppm', 0) if port else 0,
                'speed_mbps': (port.get('link') or {}).get('speed_mbps', 0) if port else 0,
                'serves': port.get('serves', []) if port else [],
                'can_identify': bool(port and 'IDENTIFY_LED' in port.get('caps', [])),
                'can_flicker': bool(port and identify.can_flicker(port)),
            })
        rows.append({'bay': row.get('bay', ''), 'note': row.get('note', ''), 'items': items})

    return {
        'available': True,
        'display': model.get('display', ''),
        'confirmed': bool(model.get('confirmed')),
        'confirmed_note': model.get('confirmed_note', ''),
        'orientation': model.get('orientation', 'front'),
        'port_types': layouts.get('port_types', {}),
        'chassis': status.get('chassis'),
        'generated': status.get('generated'),
        'rows': rows,
    }


def current():
    """The stored picture, or a fresh one when nothing has run yet."""
    stored = state.read(state.STATUS)
    return stored if stored else sweep()


def main(argv):
    command = argv[1] if len(argv) > 1 else 'status'

    if command == 'collect':
        thresholds = collector.load_json('thresholds.json')
        options, _ = settings(thresholds)
        if not options['enabled']:
            return 0
        boot = collector.boottime()
        previous = state.load_baseline(boot)
        if previous and int(time.time()) - previous.get('taken', 0) < options['poll_interval'] - 5:
            return 0
        # "collect dry" measures and stores but never mails - what you want
        # while setting thresholds, and what the installer runs once so the
        # first real cycle already has something to compare against.
        sweep(quiet=len(argv) > 2 and argv[2] == 'dry')
        return 0

    if command == 'status':
        print(json.dumps(current()))
        return 0

    if command == 'detail' and len(argv) > 2:
        wanted = argv[2]
        for port in current().get('ports', []):
            if port['if'] == wanted:
                print(json.dumps(port))
                return 0
        print(json.dumps({'status': 'error', 'message': 'no such port'}))
        return 1

    if command == 'test' and len(argv) > 2:
        print(json.dumps(loadtest.start_detached(
            argv[2], argv[3] if len(argv) > 3 else None,
            argv[4] if len(argv) > 4 else None)))
        return 0

    if command == 'runtest' and len(argv) > 2:
        thresholds = collector.load_json('thresholds.json')
        options, _ = settings(thresholds)
        thresholds['load_test'].update({'count': options['test_count'],
                                        'payload': options['test_payload'],
                                        'timeout_seconds': options['test_timeout']})
        result = loadtest.run(argv[2], argv[3] if len(argv) > 3 else None,
                              argv[4] if len(argv) > 4 else None, thresholds=thresholds)
        log('load test on %s: %s' % (argv[2], result.get('verdict', result.get('status'))))
        return 0

    if command == 'faceplate':
        print(json.dumps(faceplate()))
        return 0

    if command == 'identify' and len(argv) > 2:
        print(json.dumps(identify.start_detached(
            argv[2], int(argv[3]) if len(argv) > 3 else identify.DEFAULT_SECONDS)))
        return 0

    if command == 'blink' and len(argv) > 2:
        # the worker started above; it holds the lock while the LED is lit
        result = identify.blink(argv[2], int(argv[3]) if len(argv) > 3 else identify.DEFAULT_SECONDS)
        log('identify %s: %s' % (argv[2], result.get('status')))
        return 0

    if command == 'flicker' and len(argv) > 2:
        print(json.dumps(identify.start_flicker_detached(
            argv[2],
            int(argv[3]) if len(argv) > 3 else identify.DEFAULT_SECONDS,
            argv[4] if len(argv) > 4 else None)))
        return 0

    # The worker the line above detaches. The fifth argument is the descriptor of the
    # lock the starter already took: inheriting it is what makes "started" true, instead
    # of a promise the worker might not be able to keep half a second later.
    if command == 'runflicker' and len(argv) > 2:
        result = identify.flicker(
            argv[2],
            int(argv[3]) if len(argv) > 3 else identify.DEFAULT_SECONDS,
            argv[4] if len(argv) > 4 and argv[4] else None,
            int(argv[5]) if len(argv) > 5 and argv[5].isdigit() else None)
        log('flicker %s: %s' % (argv[2], result.get('status')))
        print(json.dumps(result))
        return 0

    if command == 'stopidentify':
        print(json.dumps(identify.stop()))
        return 0

    if command == 'testresult' and len(argv) > 2:
        print(json.dumps(loadtest.last_result(argv[2])))
        return 0

    if command == 'testmail':
        thresholds = collector.load_json('thresholds.json')
        options, root = settings(thresholds)
        try:
            mail = alerts.mail_settings(root)
            recipients = [address.strip() for address in options['recipients'].split(',') if address.strip()]
            if not recipients:
                print(json.dumps({'status': 'error',
                                  'message': 'set an alert address in the settings first'}))
                return 1
            status = current()
            worst = status['ports'][0] if status.get('ports') else None
            subject, body_html, body_text = alerts.compose(
                status, [worst] if worst else [], [], alerts.system_language())
            alerts.send(mail, recipients, '[test] ' + subject, body_html, body_text)
            print(json.dumps({'status': 'ok', 'sent_to': recipients}))
            return 0
        except Exception as failure:
            print(json.dumps({'status': 'error', 'message': str(failure)}))
            return 1

    if command == 'check':
        # For an optional Monit check program: silence means healthy.
        failing = [port for port in current().get('ports', [])
                   if port['verdict']['state'] == 'fail']
        if failing:
            worst = failing[0]
            message = '%s: %s' % (worst['label'], worst['verdict']['reasons'][0]['text'])
            print(message)
            print(message, file=sys.stderr)
            return 1
        return 0

    print(json.dumps({'status': 'error', 'message': 'unknown command: %s' % command}))
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
