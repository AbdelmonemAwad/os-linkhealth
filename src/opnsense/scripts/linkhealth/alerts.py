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

    Decides when to say something, says it once, and says it in the language
    the alert was set to.

    The mail itself reuses whatever SMTP server the firewall already sends its
    Monit alerts through, exactly as os-netreport does, so there is nothing new
    to configure for someone who already gets mail from this box.

    Monit is not used as the transport. Its alerts are plain text with the
    program's output truncated at 512 bytes, they go to Monit's own recipient
    rather than this plugin's, and it has no idea what a per-port cooldown is.
    A check script ships for people who want Monit watching as well, but it is
    off by default: with both enabled every fault arrives twice.

    Four rules keep a bad cable from becoming a bad mailbox:

      * a state must hold for two consecutive windows before anything is sent,
        so a single odd sample says nothing;
      * one mail per port per cooldown, six hours by default;
      * a port that recovers gets exactly one message saying so;
      * the first window after a reboot only establishes the baseline - the
        counters have just restarted and nothing can be compared yet.

    ---- the words ----------------------------------------------------------

    None of the sentences below are written in Python. Every one of them is a
    msgid, looked up in the very catalogue the GUI reads: the domain OPNsense
    under /usr/local/share/locale/<locale>/LC_MESSAGES/OPNsense.mo, which is
    where merge_ui_translations.py puts this plugin's Arabic at boot. The
    English string is the key, here as on the page.

    V, 2026-09-22, reference appliance, OPNsense 26.7.4_1:

        # python3 -c 'import gettext; t = gettext.translation("OPNsense",
              "/usr/local/share/locale", ["ar_SA"], fallback=True);
              print(t.gettext("{ppm} corrupted frames per million ({causes})"))'
        -> the Arabic sentence, not the English one: the catalogue answered,
              with {ppm} and {causes} still in it for this code to fill.

    That is the same msgid index.volt passes to lang._() for the same port, so
    the mail and the page cannot describe one port in two different sentences.
    Which is the point: a message that disagreed with the page it links to
    would be a bug, not a style.
