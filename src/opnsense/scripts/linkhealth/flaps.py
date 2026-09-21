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

    Counts how often a port's link has come and gone.

    The kernel announces every transition in the system log, and that log is
    read rather than dmesg for two reasons: the dmesg buffer wraps, so a
    long-running machine loses its own history, and it stamps events with
    seconds since boot instead of a time of day.

    Transitions arrive in bursts. Reseating one cable on the reference machine
    produced five kernel lines inside the same second, which is one event to a
    human, so events closer together than the debounce interval are collapsed.

    The driver's own local_faults / remote_faults counters are NOT used for
    this. They increment on administrative up and down as well, so both healthy
    ports on the reference machine carry hundreds of them.
"""

import datetime
import os
import re

LOG = '/var/log/system/latest.log'
MAX_BYTES = 2 * 1024 * 1024

# "<6>[9065] ix1: link state changed to UP" inside an RFC 5424 line that
# begins with the timestamp we need.
_LINE = re.compile(
    r'(?P<when>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}).*?'
    r'(?P<if>[a-z][a-z0-9_]*\d): link state changed to (?P<state>UP|DOWN)'
)


def _tail(path, limit):
    """The last `limit` bytes of a file, decoded loosely.

    The log holds every subsystem's output, so it can be large; an hour of link
    events always lives at the end of it.
    """
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as handle:
            if size > limit:
                handle.seek(size - limit)
                handle.readline()  # discard the partial line
            return handle.read().decode('utf-8', 'replace')
    except OSError:
        return ''


def recent(window_seconds, debounce_seconds, now=None):
    """{interface: {'count': n, 'last': epoch, 'events': [epoch, ...]}}"""
    now = now or datetime.datetime.now(datetime.timezone.utc).timestamp()
    cutoff = now - window_seconds
    seen = {}

    for match in _LINE.finditer(_tail(LOG, MAX_BYTES)):
        try:
            when = datetime.datetime.fromisoformat(match.group('when')).timestamp()
        except ValueError:
            continue
        if when < cutoff:
            continue
        entry = seen.setdefault(match.group('if'), [])
        if entry and when - entry[-1] < debounce_seconds:
            continue  # same reseat, not a second event
        entry.append(when)

    return {
        name: {
            'count': len(events),
            'last': int(events[-1]) if events else None,
            'events': [int(value) for value in events[-20:]],
        }
        for name, events in seen.items()
    }
