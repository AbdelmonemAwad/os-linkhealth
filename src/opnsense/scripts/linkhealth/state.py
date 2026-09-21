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

    Everything this plugin remembers between runs.

    The counters the hardware offers are cumulative and therefore useless on
    their own: one port on the machine this was written for has carried 5,945
    input errors since boot while being perfectly clean for hours. What matters
    is the difference between two samples, so the previous sample is kept here
    and the difference is what the rest of the plugin sees.

    Two rules protect that difference from lying:

      * the baseline records the kernel's boot time, and a change in it means
        the counters restarted from zero - the sample that spans a reboot is
        discarded rather than reported as a huge negative or a huge positive;
      * any counter that came back lower than last time is treated the same
        way, because a counter only goes backwards when it has wrapped or been
        reset.

    Writes are deliberately small and rare. The disk in the reference machine
    has 31% of its write endurance consumed, and no diagnostic is worth
    spending the rest of it.
"""

import json
import os
import tempfile
import time

STATE_DIR = '/var/db/linkhealth'
BASELINE = os.path.join(STATE_DIR, 'baseline.json')
STATUS = os.path.join(STATE_DIR, 'status.json')
ALERTS = os.path.join(STATE_DIR, 'alerts.json')
ACCUMULATOR = os.path.join(STATE_DIR, 'accumulator.json')
HISTORY = os.path.join(STATE_DIR, 'history.json')
HISTORY_INTERVAL = 300
HISTORY_KEEP = 576  # two days at one sample every five minutes


def ensure_dir():
    if not os.path.isdir(STATE_DIR):
        os.makedirs(STATE_DIR, mode=0o750, exist_ok=True)
        os.chmod(STATE_DIR, 0o750)


def read(path, default=None):
    try:
        with open(path, 'r') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default if default is not None else {}


def write(path, payload, mode=0o640):
    """Write a file in one step, so a reader never sees half of it."""
    ensure_dir()
    directory = os.path.dirname(path)
    handle, temporary = tempfile.mkstemp(dir=directory, prefix='.tmp-')
    try:
        with os.fdopen(handle, 'w') as stream:
            json.dump(payload, stream, separators=(',', ':'))
        os.chmod(temporary, mode)
        os.rename(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def diff_counters(previous, current):
    """Difference between two samples of the same counter set.

    Returns (deltas, usable). usable is False when the numbers cannot be
    compared - a counter went backwards, or there is nothing to compare with
    yet - and the caller must then treat this window as a baseline only.
    """
    if not previous:
        return {}, False

    deltas = {}
    for name, value in current.items():
        if name not in previous:
            continue
        before = previous[name]
        if value < before:
            return {}, False
        deltas[name] = value - before
    return deltas, bool(deltas)


def load_baseline(boot):
    """The previous sample, or nothing when it cannot be trusted."""
    saved = read(BASELINE)
    if not saved:
        return None
    if saved.get('boottime') != boot:
        return None  # the machine rebooted; the counters started again
    return saved


def save_baseline(boot, ports, now=None):
    write(BASELINE, {
        'boottime': boot,
        'taken': int(now or time.time()),
        'ports': ports,
    })


def append_history(status, now=None):
    """Keep a small rolling record, one sample every five minutes.

    Only what a graph needs: per port, the error rate and the link speed. The
    full status is rewritten every cycle anyway, and keeping copies of it would
    turn a diagnostic into a disk-wear problem.
    """
    now = int(now or time.time())
    history = read(HISTORY, {'samples': []})
    samples = history.get('samples') or []
    if samples and now - samples[-1].get('at', 0) < HISTORY_INTERVAL:
        return

    samples.append({
        'at': now,
        'ports': {
            port['if']: {
                'ppm': port['window'].get('error_ppm', 0),
                'mbps': port['link'].get('speed_mbps', 0),
                'state': port['verdict']['state'],
            }
            for port in status.get('ports', [])
        },
    })
    write(HISTORY, {'samples': samples[-HISTORY_KEEP:]})


def load_accumulator():
    return read(ACCUMULATOR, {'ports': {}})


def save_accumulator(payload):
    write(ACCUMULATOR, payload)


def accumulate(entry, deltas, frames, now, min_frames, max_age):
    """Add one window to what a port has gathered, and say whether it is enough.

    A one-minute window is plenty on a busy port and worthless on a quiet one.
    The firewall this was written for has gigabit ports carrying two hundred
    frames a minute, because almost everything goes over the 10G link: judging
    each minute on its own, those ports would read "not enough traffic" for
    ever and never be judged at all.

    So the window is not thrown away when it is too small - it is added to what
    came before, and the verdict waits until there is enough to mean something.
    A busy port still gets a verdict every minute, because one minute already
    clears the bar. A quiet one gets a slower verdict instead of none.

    Returns (totals, frames, seconds, ready).
    """
    entry = entry or {'since': now, 'frames': 0, 'counters': {}}
    entry['frames'] = entry.get('frames', 0) + max(0, frames)
    counters = entry.setdefault('counters', {})
    for name, value in (deltas or {}).items():
        if value and not name.startswith('_'):
            counters[name] = counters.get(name, 0) + value

    age = max(1, now - entry.get('since', now))
    ready = entry['frames'] >= min_frames or age >= max_age
    return entry, entry['frames'], age, ready


def reset_accumulator_entry(now):
    return {'since': now, 'frames': 0, 'counters': {}}


def load_alerts():
    return read(ALERTS, {'ports': {}})


def save_alerts(payload):
    write(ALERTS, payload)