"""

import gettext
import html
import re
import smtplib
import socket
import ssl
import time
import xml.etree.ElementTree as ET
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid

CONFIG = '/conf/config.xml'
RTL_LANGUAGES = ('ar', 'fa', 'he', 'ur')
ALERTING_STATES = ('warn', 'fail')

# The GUI's own catalogues, by the name gettext knows them. Nothing is
# installed for en_US - the msgid is already English - so a missing directory
# there is the normal case and not a failure.
LOCALE_DIR = '/usr/local/share/locale'
DOMAIN = 'OPNsense'
LOCALE_NAME = re.compile(r'^[a-z]{2}(_[A-Z]{2})?$')

# The short word the port grid puts in its Verdict column. Seven states, the
# seven in the design, and a state outside them is printed as it arrives -
# which is what the page does with an unknown state as well.
VERDICT_TEXT = {
    'down': 'down',
    'disabled': 'disabled',
    'idle': 'no traffic',
    'ok': 'clean',
    'watch': 'watch',
    'warn': 'warning',
    'fail': 'failing',
}

# The verdict sentences, by the code the collector stamps on every reason.
#
# Each template is character for character the one index.volt's reason_text()
# passes to lang._() for that same code, and tools/check-strings.py compares
# the two sets: a sentence reworded on the page and not here would leave the
# mail quoting last month's wording of the same fault. The numbers arrive as
# params, never baked into the string, because a translated sentence puts them
# in a different place.
REASON_TEXT = {
    'clean': 'clean - {frames} frames, no errors',
    'quiet': 'only {frames} frames in this window - not enough traffic to judge',
    'gathering': 'measuring: {frames} of {needed} frames so far, over {minutes} min',
    'baseline': 'first look at this port - measuring from here',
    'disabled': 'port is switched off in the configuration',
    'not_watched': 'this port is not being watched',
    'down': 'no cable, or nothing answering at the other end',
    'errors_fail': '{percent}% of received frames were corrupted ({causes})',
    'errors_warn': '{ppm} corrupted frames per million ({causes})',
    'errors_watch': 'a few frames are being corrupted ({ppm} per million)',
    'downshift': 'negotiated {speed} where the port supports {max} - usually a broken pair in the cable',
    'duplex': 'collisions on a full-duplex link ({count}) - the two ends disagree about duplex',
    'flapping_warn': 'the link went down and came back {count} times in the last hour',
    'flapping_fail': 'the link went down and came back {count} times in the last hour',
    'optics_rx_low': 'receive power {rx} dBm is below what this media type needs ({min} dBm)',
    'optics_rx_high': 'receive power {rx} dBm is above the overload point ({max} dBm)',
    'optics_rx_thin': 'only {margin} dB of optical margin left',
    'optics_hot': 'transceiver is running warm ({temp} C)',
    'optics_hot_fail': 'transceiver is at {temp} C',
    'optics_voltage': 'transceiver supply is {volts} V',
    'optics_gone': 'the transceiver is no longer there',
    'optics_swapped': 'the transceiver was changed',
}

# The rest of what the mail borrows from the page: the two column headings it
# shares with the port grid, the word above the interface list, and the three
# fragments reason_text() builds its sentences out of.
SHARED_TEXT = (
    'Port',
    'Verdict',
    'Serves',
    'unknown',
    '{n} Gbit/s',
    '{n} Mbit/s',
    'the driver reports errors but cannot say which kind',
)

# The five sentences that exist only in a message, because there is no page
# that says them: two subjects, the heading, the footer, and the prefix the
# "testmail" command puts in front of a subject it did not really mean. Each
# one is named rather than written where it is used, so that the msgid the
# catalogue is checked against and the msgid the mail asks for are one object.
SUBJECT_FAULT = '[{host}] {port}: {reason}'
SUBJECT_RECOVERY = '[{host}] {port} is clean again'
HEADING = 'Link health on {host}'
FOOTER = 'Sent by Link Health. The full picture is on the Link Health page, under Interfaces.'
TEST_PREFIX = '[test] {subject}'

OWN_TEXT = (SUBJECT_FAULT, SUBJECT_RECOVERY, HEADING, FOOTER, TEST_PREFIX)


def mail_strings():
    """Every msgid this file can ask the catalogue for.

    tools/check-strings.py reads this list rather than guessing at the source,
    so a sentence added to a message and forgotten in the catalogue fails the
    build instead of arriving as one English line in an Arabic mail.
    """
    return tuple(REASON_TEXT.values()) + tuple(VERDICT_TEXT.values()) + SHARED_TEXT + OWN_TEXT


def page_strings():
    """The subset of the above that the port grid must also be asking for."""
    return tuple(REASON_TEXT.values()) + tuple(VERDICT_TEXT.values()) + SHARED_TEXT


def system_language():
    try:
        return ET.parse(CONFIG).getroot().findtext('./system/language') or 'en_US'
    except Exception:
        return 'en_US'


def translator(language):
    """The stored setting resolved to a locale, and the lookup that goes with it.

    'default' is what the settings page stores for "System language", so it is
    read at the moment the mail is written rather than remembered: the owner
    switches the GUI and the next alert follows, which is the whole of the
    rule this exists to keep.

    fallback=True is the answer to a language whose catalogue is not installed:
    gettext hands back a NullTranslations, every lookup returns the msgid, and
    the msgid is correct English. So the worst case is an English mail, never a
    crash and never half a sentence. A catalogue that exists but will not parse
    would raise instead, so that is caught to the same English.
    """
    language = {'ar': 'ar_SA', 'en': 'en_US'}.get(language or '', language)
    if not language or language == 'default':
        language = system_language()
    if not LOCALE_NAME.match(language or ''):
        language = 'en_US'
    try:
        catalogue = gettext.translation(DOMAIN, LOCALE_DIR, [language], fallback=True)
    except Exception:
        catalogue = gettext.NullTranslations()
    return language, catalogue.gettext


def _fill(template, values):
    """Put the numbers into a translated sentence, by plain replacement.

    Never str.format(): a translator who writes {frame} for {frames}, or who
    leaves a lone brace in the text, would raise KeyError or ValueError inside
    the one code path whose job is to tell somebody about a problem. Plain
    replacement leaves the stray placeholder visible in the mail, which is a
    report of a bad translation rather than a message nobody receives. It is
    also exactly what index.volt's lh_fill() does with the same string.
    """
    text = template
    for key, value in values.items():
        text = text.replace('{%s}' % key, str(value))
    return text


def _number(value):
    """A measured value the way the page prints it.

    JavaScript writes String(3.0) as "3"; Python writes "3.0". The limits and
    readings in a reason are floats, so without this the mail would say
    "receive power -19.0 dBm" where the page says -19, about the same port in
    the same minute.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return '' if value is None else str(value)
    return str(int(number)) if number == int(number) else repr(number)


