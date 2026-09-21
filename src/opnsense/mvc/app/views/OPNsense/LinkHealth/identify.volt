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
 #
 # ---------------------------------------------------------------------------
 #
 # Identify, and correct what it proves.
 #
 # Everything else on this page is a measurement. The port names are not: they come from a table
 # that says which unit number carries which printed label, and a table is a claim about a machine
 # nobody in this software has ever seen. Nothing in the firmware can settle it - SMBIOS type 41,
 # the one structure that could name a jack, lists none of the twenty on the reference appliance.
 #
 # So the claim is settled the only way it can be: one socket is made to blink, a person looks at
 # the metal, and the page asks them what they saw. An answer of "yes" is the only thing this
 # plugin will ever call a confirmation, and an answer of "no" is stored as a correction in the
 # per-port override that already exists - there is no second place where a name is kept.
 #
 # This file is included inside the page script, so it shares its helpers (find_port, show_message,
 # detail_panel, reload_status and the rest) rather than restating them.
 #}

        /* --------------------------------------------------- identify, and correct what it says */

        /* Long enough to walk to the front of a rack and read a label, short enough that a socket
           left blinking because somebody wandered off is not blinking for long. The worker clamps
           it as well; this is only what the page asks for. */
        const IDENTIFY_SECONDS = 30;

        /* The value of the one option in the correction list that is not a printed label. No
           faceplate can carry this as a name, which is the whole reason it reads like this. */
        const LABEL_FREEHAND = '__other__';

        function can_identify(port) {
            return port !== null && port !== undefined && has_capability(port, 'IDENTIFY_LED');
        }

        /* The activity light needs a link and somebody answering behind the port: a frame
           addressed to nobody never reaches the wire, and a socket nobody answers stays as
           dark as it was. Both facts are already in the measurement. */
        function can_flicker(port) {
            return port !== null && port !== undefined
                && ((port.link || {}).state === 'active')
                && ((port.serves || []).length > 0);
        }

        function identify_title(label) {
            return "{{ lang._('Identify {port}') }}".replace('{port}', label);
        }

        /* Where the name above a port came from, said plainly. This is the sentence that keeps the
           page honest: a name out of the built-in table has been read by nobody, and saying so is
           the difference between a label and a confirmed label. */
        function label_provenance(port, label) {
            if (port === null || port === undefined) {
                return "{{ lang._('This socket is drawn on the front panel, but the machine reports no port behind it.') }}";
            }
            const source = port.label_source || 'interface';
            if (source === 'override') {
                return "{{ lang._('{label} is the name this firewall was told to use for {if}. Somebody typed it here; it was not read off the appliance by this plugin.') }}"
                    .replace('{label}', label).replace('{if}', port['if']);
            }
            if (source === 'chassis') {
                return "{{ lang._('This plugin calls {if} by the name {label} because the built-in table for this appliance says so. A table is a claim, not a witness: nobody has looked at the metal on your behalf.') }}"
                    .replace('{label}', label).replace('{if}', port['if']);
            }
            return "{{ lang._('No printed name is known for {if}. This appliance is not in the built-in table, so what is shown is the name the kernel gives the port.') }}"
                .replace('{if}', port['if']);
        }

        /* Which other port answers to a name right now. After a correction two rows can carry the
           same printed name - ours because a person saw it, the other because the table still says
           so - and saying which one is the honest half of that. */
        function label_owner(label, except_if) {
            const found = $.grep((status_doc || {}).ports || [], function (port) {
                return port.label === label && port['if'] !== except_if;
            });
            return found.length > 0 ? found[0] : null;
        }

        /* ------------------------------------------------------------------ the blinking itself */

        /* Nothing is done with the answer. The worker hands the LED back by itself when its time
           is up, so a stop that arrives after that has nothing left to stop. */
        function stop_blink() {
            ajaxCall('/api/linkhealth/service/stop_identify', {}, function () {});
        }

        function busy_dialog(label) {
            BootstrapDialog.show({
                type: BootstrapDialog.TYPE_WARNING,
                title: identify_title(label),
                message: $('<div/>')
                    .append($('<p/>').text("{{ lang._('Another socket is already blinking. Only one is ever lit at a time, because two of them blinking would make the answer ambiguous - which is the one thing this button exists to avoid.') }}"))
                    .append($('<p class="lh-note"/>').text("{{ lang._('Wait for it to finish, which it does by itself, or stop it here and ask again.') }}")),
                buttons: [
                    {
                        label: "{{ lang._('Stop the blinking') }}",
                        action: function (dialog) {
                            stop_blink();
                            dialog.close();
                        }
                    },
                    close_button()
                ]
            });
        }

        /* The dialog that is open while the socket blinks. It says which label the plugin believes
           is blinking, and takes one of three answers back. */
        function watch_blink(port, label, seconds, mode) {
            const beat = mode === 'flicker';
            /* A port with no printed name of its own cannot be confirmed: "yes, that one" would
               only agree that igb5 is igb5, which the LED node proves by itself. What is worth
               having from such a port is the name somebody can read on it. */
            const nameless = port === null || port === undefined || port.labelled === false;

            const $body = $('<div/>');
            $body.append($('<p/>')
                .append($('<i class="fa fa-fw ' + (beat ? 'fa-exchange' : 'fa-lightbulb-o') + '"></i> '))
                .append($('<span class="lh-strong"/>').text(
                    beat
                        ? (nameless
                            ? "{{ lang._('The socket for {if} is beating its activity light now: one second of traffic, one second of silence.') }}"
                                .replace('{if}', port ? port['if'] : label)
                            : "{{ lang._('The socket this plugin calls {label} is beating its activity light now: one second of traffic, one second of silence.') }}"
                                .replace('{label}', label))
                        : (nameless
                            ? "{{ lang._('The socket for {if} is blinking now: half a second lit, half a second dark.') }}"
                                .replace('{if}', port ? port['if'] : label)
                            : "{{ lang._('The socket this plugin calls {label} is blinking now: half a second lit, half a second dark.') }}"
                                .replace('{label}', label)))));
            $body.append($('<p/>').text(beat
                ? "{{ lang._('Go and look at the front of the appliance, then answer for what you saw there. The traffic is deliberately small - about a quarter of a megabit - so the link carries it without noticing, but this is real traffic and it does reach the device at the other end.') }}"
                : "{{ lang._('Go and look at the front of the appliance, then answer for what you saw there. Nothing in the data path is touched by this: the link stays up, and a socket with no cable in it blinks just the same.') }}"));
            $body.append($('<p class="lh-note"/>').text(label_provenance(port, label)));
            const $left = $('<p class="lh-countdown lh-tech"/>')
                .text("{{ lang._('{count} seconds left') }}".replace('{count}', seconds));
            $body.append($left);

            let remaining = seconds;
            let ticker = null;
            let settled = false;

            /* Called once, whichever way the dialog ends. The stop request leaves a flag behind
               for the worker to find, and a flag left lying about after the blinking has already
               finished would cut the NEXT blink short - so it is only sent while there is still
               something to stop. */
            function settle() {
                if (settled) {
                    return;
                }
                settled = true;
                if (ticker !== null) {
                    window.clearInterval(ticker);
                    ticker = null;
                }
                if (remaining > 0) {
                    stop_blink();
                }
            }

            const answers = [];
            /* A socket the last sweep reports no port for can still be found by its light, and
               that is worth doing; but there is nothing to record an answer against, so nothing
               is asked. */
            if (port === null || port === undefined) {
                answers.length = 0;
            } else if (!nameless) {
                answers.push({
                    id: 'lh-identify-yes',
                    label: "{{ lang._('Yes, that is the socket') }}",
                    cssClass: 'btn-success',
                    action: function (dialog) {
                        settle();
                        dialog.close();
                        confirm_label(port, label);
                    }
                });
                answers.push({
                    id: 'lh-identify-no',
                    label: "{{ lang._('No, a different socket blinked') }}",
                    cssClass: 'btn-warning',
                    action: function (dialog) {
                        settle();
                        dialog.close();
                        ask_correction(port, label, true);
                    }
                });
            } else {
                answers.push({
                    id: 'lh-identify-name',
                    label: "{{ lang._('Write down the name printed on it') }}",
                    cssClass: 'btn-primary',
                    action: function (dialog) {
                        settle();
                        dialog.close();
                        ask_correction(port, label, true);
                    }
                });
            }
            answers.push({
                id: 'lh-identify-stop',
                label: "{{ lang._('Stop the blinking') }}",
                action: function (dialog) {
                    settle();
                    dialog.close();
                }
            });

            /* Named before it exists, because a browser with no CSS transitions reports the dialog
               shown from inside the call that opens it, and the callback below reads it. */
            let dialog = null;
            dialog = BootstrapDialog.show({
                title: identify_title(label),
                message: $body,
                buttons: answers,
                onshown: function () {
                    ticker = window.setInterval(function () {
                        remaining -= 1;
                        if (remaining > 0) {
                            $left.text("{{ lang._('{count} seconds left') }}".replace('{count}', remaining));
                            return;
                        }
                        remaining = 0;
                        window.clearInterval(ticker);
                        ticker = null;
                        $left.text("{{ lang._('The blinking has finished. Ask again if you need another look.') }}");
                        /* Nothing left to stop, and pressing it now would leave that flag behind
                           for the next blink to trip over. */
                        const button = dialog === null ? null : dialog.getButton('lh-identify-stop');
                        if (button && typeof button.disable === 'function') {
                            button.disable();
                        }
                    }, 1000);
                },
                onhide: function () {
                    settle();
                }
            });
        }

        function identify_port(iface, fallback_label, mode) {
            if (find_port(iface) === null) {
                /* The drawing can be on the screen before the first sweep has been read, and the
                   answers below are recorded against the port rather than against the socket, so
                   the measurement is fetched before the light goes on and not after. */
                reload_status(function () {
                    start_blink(iface, fallback_label, mode);
                });
                return;
            }
            start_blink(iface, fallback_label, mode);
        }

        function start_blink(iface, fallback_label, mode) {
            const port = find_port(iface);
            const label = (port !== null && port.label) ? port.label : (fallback_label || iface);
            const beat = mode === 'flicker';
            /* The count of seconds is sent as digits, the way the API reads it: it ends up on a
               command line, and the controller accepts nothing that is not a plain number. */
            ajaxCall('/api/linkhealth/service/' + (beat ? 'flicker' : 'identify')
                    + '/' + encodeURIComponent(iface),
                {'seconds': String(IDENTIFY_SECONDS)}, function (data, call_status) {
                    const state = (call_status === 'success' && data) ? data.status : null;
                    if (state === 'started') {
                        /* the worker clamps what it was asked for, so the countdown follows the
                           time it actually took rather than the time we hoped for */
                        const granted = Number(data.seconds);
                        watch_blink(port, label,
                            isFinite(granted) && granted > 0 ? granted : IDENTIFY_SECONDS, mode);
                        return;
                    }
                    if (state === 'busy') {
                        busy_dialog(label);
                        return;
                    }
                    show_message(BootstrapDialog.TYPE_WARNING, identify_title(label),
                        (beat ? "{{ lang._('The beat could not be started.') }}"
                              : "{{ lang._('The blinking could not be started.') }}")
                        + failure_text(data));
                });
        }

        /* ------------------------------------------------------------------ what the person saw */

        /* The names printed on the front, as the drawing has them, grouped by bay. The drawing is
           the only list that also carries a socket the machine does not report, and a person can
           certainly have watched the light beside one of those. The panel tab has usually already
           fetched it; a correction asked for before that tab was ever opened fetches it once, and
           the two then share the one document - `panel_doc` is the page's own variable, not a copy
           kept here, so a correction always reads the layout the drawing is drawn from. */
        function layout_labels(done) {
            if (panel_doc !== null) {
                done(labels_from(panel_doc));
                return;
            }
            reload_faceplate(function (doc) {
                done(labels_from(doc));
            });
        }

        function labels_from(doc) {
            const groups = [];
            if (doc && doc.available && Array.isArray(doc.rows)) {
                $.each(doc.rows, function (index, row) {
                    const labels = $.map(row.items || [], function (item) {
                        return item.label ? String(item.label) : null;
                    });
                    if (labels.length > 0) {
                        groups.push({bay: row.bay || '', labels: labels});
                    }
                });
            }
            if (groups.length > 0) {
                return groups;
            }
            /* No drawing for this appliance yet. The ports still carry names, and grouping them by
               bay keeps the list in the machine vocabulary rather than in ours. A person on
               hardware nobody has drawn can still type what is printed. */
            const bays = {};
            const order = [];
            $.each((status_doc || {}).ports || [], function (index, port) {
                if (!port.label || port.labelled === false) {
                    return;
                }
                const bay = port.bay || '';
                if (bays[bay] === undefined) {
                    bays[bay] = [];
                    order.push(bay);
                }
                bays[bay].push(port.label);
            });
            return $.map(order, function (bay) {
                return {bay: bay, labels: bays[bay]};
            });
        }

        function ask_correction(port, label, witnessed) {
            if (port === null || port === undefined) {
                return;
            }
            const iface = port['if'];
            /* The question is not the same question in the two places it is asked from: one
               follows a light somebody just watched, the other is somebody correcting a name for
               a reason of their own. */
            const title = witnessed ? "{{ lang._('Which socket blinked?') }}" :
                "{{ lang._('Correct the printed name') }}";
            layout_labels(function (groups) {
                const $body = $('<div/>');
                $body.append($('<p/>').text(witnessed ?
                    "{{ lang._('Pick the name that is printed beside the socket you actually watched blink.') }}" :
                    "{{ lang._('Change this only for a socket you have identified yourself: by blinking it, or by pulling its cable and watching which row on this page goes down. A name written from memory is exactly the wrong kind of certainty.') }}"));
                $body.append($('<p class="lh-note lh-tech"/>').text(
                    "{{ lang._('{if} is called {label} here at the moment.') }}"
                        .replace('{if}', iface).replace('{label}', label)));

                const $select = $('<select class="form-control"/>');
                $select.append($('<option/>').attr({'value': '', 'disabled': 'disabled', 'selected': 'selected'})
                    .text("{{ lang._('choose the name you read on the socket') }}"));
                $.each(groups, function (index, group) {
                    const $group = group.bay ? $('<optgroup/>').attr('label', group.bay) : $select;
                    $.each(group.labels, function (position, name) {
                        $group.append($('<option class="lh-tech"/>').attr('value', name).text(name));
                    });
                    if (group.bay) {
                        $select.append($group);
                    }
                });
                $select.append($('<option/>').attr('value', LABEL_FREEHAND)
                    .text("{{ lang._('another name, not in this list') }}"));

                const $freehand = $('<input type="text" class="form-control lh-tech" maxlength="32"/>')
                    .attr('placeholder', "{{ lang._('the name printed beside the socket') }}")
                    .hide();
                /* A plain select and a plain input, positioned by nothing. Anything that opens a
                   menu of its own has to be told which way is forward, and this page is read from
                   the other side on the machine it was written on. */
                $body.append($('<label class="lh-label"/>').text("{{ lang._('The name printed on the socket that blinked') }}"));
                $body.append($select);
                $body.append($freehand);
                $select.on('change', function () {
                    if ($select.val() === LABEL_FREEHAND) {
                        $freehand.show().focus();
                    } else {
                        $freehand.hide();
                    }
                });

                BootstrapDialog.show({
                    type: BootstrapDialog.TYPE_WARNING,
                    title: title,
                    message: $body,
                    buttons: [
                        {
                            label: "{{ lang._('Cancel') }}",
                            action: function (dialog) {
                                dialog.close();
                            }
                        },
                        {
                            label: "{{ lang._('Save what I saw') }}",
                            cssClass: 'btn-primary',
                            action: function (dialog) {
                                const chosen = $.trim($select.val() === LABEL_FREEHAND ?
                                    String($freehand.val()) : String($select.val() || ''));
                                if (chosen === '') {
                                    /* keep the dialog open: there is nothing to save yet, and
                                       closing it would throw away the walk to the rack */
                                    $body.find('.lh-problem').remove();
                                    $body.append($('<p class="lh-problem text-danger"/>')
                                        .text("{{ lang._('No name has been chosen yet.') }}"));
                                    return;
                                }
                                dialog.close();
                                save_correction(port, label, chosen, title);
                            }
                        }
                    ]
                });
            });
        }

        /* ------------------------------------------------------------------ writing it down */

        /* The first validation message the model sent back, whatever shape it arrived in. */
        function validation_text(result) {
            const problems = result ? result.validations : null;
            let found = null;
            if (problems !== null && problems !== undefined && typeof problems === 'object') {
                $.each(problems, function (field, message) {
                    found = Array.isArray(message) ? message[0] : message;
                    return false;
                });
            }
            if (found) {
                return found;
            }
            return result ? (result.detail || result.message || null) : null;
        }

        /* One name for one interface, kept in the per-port override that the settings tab already
           edits. There is no separate store for "a person confirmed this": the override IS the
           record of what somebody saw, and a second place to keep it would only be a second place
           to disagree with. */
        function store_label(iface, label, done) {
            ajaxGet('/api/linkhealth/settings/get', {}, function (data, request_status) {
                if (request_status !== 'success' || !data || !data.linkhealth) {
                    done(false, "{{ lang._('The saved settings could not be read, so nothing was written.') }}");
                    return;
                }
                const rows = ((data.linkhealth.ports || {}).port) || {};
                let uuid = null;
                $.each(rows, function (key, row) {
                    if (row && row.interface === iface) {
                        uuid = key;
                        return false;
                    }
                });
                const payload = {'port': {'interface': iface, 'label': label}};
                let endpoint;
                if (uuid !== null) {
                    /* only the two fields are sent: the model leaves a field nobody mentioned
                       exactly as it was, so a saved neighbour address survives a renaming */
                    endpoint = '/api/linkhealth/settings/set_port/' + encodeURIComponent(uuid);
                } else {
                    endpoint = '/api/linkhealth/settings/add_port';
                    /* a new row watches its port like every other one; written out rather than
                       left to a default, so the record says what it means */
                    payload.port.enabled = '1';
                }
                ajaxCall(endpoint, payload, function (result, call_status) {
                    if (call_status === 'success' && result && result.result === 'saved') {
                        /* The overrides grid on the settings tab is now a row out of date. The
                           grid keeps its instance under the name the current implementation
                           stores it by; the old jQuery bootgrid's key is not written at all on
                           this release, so a guard asking for that one never passes and the
                           settings tab quietly keeps showing the row as it was. */
                        const $grid = $('#{{ port_grid_id }}');
                        if ($grid.length > 0 && $grid.data('UIBootgrid') !== undefined) {
                            $grid.bootgrid('reload');
                        }
                        done(true, null);
                        return;
                    }
                    done(false, validation_text(result));
                });
            });
        }

        /* "Yes, that one." The only thing this plugin will call a confirmation. */
        function confirm_label(port, label) {
            if (port === null || port === undefined) {
                return;
            }
            const iface = port['if'];
            const title = identify_title(label);
            if ((port.label_source || '') === 'override' && port.label === label) {
                const $known = $('<div/>');
                $known.append($('<p/>').text(
                    "{{ lang._('Nothing needed saving: this firewall already keeps the name {label} for {if}. What has changed is that you have now seen it for yourself.') }}"
                        .replace('{label}', label).replace('{if}', iface)));
                show_message(BootstrapDialog.TYPE_SUCCESS, title, $known);
                return;
            }
            store_label(iface, label, function (ok, detail) {
                if (!ok) {
                    show_message(BootstrapDialog.TYPE_WARNING, title,
                        "{{ lang._('What you saw could not be saved.') }}" +
                        (detail ? '<br/><br/>' + esc(detail) : ''));
                    return;
                }
                const $body = $('<div/>');
                $body.append($('<p/>').text(
                    "{{ lang._('Saved. This firewall now carries your own word for it: {if} is the socket printed {label}.') }}"
                        .replace('{if}', iface).replace('{label}', label)));
                $body.append($('<p/>').text("{{ lang._('That is what a confirmation is here - somebody watched a socket blink and said so. The built-in table happened to agree with you, but the table is not what this page is now relying on.') }}"));
                $body.append($('<p class="lh-note"/>').text("{{ lang._('The record lives in this firewall alone, as a port override on the Settings tab. Nothing was sent anywhere.') }}"));
                show_message(BootstrapDialog.TYPE_SUCCESS, title, $body);
            });
        }

        /* "No, a different socket blinked." */
        function save_correction(port, old_label, chosen, title) {
            const iface = port['if'];
            if (chosen === old_label) {
                show_message(BootstrapDialog.TYPE_SUCCESS, title,
                    "{{ lang._('Nothing changed: that is the name it carries already.') }}");
                return;
            }
            store_label(iface, chosen, function (ok, detail) {
                if (!ok) {
                    show_message(BootstrapDialog.TYPE_WARNING, title,
                        "{{ lang._('The correction was not saved.') }}" +
                        (detail ? '<br/><br/>' + esc(detail) : ''));
                    return;
                }
                const $body = $('<div/>');
                $body.append($('<p/>').text(
                    "{{ lang._('Saved. {if} is called {label} on this firewall from the next sweep, at most a minute from now.') }}"
                        .replace('{if}', iface).replace('{label}', chosen)));
                /* The other port keeps the name until somebody identifies that one too. Better to
                   say so than to let two rows quietly answer to the same name. */
                const other = label_owner(chosen, iface);
                if (other !== null) {
                    $body.append($('<p class="text-warning"/>').text(
                        "{{ lang._('{other} answers to {label} as well, because the built-in table still says so. Identify that one too and correct it, or two rows on this page will carry the same name.') }}"
                            .replace('{other}', other['if']).replace('{label}', chosen)));
                }
                $body.append($('<p/>').text(
                    "{{ lang._('This correction is kept in this firewall and nowhere else. The table the plugin ships is unchanged: it still says {old}, and every other machine of this model still gets that name.') }}"
                        .replace('{old}', old_label)));
                $body.append($('<p class="lh-note"/>')
                    .text("{{ lang._('If the shipped table is wrong rather than this one appliance, the fix is one JSON block in chassis.json. The guide docs/contributing-chassis.md walks through it, and its last section says what to put in the pull request - including which ports you checked by looking, which is the part that makes a label worth trusting.') }}")
                    /* the space belongs to the page, not to the sentence: a translator should
                       never have to notice that one is hiding at the end of a string */
                    .append(document.createTextNode(' '))
                    .append($('<a class="lh-tech" target="_blank" rel="noopener"/>')
                        .attr('href', 'https://github.com/AbdelmonemAwad/os-linkhealth')
                        .text('github.com/AbdelmonemAwad/os-linkhealth')));
                show_message(BootstrapDialog.TYPE_SUCCESS, title, $body);
            });
        }

        /* ------------------------------------------------------------------ on the detail tab */

        function identify_panel(port) {
            const $content = $('<div class="panel-body"/>');
            $content.append($('<p/>').text(label_provenance(port, port.label)));
            if (can_identify(port)) {
                $content.append($('<p/>').text("{{ lang._('Nothing in the firmware carries the names printed on the metal, so this plugin cannot read them. What it can do is make this one socket blink while you stand in front of the appliance.') }}"));
                $content.append($('<p/>').append(
                    $('<button type="button" class="btn btn-default lh-identify"/>')
                        .attr('data-if', port['if'])
                        .attr('data-label', port.label || port['if'])
                        .append($('<i class="fa fa-fw fa-lightbulb-o"></i> '))
                        .append($('<span/>').text("{{ lang._('Blink this socket for {count} seconds') }}"
                            .replace('{count}', IDENTIFY_SECONDS)))));
            } else if (port.identify_note) {
                /* The node is there and the write is accepted and the socket stays dark. That is
                   not something this page worked out; it is something somebody looked at, wrote
                   into the chassis table, and had checked against the card actually present. */
                $content.append($('<p class="lh-muted"/>').text(lh_data(port.identify_note)));
            } else {
                $content.append($('<p class="lh-muted"/>').text("{{ lang._('The driver of this port registers no identification LED, so there is no light here to blink. Pull the cable and watch which row on this page goes down: that is the other way to be sure, and it is just as good a witness.') }}"));
            }
            /* The LED node existing is not the same as a light being wired to it. On the
               appliance this plugin was written for, the 10G cages register the node, accept
               the write and light nothing at all, because the driver drives one fixed LED
               index that this board does not connect. No interface can be asked about that -
               it can only be found out by looking - so where the other light is available it
               is offered beside the first, and the page says which is which. */
            if (can_flicker(port)) {
                $content.append($('<p class="lh-note"/>').text("{{ lang._('Not every socket has its identification light wired: some take the command and stay dark. Every socket has an activity light, and that one can be made to beat on purpose - one second of traffic, one second of silence, which is a rhythm ordinary traffic does not have.') }}"));
                $content.append($('<p/>').append(
                    $('<button type="button" class="btn btn-default lh-flicker"/>')
                        .attr('data-if', port['if'])
                        .attr('data-label', port.label || port['if'])
                        .append($('<i class="fa fa-fw fa-exchange"></i> '))
                        .append($('<span/>').text("{{ lang._('Beat the activity light for {count} seconds') }}"
                            .replace('{count}', IDENTIFY_SECONDS)))));
            }
            $content.append($('<p/>').append(
                $('<a href="#" class="lh-rename"/>')
                    .attr('data-if', port['if'])
                    .text("{{ lang._('the name printed on this socket is not the one above') }}")));
            return detail_panel("{{ lang._('Which socket is this?') }}", $content,
                "{{ lang._('A port name is only ever as good as the last person who looked. This plugin will not call a mapping confirmed on the strength of a table: a confirmation here is somebody who watched a socket blink and said which one it was.') }}");
        }

        /* One listener for everything that asks for a blink, wherever it was drawn: the command in
           the ports grid, the button on the port detail, and the light above a socket on the front
           panel drawing. The grid replaces its rows on every sweep, the detail panels are rebuilt
           with them and the drawing is redrawn whenever the layout changes, so a delegated listener
           is the only one that survives all three.

           That makes this the contract the drawing is written against: anything carrying the class
           lh-identify and a data-if gets a blink, with data-label naming the socket in the dialog
           for the moment before the ports list has been read. An element that is not a button of
           its own is left to turn a key press into a click where it is drawn, which is where it is
           also known whether the press has to be kept from the control underneath it. */
        $(document).on('click', '.lh-identify', function (event) {
            event.preventDefault();
            /* Read from the element and not through jQuery, which remembers the first value it
               was asked for: a light on the drawing is a node that stays while the sweep behind
               it changes, and a remembered interface name is how the wrong socket ends up
               blinking. That is the one mistake this whole feature exists to prevent. */
            const iface = this.getAttribute('data-if') || '';
            if (iface === '') {
                return;
            }
            identify_port(iface, this.getAttribute('data-label'));
        });

        /* The way to a correction for a port that cannot blink at all - and for the person who
           already knows, because they pulled the cable, which socket this is. */
        /* The same contract as .lh-identify, one mode along: anything carrying lh-flicker and
           a data-if beats its activity light instead of its identification LED. */
        $(document).on('click', '.lh-flicker', function (event) {
            event.preventDefault();
            const element = event.currentTarget;
            identify_port(element.getAttribute('data-if'),
                          element.getAttribute('data-label'), 'flicker');
        });

        $(document).on('click', '.lh-rename', function (event) {
            event.preventDefault();
            const port = find_port(this.getAttribute('data-if') || '');
            if (port !== null) {
                ask_correction(port, port.label || port['if'], false);
            }
        });
