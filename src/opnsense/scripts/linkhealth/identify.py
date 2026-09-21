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

    Makes one socket on the front of the appliance blink, so a person standing
    in front of it can read the label printed beside it.

    This is the honest answer to "which socket is igb10?". The machine cannot
    read its own silk-screen - SMBIOS describes none of these ports - so every
    table of printed names is a claim until somebody looks at the metal. This
    turns looking into one button.

    It works through led(4): FreeBSD's e1000 and ixgbe drivers register one
    /dev/led/<interface> node per port, and writing a pattern to it drives the
    NIC's own identification LED through the controller's LED register. Three
    properties make it the right mechanism:

      * it touches nothing that carries traffic - the link does not drop, no
        state is lost, and a port in the middle of a transfer keeps going;
      * it works on a port with NO cable in it, which is exactly the port whose
        name you want before you plug something in;
      * writing "0" hands the LED back to the driver, so nothing has to be
        remembered or cleaned up later.

    The pattern language is led(4)'s own: a string of hex digits, each one a
    brightness step of about 1/10 second, repeated. "f0" is half a second lit
    then half dark - a slow, obvious blink that cannot be mistaken for traffic.
"""

import fcntl
import json
import os
import re
import subprocess
import time

import collector

LED_DIR = '/dev/led'
LOCK = '/var/run/linkhealth-identify.lock'

# Slow and deliberate: a tenth of a second per digit, so this is half a second
# bright and half a second dark. Traffic activity flickers; this does not.
PATTERN = 'f0'
RESTORE = '0'
MAX_SECONDS = 300
DEFAULT_SECONDS = 30

# The beat for identification by traffic. One second of packets, one second of silence:
# slow enough to read across a rack, and small enough that the link never notices. 500
# packets of 56 bytes is about a quarter of a megabit - the load test, by contrast, is
# meant to saturate the link and is never automatic for exactly that reason.
FLICKER_RATE = 500
FLICKER_INTERVAL = 0.002
FLICKER_PAYLOAD = 56
FLICKER_ON = 1.0
FLICKER_OFF = 1.0


def node_for(interface):
    """The LED node for a port, if the driver registered one."""
    if not re.match(r'^[a-z][a-z0-9]*[0-9]$', interface or ''):
        return None
    path = os.path.join(LED_DIR, interface)
    return path if os.path.exists(path) else None


def available():
    """Every port whose LED this machine can drive."""
    try:
        return sorted(name for name in os.listdir(LED_DIR)
                      if re.match(r'^[a-z]+[0-9]+$', name))
    except OSError:
        return []


def _write(path, value):
    with open(path, 'w') as node:
        node.write(value)


def blink(interface, seconds=DEFAULT_SECONDS):
    """Blink one port for a while, then put its LED back as it was.

    Holds a lock for the whole run: two ports blinking at once would defeat
    the entire point of the exercise.
    """
    path = node_for(interface)
    if path is None:
        return {'status': 'error',
                'message': 'this port has no identification LED that the driver exposes'}

    seconds = max(1, min(int(seconds or DEFAULT_SECONDS), MAX_SECONDS))

    lock = open(LOCK, 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return {'status': 'busy', 'message': 'another port is being identified right now'}

    started = int(time.time())
    # A stop asked for while nothing was blinking leaves its flag on disk, and
    # the loop below would read it as an order to end this blink before the
    # light was ever seen. The flag only means anything to whoever holds the
    # lock, and the lock has just been taken, so anything still lying here
    # belongs to a blink that is already over.
    try:
        os.unlink(LOCK + '.stop')
    except OSError:
        pass

    try:
        _write(path, PATTERN)
        # The LED keeps the pattern by itself; we only have to be here to stop
        # it. Sleeping in small steps means a stop request is honoured quickly.
        deadline = time.time() + seconds
        while time.time() < deadline:
            if os.path.exists(LOCK + '.stop'):
                break
            time.sleep(0.5)
        return {'status': 'done', 'interface': interface,
                'started': started, 'seconds': seconds}
    except OSError as failure:
        return {'status': 'error', 'message': str(failure)}
    finally:
        # Always hand the LED back, even if something above went wrong: a port
        # left blinking is a port that lies to the next person who walks in.
        try:
            _write(path, RESTORE)
        except OSError:
            pass
        for leftover in (LOCK + '.stop',):
            try:
                os.unlink(leftover)
            except OSError:
                pass
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
        except OSError:
            pass


def stop():
    """Ask a running blink to finish early.

    The flag is only laid down when there is a blink to stop. A GUI has every
    reason to press this after the LED has already been handed back - the
    button is still on the screen, and two people can be looking at the same
    page - and a flag written then would wait on disk for the next person to
    ask for a socket, whose blink would end before they had looked up. A
    button that does nothing is honest; a socket that reports "started" and
    stays dark is not.
    """
    probe = open(LOCK, 'w')
    try:
        # Taking the lock means the worker has let go of it, so there is
        # nothing left to stop.
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(probe, fcntl.LOCK_UN)
        return {'status': 'idle', 'message': 'no port is being identified'}
    except OSError:
        pass
    finally:
        probe.close()

    try:
        open(LOCK + '.stop', 'w').close()
        return {'status': 'stopping'}
    except OSError as failure:
        return {'status': 'error', 'message': str(failure)}


def can_flicker(port):
    """Whether making this port's activity light beat is possible at all.

    It needs a link, and it needs somebody at the other end: a frame addressed to
    nobody is dropped before it reaches the wire, and a socket nobody answers would
    stay as dark as it was.
    """
    return ((port.get('link') or {}).get('state') == 'active'
            and bool(port.get('serves')))


def flicker(interface, seconds=DEFAULT_SECONDS, target=None):
    """Identify a socket by making its activity light beat.

    The identification LED is not the only light on a socket, and on some hardware it
    is not a light at all. The 10G cages on the appliance this plugin was written for
    accept a write to /dev/led and light nothing: FreeBSD's ixgbe driver drives one
    fixed LED index and this board does not wire it (verified - the node exists, the
    write is accepted, and nothing on the front of the machine changes).

    What every socket has is an activity light, and activity is the one thing a
    firewall can produce deliberately. A second of packets, a second of silence,
    repeated, is a beat no ordinary traffic makes: even a busy uplink does not go
    completely quiet on a one-second rhythm, and a quiet port goes from dark to
    flickering, which is unmistakable.

    Two things keep it honest:

      * it is gentle on purpose. FLICKER_RATE small packets a second is about a
        quarter of a megabit - three orders of magnitude below what the load test
        deliberately does to a link, and far below anything a user would notice;
      * it holds the same lock as the LED blink, because two sockets identifying
        themselves at once defeats the entire point.
    """
    port = None
    for candidate in collector.collect()['ports']:
        if candidate['if'] == interface:
            port = candidate
            break
    if port is None:
        return {'status': 'error', 'message': 'no such port: %s' % interface}
    if (port.get('link') or {}).get('state') != 'active':
        return {'status': 'error',
                'message': 'the link is down, so there is no activity light to beat'}

    if not target:
        found = port.get('serves') or []
        if not found:
            return {'status': 'error',
                    'message': 'nothing is visible behind this port to send to - '
                               'set a neighbour address in the settings'}
        target = found[0]

    seconds = max(1, min(int(seconds or DEFAULT_SECONDS), MAX_SECONDS))

    lock = open(LOCK, 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return {'status': 'busy', 'message': 'another port is being identified right now'}

    started = int(time.time())
    # Same reasoning as blink(): a stop asked for while nothing was running left a flag
    # behind, and it belongs to something already over.
    try:
        os.unlink(LOCK + '.stop')
    except OSError:
        pass

    try:
        deadline = time.time() + seconds
        beats = 0
        while time.time() < deadline:
            if os.path.exists(LOCK + '.stop'):
                break
            try:
                subprocess.run(['/sbin/ping', '-q', '-i', str(FLICKER_INTERVAL),
                                '-c', str(FLICKER_RATE), '-s', str(FLICKER_PAYLOAD), target],
                               capture_output=True, timeout=FLICKER_ON + 5)
            except (subprocess.TimeoutExpired, OSError):
                # A neighbour that stopped answering mid-run is not a reason to leave the
                # caller without an answer; the beat simply stops being visible.
                break
            beats += 1
            quiet = time.time() + FLICKER_OFF
            while time.time() < quiet and time.time() < deadline:
                if os.path.exists(LOCK + '.stop'):
                    break
                time.sleep(0.1)
        return {'status': 'done', 'interface': interface, 'target': target,
                'started': started, 'seconds': seconds, 'beats': beats}
    finally:
        try:
            os.unlink(LOCK + '.stop')
        except OSError:
            pass
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
        except OSError:
            pass


def start_flicker_detached(interface, seconds=DEFAULT_SECONDS, target=None):
    """Start the beat and return at once, the way the GUI needs it."""
    probe = open(LOCK, 'w')
    try:
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(probe, fcntl.LOCK_UN)
    except OSError:
        return {'status': 'busy', 'message': 'another port is being identified right now'}
    finally:
        probe.close()

    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'linkhealth.py')
    command = ['/usr/local/bin/python3', script, 'runflicker', interface, str(seconds)]
    if target:
        command.append(target)
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return {'status': 'started', 'interface': interface, 'seconds': seconds, 'mode': 'flicker'}


def start_detached(interface, seconds=DEFAULT_SECONDS):
    """Start blinking and return at once, the way the GUI needs it.

    configd waits for whatever it starts and the browser cannot hold a request
    open for half a minute, so the blinking is handed to a session of its own.
    """
    if node_for(interface) is None:
        return {'status': 'error',
                'message': 'this port has no identification LED that the driver exposes'}

    probe = open(LOCK, 'w')
    try:
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(probe, fcntl.LOCK_UN)
    except OSError:
        return {'status': 'busy', 'message': 'another port is being identified right now'}
    finally:
        probe.close()

    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'linkhealth.py')
    subprocess.Popen(['/usr/local/bin/python3', script, 'blink', interface, str(seconds)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return {'status': 'started', 'interface': interface, 'seconds': seconds}


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'list':
        print(json.dumps({'ports': available()}))
    elif len(sys.argv) > 2:
        print(json.dumps(blink(sys.argv[2],
                               int(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_SECONDS)))
    else:
        print(json.dumps({'status': 'error', 'message': 'usage: identify.py <interface> [seconds]'}))