def _count(value):
    """A frame or error count, with thousands separators.

    The page reaches these through toLocaleString(), which follows the browser
    and may draw Arabic-Indic digits. A mail has no browser to follow, so it
    writes 38,100 in every language - the same choice os-netreport's Arabic
    reports already make.
    """
    try:
        return format(int(value), ',')
    except (TypeError, ValueError):
        return _number(value)


def _speed(say, mbps):
    try:
        value = int(mbps or 0)
    except (TypeError, ValueError):
        value = 0
    if not value:
        return say('unknown')
    if value >= 1000:
        return _fill(say('{n} Gbit/s'), {'n': _number(value / 1000.0)})
    return _fill(say('{n} Mbit/s'), {'n': _number(value)})


def _causes(say, causes, port):
    """The three biggest named counters behind an error rate, largest first.

    The counter names themselves are left exactly as the driver map gives
    them. The page prints them through its data-string map, which does not
    carry counter labels, so both come out in English - and both being wrong
    in the same way is the condition this file is written to keep. When those
    labels are catalogued, they must be catalogued for both at once.
    """
    counts = causes or {}
    meta = (port or {}).get('counter_meta') or {}
    names = sorted(counts, key=lambda name: -counts[name])
    if not names:
        return say('the driver reports errors but cannot say which kind')
    return ', '.join('%s: %s' % ((meta.get(name) or {}).get('label') or name, _count(counts[name]))
                     for name in names[:3])


