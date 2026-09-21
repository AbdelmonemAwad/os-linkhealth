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

    Decides when to say something, and says it once.

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
"""

import html
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


def compose(status, faults, recoveries, language='en_US'):
    """One mail covering everything that changed, not one mail per finding."""
    direction = 'rtl' if language[:2] in RTL_LANGUAGES else 'ltr'
    chassis = status.get('chassis') or {}
    host = socket.gethostname()

    if faults:
        worst = max(faults, key=lambda port: ORDER(port['verdict']['state']))
        subject = '[%s] %s: %s' % (host, worst['label'], worst['verdict']['reasons'][0]['text'])
    else:
        subject = '[%s] %s is clean again' % (host, recoveries[0]['label'])

    rows = []
    text_lines = []
    for port, kind in [(port, 'fault') for port in faults] + [(port, 'ok') for port in recoveries]:
        verdict = port['verdict']
        colour = {'fail': '#c0392b', 'warn': '#e67e22', 'watch': '#f1c40f'}.get(verdict['state'], '#27ae60')
        where = port.get('name') or ''
        serves = ', '.join(port.get('serves') or [])
        detail = '<br>'.join(html.escape(reason['text']) for reason in verdict['reasons'])
        rows.append(
            '<tr>'
            '<td style="padding:8px;border-bottom:1px solid #eee"><b>%s</b><br>'
            '<span style="color:#888;font-size:12px">%s%s</span></td>'
            '<td style="padding:8px;border-bottom:1px solid #eee;color:%s"><b>%s</b></td>'
            '<td style="padding:8px;border-bottom:1px solid #eee">%s</td>'
            '</tr>' % (
                html.escape(port['label']),
                html.escape('%s (%s)' % (where, port['if']) if where else port['if']),
                html.escape(' - serves %s' % serves) if serves else '',
                colour, html.escape(verdict['state']), detail))
        text_lines.append('%s [%s] %s' % (
            port['label'], verdict['state'],
            '; '.join(reason['text'] for reason in verdict['reasons'])))

    body_html = (
        '<html><body dir="%s" style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;'
        'color:#222;background:#fff">'
        '<h2 style="margin:0 0 4px">Link health on %s</h2>'
        '<div style="color:#888;font-size:13px;margin-bottom:16px">%s &middot; %s</div>'
        '<table style="border-collapse:collapse;width:100%%;max-width:720px">%s</table>'
        '<p style="color:#888;font-size:12px;margin-top:18px">'
        'Sent by Link Health. The full picture is under Interfaces &rsaquo; Link Health.</p>'
        '</body></html>'
    ) % (direction, html.escape(host), html.escape(chassis.get('display', '')),
         time.strftime('%Y-%m-%d %H:%M'), ''.join(rows))

    body_text = 'Link health on %s (%s)\n%s\n\n%s\n' % (
        host, chassis.get('display', ''), time.strftime('%Y-%m-%d %H:%M'), '\n'.join(text_lines))

    return subject, body_html, body_text


def system_language():
    try:
        return ET.parse(CONFIG).getroot().findtext('./system/language') or 'en_US'
    except Exception:
        return 'en_US'
