{#
 # Copyright (C) 2026 Abdelmonem Awad <eg2@live.com>
 # All rights reserved.
 #
 # Redistribution and use in source and binary forms, with or without modification,
 # are permitted provided that the following conditions are met:
 #
 # 1. Redistributions of source code must retain the above copyright notice,
 #    this list of conditions and the following disclaimer.
 #
 # 2. Redistributions in binary form must reproduce the above copyright notice,
 #    this list of conditions and the following disclaimer in the documentation
 #    and/or other materials provided with the distribution.
 #
 # THIS SOFTWARE IS PROVIDED "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES,
 # INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 # AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 # AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
 # OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 # SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 # INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 # CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 # ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 # POSSIBILITY OF SUCH DAMAGE.
 #}

<script>
    $(document).ready(function () {

        /* The collector rewrites status.json once a minute. Asking any more often than that only
           costs a read of a document that has not changed since the last look. */
        const REFRESH_MS = 60000;
        /* A load test itself is over in seconds, but what it measured only reaches this page with
           the sweep that follows it, so polling faster than this buys nothing and every poll is a
           configd round trip on a firewall that has better things to do. */
        const TEST_POLL_MS = 5000;
        /* And stop waiting well past that. A worker that never finishes must not be able to hold
           this page open; the test keeps running, we simply stop watching it. */
        const TEST_WAIT_MS = 180000;

        let status_doc = null;      /* the last document we fetched, so the detail tab can redraw for free */
        let current_port = null;    /* interface name of the port the detail tab is showing */
        let test_running = false;   /* a load test this page started is still being waited for */

        /* Short words for the grid; the collector's own sentence is shown underneath them. The
           states are the seven in the design, nothing else is ever written into a verdict. */
        const verdict_text = {
            'down': "{{ lang._('down') }}",
            'disabled': "{{ lang._('disabled') }}",
            'idle': "{{ lang._('no traffic') }}",
            'ok': "{{ lang._('clean') }}",
            'watch': "{{ lang._('watch') }}",
            'warn': "{{ lang._('warning') }}",
            'fail': "{{ lang._('failing') }}"
        };

        /* Grey for the three states that are not a judgement at all. A port with too little
           traffic has not been cleared of anything, so it must never be given the green label. */
        const verdict_class = {
            'down': 'label-default',
            'disabled': 'label-default',
            'idle': 'label-default',
            'ok': 'label-success',
            'watch': 'label-info',
            'warn': 'label-warning',
            'fail': 'label-danger'
        };

        /* Sort order for "worst first", which is really "most worth reading first". The four
           judged states come before the three that are not a judgement. A dark port sinks to the
           bottom on purpose: a port that is down while it is configured up is already a failure by
           the rules, so a row that is merely down is one that is meant to be dark. */
        const verdict_rank = {
            'fail': 0, 'warn': 1, 'watch': 2, 'ok': 3, 'idle': 4, 'down': 5, 'disabled': 6
        };

        /* A reason carries the same word the verdict does, plus `info` for a remark that is not a
           complaint at all. Anything else falls through to a quiet bullet rather than to a red
           cross: an unknown severity is not a reason to shout. */
        const severity_class = {
            'info': 'fa-info-circle text-info',
            'watch': 'fa-info-circle text-info',
            'warn': 'fa-exclamation-triangle text-warning',
            'fail': 'fa-times-circle text-danger'
        };

        /* The words for a link that is carrying nothing. They come out of the collector in
           English, and they are read by a person, so they are translated here like every other
           word on the page instead of being printed as they arrive. */
        const link_state_text = {
            'active': "{{ lang._('up') }}",
            'down': "{{ lang._('no link') }}",
            'disabled': "{{ lang._('switched off') }}"
        };

        /* What a hardware counter means, in the classes the driver map gives them. The verdict
           only ever adds up the ones that describe a physical fault; the rest are shown so that a
           large number in the table is not read as damage. */
        const counter_class_text = {
            'cable': "{{ lang._('a physical fault') }}",
            'aggregate': "{{ lang._('the total the driver keeps') }}",
            'duplex': "{{ lang._('duplex mismatch') }}",
            'flap': "{{ lang._('link changes') }}",
            'load': "{{ lang._('load, not damage') }}",
            'ignore': "{{ lang._('not a fault') }}"
        };

        /* What each capability flag means, in the words of the design. The flags are printed
           verbatim because they are what the README and the driver map call them. */
        const capability_help = {
            'LINK': "{{ lang._('The link state can be read.') }}",
            'NETSTAT': "{{ lang._('Input and output error totals, without a cause.') }}",
            'MEDIA_LADDER': "{{ lang._('The list of speeds this port advertises, so a downshift can be seen.') }}",
            'COUNTERS': "{{ lang._('Hardware counters per cause: CRC, alignment, length and the rest.') }}",
            'OPTICS_INVENTORY': "{{ lang._('The transceiver identifies itself: type, vendor, part and serial number.') }}",
            'OPTICS_DOM': "{{ lang._('The transceiver also reports temperature, voltage, power and bias.') }}"
        };

        /* ------------------------------------------------------------------ small helpers */

        function esc(value) {
            return $('<div/>').text(value === null || value === undefined ? '' : value).html();
        }

        /* Interface names, addresses, counts and readings are written left to right even in the
           Arabic layout. Letting each one keep its own direction is what stops a minus sign or a
           dotted address from being reordered around the words next to it. A value that is not
           there comes back as nothing at all, so that the tables below drop its row instead of
           printing an empty box where a measurement would be. */
        function tech(value) {
            if (value === null || value === undefined || value === '') {
                return '';
            }
            return '<span class="lh-tech">' + esc(value) + '</span>';
        }

        function count(value) {
            const number = Number(value);
            if (!isFinite(number)) {
                return '';
            }
            return Math.round(number).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
        }

        function fixed(value, digits) {
            const number = Number(value);
            if (value === null || value === undefined || value === '' || !isFinite(number)) {
                return null;
            }
            return number.toFixed(digits);
        }

        function percent_text(value) {
            const shown = fixed(value, 1);
            return shown === null ? null : shown + '%';
        }

        /* The rate over the window, never a counter since boot. Above one percent a percentage is
           what a person reads; below it parts per million keeps the small numbers visible. */
        function format_rate(ppm) {
            const value = Number(ppm);
            if (!isFinite(value)) {
                return '';
            }
            if (value >= 10000) {
                return (value / 10000).toFixed(2) + '%';
            }
            return count(Math.round(value)) + ' ' + "{{ lang._('ppm') }}";
        }

        function speed_text(mbps) {
            const value = Number(mbps);
            if (!isFinite(value) || value <= 0) {
                return '';
            }
            if (value >= 1000) {
                const gbps = value / 1000;
                return (gbps % 1 === 0 ? gbps : gbps.toFixed(1)) + 'G';
            }
            return value + 'M';
        }

        /* Megabits per second for a media name spelled the way the driver spells it: "1000baseT"
           is 1000 and "10Gbase-SR" is 10000. The same rule the collector applies, because the
           ladder arrives as the driver's own text and the rung in use has to be found in it. */
        function media_speed(media) {
            const match = /^(\d+)\s*([GM])?base/i.exec(String(media === null || media === undefined ? '' : media).trim());
            if (match === null) {
                return 0;
            }
            const value = parseInt(match[1], 10);
            return (match[2] || 'M').toUpperCase() === 'G' ? value * 1000 : value;
        }

        function format_time(epoch) {
            const seconds = Number(epoch);
            if (!isFinite(seconds) || seconds <= 0) {
                return '';
            }
            const when = new Date(seconds * 1000);
            return isNaN(when.getTime()) ? '' : when.toLocaleString();
        }

        function duration_text(seconds) {
            const value = Number(seconds);
            if (!isFinite(value) || value <= 0) {
                return '';
            }
            if (value % 3600 === 0) {
                return "{{ lang._('{count} h') }}".replace('{count}', value / 3600);
            }
            if (value % 60 === 0) {
                return "{{ lang._('{count} min') }}".replace('{count}', value / 60);
            }
            return "{{ lang._('{count} s') }}".replace('{count}', value);
        }

        function has_capability(port, flag) {
            return $.inArray(flag, port.caps || []) !== -1;
        }

        function port_title(port) {
            return port.label || port['if'] || '';
        }

        /* The per-cause counters for the window. The contract keeps them under `counters`; the
           collector writes them inside the window it measured them over. Either is read, and the
           cumulative totals that sit beside them never are: those count since boot, and a port on
           this machine has carried thousands of them while being perfectly clean for hours. */
        function window_counters(port) {
            if (port.counters !== null && typeof port.counters === 'object') {
                return port.counters;
            }
            const measured = (port.window || {}).counters;
            return (measured !== null && typeof measured === 'object') ? measured : {};
        }

        function close_button() {
            return {
                label: "{{ lang._('Close') }}",
                action: function (dialog) {
                    dialog.close();
                }
            };
        }

        function show_message(type, title, message) {
            BootstrapDialog.show({
                type: type,
                title: title,
                message: message,
                buttons: [close_button()]
            });
        }

        /* The API answers a refusal with `detail` and the script behind it with `message`. Take
           whichever arrived, because which one does depends on how far the request got. */
        function failure_text(data) {
            const detail = data ? (data.detail || data.message) : null;
            return detail ? '<br/><br/>' + esc(detail) : '';
        }

        /* A line of facts with an em dash between them. Each piece gets its own direction: a
           model name and a timestamp are written left to right, the words around them are not,
           and running them together as one string scrambles both. */
        function fill_summary($target, parts) {
            $target.empty();
            $.each(parts, function (index, part) {
                if (index > 0) {
                    $target.append(document.createTextNode(' — '));
                }
                $target.append($('<span class="lh-tech"/>').text(part));
            });
        }

        /* ------------------------------------------------------------------ the ports grid */

        function optics_summary(port) {
            const optics = port.optics || {};
            if (!optics.present) {
                return '';
            }
            return [optics.type, optics.vendor, optics.pn].filter(Boolean).join(' ');
        }

        /* The grid sorts and filters on flat values, so each row carries a handful of plain
           fields beside the nested document. Nothing is recomputed from them; they are only there
           for the column headers to sort by. */
        function port_rows(doc) {
            const rows = [];
            $.each(doc.ports || [], function (index, port) {
                const row = $.extend({}, port);
                const link = port.link || {};
                const window_stats = port.window || {};
                const state = (port.verdict || {}).state || 'ok';
                row.verdict_state = state;
                row.verdict_rank = verdict_rank[state] !== undefined ? verdict_rank[state] : 9;
                row.error_ppm = Number(window_stats.error_ppm || 0);
                row.link_speed = Number(link.speed_mbps || 0);
                row.serves_text = (port.serves || []).join(' ');
                row.optics_text = optics_summary(port);
                rows.push(row);
            });
            /* Worst first, because the page exists to put the failing port under the owner's eyes
               before he has read a single column heading. Ties fall back to the measured rate and
               then to the chassis label, so the order is the same on every reload. */
            rows.sort(function (a, b) {
                if (a.verdict_rank !== b.verdict_rank) {
                    return a.verdict_rank - b.verdict_rank;
                }
                if (a.error_ppm !== b.error_ppm) {
                    return b.error_ppm - a.error_ppm;
                }
                return String(port_title(a)).localeCompare(String(port_title(b)), undefined, {numeric: true});
            });
            return rows;
        }

        /* ------------------------------------------------------------------
           Two kinds of English reach this page without passing a translator,
           and both are handled the way the rest of OPNsense handles text: the
           English string is the key, lang._() looks it up in the catalogue,
           and the catalogue is where the Arabic lives.

           1. VERDICT SENTENCES. The collector builds them in Python with the
              numbers already inside, so they arrive as finished prose. But
              every reason also carries its `code` and the `params` the
              sentence was made from, so the page rebuilds the sentence from a
              template of its own - and that template is an ordinary key.

           2. LAYOUT DESCRIPTIONS. Bay names and the notes beside them are data
              in faceplates.json, and data files carry no translations. The
              English in those files is used as the key here; anything the
              catalogue does not know is shown unchanged, which is also what
              happens on hardware whose layout somebody else contributed.
           ------------------------------------------------------------------ */
        /* Translatable text that lives in the DATA files, not in this page: bay names,
           the notes beside them, and the sentence a chassis table uses to say that its
           sockets have no light. lang._() resolves against a literal at compile time, so
           each one is listed here as its own key - and the list is GENERATED, by
           tools/gen-data-strings.py, because a note added to chassis.json and forgotten
           here comes out in English on an Arabic page with nothing to say so. */
        /* Translatable text that lives in the DATA files, not in this page: bay names,
           the notes beside them, and the sentence a chassis table uses to say that its
           sockets have no light. lang._() resolves against a literal at compile time, so
           each one is listed as its own key - and lh_data_text is GENERATED, by
           tools/gen-data-strings.py, because a note added to chassis.json and forgotten
           here comes out in English on an Arabic page with nothing to say so. */
        const lh_data_text = {
            'FleXi module, bay A': "{{ lang._('FleXi module, bay A') }}",
            'This bay enumerates before the faceplate ports.':
                "{{ lang._('This bay enumerates before the faceplate ports.') }}",
            'faceplate': "{{ lang._('faceplate') }}",
            'faceplate, 1G SFP': "{{ lang._('faceplate, 1G SFP') }}",
            'Intel I210 Fiber. The driver has no transceiver access, so these cages report no module data even with a module seated - the plugin shows link, media and error counters for them and nothing optical.':
                "{{ lang._('Intel I210 Fiber. The driver has no transceiver access, so these cages report no module data even with a module seated - the plugin shows link, media and error counters for them and nothing optical.') }}",
            'Looked at on the reference appliance: the cages have no identification light this firewall can drive. The node is there and the write is accepted, and nothing lights, because the cage lights on this board are not wired to the controller pins the driver drives - the copper ports on the same machine do blink. Use the activity light instead.':
                "{{ lang._('Looked at on the reference appliance: the cages have no identification light this firewall can drive. The node is there and the write is accepted, and nothing lights, because the cage lights on this board are not wired to the controller pins the driver drives - the copper ports on the same machine do blink. Use the activity light instead.') }}",
            'faceplate, 10G SFP+': "{{ lang._('faceplate, 10G SFP+') }}",
            'Intel X520. Full transceiver telemetry.':
                "{{ lang._('Intel X520. Full transceiver telemetry.') }}",
            'No port table for this model yet. Add one to chassis.json - see docs/contributing-chassis.md - and the labels appear on the next poll.':
                "{{ lang._('No port table for this model yet. Add one to chassis.json - see docs/contributing-chassis.md - and the labels appear on the next poll.') }}",
            'Ports keep their interface names. Set a label per port by hand under Interfaces > Link Health > Settings, or contribute a table for this appliance.':
                "{{ lang._('Ports keep their interface names. Set a label per port by hand under Interfaces > Link Health > Settings, or contribute a table for this appliance.') }}",
            'Port order and bay grouping are corroborated by three independent witnesses on the reference machine: the owner\'s own interface names, the chip and PCI-subdevice boundaries, and two contiguous MAC blocks (8 addresses under one OUI for the module, 12 under another for the mainboard, in printed order). The physical ARRANGEMENT below - which row, and left-to-right position - is drawn from the owner\'s description and has not been checked against the metal.':
                "{{ lang._('Port order and bay grouping are corroborated by three independent witnesses on the reference machine: the owner\'s own interface names, the chip and PCI-subdevice boundaries, and two contiguous MAC blocks (8 addresses under one OUI for the module, 12 under another for the mainboard, in printed order). The physical ARRANGEMENT below - which row, and left-to-right position - is drawn from the owner\'s description and has not been checked against the metal.') }}",
            'An expansion bay. Its ports enumerate before the faceplate ports, which is why PortA1 is igb0 while the printed Port1 is igb8.':
                "{{ lang._('An expansion bay. Its ports enumerate before the faceplate ports, which is why PortA1 is igb0 while the printed Port1 is igb8.') }}",
            'These cages have no identification light this firewall can drive: the driver registers the LED node, accepts the write and nothing lights, because it drives one fixed LED index that this board does not wire. Measured on this appliance, where the copper ports do blink. Use the activity light instead - the second button on the port detail.':
                "{{ lang._('These cages have no identification light this firewall can drive: the driver registers the LED node, accepts the write and nothing lights, because it drives one fixed LED index that this board does not wire. Measured on this appliance, where the copper ports do blink. Use the activity light instead - the second button on the port detail.') }}"
        };

        function lh_data(text) {
            if (!text) { return ''; }
            return lh_data_text[text] || text;
        }

        function lh_num(value) {
            const number = Number(value);
            return isNaN(number) ? String(value != null ? value : '') : number.toLocaleString();
        }

        function lh_speed(mbps) {
            const value = Number(mbps) || 0;
            if (!value) { return "{{ lang._('unknown') }}"; }
            return value >= 1000
                ? "{{ lang._('{n} Gbit/s') }}".replace('{n}', value / 1000)
                : "{{ lang._('{n} Mbit/s') }}".replace('{n}', value);
        }

        function lh_causes(causes, port) {
            const meta = (port || {}).counter_meta || {};
            const names = Object.keys(causes || {});
            if (names.length === 0) {
                return "{{ lang._('the driver reports errors but cannot say which kind') }}";
            }
            names.sort(function (a, b) { return causes[b] - causes[a]; });
            return names.slice(0, 3).map(function (name) {
                const label = (meta[name] || {}).label || name;
                return lh_data(label) + ': ' + lh_num(causes[name]);
            }).join(', ');
        }

        function lh_fill(template, values) {
            let out = template;
            $.each(values, function (key, value) {
                out = out.split('{' + key + '}').join(String(value));
            });
            return out;
        }

        function reason_text(reason, port) {
            if (!reason) { return ''; }
            const p = reason.params || {};
            switch (reason.code) {
                case 'clean':
                    return lh_fill("{{ lang._('clean - {frames} frames, no errors') }}",
                        {frames: lh_num(p.frames)});
                case 'quiet':
                    return lh_fill("{{ lang._('only {frames} frames in this window - not enough traffic to judge') }}",
                        {frames: lh_num(p.frames)});
                case 'gathering':
                    return lh_fill("{{ lang._('measuring: {frames} of {needed} frames so far, over {minutes} min') }}",
                        {frames: lh_num(p.frames), needed: lh_num(p.needed),
                         minutes: Math.max(1, Math.floor((p.seconds || 0) / 60))});
                case 'baseline':
                    return "{{ lang._('first look at this port - measuring from here') }}";
                case 'disabled':
                    return "{{ lang._('port is switched off in the configuration') }}";
                case 'not_watched':
                    return "{{ lang._('this port is not being watched') }}";
                case 'down':
                    return "{{ lang._('no cable, or nothing answering at the other end') }}";
                case 'errors_fail':
                    return lh_fill("{{ lang._('{percent}% of received frames were corrupted ({causes})') }}",
                        {percent: p.percent, causes: lh_causes(p.causes, port)});
                case 'errors_warn':
                    return lh_fill("{{ lang._('{ppm} corrupted frames per million ({causes})') }}",
                        {ppm: lh_num(p.ppm), causes: lh_causes(p.causes, port)});
                case 'errors_watch':
                    return lh_fill("{{ lang._('a few frames are being corrupted ({ppm} per million)') }}",
                        {ppm: lh_num(p.ppm)});
                case 'downshift':
                    return lh_fill("{{ lang._('negotiated {speed} where the port supports {max} - usually a broken pair in the cable') }}",
                        {speed: lh_speed(p.speed), max: lh_speed(p.max_speed)});
                case 'duplex':
                    return lh_fill("{{ lang._('collisions on a full-duplex link ({count}) - the two ends disagree about duplex') }}",
                        {count: lh_num(p.collisions)});
                case 'flapping_warn':
                case 'flapping_fail':
                    return lh_fill("{{ lang._('the link went down and came back {count} times in the last hour') }}",
                        {count: lh_num(p.count)});
                case 'optics_rx_low':
                    return lh_fill("{{ lang._('receive power {rx} dBm is below what this media type needs ({min} dBm)') }}",
                        {rx: p.rx_dbm, min: p.minimum});
                case 'optics_rx_high':
                    return lh_fill("{{ lang._('receive power {rx} dBm is above the overload point ({max} dBm)') }}",
                        {rx: p.rx_dbm, max: p.maximum});
                case 'optics_rx_thin':
                    return lh_fill("{{ lang._('only {margin} dB of optical margin left') }}",
                        {margin: p.margin});
                case 'optics_hot':
                    return lh_fill("{{ lang._('transceiver is running warm ({temp} C)') }}", {temp: p.temp_c});
                case 'optics_hot_fail':
                    return lh_fill("{{ lang._('transceiver is at {temp} C') }}", {temp: p.temp_c});
                case 'optics_voltage':
                    return lh_fill("{{ lang._('transceiver supply is {volts} V') }}", {volts: p.vcc_v});
                case 'optics_gone':
                    return "{{ lang._('the transceiver is no longer there') }}";
                case 'optics_swapped':
                    return "{{ lang._('the transceiver was changed') }}";
                default:
                    return reason.text || reason.code || '';
            }
        }

        const port_formatters = {
            port: function (column, row) {
                let html = '<span class="lh-port lh-tech">' + esc(port_title(row)) + '</span>';
                if (row.bay) {
                    html += '<span class="lh-sub">' + esc(lh_data(row.bay)) + '</span>';
                }
                return html;
            },
            iface: function (column, row) {
                let html = tech(row['if']);
                if (row.name) {
                    html += '<span class="lh-sub lh-tech">' + esc(row.name) + '</span>';
                }
                return html;
            },
            serves: function (column, row) {
                const neighbours = row.serves || [];
                if (neighbours.length === 0) {
                    return '<span class="lh-muted">' + "{{ lang._('nothing seen yet') }}" + '</span>';
                }
                let html = $.map(neighbours.slice(0, 3), function (address) {
                    return tech(address);
                }).join('<br/>');
                if (neighbours.length > 3) {
                    html += '<span class="lh-sub">' +
                        "{{ lang._('and {count} more') }}".replace('{count}', neighbours.length - 3) + '</span>';
                }
                return html;
            },
            link: function (column, row) {
                const link = row.link || {};
                const active = link.state === 'active';
                let html = '<i class="fa fa-fw fa-plug ' + (active ? 'text-success' : 'text-muted') + '"></i> ';
                if (!active) {
                    return html + '<span class="lh-muted">' +
                        (link_state_text[link.state] || "{{ lang._('unknown') }}") + '</span>';
                }
                html += tech(speed_text(link.speed_mbps));
                if (link.media) {
                    html += '<span class="lh-sub lh-tech">' + esc(link.media) + '</span>';
                }
                if (link.downshift) {
                    html += '<span class="lh-sub text-warning">' +
                        "{{ lang._('below the {max} this port offers') }}".replace('{max}', speed_text(link.max_speed_mbps)) +
                        '</span>';
                }
                return html;
            },
            rate: function (column, row) {
                const state = row.verdict_state;
                /* A port that carried almost nothing has no rate worth printing. A green zero here
                   would claim a clean cable that nothing has actually put under load. */
                if (state === 'idle') {
                    return '<span class="lh-muted">' + "{{ lang._('not enough traffic') }}" + '</span>';
                }
                if (state === 'down' || state === 'disabled') {
                    return '<span class="lh-muted">&mdash;</span>';
                }
                const window_stats = row.window || {};
                let colour = 'text-success';
                if (state === 'fail') {
                    colour = 'text-danger';
                } else if (state === 'warn') {
                    colour = 'text-warning';
                } else if (state === 'watch') {
                    colour = 'text-info';
                }
                return '<span class="lh-tech ' + colour + '">' + esc(format_rate(row.error_ppm)) + '</span>' +
                    '<span class="lh-sub lh-tech">' +
                    esc("{{ lang._('{errors} bad of {frames} frames') }}"
                        .replace('{errors}', count(window_stats.rx_errors || 0))
                        .replace('{frames}', count(window_stats.rx_frames || 0))) +
                    '</span>';
            },
            verdict: function (column, row) {
                const state = row.verdict_state;
                const reasons = (row.verdict || {}).reasons || [];
                let html = '<span class="label ' + (verdict_class[state] || 'label-default') + '">' +
                    esc(verdict_text[state] || state) + '</span>';
                if (reasons.length > 0) {
                    /* the collector puts the reason it judged on first */
                    html += '<span class="lh-sub">' + esc(reason_text(reasons[0], row)) + '</span>';
                    if (reasons.length > 1) {
                        html += '<span class="lh-sub lh-muted">' +
                            "{{ lang._('and {count} more') }}".replace('{count}', reasons.length - 1) + '</span>';
                    }
                }
                return html;
            },
            optics: function (column, row) {
                const optics = row.optics || {};
                /* No box at all when the port has no transceiver telemetry. An empty one would
                   read as a missing measurement rather than as a driver that never offers one. */
                if (!optics.present) {
                    return '<span class="lh-muted">&mdash;</span>';
                }
                let html = tech(optics.type);
                const identity = [optics.vendor, optics.pn].filter(Boolean).join(' ');
                if (identity) {
                    html += '<span class="lh-sub lh-tech">' + esc(identity) + '</span>';
                }
                const readings = [];
                const rx = fixed(optics.rx_dbm, 2);
                const temperature = fixed(optics.temp_c, 1);
                if (rx !== null) {
                    readings.push(rx + ' dBm');
                }
                if (temperature !== null) {
                    readings.push(temperature + ' °C');
                }
                if (readings.length > 0) {
                    html += '<span class="lh-sub lh-tech">' + esc(readings.join(' · ')) + '</span>';
                }
                if (optics.alarm) {
                    html += '<span class="lh-sub"><span class="label label-danger">' +
                        "{{ lang._('module alarm') }}" + '</span></span>';
                } else if (optics.warning) {
                    html += '<span class="lh-sub"><span class="label label-warning">' +
                        "{{ lang._('module warning') }}" + '</span></span>';
                }
                return html;
            }
        };

        /* Which columns are worth a narrow window, in the order they stop being worth it.
           A phone is left with the socket, the verdict and the buttons: the verdict sentence
           already carries the frame count and the error rate that the dropped columns would
           have shown, so nothing is lost that is not said in words somewhere else. */
        const GRID_OPTIONAL = ['serves_text', 'optics_text', 'if', 'link_speed', 'error_ppm'];
        const GRID_NARROW = 768;
        const GRID_MEDIUM = 1200;
        let grid_hidden_now = null;

        function fit_grid_to_window() {
            const wrapper = $('#grid-ports').data('UIBootgrid');
            if (!wrapper || typeof wrapper.getTable !== 'function') {
                return;
            }
            const table = wrapper.getTable();
            if (!table || typeof table.hideColumn !== 'function') {
                return;
            }
            const width = window.innerWidth;
            let hidden;
            if (width < GRID_NARROW) {
                hidden = GRID_OPTIONAL;
            } else if (width < GRID_MEDIUM) {
                hidden = ['serves_text', 'optics_text'];
            } else {
                hidden = [];
            }
            const signature = hidden.join(',');
            if (signature === grid_hidden_now) {
                return;
            }
            grid_hidden_now = signature;
            $.each(GRID_OPTIONAL, function (index, field) {
                try {
                    if (hidden.indexOf(field) >= 0) {
                        table.hideColumn(field);
                    } else {
                        table.showColumn(field);
                    }
                } catch (ignored) {
                    /* a column this grid has not finished building yet: the next sweep,
                       or the next resize, will find it */
                }
            });
            /* A row keeps the height it was first measured at, so hiding the column that
               made it tall leaves the gap behind: a row carrying three addresses stayed
               142 px with nothing in it. Only a full redraw measures them again. */
            try {
                table.redraw(true);
            } catch (ignored) {
                /* the grid is mid-build; the next call redraws it */
            }
        }

        /* A phone turned on its side is a different window, and so is a desktop pane dragged
           narrow. Debounced because a drag fires this by the dozen and each call relays out
           every row. */
        let grid_fit_timer = null;
        $(window).on('resize', function () {
            window.clearTimeout(grid_fit_timer);
            grid_fit_timer = window.setTimeout(fit_grid_to_window, 200);
        });

        /* The grid restores its column titles from the browser, and a title saved while
           the GUI was in another language survives the change - so the headings can be
           one language while everything the server just rendered is another. Measured
           here: eight Arabic headings on an otherwise English page, restored from a copy
           saved weeks before. The titles the server sent are in the table right now, so
           they can simply be compared, and the saved copy dropped when it disagrees.

           Widths and order go with it, which is a real cost and the reason this only
           fires when the titles actually differ: somebody who has not changed language
           never reaches it. */
        function drop_stale_column_state(grid_id) {
            const key = 'tabulator-' + window.location.pathname + '#' + grid_id + '-columns';
            let saved;
            try {
                saved = JSON.parse(window.localStorage.getItem(key) || 'null');
            } catch (ignored) {
                /* Unparseable is as stale as mismatched, and for the same reason. */
                saved = null;
            }
            if (!Array.isArray(saved) || saved.length === 0) {
                return;
            }
            const sent = {};
            $('#' + grid_id + ' thead th').each(function () {
                const id = $(this).attr('data-column-id');
                if (id) {
                    sent[id] = $(this).text().trim();
                }
            });
            const stale = saved.some(function (column) {
                const id = column.field || column.id;
                return id && sent[id] !== undefined && column.title !== undefined
                    && column.title !== sent[id];
            });
            if (stale) {
                try {
                    window.localStorage.removeItem(key);
                } catch (ignored) {
                    /* A browser that refuses to forget is one this page cannot fix. */
                }
            }
        }

        drop_stale_column_state('grid-ports');

        $('#grid-ports').UIBootgrid({
            datakey: 'if',
            options: {
                /* the whole document arrives in one call and is small, so sort and page it here */
                ajax: false,
                selection: false,
                multiSelect: false,
                /* let a cell wrap: every row carries a second, quieter line under the first */
                responsive: true,
                disableScroll: true,
                formatters: port_formatters,
                sorters: {
                    /* the flat sort fields are numbers, and a column with no sorter of its own is
                       compared as text, which would put 900 ppm above 1000 ppm */
                    number: function (a, b) {
                        const left = parseFloat(a);
                        const right = parseFloat(b);
                        return (isFinite(left) ? left : 0) - (isFinite(right) ? right : 0);
                    }
                }
            },
            commands: {
                detail: {
                    method: function (event, cell) {
                        show_detail(cell.getData()['if']);
                    },
                    classname: 'fa fa-fw fa-search',
                    title: "{{ lang._('Details') }}",
                    sequence: 10
                },
                identify: {
                    filter: function (cell) {
                        /* Only where the driver registered an LED node for this port. Everywhere
                           else the button could do nothing but come back saying so, and a button
                           that always fails teaches the wrong thing about what this page knows.
                           A dark port is not excluded: a socket with no cable in it blinks just
                           the same, and that is precisely the socket whose name you want before
                           you plug anything into it. */
                        return has_capability(cell.getData(), 'IDENTIFY_LED');
                    },
                    method: function (event, cell) {
                        identify_port(cell.getData()['if']);
                    },
                    classname: 'fa fa-fw fa-lightbulb-o',
                    title: "{{ lang._('Blink this socket') }}",
                    sequence: 15
                },
                flicker: {
                    filter: function (cell) {
                        /* Offered wherever it can work, not only where the LED failed: whether a
                           board wires its identification light is not something this page can
                           ask, only something the owner finds out by pressing the other button
                           and looking. Two ways offered honestly beat one way that silently
                           does nothing. */
                        return can_flicker(cell.getData());
                    },
                    method: function (event, cell) {
                        identify_port(cell.getData()['if'], null, 'flicker');
                    },
                    classname: 'fa fa-fw fa-exchange',
                    title: "{{ lang._('Beat this activity light') }}",
                    sequence: 17
                },
                test: {
                    filter: function (cell) {
                        /* A dark port cannot be flooded and the collector would refuse the test,
                           so do not offer a button that is guaranteed to come back with an error. */
                        return ((cell.getData().link || {}).state === 'active');
                    },
                    method: function (event, cell) {
                        ask_for_test(cell.getData());
                    },
                    classname: 'fa fa-fw fa-bolt',
                    title: "{{ lang._('Test this port') }}",
                    sequence: 20
                }
            }
        });

        function render_summary(doc) {
            const chassis = doc.chassis || {};
            const parts = [];
            const machine = [chassis.vendor, chassis.model].filter(Boolean).join(' ');
            if (machine) {
                parts.push(machine);
            }
            /* The very first sweep has no earlier reading to measure against, so it carries no
               window. Saying "measured over" and then nothing would read as a lost number. */
            const measured = duration_text(doc.window_seconds);
            if (measured !== '') {
                parts.push("{{ lang._('measured over {duration}') }}".replace('{duration}', measured));
            }
            if (doc.generated) {
                parts.push("{{ lang._('last sweep {time}') }}".replace('{time}', format_time(doc.generated)));
            }
            fill_summary($('#ports-summary'), parts);
        }

        /* `after` runs on every outcome, including the ones that brought no document back. A
           caller that put a spinner on a button or has a dialog waiting on the answer has to be
           let go of again even when the firewall did not answer. */
        function reload_status(after) {
            ajaxGet('/api/linkhealth/service/status', {}, function (data, request_status) {
                if (request_status !== 'success') {
                    /* a request that did not come back is not an answer about the ports, so leave
                       the measurement that is already on the screen where it is */
                    $('#ports-summary').text("{{ lang._('The last measurement could not be read from the firewall.') }}");
                } else if (!data || !Array.isArray(data.ports)) {
                    $('#grid-ports').bootgrid('clear');
                    $('#ports-summary').text("{{ lang._('No measurement has been written yet. The collector runs once a minute, so give it one cycle.') }}");
                } else {
                    status_doc = data;
                    render_summary(data);
                    fill_detail_picker(data);
                    const rows = port_rows(data);
                    if (rows.length > 0) {
                        $('#grid-ports').bootgrid('replace', rows);
                        fit_grid_to_window();
                    } else {
                        /* replace() refuses an empty list, and a firewall with no physical port
                           left to watch is a real answer */
                        $('#grid-ports').bootgrid('clear');
                    }
                    if (current_port !== null) {
                        render_detail(current_port);
                    }
                }
                if (typeof after === 'function') {
                    after(status_doc);
                }
            });
        }

        function find_port(iface) {
            if (status_doc === null) {
                return null;
            }
            const found = $.grep(status_doc.ports || [], function (port) {
                return port['if'] === iface;
            });
            return found.length > 0 ? found[0] : null;
        }

        /* ------------------------------------------------------------------ the detail tab */

        function detail_table(rows) {
            /* A row whose value is missing is left out entirely. An empty line would read as a
               measurement that came back blank instead of one that was never available. */
            const $table = $('<table class="table table-condensed lh-detail"/>');
            const $body = $('<tbody/>');
            $.each(rows, function (index, row) {
                if (row[1] === null || row[1] === undefined || row[1] === '') {
                    return;
                }
                $body.append($('<tr/>')
                    .append($('<th/>').text(row[0]))
                    .append($('<td/>').html(row[1])));
            });
            $table.append($body);
            return $table;
        }

        /* content is one element or a list of them; a table handed over on its own becomes a
           direct child of the panel, which is what strips its outer border in this theme */
        function detail_panel(title, content, footnote) {
            const $panel = $('<div class="panel panel-default"/>');
            $panel.append($('<div class="panel-heading"/>').append($('<h3 class="panel-title"/>').text(title)));
            $.each(Array.isArray(content) ? content : [content], function (index, $part) {
                $panel.append($part);
            });
            if (footnote) {
                $panel.append($('<div class="panel-footer lh-note"/>').text(footnote));
            }
            return $panel;
        }

        function identity_panel(port) {
            const capabilities = $.map(port.caps || [], function (flag) {
                const help = capability_help[flag];
                return '<span class="label label-default lh-tech"' +
                    (help ? ' data-toggle="tooltip" title="' + esc(help) + '"' : '') + '>' + esc(flag) + '</span>';
            }).join(' ');
            const neighbours = port.serves || [];
            return detail_panel("{{ lang._('Port') }}", detail_table([
                ["{{ lang._('Chassis label') }}", tech(port.label)],
                ["{{ lang._('Bay') }}", esc(lh_data(port.bay))],
                /* The chassis table carries a note for a bay whose hardware cannot do what the
                   neighbouring bays can - an SFP cage on a driver with no transceiver access, for
                   one. Without it, a missing optics panel looks like something went wrong. */
                ["{{ lang._('About this bay') }}", esc(lh_data(port.bay_note))],
                ["{{ lang._('Interface') }}", tech(port['if'])],
                ["{{ lang._('Name in the configuration') }}", tech(port.name)],
                ["{{ lang._('Configuration key') }}", tech(port.confkey)],
                ["{{ lang._('Driver') }}", port.driver ? tech(port.driver + (port.unit === undefined ? '' : port.unit)) : ''],
                ["{{ lang._('Chip') }}", tech(port.chip)],
                ["{{ lang._('Serves') }}", neighbours.length > 0 ? $.map(neighbours, function (address) {
                    return tech(address);
                }).join('<br/>') : '<span class="lh-muted">' + "{{ lang._('nothing seen yet') }}" + '</span>'],
                ["{{ lang._('What can be read here') }}", capabilities]
            ]), "{{ lang._('Neighbours come from the bridge address table, which is the only place that still knows which port a host is actually behind.') }}");
        }

        function verdict_panel(port) {
            const verdict = port.verdict || {};
            const state = verdict.state || 'ok';
            const reasons = verdict.reasons || [];
            const $content = $('<div class="panel-body"/>');
            $content.append($('<span class="label lh-verdict ' + (verdict_class[state] || 'label-default') + '"/>')
                .text(verdict_text[state] || state));
            if (verdict.since) {
                $content.append($('<span class="lh-sub lh-tech"/>')
                    .text("{{ lang._('since {time}') }}".replace('{time}', format_time(verdict.since))));
            }
            const $reasons = $('<ul class="lh-reasons"/>');
            $.each(reasons, function (index, reason) {
                const icon = severity_class[reason.severity] || 'fa-circle-o text-muted';
                $reasons.append($('<li/>')
                    .append($('<i class="fa fa-fw ' + icon + '"></i> '))
                    .append($('<span/>').text(reason_text(reason, port))));
            });
            if (reasons.length > 0) {
                $content.append($reasons);
            }
            return detail_panel("{{ lang._('Verdict') }}", $content);
        }

        function window_panel(port) {
            const window_stats = port.window || {};
            const state = (port.verdict || {}).state;
            let rate;
            if (state === 'idle') {
                rate = '<span class="lh-muted">' + "{{ lang._('not enough traffic to judge') }}" + '</span>';
            } else if (state === 'down' || state === 'disabled') {
                rate = '<span class="lh-muted">&mdash;</span>';
            } else {
                rate = tech(format_rate(window_stats.error_ppm));
            }
            return detail_panel("{{ lang._('This window') }}", detail_table([
                /* The port's own stretch, not the sweep interval. A quiet port keeps gathering
                   across sweeps until it has enough frames to judge, so the counts below can
                   cover a quarter of an hour while the document's own window is one minute.
                   Printing the sweep here would put every number beside a period nobody
                   measured it over. */
                ["{{ lang._('Window length') }}",
                    tech(duration_text(window_stats.seconds || (status_doc || {}).window_seconds))],
                ["{{ lang._('Frames in') }}", tech(count(window_stats.rx_frames))],
                ["{{ lang._('Frames out') }}", tech(count(window_stats.tx_frames))],
                ["{{ lang._('Bytes in') }}", tech(count(window_stats.rx_bytes))],
                ["{{ lang._('Bytes out') }}", tech(count(window_stats.tx_bytes))],
                ["{{ lang._('Errors in') }}", tech(count(window_stats.rx_errors))],
                ["{{ lang._('Errors out') }}", tech(count(window_stats.tx_errors))],
                ["{{ lang._('Error rate') }}", rate]
            ]), "{{ lang._('Every number here is the change over one window. The totals since boot are not a rate: one healthy port on this machine has carried thousands of input errors since it last came up.') }}");
        }

        function counters_panel(port) {
            const counters = window_counters(port);
            const names = Object.keys(counters);
            if (!has_capability(port, 'COUNTERS') || names.length === 0) {
                return null;
            }
            const meta = port.counter_meta || {};
            const $table = $('<table class="table table-condensed lh-detail"/>');
            $table.append($('<thead/>').append($('<tr/>')
                .append($('<th/>').text("{{ lang._('Counter') }}"))
                .append($('<th/>').text("{{ lang._('What it counts') }}"))
                .append($('<th/>').text("{{ lang._('This window') }}"))));
            const $body = $('<tbody/>');
            $.each(names, function (index, name) {
                const delta = Number(counters[name]) || 0;
                const kind = (meta[name] || {})['class'];
                const $row = $('<tr/>');
                /* The sysctl leaf name is left exactly as the driver spells it, so that what is on
                   the screen is what can be typed into sysctl to see the same number. */
                $row.append($('<td/>').append($('<span class="lh-tech"/>').text(name)));
                $row.append($('<td class="lh-muted"/>').text(counter_class_text[kind] || ''));
                /* Only a counter the driver map calls a physical fault is given any weight. The
                   large numbers on this page are mostly the harmless ones - one clean port here
                   carries sixteen thousand checksum errors - and making them all stand out would
                   say the opposite of what the map says about them. */
                $row.append($('<td/>').append($('<span class="lh-tech"/>')
                    .addClass(delta > 0 && kind === 'cable' ? 'lh-strong' : '').text(count(delta))));
                $body.append($row);
            });
            $table.append($body);
            return detail_panel("{{ lang._('Hardware counters') }}", $table,
                "{{ lang._('Not every counter is damage. A provably clean port on this machine carries tens of thousands of checksum errors, and the flow control and missed-packet counters follow load rather than a bad cable. The middle column is what the driver map says each one means, and the verdict only adds up the ones marked as a physical fault, which is why nothing here is coloured.') }}");
        }

        /* The ladder is the list of media the port advertises, in the words `ifconfig -m` printed.
           Plain strings are what the collector passes through; an entry that carries its own speed
           is accepted too. The order is left alone: it is the order the PHY itself advertises, and
           reordering it would be our invention, not the hardware's. */
        function ladder_entries(link) {
            const listed = Array.isArray(link.supported_media) ? link.supported_media : [];
            return $.map(listed, function (entry) {
                if (entry !== null && typeof entry === 'object') {
                    const media = entry.media || entry.name || '';
                    const speed = Number(entry.speed_mbps);
                    return {media: media, speed_mbps: isFinite(speed) && speed > 0 ? speed : media_speed(media)};
                }
                const media = String(entry);
                return {media: media, speed_mbps: media_speed(media)};
            }).filter(function (entry) {
                return entry.media !== '';
            });
        }

        function ladder_panel(port) {
            const link = port.link || {};
            const entries = ladder_entries(link);
            if (!has_capability(port, 'MEDIA_LADDER') || entries.length === 0) {
                return null;
            }
            const negotiated = String(link.media || '');
            let active = -1;
            for (let i = 0; i < entries.length; i++) {
                /* The ladder spells a rung with its duplex - "1000baseT full-duplex" - while the
                   negotiated media is the bare name, so the rung in use is the one that name
                   begins. An exact match is taken first, because a bare rung exists as well. */
                if (entries[i].media === negotiated) {
                    active = i;
                    break;
                }
                if (active < 0 && negotiated !== '' && entries[i].media.indexOf(negotiated + ' ') === 0) {
                    active = i;
                }
            }
            if (active < 0) {
                /* the negotiated string is not always spelled the way the ladder spells it, so
                   fall back on the one rung that runs at the speed we ended up with */
                const speed = Number(link.speed_mbps);
                for (let i = 0; i < entries.length; i++) {
                    if (speed > 0 && entries[i].speed_mbps === speed) {
                        active = i;
                        break;
                    }
                }
            }
            const $content = $('<div class="panel-body"/>');
            if (link.downshift) {
                /* No arrow here. An arrow would point the wrong way as soon as the page is read
                   right to left, and the sentence says it better anyway. */
                $content.append($('<div class="alert alert-warning lh-alert"/>').text(
                    "{{ lang._('This port has settled on {speed} although it advertises {max}. A cable with one broken pair does exactly this.') }}"
                        .replace('{speed}', speed_text(link.speed_mbps))
                        .replace('{max}', speed_text(link.max_speed_mbps))));
            }
            const $list = $('<ul class="lh-ladder"/>');
            $.each(entries, function (index, entry) {
                const chosen = index === active;
                const $item = $('<li/>').addClass(chosen ? 'lh-rung' : '');
                $item.append($('<i class="fa fa-fw ' + (chosen ? 'fa-dot-circle-o' : 'fa-circle-o lh-muted') + '"></i>'));
                $item.append($('<span class="lh-tech"/>').text(entry.media));
                $list.append($item);
            });
            $content.append($list);
            return detail_panel("{{ lang._('What this port can do') }}", $content,
                "{{ lang._('The marked rung is the one in use. This list is the richest signal available on every driver, including the ones that keep no error counters at all.') }}");
        }

        /* Limits may be written as a pair or as a named low and high; take either, and take
           neither as "no limit published for this reading". */
        function limit_pair(limits) {
            if (!limits) {
                return null;
            }
            let low = null;
            let high = null;
            if (Array.isArray(limits) && limits.length === 2) {
                low = limits[0];
                high = limits[1];
            } else if (typeof limits === 'object') {
                low = limits.low !== undefined ? limits.low : limits.min;
                high = limits.high !== undefined ? limits.high : limits.max;
            }
            low = (low === null || low === undefined || !isFinite(Number(low))) ? null : Number(low);
            high = (high === null || high === undefined || !isFinite(Number(high))) ? null : Number(high);
            return (low === null && high === null) ? null : {low: low, high: high};
        }

        /* One reading of the module, with the range it is allowed to sit in when the collector
           published one. The range is the module's own, clamped to the standard for its media
           type, because a vendor sets its alarm far looser than the media can actually take. */
        function optics_reading(optics, key, digits, unit) {
            const shown = fixed(optics[key], digits);
            if (shown === null) {
                return null;
            }
            const value = Number(optics[key]);
            const limits = limit_pair((optics.limits || {})[key]);
            const outside = limits !== null &&
                ((limits.low !== null && value < limits.low) || (limits.high !== null && value > limits.high));
            let html = '<span class="lh-tech' + (outside ? ' text-danger lh-strong' : '') + '">' +
                esc(shown + ' ' + unit) + '</span>';
            if (limits !== null) {
                const low = limits.low === null ? '' : limits.low.toFixed(digits);
                const high = limits.high === null ? '' : limits.high.toFixed(digits);
                let sentence;
                if (low !== '' && high !== '') {
                    sentence = "{{ lang._('allowed {low} to {high}') }}".replace('{low}', low).replace('{high}', high);
                } else if (high !== '') {
                    sentence = "{{ lang._('allowed up to {high}') }}".replace('{high}', high);
                } else {
                    sentence = "{{ lang._('allowed from {low}') }}".replace('{low}', low);
                }
                html += '<span class="lh-sub lh-tech">' + esc(sentence + ' ' + unit) + '</span>';
            }
            return html;
        }

        function optics_panel(port) {
            const optics = port.optics || {};
            if (!has_capability(port, 'OPTICS_INVENTORY') || !optics.present) {
                return null;
            }
            const rows = [
                ["{{ lang._('Type') }}", tech(optics.type)],
                ["{{ lang._('Vendor') }}", tech(optics.vendor)],
                ["{{ lang._('Part number') }}", tech(optics.pn)],
                ["{{ lang._('Serial number') }}", tech(optics.sn)],
                ["{{ lang._('Manufactured') }}", tech(optics.date)]
            ];
            if (has_capability(port, 'OPTICS_DOM')) {
                rows.push(["{{ lang._('Temperature') }}", optics_reading(optics, 'temp_c', 1, '°C')]);
                rows.push(["{{ lang._('Supply voltage') }}", optics_reading(optics, 'vcc_v', 2, 'V')]);
                rows.push(["{{ lang._('Receive power') }}", optics_reading(optics, 'rx_dbm', 2, 'dBm')]);
                rows.push(["{{ lang._('Transmit power') }}", optics_reading(optics, 'tx_dbm', 2, 'dBm')]);
                rows.push(["{{ lang._('Transmit bias') }}", optics_reading(optics, 'tx_bias_ma', 2, 'mA')]);
                /* Only when the document actually carries them. A module that reports its
                   readings without the flag bytes leaves both of these absent, and a green
                   "none set" there would be a reassurance nobody measured - which is precisely
                   the wrong thing to print about the one signal that stayed silent through a
                   real fault on this machine. */
                if (optics.alarm !== undefined || optics.warning !== undefined) {
                    rows.push(["{{ lang._('Module flags') }}", optics.alarm ?
                        '<span class="label label-danger">' + "{{ lang._('alarm') }}" + '</span>' :
                        (optics.warning ? '<span class="label label-warning">' + "{{ lang._('warning') }}" + '</span>' :
                            '<span class="label label-success">' + "{{ lang._('none set') }}" + '</span>')]);
                }
            }
            return detail_panel("{{ lang._('Transceiver') }}", detail_table(rows),
                "{{ lang._('These readings corroborate, they never decide. During a measured fault on this machine, with one frame in twenty-six failing its check, both modules read inside their own limits and set no flag at all. The identity above is the part that finds such a fault: the two ends of one fibre held different modules. Limits are the ones the module publishes, clamped to the standard for its type, because vendors set them far looser.') }}");
        }

        function flaps_panel(port) {
            const flaps = port.flaps || {};
            if (flaps.count === undefined || flaps.count === null) {
                return null;
            }
            const $content = $('<div class="panel-body"/>');
            /* The period is named only when the document carries it. A sentence that ends in an
               empty gap reads as a lost number rather than as one nobody wrote down. */
            const period = duration_text(flaps.window_seconds);
            $content.append($('<p/>').text(period === '' ?
                "{{ lang._('{count} link changes in the period the verdict counts.') }}"
                    .replace('{count}', count(flaps.count)) :
                "{{ lang._('{count} link changes in the last {duration}.') }}"
                    .replace('{count}', count(flaps.count))
                    .replace('{duration}', period)));
            if (flaps.last) {
                $content.append($('<p class="lh-tech"/>').text(
                    "{{ lang._('Most recent: {time}') }}".replace('{time}', format_time(flaps.last))));
            }
            /* The individual moments are kept by the collector when it can. They are worth
               showing, because a burst in one evening reads very differently from one a week. */
            const moments = Array.isArray(flaps.events) ? flaps.events : [];
            if (moments.length > 0) {
                const $list = $('<ul class="lh-flaps"/>');
                $.each(moments, function (index, entry) {
                    const when = (entry !== null && typeof entry === 'object') ? entry.when : entry;
                    const state = (entry !== null && typeof entry === 'object') ? entry.state : null;
                    $list.append($('<li class="lh-tech"/>').text(format_time(when) + (state ? ' — ' + state : '')));
                });
                $content.append($list);
            }
            return detail_panel("{{ lang._('Link changes') }}", $content,
                "{{ lang._('Reconfiguring an interface looks exactly like a flap, so a handful of them is not a fault on its own. Three within an hour is the point at which the verdict reacts.') }}");
        }

        /* When the run ended. The contract calls that moment `when`; the worker's own result file
           calls it `finished`, and while the test is still going it has only `started`. Whichever
           is there is what tells this page apart from the run before it. */
        function test_time(test) {
            if (test === null || test === undefined) {
                return 0;
            }
            const candidates = [test.when, test.finished, test.started];
            for (let i = 0; i < candidates.length; i++) {
                const value = Number(candidates[i]);
                if (isFinite(value) && value > 0) {
                    return value;
                }
            }
            return 0;
        }

        function test_rows(test) {
            return [
                ["{{ lang._('Run at') }}", tech(format_time(test_time(test)))],
                ["{{ lang._('Target') }}", tech(test.target)],
                ["{{ lang._('Frames') }}", tech(count(test.frames))],
                ["{{ lang._('Errors') }}", tech(count(test.errors))],
                ["{{ lang._('Error rate') }}", tech(format_rate(test.error_ppm))],
                ["{{ lang._('Packet loss') }}", tech(percent_text(test.loss_pct))]
            ];
        }

        function last_test_panel(port) {
            const test = port.last_test || null;
            /* The result file says what became of the run. A document written before any test was
               asked for carries nothing at all, which is not the same as a test that failed. */
            const status = test === null ? 'none' : (test.status || 'done');
            const content = [];
            if (status === 'none') {
                content.push($('<div class="panel-body lh-muted"/>')
                    .text("{{ lang._('This port has not been load tested.') }}"));
            } else if (status === 'running') {
                content.push($('<div class="panel-body"/>')
                    .append($('<i class="fa fa-spinner fa-pulse"></i> '))
                    .append($('<span/>').text("{{ lang._('A test is running on this port.') }}")));
            } else if (status === 'error' || status === 'failed') {
                content.push($('<div class="panel-body lh-muted"/>')
                    .text(test.message || test.detail || "{{ lang._('The last test did not finish.') }}"));
            } else {
                content.push(detail_table(test_rows(test)));
            }
            content.push($('<div class="panel-body"/>').append(
                $('<button type="button" class="btn btn-default lh-test"/>')
                    .attr('data-if', port['if'])
                    .append($('<i class="fa fa-fw fa-bolt"></i> '))
                    .append($('<span/>').text("{{ lang._('Test this port') }}"))));
            return detail_panel("{{ lang._('Load test') }}", content,
                "{{ lang._('Packet loss on its own says nothing about a cable: a switch answering a flood out of its own processor drops packets while every frame that does arrive is intact. The error count is the number that matters here.') }}");
        }

        function render_detail(iface) {
            const $body = $('#detail-body').empty();
            $('#detail-pick').val(iface === null ? '' : iface);
            const port = find_port(iface);
            if (port === null) {
                $('#detail-heading').empty();
                $('#detail-empty').show();
                return;
            }
            $('#detail-empty').hide();
            const state = (port.verdict || {}).state || 'ok';
            $('#detail-heading').empty()
                .append($('<span class="lh-port lh-tech"/>').text(port_title(port)))
                .append(document.createTextNode(' '))
                .append($('<span class="label ' + (verdict_class[state] || 'label-default') + '"/>')
                    .text(verdict_text[state] || state))
                .append($('<span class="lh-sub lh-tech"/>').text([port['if'], port.name].filter(Boolean).join(' · ')));

            const panels = [identity_panel(port), verdict_panel(port), identify_panel(port),
                window_panel(port), ladder_panel(port), counters_panel(port), optics_panel(port),
                flaps_panel(port), last_test_panel(port)];
            const $row = $('<div class="row"/>');
            let placed = 0;
            $.each(panels, function (index, $panel) {
                if ($panel === null) {
                    return;
                }
                $row.append($('<div class="col-md-6"/>').append($panel));
                placed += 1;
                /* Bootstrap columns float, so a tall panel traps the next one against its side.
                   A break after every pair keeps the two columns honest whichever side they
                   float to, which is the other side entirely in the Arabic layout. */
                if (placed % 2 === 0) {
                    $row.append($('<div class="clearfix"/>'));
                }
            });
            $body.append($row);
            $body.find('[data-toggle="tooltip"]').tooltip({container: 'body'});
        }

        /* The list is rebuilt from every sweep rather than once at load, because a port can
           appear or leave between sweeps - a transceiver seated, a module pulled - and a picker
           offering a port that is no longer there would answer with an empty panel. */
        function fill_detail_picker(doc) {
            const $pick = $('#detail-pick');
            if ($pick.length === 0) {
                return;
            }
            const keep = current_port;
            $pick.find('option').slice(1).remove();
            $.each((doc || {}).ports || [], function (index, port) {
                $pick.append($('<option/>').attr('value', port['if'])
                    .text(port_title(port) + ' · ' + port['if']));
            });
            $pick.val(keep === null ? '' : keep);
            if ($pick.val() === null) {
                $pick.val('');
            }
        }

        function show_detail(iface) {
            current_port = iface;
            render_detail(iface);
            $('a[href="#detail"]').tab('show');
        }

        /* ------------------------------------------------------------------ the front panel

           A drawing, never a photograph. What the front of an appliance looks like is the
           vendor's artwork and cannot be shipped under this licence; how many sockets it has, in
           what order, in which bay, and what is printed beside them are facts, and facts are not
           owned. So the panel is drawn from those facts alone, and it is never given more
           authority than it has earned: while nobody has stood in front of the machine and
           checked the arrangement, the page says so in as many words.

           The geometry does not mirror. An inline SVG keeps its coordinates under dir="rtl" - a
           rect at x=0 stays against the same edge - while text-anchor start and end do flip with
           the inherited direction, which would drag every printed label off the socket it names.
           So the drawing is pinned to direction: ltr, every label is centred, and only the blocks
           around it follow the page. */

        const FP_PAD = 14;      /* the metal around the sockets, in the units the layout file uses */
        const FP_GAP = 12;      /* between two sockets: enough that two printed labels never touch */
        const FP_LABEL = 17;    /* the line under a socket that carries its printed name */
        const FP_SCALE = 1.25;  /* drawing units to screen pixels, before the page shrinks it to fit */
        const SVG_NS = 'http://www.w3.org/2000/svg';

        /* The framework's own contextual colours, so that a theme which repaints the page
           repaints the drawing with it. Every shape is filled and stroked with `currentColor`,
           which is exactly what these classes set, so one class on the group colours the outline,
           the fill and the label in one go. Grey for the three states that are not a judgement,
           and for a socket the machine says nothing about. */
        const faceplate_class = {
            'fail': 'text-danger',
            'warn': 'text-warning',
            'watch': 'text-info',
            'ok': 'text-success',
            'idle': 'text-muted',
            'down': 'text-muted',
            'disabled': 'text-muted',
            'absent': 'text-muted'
        };

        /* The drawing knows one word the ports list does not: a socket the layout draws and the
           firewall reports no port for. */
        const faceplate_text = $.extend({}, verdict_text, {
            'absent': "{{ lang._('not reported') }}"
        });

        /* Worst first in the key as well, so it reads in the order the ports list is sorted in. */
        const faceplate_order = ['fail', 'warn', 'watch', 'ok', 'idle', 'down', 'disabled', 'absent'];

        /* The last panel document, read by a tooltip as it opens and by the correction dialog in
           the identify file included further down. That file is rendered into this same scope and
           reads this very variable by this name, so the name is part of the contract between the
           two and not a local choice: renaming it here alone takes the correction dialog with it,
           and it goes with a ReferenceError on the click rather than with a visible failure. */
        let panel_doc = null;
        let faceplate_drawn = null;     /* the layout the drawing now on the screen was built from */
        let faceplate_nodes = {};       /* socket key to its group, so a sweep only swaps a class */

        function svg_node(name, attributes) {
            const node = document.createElementNS(SVG_NS, name);
            $.each(attributes || {}, function (key, value) {
                node.setAttribute(key, String(value));
            });
            return node;
        }

        /* A state this page has no colour for is drawn as a socket nobody reported rather than
           written into a class name of its own: whatever arrives here ends up in a class
           attribute, and the vocabulary of a verdict is closed. */
        function faceplate_state(item) {
            const state = (item || {}).state;
            return faceplate_class[state] ? state : 'absent';
        }

        /* The silhouette of an 8P8C jack: the opening, with the channel the cable's latch rides
           in cut out of one edge. That outline is what lets a person tell a copper socket from a
           transceiver cage across the room, which is the whole reason for drawing shapes at all
           instead of a row of identical boxes. */
        function rj45_path(w, h) {
            const tab = Math.max(8, Math.round(w * 0.34));
            const depth = Math.max(5, Math.round(h * 0.28));
            const side = (w - tab) / 2;
            return 'M0,0 h' + w + ' v' + (h - depth) + ' h-' + side + ' v' + depth +
                ' h-' + tab + ' v-' + depth + ' h-' + side + ' Z';
        }

        /* The eight contacts along the top of the opening. They carry no information - they are
           there so the shape is recognised rather than deciphered. */
        function rj45_contacts(w, h) {
            const group = svg_node('g', {'class': 'lh-fp-detail'});
            const span = w * 0.62;
            const start = (w - span) / 2;
            for (let i = 0; i < 8; i++) {
                /* rounded, because a coordinate is written into the document as text and a
                   sixteenth decimal place of a contact nobody can see is only noise in it */
                const x = Math.round((start + (span * i) / 7) * 100) / 100;
                group.appendChild(svg_node('line', {
                    x1: x, y1: Math.round(h * 16) / 100, x2: x, y2: Math.round(h * 44) / 100
                }));
            }
            return group;
        }

        /* A cage and the opening inside it. What is seated in the cage is deliberately not drawn:
           a module comes and goes, and this drawing is about the metal that stays. */
        function socket_shape(shape, w, h) {
            if (shape === 'rj45') {
                return [svg_node('path', {'class': 'lh-fp-body', d: rj45_path(w, h)}),
                    rj45_contacts(w, h)];
            }
            if (shape === 'cage') {
                const inset = 4;
                const detail = svg_node('g', {'class': 'lh-fp-detail'});
                detail.appendChild(svg_node('rect', {
                    x: inset, y: inset,
                    width: Math.max(2, w - inset * 2), height: Math.max(2, h - inset * 2), rx: 1
                }));
                return [svg_node('rect', {'class': 'lh-fp-body', x: 0, y: 0, width: w, height: h, rx: 2}),
                    detail];
            }
            return [svg_node('rect', {'class': 'lh-fp-body', x: 0, y: 0, width: w, height: h, rx: 3})];
        }

        /* One bay, drawn as its own block of metal. Sockets are set on a single line and bottom
           aligned, so that a row of mixed heights still has all of its printed labels on one
           line, the way they are printed on the appliance. */
        function draw_bay(row, row_index, types) {
            const items = row.items || [];
            const placed = [];
            let width = FP_PAD;
            let tallest = 0;
            $.each(items, function (index, item) {
                const hint = types[item.type] || types.unknown || {};
                const w = Number(hint.width) > 0 ? Number(hint.width) : 34;
                const h = Number(hint.height) > 0 ? Number(hint.height) : 30;
                placed.push({item: item, index: index, x: width, w: w, h: h, shape: hint.shape || 'plain'});
                width += w + FP_GAP;
                tallest = Math.max(tallest, h);
            });
            width = items.length > 0 ? width - FP_GAP + FP_PAD : FP_PAD * 2;
            const height = FP_PAD * 2 + tallest + FP_LABEL;
            const svg = svg_node('svg', {
                'class': 'lh-fp-svg',
                viewBox: '0 0 ' + width + ' ' + height,
                width: Math.round(width * FP_SCALE),
                height: Math.round(height * FP_SCALE),
                preserveAspectRatio: 'xMidYMid meet',
                role: 'group',
                'aria-label': row.bay || ''
            });
            svg.appendChild(svg_node('rect', {
                'class': 'lh-fp-panel',
                x: 0.5, y: 0.5, width: width - 1, height: height - 1, rx: 4
            }));
            $.each(placed, function (order, spot) {
                const key = 'r' + row_index + 'i' + spot.index;
                const group = svg_node('g', {
                    'class': 'lh-fp-socket',
                    'data-key': key,
                    transform: 'translate(' + spot.x + ',' + (FP_PAD + tallest - spot.h) + ')'
                });
                /* The area that answers the mouse. A socket drawn as an outline catches a pointer
                   on the line and nowhere else, and the label under it would catch nothing at
                   all, so the whole cell is given something transparent to be hovered. */
                group.appendChild(svg_node('rect', {
                    'class': 'lh-fp-hit',
                    x: -3, y: -3, width: spot.w + 6, height: spot.h + FP_LABEL + 3
                }));
                $.each(socket_shape(spot.shape, spot.w, spot.h), function (index, node) {
                    group.appendChild(node);
                });
                const label = svg_node('text', {
                    'class': 'lh-fp-label',
                    x: spot.w / 2,
                    y: spot.h + FP_LABEL - 5,
                    /* middle, always. start and end are the two that turn over with the page. */
                    'text-anchor': 'middle'
                });
                label.textContent = spot.item.label || '';
                group.appendChild(label);
                /* The light above the socket, and the only thing on this page that can prove the
                   drawing right: a layout is a claim until somebody has watched one jack blink.
                   It is drawn quietly on purpose - an appliance has link lights of its own, and a
                   bright dot here would be read as one of them. The identify file further down
                   listens for the class; everything it needs is in these two attributes. */
                const led = svg_node('circle', {
                    'class': 'lh-fp-led lh-fp-led-dark',
                    cx: spot.w / 2, cy: -6, r: 3.5
                });
                group.appendChild(led);
                svg.appendChild(group);
                faceplate_nodes[key] = {group: group, led: led};
            });
            return svg;
        }

        /* What the drawing is made of, as one string. While it does not change there is nothing
           to rebuild: a sweep only changes what each socket is doing, and that is a class. */
        function faceplate_signature(doc) {
            if (!doc || !doc.available) {
                return 'none';
            }
            return [doc.display, doc.orientation, doc.confirmed ? '1' : '0', doc.confirmed_note,
                $.map(doc.rows || [], function (row) {
                    return row.bay + '[' + $.map(row.items || [], function (item) {
                        return item.label + ':' + item.type;
                    }).join(',') + ']';
                }).join('|')].join('␟');
        }

        function build_faceplate(doc) {
            const $drawing = $('#faceplate-drawing');
            /* a tooltip left open over a socket that is about to be replaced would be left
               hanging on the page with nothing under it to point at */
            $drawing.find('.lh-fp-socket').tooltip('destroy');
            $drawing.empty();
            faceplate_nodes = {};
            const types = doc.port_types || {};
            $.each(doc.rows || [], function (row_index, row) {
                const $body = $('<div class="lh-fp-bay-body"/>').append(draw_bay(row, row_index, types));
                /* A bay note is the sentence that stops a surprise being read as a fault - on
                   this appliance it is what explains why the module bay enumerates first. */
                if (row.note) {
                    $body.append($('<div class="lh-note lh-fp-bay-note"/>').text(lh_data(row.note)));
                }
                $drawing.append($('<div class="lh-fp-bay"/>')
                    .append($('<div class="lh-fp-bay-name"/>').text(lh_data(row.bay)))
                    .append($body));
            });
        }

        function paint_faceplate(doc) {
            $.each(doc.rows || [], function (row_index, row) {
                $.each(row.items || [], function (index, item) {
                    const drawn = faceplate_nodes['r' + row_index + 'i' + index];
                    if (drawn === undefined) {
                        return;
                    }
                    const group = drawn.group;
                    const state = faceplate_state(item);
                    const iface = item['if'] || '';
                    /* Built once, repainted ever after: a sweep that changed a verdict costs one
                       attribute per socket, and nothing on the screen moves while it is read. */
                    group.setAttribute('class', 'lh-fp-socket lh-fp-' + state + ' ' +
                        faceplate_class[state] + (iface === '' ? '' : ' lh-fp-open'));
                    group.setAttribute('data-if', iface);
                    group.setAttribute('aria-label', [item.label, faceplate_text[state] || state, iface]
                        .filter(Boolean).join(' — '));
                    if (iface === '') {
                        group.removeAttribute('tabindex');
                        group.removeAttribute('role');
                    } else {
                        group.setAttribute('tabindex', '0');
                        group.setAttribute('role', 'button');
                    }
                    /* The light is offered only where the driver registered one. A button that
                       answers with "this port has no LED" every time is worse than no button. */
                    if (iface !== '' && item.can_identify === true) {
                        drawn.led.setAttribute('class', 'lh-fp-led lh-identify');
                        drawn.led.setAttribute('data-if', iface);
                        drawn.led.setAttribute('data-label', item.label || iface);
                        drawn.led.setAttribute('role', 'button');
                        drawn.led.setAttribute('tabindex', '0');
                        drawn.led.setAttribute('aria-label',
                            "{{ lang._('Blink the light on {port}') }}".replace('{port}', item.label || iface));
                    } else {
                        drawn.led.setAttribute('class', 'lh-fp-led lh-fp-led-dark');
                        $.each(['data-if', 'data-label', 'role', 'tabindex', 'aria-label'], function (i, name) {
                            drawn.led.removeAttribute(name);
                        });
                    }
                });
            });
        }

        function faceplate_item(key) {
            const match = /^r(\d+)i(\d+)$/.exec(String(key || ''));
            if (match === null || panel_doc === null) {
                return null;
            }
            const row = (panel_doc.rows || [])[Number(match[1])];
            return (row ? (row.items || [])[Number(match[2])] : null) || null;
        }

        /* The same rule the grid follows: a port that carried almost nothing has no rate worth
           printing, and a dark one has none at all. A green zero here would claim a clean cable
           that nothing has put under load. */
        function socket_rate(item) {
            const state = faceplate_state(item);
            /* A whole sentence rather than a number, because the answer for a quiet port is not
               a rate at all and "error rate not enough traffic" is not a sentence. */
            if (state === 'idle') {
                return "{{ lang._('not enough traffic to judge') }}";
            }
            if (state === 'down' || state === 'disabled' || state === 'absent') {
                return '';
            }
            return "{{ lang._('error rate {rate}') }}".replace('{rate}', format_rate(item.error_ppm));
        }

        function socket_tooltip(key) {
            const item = faceplate_item(key);
            if (item === null) {
                return '';
            }
            const state = faceplate_state(item);
            const lines = ['<div class="lh-fp-tip-name lh-tech">' + esc(item.label || '') + '</div>',
                '<div>' + esc(faceplate_text[state] || state) + '</div>'];
            if (state === 'absent') {
                /* What the collector actually found is that nothing answered to this printed name
                   in this bay - which is not the same as the machine having no port there. A
                   socket whose name has just been corrected by hand leaves the name it used to
                   carry with no port behind it, and saying "the firewall reports no port" would
                   be a claim about the hardware that the correction has just made false. */
                lines.push('<div class="lh-fp-tip-note">' +
                    "{{ lang._('The drawing has a socket here, and no port on this firewall answers to that name.') }}" +
                    '</div>');
                return lines.join('');
            }
            const identity = [item['if'], item.name].filter(Boolean).join(' · ');
            if (identity !== '') {
                lines.push('<div>' + tech(identity) + '</div>');
            }
            const speed = speed_text(item.speed_mbps);
            if (speed !== '') {
                lines.push('<div>' + tech(speed) + '</div>');
            }
            const neighbours = item.serves || [];
            if (neighbours.length === 0) {
                lines.push('<div class="lh-fp-tip-note">' + "{{ lang._('nothing seen yet') }}" + '</div>');
            } else {
                let served = $.map(neighbours.slice(0, 3), function (address) {
                    return tech(address);
                }).join('<br/>');
                if (neighbours.length > 3) {
                    served += '<div class="lh-fp-tip-note">' +
                        esc("{{ lang._('and {count} more') }}".replace('{count}', neighbours.length - 3)) +
                        '</div>';
                }
                lines.push('<div>' + served + '</div>');
            }
            const rate = socket_rate(item);
            if (rate !== '') {
                lines.push('<div>' + esc(rate) + '</div>');
            }
            if (item['if']) {
                lines.push('<div class="lh-fp-tip-note">' +
                    "{{ lang._('click for everything this port has to say') }}" + '</div>');
            }
            if (item['if'] && item.can_identify === true) {
                lines.push('<div class="lh-fp-tip-note">' +
                    "{{ lang._('the light above the socket makes this one blink on the appliance') }}" +
                    '</div>');
            }
            return lines.join('');
        }

        function render_legend(doc) {
            const seen = {};
            $.each(doc.rows || [], function (row_index, row) {
                $.each(row.items || [], function (index, item) {
                    seen[faceplate_state(item)] = true;
                });
            });
            const $legend = $('#faceplate-legend');
            $.each(faceplate_order, function (index, state) {
                if (seen[state] !== true) {
                    return;     /* a key for a colour that is nowhere on the drawing explains nothing */
                }
                $legend.append($('<span class="lh-fp-key"/>')
                    .append($('<i class="fa fa-fw ' + (state === 'absent' ? 'fa-square-o' : 'fa-square') +
                        ' ' + faceplate_class[state] + '"></i> '))
                    .append($('<span/>').text(faceplate_text[state] || state)));
            });
        }

        /* No drawing for this model. An empty box would say the page is broken, so the document
           falls back to what it does know - the ports, in the order the chassis table puts them -
           and says in one sentence what it would take to draw this appliance. */
        function render_no_layout(doc) {
            const $drawing = $('#faceplate-drawing');
            $drawing.find('.lh-fp-socket').tooltip('destroy');
            $drawing.empty();
            faceplate_nodes = {};
            $drawing.append($('<div class="alert alert-info lh-fp-alert" role="alert"/>')
                .append($('<i class="fa fa-fw fa-info-circle"></i> '))
                .append($('<span/>').text("{{ lang._('There is no front panel drawing for this appliance yet, so its ports are listed instead.') }}"))
                .append($('<div class="lh-note"/>').text("{{ lang._('A layout is one short block in faceplates.json: the bays, the label printed beside each socket, and what kind of socket it is. Nothing is traced from a vendor picture and no photograph is shipped, so contributing one means counting the sockets in front of you and writing down what is printed there.') }}")));
            const ports = doc.ports || [];
            if (ports.length === 0) {
                return;
            }
            const $table = $('<table class="table table-condensed table-hover lh-fp-list"/>');
            $table.append($('<thead/>').append($('<tr/>')
                .append($('<th/>').text("{{ lang._('Port') }}"))
                .append($('<th/>').text("{{ lang._('Interface') }}"))
                .append($('<th/>').text("{{ lang._('Verdict') }}"))));
            const $body = $('<tbody/>');
            $.each(ports, function (index, port) {
                const state = faceplate_state(port);
                const $row = $('<tr/>');
                if (port['if']) {
                    $row.addClass('lh-fp-open').attr('data-if', port['if']);
                }
                $row.append($('<td/>').append($('<span class="lh-port lh-tech"/>').text(port.label || '')));
                $row.append($('<td/>').append($('<span class="lh-tech"/>').text(port['if'] || '')));
                $row.append($('<td/>').append($('<span class="label ' + (verdict_class[state] || 'label-default') + '"/>')
                    .text(faceplate_text[state] || state)));
                $body.append($row);
            });
            $drawing.append($table.append($body));
        }

        function render_faceplate(doc) {
            const chassis = doc.chassis || {};
            const parts = [];
            const machine = doc.display || [chassis.vendor, chassis.model].filter(Boolean).join(' ');
            if (machine) {
                parts.push(machine);
            }
            if (doc.generated) {
                parts.push("{{ lang._('last sweep {time}') }}".replace('{time}', format_time(doc.generated)));
            }
            fill_summary($('#faceplate-summary'), parts);
            const $warning = $('#faceplate-warning').empty();
            const $caption = $('#faceplate-caption').empty();
            $('#faceplate-legend').empty();

            if (!doc.available) {
                /* The fallback is a list and costs nothing to write again. Forgetting the
                   signature is what makes a model that gains a layout later draw itself on the
                   very next sweep instead of after a reload. */
                faceplate_drawn = null;
                render_no_layout(doc);
                return;
            }

            /* Until somebody has stood in front of the machine, the page says so. This is a
               reasoned arrangement, not a checked one, and a guess that looks like a diagram is
               read as a fact by everyone who sees it. */
            if (!doc.confirmed) {
                const $alert = $('<div class="alert alert-warning lh-fp-alert" role="alert"/>')
                    .append($('<i class="fa fa-fw fa-exclamation-triangle"></i> '))
                    .append($('<strong/>').text("{{ lang._('This arrangement has not been checked against the metal yet.') }}"));
                if (doc.confirmed_note) {
                    $alert.append($('<div class="lh-note"/>').text(lh_data(doc.confirmed_note)));
                }
                $warning.append($alert);
            }

            const signature = faceplate_signature(doc);
            if (signature !== faceplate_drawn) {
                build_faceplate(doc);
                faceplate_drawn = signature;
            }
            paint_faceplate(doc);

            $caption.append($('<div class="lh-fp-caption-line"/>').text(
                (doc.orientation === 'rear' || doc.orientation === 'back') ?
                    "{{ lang._('as you face the back of the appliance') }}" :
                    "{{ lang._('as you face the front of the appliance') }}"));
            /* Where each part of this drawing comes from, said plainly. The sockets, their order
               and the names beside them are the layout this plugin ships for this model - nothing
               in the firmware carries a silk-screen name, so the appliance cannot have told us.
               What this machine contributes is the join: which interface answers to each name, and
               what that port is doing. Saying the drawing came off the metal would be the same
               overclaim the warning above exists to prevent. */
            $caption.append($('<div class="lh-note"/>').text(
                "{{ lang._('Drawn, never photographed: the shapes are generic and nothing here is traced from a vendor picture. The sockets, their order, their bays and the names printed beside them are the layout this plugin ships for this model, not something the firewall can read off its own metal. What this machine adds is which interface sits behind each name, and what that port is doing.') }}"));
            render_legend(doc);
        }

        function reload_faceplate(after) {
            ajaxGet('/api/linkhealth/service/faceplate', {}, function (data, request_status) {
                /* Every real answer carries `available`, true or false. Anything without it is
                   the API saying it could not ask, which is not an answer about the panel - so
                   the drawing already on the screen is left exactly where it is. */
                if (request_status !== 'success' || !data || data.available === undefined) {
                    const detail = data ? (data.detail || data.message) : null;
                    $('#faceplate-summary').text("{{ lang._('The front panel could not be read from the firewall.') }}" +
                        (detail ? ' ' + detail : ''));
                } else {
                    panel_doc = data;
                    render_faceplate(data);
                }
                if (typeof after === 'function') {
                    after(panel_doc);
                }
            });
        }

        function open_socket(element) {
            const iface = element.getAttribute('data-if');
            if (!iface) {
                return;
            }
            /* the tooltip belongs to a socket that is about to be behind another tab */
            $(element).tooltip('hide');
            if (find_port(iface) === null) {
                /* the panel can be on the screen before the measurements have arrived */
                reload_status(function () {
                    show_detail(iface);
                });
                return;
            }
            show_detail(iface);
        }

        /* One delegated tooltip for the whole drawing. The sockets are thrown away whenever a
           layout changes and a handler on the container outlives them; and because the text is
           built at the moment the tooltip opens, it says what the last sweep measured rather than
           what was true when the socket was drawn. */
        $('#faceplate-drawing').tooltip({
            selector: '.lh-fp-socket',
            container: 'body',
            html: true,
            placement: 'top',
            title: function () {
                return socket_tooltip(this.getAttribute('data-key'));
            }
        });

        $('#faceplate-drawing').on('click', '.lh-fp-open', function (event) {
            /* The light sits inside the socket it belongs to, and asking for a blink is not
               asking to read the counters. Its own listener is on the document, so the click is
               left to carry on up rather than being stopped here. */
            if ($(event.target).closest('.lh-identify').length > 0) {
                return;
            }
            open_socket(this);
        });

        /* A socket and its light are both announced as buttons, so they answer the two keys a
           button answers. The light is the deeper of the two, so stopping there is what keeps
           one key press from also opening the port behind it. */
        $('#faceplate-drawing').on('keydown', '.lh-identify', function (event) {
            if (event.which === 13 || event.which === 32) {
                event.preventDefault();
                event.stopPropagation();
                $(this).trigger('click');
            }
        });

        $('#faceplate-drawing').on('keydown', '.lh-fp-open', function (event) {
            if (event.which === 13 || event.which === 32) {
                event.preventDefault();
                open_socket(this);
            }
        });

        $('#faceplateRefreshAct').click(function () {
            const $icon = $(this).find('i');
            $icon.addClass('fa-spin');
            reload_faceplate(function () {
                $icon.removeClass('fa-spin');
            });
        });

        /* ------------------------------------------------------------------ the load test */

        function test_title(port) {
            return "{{ lang._('Test {port}') }}".replace('{port}', port_title(port));
        }

        function ask_for_test(port) {
            const neighbours = port.serves || [];
            if (neighbours.length === 0) {
                /* The collector only accepts a target it has already seen behind this port in the
                   bridge table, so with an empty list there is nothing it would take. Say that
                   rather than offering a box whose every answer is refused. */
                show_message(BootstrapDialog.TYPE_WARNING, test_title(port),
                    "{{ lang._('No neighbour has been seen on this port, so there is nothing to send test traffic to. Connect the cable, let one sweep pass, and try again.') }}");
                return;
            }
            const $body = $('<div/>');
            $body.append($('<p/>').text(
                "{{ lang._('The test floods the neighbour with large packets and fills the link for as long as it runs. Anything else sharing this port will be slow meanwhile.') }}"));
            const $select = $('<select class="form-control"/>');
            $.each(neighbours, function (index, address) {
                $select.append($('<option/>').attr('value', address).text(address));
            });
            /* A plain select and nothing else. The framework's own control opens where the browser
               puts it; anything that positions its own menu has to be told which way is forward. */
            $body.append($('<label class="lh-label"/>').text("{{ lang._('Send the test traffic to') }}"));
            $body.append($select);
            BootstrapDialog.show({
                type: BootstrapDialog.TYPE_WARNING,
                title: test_title(port),
                message: $body,
                buttons: [
                    {
                        label: "{{ lang._('Cancel') }}",
                        action: function (dialog) {
                            dialog.close();
                        }
                    },
                    {
                        label: "{{ lang._('Saturate the link and test') }}",
                        cssClass: 'btn-primary',
                        action: function (dialog) {
                            const target = $select.val();
                            dialog.close();
                            start_test(port, target);
                        }
                    }
                ]
            });
        }

        function busy_message(other) {
            if (other) {
                return "{{ lang._('A test is already running on {port}. Only one port can be flooded at a time; wait for it to finish.') }}"
                    .replace('{port}', other);
            }
            return "{{ lang._('A test is already running. Only one port can be flooded at a time; wait for it to finish.') }}";
        }

        function start_test(port, target) {
            const started_at = Math.floor(Date.now() / 1000);
            const $waiting = $('<div/>')
                .append($('<i class="fa fa-spinner fa-pulse"></i> '))
                .append($('<span/>').text("{{ lang._('The test is running and the link is saturated until it finishes.') }}"))
                .append($('<p class="lh-note"/>').text("{{ lang._('Closing this window does not stop the test. Its result appears with the sweep that follows it, at most a minute after the flood itself is over.') }}"));
            const dialog = BootstrapDialog.show({
                title: test_title(port),
                message: $waiting,
                buttons: [close_button()]
            });
            test_running = true;
            ajaxCall('/api/linkhealth/service/run_test/' + encodeURIComponent(port['if']),
                {'target': target}, function (data) {
                    const state = data ? data.status : null;
                    if (state === 'started' || state === 'running') {
                        poll_test(port, dialog, started_at, Date.now() + TEST_WAIT_MS);
                        return;
                    }
                    test_running = false;
                    dialog.close();
                    if (state === 'busy') {
                        show_message(BootstrapDialog.TYPE_WARNING, test_title(port),
                            busy_message(data.interface));
                        return;
                    }
                    show_message(BootstrapDialog.TYPE_WARNING, test_title(port),
                        "{{ lang._('The test could not be started.') }}" + failure_text(data));
                });
        }

        /* There is one place a measurement lives, and a load test writes into it like every other
           measurement: the port's own entry in the status document. So waiting for the answer is
           simply reading that document again until this run shows up in it, which has the pleasant
           side effect of keeping the grid behind the dialog current while the link is loaded. */
        function poll_test(port, dialog, started_at, deadline) {
            window.setTimeout(function () {
                if (!dialog.isOpened()) {
                    /* the owner closed the window; the worker carries on without us watching */
                    test_running = false;
                    reload_status();
                    return;
                }
                reload_status(function () {
                    if (!dialog.isOpened()) {
                        test_running = false;
                        return;
                    }
                    const fresh = find_port(port['if']);
                    const test = fresh === null ? null : (fresh.last_test || null);
                    /* A result stamped before the button was pressed belongs to the run before
                       this one, whatever it says, so it is not an answer to this question. */
                    const landed = test !== null && test_time(test) >= started_at - 2;
                    const state = landed ? (test.status || 'done') : null;
                    if (state === 'error' || state === 'failed') {
                        test_running = false;
                        dialog.close();
                        show_message(BootstrapDialog.TYPE_WARNING, test_title(port),
                            "{{ lang._('The test did not finish.') }}" + failure_text(test));
                        return;
                    }
                    if (landed && state !== 'running') {
                        test_running = false;
                        dialog.close();
                        show_test_result(port, started_at);
                        return;
                    }
                    /* Still running - and so is a document the collector has not rewritten yet,
                       and a request that came back with nothing. One hiccup in one request is not
                       a finished test, so keep waiting until the deadline says otherwise. */
                    if (Date.now() > deadline) {
                        test_running = false;
                        dialog.close();
                        show_message(BootstrapDialog.TYPE_WARNING, test_title(port),
                            "{{ lang._('The test is taking longer than expected. Its result will appear under the port detail as soon as the collector has written a sweep that contains it.') }}");
                        return;
                    }
                    poll_test(port, dialog, started_at, deadline);
                });
            }, TEST_POLL_MS);
        }

        function show_test_result(port, started_at) {
            const fresh = find_port(port['if']);
            const test = fresh === null ? null : (fresh.last_test || null);
            /* A result from before we pressed the button is the previous run, not this one. */
            if (test === null || test_time(test) < started_at - 2) {
                show_message(BootstrapDialog.TYPE_WARNING, test_title(port),
                    "{{ lang._('The test finished without leaving a result. The collector log will say why.') }}");
                return;
            }
            if (test.status === 'error' || test.status === 'failed') {
                show_message(BootstrapDialog.TYPE_WARNING, test_title(port),
                    "{{ lang._('The test did not finish.') }}" + failure_text(test));
                return;
            }
            const clean = Number(test.errors || 0) === 0;
            const $body = $('<div/>');
            $body.append($('<p/>').text(clean ?
                "{{ lang._('The link carried the whole flood without corrupting a frame.') }}" :
                "{{ lang._('The link corrupted frames while it was loaded.') }}"));
            $body.append(detail_table(test_rows(test)));
            show_message(clean ? BootstrapDialog.TYPE_SUCCESS : BootstrapDialog.TYPE_WARNING,
                test_title(port), $body);
        }

        /* the button on the detail tab is rebuilt on every draw, so listen on the container */
        $('#detail-pick').on('change', function () {
            const iface = $(this).val();
            current_port = iface === '' ? null : iface;
            if (current_port === null) {
                $('#detail-heading').empty();
                $('#detail-body').empty();
                $('#detail-empty').show();
            } else {
                render_detail(current_port);
            }
        });

        $('#detail-body').on('click', '.lh-test', function () {
            const port = find_port($(this).data('if'));
            if (port !== null) {
                ask_for_test(port);
            }
        });

        {# Blinking a socket and correcting what that proves is one story, and it is long enough to
         # read on its own. It is kept in a file of its own beside this one and included here, so
         # that it shares these helpers instead of restating them; the grid id goes with it because
         # a correction is saved through the very same override grid the settings tab edits. #}
        {{ partial("OPNsense/LinkHealth/identify", ['port_grid_id': formGridPort['table_id']]) }}

        /* ------------------------------------------------------------------ settings */

        const data_get_map = {'frm_general': '/api/linkhealth/settings/get'};
        mapDataToFormUI(data_get_map).done(function () {
            formatTokenizersUI();
            $('.selectpicker').selectpicker('refresh');
        });

        $('#saveAct').click(function () {
            saveFormToEndpoint('/api/linkhealth/settings/set', 'frm_general', function () {
                $('#saveAct_done').show().delay(2000).fadeOut();
            });
        });

        /* per-port overrides: a hand-given label, and whatever else the model lets a port carry */
        $("#{{ formGridPort['table_id'] }}").UIBootgrid({
            search: '/api/linkhealth/settings/search_port',
            get: '/api/linkhealth/settings/get_port/',
            set: '/api/linkhealth/settings/set_port/',
            add: '/api/linkhealth/settings/add_port/',
            del: '/api/linkhealth/settings/del_port/',
            toggle: '/api/linkhealth/settings/toggle_port/'
        });

        /* ------------------------------------------------------------------ page plumbing */

        $('#refreshAct').click(function () {
            const $icon = $(this).find('i');
            $icon.addClass('fa-spin');
            reload_status(function () {
                $icon.removeClass('fa-spin');
            });
        });

        window.setInterval(function () {
            if (test_running) {
                return;     /* the test's own poll will reload once it lands */
            }
            if ($('#faceplate').hasClass('active')) {
                /* the panel carries its own copy of every verdict, so it is its own document */
                reload_faceplate();
                return;
            }
            if (!$('#ports').hasClass('active') && !$('#detail').hasClass('active')) {
                return;     /* nobody is looking at a measurement right now */
            }
            reload_status();
        }, REFRESH_MS);

        reload_status();

        /* keep the selected tab in the url */
        const selected_tab = window.location.hash !== '' ? window.location.hash : '#ports';
        $('a[href="' + selected_tab + '"]').tab('show');
        /* The panel is fetched when its tab is opened, and the handler for that is bound below,
           after the line above has already opened one. So a page opened straight at the panel -
           a bookmark, or a reload - has to be given its first fetch here. */
        if (selected_tab === '#faceplate') {
            reload_faceplate();
        }
        $('.nav-tabs a').on('shown.bs.tab', function (e) {
            history.pushState(null, null, e.target.hash);
        });

        /* An override saved on the settings tab changes a label, and the collector picks it up on
           its next sweep, so coming back to the ports tab is a good moment to look again. Bound
           after the tab above has been restored, so opening the page fetches once and not twice. */
        $('a[href="#ports"]').on('shown.bs.tab', function () {
            reload_status();
        });

        /* The panel costs a second configd call, so it is only ever fetched for somebody who is
           actually looking at it. */
        $('a[href="#faceplate"]').on('shown.bs.tab', function () {
            reload_faceplate();
        });
    });
</script>

<style>
    /* Nothing here is placed with a `left` or a `right`. The mirrored stylesheets are built from
       the files under www/css, so an offset written in a page template survives into the Arabic
       layout untouched and lands against the wrong edge. Logical properties turn with the page. */

    /* Interface names, addresses, counts and readings are written left to right even when the
       sentence around them is not. Letting each one carry its own direction is what keeps a minus
       sign, a dotted address or a slash from being reordered by the text beside it. */
    .lh-tech {
        unicode-bidi: plaintext;
    }

    /* The same treatment for whole sentences, not only for values. Until these
       strings are translated they are English inside a right-to-left page, and
       the bidirectional algorithm moves a trailing full stop - or the plus in
       "10G SFP+" - to the opposite end of the line. plaintext makes each
       element take its direction from its own first strong character, so an
       English sentence reads left to right and an Arabic one right to left,
       with no marker characters buried in the markup. */
    /* every element this plugin draws, rather than a list of class names that
       has to be kept in step with the markup - the one that was missed here
       first time was the bay label, and the symptom was "10G SFP+" rendering
       as "+10G SFP" */
    [class^="lh-"],
    [class*=" lh-"],
    .tab-pane .alert,
    .tab-pane .help-block,
    .tab-pane small,
    .tab-pane td,
    .tab-pane th {
        unicode-bidi: plaintext;
        text-align: start;
    }

    /* the chassis label is the thing the owner looks for first */
    .lh-port {
        font-size: 115%;
        font-weight: bold;
    }

    .lh-verdict {
        font-size: 100%;
    }

    /* the quieter second line under a value */
    .lh-sub {
        display: block;
        white-space: normal;
        font-size: 85%;
        opacity: 0.75;
    }

    .lh-muted {
        opacity: 0.6;
    }

    .lh-strong {
        font-weight: bold;
    }

    .lh-note {
        font-size: 85%;
        opacity: 0.75;
    }

    .lh-label {
        display: block;
        margin-top: 10px;
        margin-bottom: 4px;
    }

    /* The seconds left of a blink, meant to be read from a step away: the screen is over there
       and the appliance is over here, and the whole point of the number is that it can be
       glanced at on the way back. */
    .lh-countdown {
        font-size: 130%;
        margin-top: 10px;
        margin-bottom: 0;
    }

    .lh-detail > tbody > tr > th {
        width: 45%;
        font-weight: normal;
        opacity: 0.8;
    }

    /* The ladder is a list, not a table: one rung a line, the negotiated one marked with an icon
       that carries no direction of its own. An arrow would point the wrong way in Arabic. */
    .lh-ladder,
    .lh-reasons,
    .lh-flaps {
        list-style: none;
        margin: 0;
        padding: 0;
    }

    .lh-ladder > li,
    .lh-reasons > li,
    .lh-flaps > li {
        padding-top: 2px;
        padding-bottom: 2px;
    }

    .lh-ladder > li.lh-rung {
        font-weight: bold;
    }

    .lh-alert {
        padding: 8px 12px;
    }

    .lh-detail-heading {
        padding: 10px 15px;
    }

    .lh-panels {
        padding: 0 10px 10px;
    }

    /* ---------------------------------------------------------------- the front panel drawing */

    /* The drawing is pinned to left to right whichever way the page reads. An inline SVG keeps
       its coordinates under dir="rtl" - a rect at x=0 stays against the same edge - while
       text-anchor start and end do turn over with the inherited direction, which would drag every
       printed label off the socket it names. Pinning the direction and centring every label is
       what keeps the two in step; the blocks around the drawing follow the page as they should. */
    .lh-fp-svg {
        direction: ltr;
        max-width: 100%;
        height: auto;
    }

    .lh-fp {
        padding: 0 10px;
    }

    /* The bay name sits beside its drawing, and "beside" is whichever side the page begins on: a
       flex row turns with the direction of the document, so no side is named here. Where the
       window is too narrow for both, the name wraps above the metal rather than squeezing it. */
    .lh-fp-bay {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        padding: 6px 0;
    }

    .lh-fp-bay-name {
        flex: 0 0 13em;
        padding-bottom: 4px;
        padding-inline-end: 12px;
    }

    .lh-fp-bay-body {
        flex: 1 1 18em;
        min-width: 0;
    }

    .lh-fp-bay-note {
        padding-top: 4px;
    }

    .lh-detail-toolbar {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 8px;
        padding: 10px 10px 0;
    }

    .lh-detail-toolbar select {
        display: inline-block;
        width: auto;
        max-width: 100%;
    }

    .lh-fp-alert {
        margin: 10px;
    }

    .lh-fp-caption,
    .lh-fp-legend {
        padding: 4px 10px 10px;
    }

    .lh-fp-caption-line {
        font-style: italic;
    }

    .lh-fp-key {
        display: inline-block;
        padding-inline-end: 14px;
        font-size: 85%;
    }

    .lh-fp-list {
        margin: 0 10px;
        width: auto;
    }

    /* The panel is the metal the sockets are set into. It is not a measurement, so it stays quiet. */
    .lh-fp-panel {
        fill: none;
        stroke: currentColor;
        stroke-opacity: 0.3;
    }

    /* Every shape takes its colour from the contextual class the state puts on the group above
       it. That is what lets one swapped class repaint a socket, and what makes the drawing follow
       a theme into the dark without this file knowing a single colour value. */
    .lh-fp-body {
        fill: currentColor;
        fill-opacity: 0.18;
        stroke: currentColor;
        stroke-width: 1.5;
    }

    .lh-fp-detail {
        fill: none;
        stroke: currentColor;
        stroke-opacity: 0.5;
        stroke-width: 1;
    }

    .lh-fp-label {
        fill: currentColor;
        font-size: 10px;
    }

    .lh-fp-hit {
        fill: transparent;
        stroke: none;
    }

    /* A socket the drawing knows and the firewall reports no port for: outlined, and empty,
       because there is nothing behind it to colour in. */
    .lh-fp-absent .lh-fp-body {
        fill: none;
        stroke-dasharray: 4 3;
    }

    .lh-fp-absent .lh-fp-detail {
        display: none;
    }

    .lh-fp-open {
        cursor: pointer;
    }

    .lh-fp-open:hover .lh-fp-body {
        fill-opacity: 0.4;
    }

    /* Reached by the keyboard as well as by the mouse, and the marker is the outline of the
       socket itself rather than a box around it, which would sit against the wrong edge. */
    .lh-fp-socket:focus {
        outline: none;
    }

    .lh-fp-socket:focus .lh-fp-body {
        stroke-width: 3;
    }

    /* The light above a socket. It is drawn quietly because an appliance carries link lights of
       its own and a bright dot here would be read as one of them; it comes up to full strength
       when the socket it belongs to is under the pointer or under the keyboard. */
    .lh-fp-led {
        fill: currentColor;
        fill-opacity: 0.25;
        stroke: currentColor;
        stroke-opacity: 0.5;
        cursor: pointer;
    }

    .lh-fp-socket:hover .lh-fp-led,
    .lh-fp-socket:focus .lh-fp-led,
    .lh-fp-led:focus {
        fill-opacity: 0.95;
        stroke-opacity: 1;
    }

    .lh-fp-led:focus {
        outline: none;
    }

    /* a port whose driver registers no LED node has no light to offer, and an unlit one that does
       nothing when it is pressed would be a worse answer than none */
    .lh-fp-led-dark {
        display: none;
    }

    .lh-fp-tip-name {
        font-weight: bold;
    }

    .lh-fp-tip-note {
        font-size: 85%;
        opacity: 0.8;
    }
</style>

<ul class="nav nav-tabs" data-tabs="tabs" id="maintabs">
    <li><a data-toggle="tab" href="#ports">{{ lang._('Ports') }}</a></li>
    <li><a data-toggle="tab" href="#faceplate">{{ lang._('Front panel') }}</a></li>
    <li><a data-toggle="tab" href="#detail">{{ lang._('Port detail') }}</a></li>
    <li><a data-toggle="tab" href="#settings">{{ lang._('Settings') }}</a></li>
</ul>

<div class="tab-content content-box" id="linkhealth">
    <div id="ports" class="tab-pane fade in">
        <div class="alert alert-info" role="alert" style="margin: 10px;">
            <i class="fa fa-fw fa-info-circle"></i>
            <span id="ports-summary">{{ lang._('Reading the last measurement...') }}</span>
            <button id="refreshAct" class="btn btn-xs btn-default" type="button">
                <i class="fa fa-fw fa-refresh"></i> {{ lang._('Refresh') }}
            </button>
        </div>
        <table id="grid-ports" class="table table-condensed table-hover table-striped">
            <thead>
                <tr>
                    <th data-column-id="label" data-formatter="port" data-width="9em">{{ lang._('Port') }}</th>
                    <th data-column-id="if" data-formatter="iface" data-width="10em">{{ lang._('Interface') }}</th>
                    <th data-column-id="serves_text" data-formatter="serves">{{ lang._('Serves') }}</th>
                    <th data-column-id="link_speed" data-formatter="link" data-sorter="number" data-width="10em">{{ lang._('Link') }}</th>
                    <th data-column-id="error_ppm" data-formatter="rate" data-sorter="number" data-width="10em">{{ lang._('Errors in this window') }}</th>
                    <th data-column-id="verdict_rank" data-formatter="verdict" data-sorter="number" data-width="14em">{{ lang._('Verdict') }}</th>
                    <th data-column-id="optics_text" data-formatter="optics">{{ lang._('Transceiver') }}</th>
                    <th data-column-id="commands" data-formatter="commands" data-sortable="false" data-width="100">{{ lang._('Commands') }}</th>
                </tr>
            </thead>
            <tbody></tbody>
            <tfoot></tfoot>
        </table>
    </div>

    <div id="faceplate" class="tab-pane fade in">
        <div class="alert alert-info" role="alert" style="margin: 10px;">
            <i class="fa fa-fw fa-info-circle"></i>
            <span id="faceplate-summary">{{ lang._('Reading the front panel layout...') }}</span>
            <button id="faceplateRefreshAct" class="btn btn-xs btn-default" type="button">
                <i class="fa fa-fw fa-refresh"></i> {{ lang._('Refresh') }}
            </button>
        </div>
        <div id="faceplate-warning"></div>
        <div id="faceplate-drawing" class="lh-fp"></div>
        <div id="faceplate-caption" class="lh-fp-caption"></div>
        <div id="faceplate-legend" class="lh-fp-legend"></div>
    </div>

    <div id="detail" class="tab-pane fade in">
        <!-- Until now this tab could only be reached through the magnifier in the ports table,
             which on a narrow screen sits in a column that has been pushed off the side. The tab
             now carries its own way of choosing a port, so it is never a page with nothing on it. -->
        <div class="lh-detail-toolbar">
            <label for="detail-pick">{{ lang._('Show port') }}</label>
            <select id="detail-pick" class="form-control">
                <option value="">{{ lang._('- choose a port -') }}</option>
            </select>
        </div>
        <div id="detail-empty" class="alert alert-info" role="alert" style="margin: 10px;">
            <i class="fa fa-fw fa-info-circle"></i>
            {{ lang._('Choose a port above, or press the magnifier beside any port on the Ports tab, to see every counter, the speeds it offers, its transceiver and its last load test.') }}
        </div>
        <div id="detail-heading" class="lh-detail-heading"></div>
        <div id="detail-body" class="lh-panels"></div>
    </div>

    <div id="settings" class="tab-pane fade in">
        {{ partial("layout_partials/base_form", ['fields': generalForm, 'id': 'frm_general']) }}
        <div class="col-md-12" style="padding: 10px 15px 20px;">
            <button class="btn btn-primary" id="saveAct" type="button"><b>{{ lang._('Save') }}</b></button>
            <span id="saveAct_done" class="text-success" style="display: none; margin: 0 10px;">
                <i class="fa fa-check"></i> {{ lang._('Saved') }}
            </span>
        </div>
        <div class="col-md-12">
            <div class="alert alert-info" role="alert" style="margin: 10px 0;">
                <i class="fa fa-fw fa-info-circle"></i>
                {{ lang._('Give a port its own name, or let it be judged by its own thresholds. An override reaches the ports list on the next sweep, at most a minute from now.') }}
            </div>
            {{ partial('layout_partials/base_bootgrid_table', formGridPort + {'command_width': '120'}) }}
        </div>
    </div>
</div>

{{ partial("layout_partials/base_dialog", ['fields': portForm, 'id': formGridPort['edit_dialog_id'], 'label': lang._('Edit port override')]) }}