def reason_text(say, reason, port=None):
    """One verdict sentence, from the code and the numbers the collector sent.

    A code this file does not know falls back to the English the collector
    built, which is the same fallback the page takes: a reason added later
    still says something true, in the wrong language, instead of nothing.
    """
    reason = reason or {}
    template = REASON_TEXT.get(reason.get('code'))
    if template is None:
        return reason.get('text') or reason.get('code') or ''

    p = reason.get('params') or {}
    code = reason['code']
    if code in ('clean', 'quiet'):
        values = {'frames': _count(p.get('frames'))}
    elif code == 'gathering':
        values = {'frames': _count(p.get('frames')), 'needed': _count(p.get('needed')),
                  'minutes': max(1, int(p.get('seconds') or 0) // 60)}
    elif code == 'errors_fail':
        values = {'percent': _number(p.get('percent')), 'causes': _causes(say, p.get('causes'), port)}
    elif code == 'errors_warn':
        values = {'ppm': _count(p.get('ppm')), 'causes': _causes(say, p.get('causes'), port)}
    elif code == 'errors_watch':
        values = {'ppm': _count(p.get('ppm'))}
    elif code == 'downshift':
        values = {'speed': _speed(say, p.get('speed')), 'max': _speed(say, p.get('max_speed'))}
    elif code == 'duplex':
        values = {'count': _count(p.get('collisions'))}
    elif code in ('flapping_warn', 'flapping_fail'):
        values = {'count': _count(p.get('count'))}
    elif code == 'optics_rx_low':
        values = {'rx': _number(p.get('rx_dbm')), 'min': _number(p.get('minimum'))}
    elif code == 'optics_rx_high':
        values = {'rx': _number(p.get('rx_dbm')), 'max': _number(p.get('maximum'))}
    elif code == 'optics_rx_thin':
        values = {'margin': _number(p.get('margin'))}
    elif code in ('optics_hot', 'optics_hot_fail'):
        values = {'temp': _number(p.get('temp_c'))}
    elif code == 'optics_voltage':
        values = {'volts': _number(p.get('vcc_v'))}
    else:
        values = {}

    return _fill(say(template), values)


def verdict_word(say, state):
    return say(VERDICT_TEXT[state]) if state in VERDICT_TEXT else state


def mail_settings(root, override_sender=None):
    """The SMTP settings Monit already uses. Nothing new to configure."""
    monit = root.find('./OPNsense/monit/general')
    if monit is None or not monit.findtext('mailserver'):
        raise RuntimeError('No mail server configured. Set one under Services: Monit.')

    port = int(monit.findtext('port') or 25)
    use_ssl = monit.findtext('ssl') == '1'
    security = 'ssl' if port == 465 else ('starttls' if use_ssl or port == 587 else 'none')
    user = monit.findtext('username') or ''
    hostname = (root.findtext('./system/hostname') or 'opnsense')
    domain = (root.findtext('./system/domain') or 'local')

    return {
        'host': monit.findtext('mailserver').split(',')[0].strip(),
        'port': port,
        'security': security,
        'user': user,
        'password': monit.findtext('password') or '',
        'sender': override_sender or (user if '@' in user else 'opnsense@%s.%s' % (hostname, domain)),
        'hostname': '%s.%s' % (hostname, domain),
    }


def send(settings, recipients, subject, body_html, body_text):
    message = MIMEMultipart('alternative')
    message['Subject'] = Header(subject, 'utf-8')
    message['From'] = formataddr((str(Header('OPNsense', 'utf-8')), settings['sender']))
    message['To'] = ', '.join(recipients)
    message['Date'] = formatdate(localtime=True)
    message['Message-ID'] = make_msgid(domain=settings['sender'].split('@')[-1])
    message.attach(MIMEText(body_text, 'plain', 'utf-8'))
    message.attach(MIMEText(body_html, 'html', 'utf-8'))

    context = ssl.create_default_context()
    if settings['security'] == 'ssl':
        server = smtplib.SMTP_SSL(settings['host'], settings['port'], timeout=60, context=context)
    else:
        server = smtplib.SMTP(settings['host'], settings['port'], timeout=60)
        if settings['security'] == 'starttls':
            server.starttls(context=context)
    with server:
        if settings['user'] and settings['password']:
            server.login(settings['user'], settings['password'])
        server.sendmail(settings['sender'], recipients, message.as_string())


def decide(status, previous, options, now=None):
    """Which ports deserve a message this time.

    `previous` is what was remembered after the last run; the updated version
    is returned alongside the decisions so the caller can store it.
    """
    now = int(now or time.time())
    memory = dict(previous.get('ports') or {})
    cooldown = int(options.get('cooldown_hours', 6)) * 3600
    needed = int(options.get('windows_before_alert', 2))
    faults, recoveries = [], []

    for port in status.get('ports', []):
        key = port['if']
        state = port['verdict']['state']
        seen = memory.get(key) or {}
        streak = seen.get('streak', 0) + 1 if seen.get('state') == state else 1

        entry = {
            'state': state,
            'streak': streak,
            'last_seen': now,
            'alerted_state': seen.get('alerted_state'),
            'alerted_at': seen.get('alerted_at'),
        }

        if state in ALERTING_STATES and streak >= needed:
            escalated = entry['alerted_state'] and ORDER(state) > ORDER(entry['alerted_state'])
            quiet_for = now - (entry['alerted_at'] or 0)
            if entry['alerted_state'] != state and (escalated or quiet_for >= cooldown or not entry['alerted_at']):
                faults.append(port)
                entry['alerted_state'] = state
                entry['alerted_at'] = now
            elif entry['alerted_state'] == state and quiet_for >= cooldown:
                faults.append(port)
                entry['alerted_at'] = now
        elif state in ('ok', 'idle') and entry['alerted_state'] in ALERTING_STATES and streak >= needed:
            if options.get('send_recovery', True):
                recoveries.append(port)
            entry['alerted_state'] = None
            entry['alerted_at'] = None

        memory[key] = entry

    return faults, recoveries, {'ports': memory, 'updated': now}


def ORDER(state):
    return {'ok': 0, 'idle': 0, 'down': 0, 'disabled': 0, 'watch': 1, 'warn': 2, 'fail': 3}.get(state, 0)


def compose(status, faults, recoveries, language='default'):
    """One mail covering everything that changed, not one mail per finding.

    `language` is the stored setting, not a locale: 'default' means the system
    language, and resolving it here rather than at the call site is what lets
    every caller - the sweep and the test command alike - obey the same rule
    without repeating it.
    """
    language, say = translator(language)
    direction = 'rtl' if language.split('_')[0] in RTL_LANGUAGES else 'ltr'
    chassis = status.get('chassis') or {}
    host = socket.gethostname()

    if faults:
        worst = max(faults, key=lambda port: ORDER(port['verdict']['state']))
        subject = _fill(say(SUBJECT_FAULT), {
            'host': host, 'port': worst['label'],
            'reason': reason_text(say, worst['verdict']['reasons'][0], worst)})
    else:
        subject = _fill(say(SUBJECT_RECOVERY),
                        {'host': host, 'port': recoveries[0]['label']})

    rows = []
    text_lines = []
    # Two columns, and they are the port grid's own two: the page shows the
    # verdict word with its sentence underneath, so the mail does too. The one
    # difference is deliberate - the grid shows the first reason and "and N
    # more", because it has a detail page to open. A mail has nowhere to click,
    # so it carries all of them.
    for port in list(faults) + list(recoveries):
        verdict = port['verdict']
        colour = {'fail': '#c0392b', 'warn': '#e67e22', 'watch': '#f1c40f'}.get(verdict['state'], '#27ae60')
        where = port.get('name') or ''
        serves = ', '.join(port.get('serves') or [])
        under = [html.escape('%s (%s)' % (where, port['if']) if where else port['if'])]
        if serves:
            under.append(html.escape('%s: %s' % (say('Serves'), serves)))
        word = verdict_word(say, verdict['state'])
        sentences = [reason_text(say, reason, port) for reason in verdict['reasons']]

        rows.append(
            '<tr>'
            '<td style="padding:8px;border-bottom:1px solid #eee;text-align:start;vertical-align:top">'
            '<b>%s</b><br><span style="color:#888;font-size:12px">%s</span></td>'
            '<td style="padding:8px;border-bottom:1px solid #eee;text-align:start;vertical-align:top">'
            '<b style="color:%s">%s</b><br><span style="color:#555;font-size:13px">%s</span></td>'
            '</tr>' % (
                html.escape(port['label']), ' &middot; '.join(under),
                colour, html.escape(word),
                '<br>'.join(html.escape(sentence) for sentence in sentences)))
        text_lines.append('%s [%s] %s' % (port['label'], word, '; '.join(sentences)))

    heading = _fill(say(HEADING), {'host': host})
    stamp = [chassis.get('display', ''), time.strftime('%Y-%m-%d %H:%M')]
    stamp = [part for part in stamp if part]

    body_html = (
        '<html lang="%s"><body dir="%s" style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;'
        'color:#222;background:#fff">'
        '<h2 style="margin:0 0 4px">%s</h2>'
        '<div style="color:#888;font-size:13px;margin-bottom:16px">%s</div>'
        '<table style="border-collapse:collapse;width:100%%;max-width:720px">'
        '<thead><tr>'
        '<th style="padding:8px;border-bottom:2px solid #ddd;text-align:start;font-size:12px;'
        'color:#888;text-transform:uppercase">%s</th>'
        '<th style="padding:8px;border-bottom:2px solid #ddd;text-align:start;font-size:12px;'
        'color:#888;text-transform:uppercase">%s</th>'
        '</tr></thead><tbody>%s</tbody></table>'
        '<p style="color:#888;font-size:12px;margin-top:18px">%s</p>'
        '</body></html>'
    ) % (language.replace('_', '-'), direction, html.escape(heading),
         ' &middot; '.join(html.escape(part) for part in stamp),
         html.escape(say('Port')), html.escape(say('Verdict')), ''.join(rows),
         html.escape(say(FOOTER)))

    body_text = '%s\n%s\n\n%s\n' % (heading, ' - '.join(stamp), '\n'.join(text_lines))

    return subject, body_html, body_text


def test_subject(subject, language='default'):
    """The one word a test message adds to a subject it does not really mean."""
    _, say = translator(language)
    return _fill(say(TEST_PREFIX), {'subject': subject})
