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

    Hands the numbers to whatever already does graphing.

    This plugin owns the verdict, not the time series. If node_exporter is
    installed its textfile directory gets a file each cycle; if it is not, the
    directory does not exist and nothing happens - no directory is created and
    no ownership is changed, because that belongs to node_exporter's own
    startup, not to us.

    Counters are exported cumulatively, the way Prometheus expects, even though
    the plugin itself judges only deltas. The verdict goes out as a small gauge
    so an alerting rule elsewhere can act on the same conclusion.
"""

import os
import tempfile

TEXTFILE_DIR = '/var/tmp/node_exporter'
FILENAME = 'linkhealth.prom'

STATE_VALUE = {'ok': 0, 'idle': 1, 'down': 2, 'disabled': 3, 'watch': 4, 'warn': 5, 'fail': 6}


def _escape(value):
    return str(value).replace('\\', '\\\\').replace('"', '\\"').replace('\n', ' ')


def write(status, directory=TEXTFILE_DIR):
    """Write the metrics file, or do nothing at all if nobody is collecting."""
    if not os.path.isdir(directory):
        return False

    lines = [
        '# HELP linkhealth_port_state Verdict per port (0 ok, 1 idle, 2 down, 3 disabled, 4 watch, 5 warn, 6 fail).',
        '# TYPE linkhealth_port_state gauge',
    ]
    rates, errors, frames, speed, optics_rx, optics_temp = [], [], [], [], [], []

    for port in status.get('ports', []):
        labels = 'interface="%s",label="%s",name="%s"' % (
            _escape(port['if']), _escape(port['label']), _escape(port.get('name', '')))
        verdict = port['verdict']
        window = port.get('window') or {}
        totals = port.get('totals') or {}
        link = port.get('link') or {}
        optics = port.get('optics') or {}

        lines.append('linkhealth_port_state{%s} %d' % (labels, STATE_VALUE.get(verdict['state'], 1)))
        rates.append('linkhealth_port_error_ppm{%s} %d' % (labels, window.get('error_ppm', 0)))
        errors.append('linkhealth_port_rx_errors_total{%s} %d' % (labels, totals.get('rx_errors', 0)))
        frames.append('linkhealth_port_rx_frames_total{%s} %d' % (labels, totals.get('rx_frames', 0)))
        speed.append('linkhealth_port_speed_mbps{%s} %d' % (labels, link.get('speed_mbps', 0)))
        if optics.get('rx_dbm') is not None:
            optics_rx.append('linkhealth_optic_rx_dbm{%s} %.2f' % (labels, optics['rx_dbm']))
        if optics.get('temp_c') is not None:
            optics_temp.append('linkhealth_optic_temperature_celsius{%s} %.2f' % (labels, optics['temp_c']))

    for header, block in (
        ('# HELP linkhealth_port_error_ppm Corrupted frames per million received, over the last window.\n'
         '# TYPE linkhealth_port_error_ppm gauge', rates),
        ('# HELP linkhealth_port_rx_errors_total Receive errors since boot.\n'
         '# TYPE linkhealth_port_rx_errors_total counter', errors),
        ('# HELP linkhealth_port_rx_frames_total Frames received since boot.\n'
         '# TYPE linkhealth_port_rx_frames_total counter', frames),
        ('# HELP linkhealth_port_speed_mbps Negotiated link speed.\n'
         '# TYPE linkhealth_port_speed_mbps gauge', speed),
        ('# HELP linkhealth_optic_rx_dbm Transceiver receive power.\n'
         '# TYPE linkhealth_optic_rx_dbm gauge', optics_rx),
        ('# HELP linkhealth_optic_temperature_celsius Transceiver temperature.\n'
         '# TYPE linkhealth_optic_temperature_celsius gauge', optics_temp),
    ):
        if block:
            lines.append(header)
            lines.extend(block)

    payload = '\n'.join(lines) + '\n'
    handle, temporary = tempfile.mkstemp(dir=directory, prefix='.linkhealth-')
    try:
        with os.fdopen(handle, 'w') as stream:
            stream.write(payload)
        os.chmod(temporary, 0o644)
        os.rename(temporary, os.path.join(directory, FILENAME))
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        return False
    return True
