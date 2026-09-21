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

    Turns a window of measurements into a sentence a person can act on.

    The order of the checks is the order of their authority:

      1. frames corrupted on the wire - the only signal that has ever caught a
         real fault on the machine this was written for;
      2. a link negotiating below the speed its own PHY advertises, which is
         the classic broken pair, and the one rich check available even on
         hardware that counts no errors at all;
      3. collisions on a full-duplex link, which mean a duplex mismatch;
      4. a link that keeps coming and going;
      5. the transceiver's own telemetry, which is corroboration and never a
         verdict: during a genuine fault that corrupted 3.81% of frames, both
         modules reported every value inside its own limits with no flag set.

    A port carrying too little traffic gets no verdict at all. A rate computed
    from a handful of frames is not a measurement, and a green tick on a port
    nobody is using is a lie that costs somebody an afternoon.
"""

ORDER = {'ok': 0, 'idle': 0, 'down': 0, 'disabled': 0, 'info': 0, 'watch': 1, 'warn': 2, 'fail': 3}


def _reason(code, severity, text, advisory=False, **params):
    return {'code': code, 'severity': severity, 'text': text,
            'advisory': advisory, 'params': params}


def _worst(reasons):
    """The state, decided only by what measures the link itself.

    Transceiver readings are carried alongside and shown, but they never set
    the state. That is not a stylistic choice: through a fault that corrupted
    3.81% of received frames, both modules reported every value inside its own
    limits. A tool that let optics speak for the link would have called that
    port healthy, and a tool that let optics condemn a link would raise alarms
    about ports that are carrying traffic perfectly well.
    """
    state = 'ok'
    for reason in reasons:
        if reason.get('advisory'):
            continue
        if ORDER.get(reason['severity'], 0) > ORDER.get(state, 0):
            state = reason['severity']
    return state


def _optics_limits(port, thresholds):
    """The receive/transmit power range for whatever is plugged in.

    The module's own thresholds are richer but looser: a transceiver on the
    reference machine declares its low-power alarm 6.2 dB below the floor its
    own media type allows. The specification wins.
    """
    media = (port.get('optics') or {}).get('type', '') + ' ' + port['link'].get('media', '')
    media = media.upper().replace(' ', '')
    table = thresholds['optics']['media']
    for key, limits in table.items():
        if key == 'default':
            continue
        if key.replace('-', '').replace('BASE', 'BASE') in media.replace('-', ''):
            return limits
    return table['default']


def judge(port, deltas, usable, flap, thresholds, window_seconds):
    """Everything the plugin has to say about one port, this window."""
    link = port['link']
    reasons = []

    if not link['admin_up']:
        return {'state': 'disabled', 'reasons': [
            _reason('disabled', 'info', 'port is switched off in the configuration')]}

    if link['state'] != 'active':
        return {'state': 'down', 'reasons': [
            _reason('down', 'info', 'no cable, or nothing answering at the other end')]}

    # ---- how much did we see, and how much of it was broken ----------------
    frames = deltas.get('_rx_frames')
    if frames is None:
        frames = deltas.get('totals_rx_frames', 0)

    cable_errors = 0
    aggregate_errors = deltas.get('totals_rx_errors', 0)
    duplex_errors = 0
    named = {}

    for name, value in deltas.items():
        meta = port.get('counter_meta', {}).get(name)
        if not meta or not value:
            continue
        if meta['class'] == 'cable':
            cable_errors += value
            named[name] = value
        elif meta['class'] == 'duplex':
            duplex_errors += value
            named[name] = value

    # The per-cause counters are preferred; the driver's own total is the
    # fallback for hardware that offers nothing else. They are never added:
    # the total already contains the causes.
    errors = cable_errors if 'COUNTERS' in port['caps'] and named else aggregate_errors

    if not usable:
        return {'state': 'idle', 'reasons': [
            _reason('baseline', 'info', 'first look at this port - measuring from here')]}

    if frames < thresholds['min_frames']:
        return {'state': 'idle', 'frames': frames, 'reasons': [
            _reason('quiet', 'info',
                    'only %d frames in this window - not enough traffic to judge' % frames,
                    frames=frames)]}

    ppm = int(round(errors * 1000000.0 / frames)) if frames else 0
    limits = thresholds['error_ppm']

    if ppm >= limits['fail']:
        reasons.append(_reason(
            'errors_fail', 'fail',
            '%.2f%% of received frames were corrupted (%s)' % (ppm / 10000.0, _causes(named, port)),
            ppm=ppm, percent=round(ppm / 10000.0, 3), causes=named))
    elif ppm >= limits['warn']:
        reasons.append(_reason(
            'errors_warn', 'warn',
            '%d corrupted frames per million (%s)' % (ppm, _causes(named, port)),
            ppm=ppm, causes=named))
    elif ppm >= limits['watch']:
        reasons.append(_reason(
            'errors_watch', 'watch',
            'a few frames are being corrupted (%d per million)' % ppm,
            ppm=ppm, causes=named))

    # ---- negotiated below what the port can do -----------------------------
    if link['downshift']:
        reasons.append(_reason(
            'downshift', 'warn',
            'negotiated %s where the port supports %s - usually a broken pair in the cable'
            % (_speed(link['speed_mbps']), _speed(link['max_speed_mbps'])),
            speed=link['speed_mbps'], max_speed=link['max_speed_mbps']))

    # ---- collisions on a full-duplex link ----------------------------------
    if duplex_errors and link.get('duplex') == 'full':
        duplex_ppm = int(round(duplex_errors * 1000000.0 / frames)) if frames else 0
        severity = 'fail' if duplex_ppm >= thresholds['duplex_ppm']['fail'] else 'warn'
        reasons.append(_reason(
            'duplex', severity,
            'collisions on a full-duplex link (%d) - the two ends disagree about duplex'
            % duplex_errors,
            collisions=duplex_errors, ppm=duplex_ppm))

    # ---- the link coming and going -----------------------------------------
    count = (flap or {}).get('count', 0)
    if count >= thresholds['flaps']['fail']:
        reasons.append(_reason(
            'flapping_fail', 'fail',
            'the link went down and came back %d times in the last hour' % count, count=count))
    elif count >= thresholds['flaps']['warn']:
        reasons.append(_reason(
            'flapping_warn', 'warn',
            'the link went down and came back %d times in the last hour' % count, count=count))

    reasons.extend(optics_advice(port, thresholds))

    if not reasons:
        reasons.append(_reason('clean', 'info', 'clean - %s frames, no errors' % f'{frames:,}',
                               frames=frames))

    return {
        'state': _worst(reasons),
        'frames': frames,
        'errors': errors,
        'ppm': ppm,
        'reasons': reasons,
    }


def optics_advice(port, thresholds):
    """Advisory notes about the transceiver. Never the verdict on their own."""
    optics = port.get('optics') or {}
    if not optics.get('present'):
        return []

    notes = []
    limits = _optics_limits(port, thresholds)
    spec = thresholds['optics']

    rx = optics.get('rx_dbm')
    if rx is not None:
        low, high = limits['rx_dbm']
        if rx < low:
            notes.append(_reason(
                'optics_rx_low', 'watch',
                'receive power %.2f dBm is below what this media type needs (%.1f dBm)' % (rx, low),
                rx_dbm=rx, minimum=low, advisory=True))
        elif rx > high:
            notes.append(_reason(
                'optics_rx_high', 'watch',
                'receive power %.2f dBm is above the overload point (%.1f dBm)' % (rx, high),
                rx_dbm=rx, maximum=high, advisory=True))
        elif rx - low < spec['margin_db']['thin']:
            notes.append(_reason(
                'optics_rx_thin', 'info',
                'only %.1f dB of optical margin left' % (rx - low), margin=round(rx - low, 1), advisory=True))

    temperature = optics.get('temp_c')
    if temperature is not None:
        if temperature >= spec['temperature_c']['fail']:
            notes.append(_reason(
                'optics_hot_fail', 'warn',
                                 'transceiver is at %.0f C' % temperature, temp_c=temperature, advisory=True))
        elif temperature >= spec['temperature_c']['warn']:
            notes.append(_reason(
                'optics_hot', 'info',
                                 'transceiver is running warm (%.0f C)' % temperature,
                                 temp_c=temperature, advisory=True))

    voltage = optics.get('vcc_v')
    if voltage is not None:
        if voltage < spec['voltage_v']['min'] or voltage > spec['voltage_v']['max']:
            notes.append(_reason(
                'optics_voltage', 'watch',
                                 'transceiver supply is %.2f V' % voltage, vcc_v=voltage, advisory=True))

    return notes


def module_changed(port, previous_optics):
    """Someone swapped or pulled a transceiver since the last look.

    Worth saying out loud: on the machine this was written for, the fault that
    cost an evening was two ends of one fibre holding different modules, and
    the serial number is the only place that is ever visible.
    """
    optics = port.get('optics') or {}
    before = previous_optics or {}
    if not before.get('present'):
        return None
    if not optics.get('present'):
        return _reason('optics_gone', 'warn', 'the transceiver is no longer there',
                       was=before.get('pn', ''))
    if before.get('sn') and optics.get('sn') and before['sn'] != optics['sn']:
        return _reason('optics_swapped', 'info',
                       'the transceiver was changed (%s -> %s)' % (before.get('pn', '?'), optics.get('pn', '?')),
                       was=before.get('sn'), now=optics.get('sn'))
    return None


def _speed(mbps):
    if not mbps:
        return 'unknown'
    return '%d Gbit/s' % (mbps // 1000) if mbps >= 1000 else '%d Mbit/s' % mbps


def _causes(named, port):
    """Name what the hardware actually counted, in words, worst first."""
    if not named:
        return 'the driver reports errors but cannot say which kind'
    meta = port.get('counter_meta', {})
    parts = sorted(named.items(), key=lambda item: -item[1])
    return ', '.join('%s: %s' % (meta.get(name, {}).get('label', name), f'{value:,}')
                     for name, value in parts[:3])
