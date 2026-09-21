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

    Asks a quiet port a direct question.

    A port carrying no traffic produces no errors, so passive watching can
    never clear it or condemn it. This pushes a known number of frames at the
    host on the other end and counts how many came back damaged - the method
    that both proved a fibre fault and, after the cable was fixed, proved it
    gone, on the machine this plugin was written for.

    Two honesty rules are baked in:

      * Packet loss alone is never a verdict. A switch answering a flood of
        pings dropped 0.6% of them with zero damaged frames, because its CPU
        was protecting itself. The damaged-frame count is what is judged; the
        loss figure is shown beside it as context.
      * The test saturates the link while it runs, so it is never automatic,
        never scheduled, and only ever one at a time.
"""

import fcntl
import json
import os
import re
import subprocess
import time

import collector
import state

LOCK = '/var/run/linkhealth-test.lock'


def result_path(interface):
    return os.path.join(state.STATE_DIR, 'test-%s.json' % re.sub(r'[^a-z0-9]', '', interface))


def neighbours(interface):
    """Addresses seen behind this port, nearest thing to "who is plugged in"."""
    snapshot = collector.collect()
    for port in snapshot['ports']:
        if port['if'] == interface:
            return port.get('serves') or []
    return []


def _counters(interface):
    """Everything countable about one port, right now."""
    snapshot = collector.collect()
    for port in snapshot['ports']:
        if port['if'] == interface:
            values = dict(port['counters'])
            totals = port.get('totals') or {}
            values['totals_rx_frames'] = totals.get('rx_frames', 0)
            values['totals_rx_errors'] = totals.get('rx_errors', 0)
            return values, port
    return None, None


def _gave_up(interface, started, message):
    """Record a test that never ran, in the file the page is already watching.

    start_detached() writes "running" into the port's result file before the
    worker is even launched, and nothing else ever rewrites it. A worker that
    gave up without replacing that line would leave the GUI waiting out its
    whole deadline for a test that is not happening, and would leave the port's
    last_test saying "running" in every sweep from then on. So every way out of
    run() that is not a measurement comes through here.

    The timestamps are what let the page tell this answer from the one before
    it: it ignores any result stamped earlier than the moment it asked.
    """
    result = {'status': 'error', 'interface': interface, 'message': message,
              'started': started, 'finished': int(time.time())}
    state.write(result_path(interface), result)
    return result


def run(interface, target=None, count=None, payload=None, thresholds=None):
    """Load the link and measure. Returns the result and also stores it."""
    thresholds = thresholds or collector.load_json('thresholds.json')
    defaults = thresholds['load_test']
    count = int(count or defaults['count'])
    started = int(time.time())

    lock = open(LOCK, 'w')
    try:
        # The worker holds the lock, not whoever asked for the test: a lock
        # held by the starter dies with it, and a second test could then begin
        # while the first is still flooding the link.
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return _gave_up(interface, started, 'another test is already running')

    try:
        before, port = _counters(interface)
        if port is None:
            return _gave_up(interface, started, 'no such port: %s' % interface)
        if port['link']['state'] != 'active':
            return _gave_up(interface, started, 'the link is down, there is nothing to test')

        if not target:
            found = port.get('serves') or []
            if not found:
                return _gave_up(interface, started,
                                'nothing is visible behind this port to test against - '
                                'set a neighbour address in the settings')
            target = found[0]

        if payload is None:
            payload = defaults['payload'] if port.get('mtu', 1500) >= 9000 else defaults['payload_small']

        ping = subprocess.run(
            ['/sbin/ping', '-q', '-f', '-s', str(payload), '-c', str(count), target],
            capture_output=True, text=True, timeout=defaults['timeout_seconds'])
        loss = 0.0
        match = re.search(r'([\d.]+)% packet loss', ping.stdout)
        if match:
            loss = float(match.group(1))

        after, port = _counters(interface)
        deltas = {name: after[name] - before.get(name, 0)
                  for name in after if after[name] >= before.get(name, 0)}

        frames = deltas.get('totals_rx_frames', 0)
        errors = 0
        named = {}
        for name, value in deltas.items():
            meta = port.get('counter_meta', {}).get(name)
            if meta and meta['class'] == 'cable' and value:
                errors += value
                named[name] = value
        if not named:
            errors = deltas.get('totals_rx_errors', 0)

        ppm = int(round(errors * 1000000.0 / frames)) if frames else 0
        verdict = 'clean' if errors == 0 else (
            'failing' if ppm >= thresholds['error_ppm']['fail'] else 'degraded')

        result = {
            'status': 'done',
            'interface': interface,
            'label': port['label'],
            'target': target,
            'started': started,
            'finished': int(time.time()),
            'sent': count,
            'payload': payload,
            'frames': frames,
            'errors': errors,
            'error_ppm': ppm,
            'causes': named,
            'loss_pct': loss,
            'verdict': verdict,
            'note': 'packet loss on its own is not a fault - a busy device may rate-limit pings',
        }
        state.write(result_path(interface), result)
        return result
    except subprocess.TimeoutExpired:
        return _gave_up(interface, started, 'the test did not finish in time')
    finally:
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
        except OSError:
            pass


def start_detached(interface, target=None, count=None):
    """Launch the test and return at once.

    configd waits for whatever it starts, and the GUI cannot sit on an open
    request for two minutes, so the work is handed to a session of its own and
    the browser polls for the file it leaves behind.
    """
    if os.path.exists(LOCK):
        try:
            probe = open(LOCK, 'w')
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(probe, fcntl.LOCK_UN)
            probe.close()
        except OSError:
            return {'status': 'busy', 'message': 'another test is already running'}

    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'linkhealth.py')
    command = ['/usr/local/bin/python3', script, 'runtest', interface]
    if target:
        command.append(target)
    if count:
        command.append(str(count))

    state.write(result_path(interface), {'status': 'running', 'interface': interface,
                                         'started': int(time.time())})
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return {'status': 'started', 'interface': interface}


def last_result(interface):
    return state.read(result_path(interface), {'status': 'none'})
