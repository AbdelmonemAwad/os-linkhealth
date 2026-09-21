/*
 * Copyright (C) 2026 Abdelmonem Awad <eg2@live.com>
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
 * INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 * AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 * AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
 * OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 */

/*
 * Dashboard widget for Link Health: a strip of the front panel, then one line per physical port,
 * worst first.
 *
 * The strip is one small block per socket, in the order the sockets are laid out on the metal, so
 * a fault has a position on the box and not only a name in a list. It is drawn from the faceplate
 * document - port counts, order, bays and printed labels, which are facts and not anybody's
 * artwork - and coloured from the same status file the list below it reads. An appliance with no
 * layout of its own simply has no strip; nothing says so, because a dashboard panel is not the
 * place to explain what is missing.
 *
 * A line carries the name the chassis prints on the faceplate, a colour for the verdict, the
 * error rate over the last window, and the optical receive power where the port can report one.
 * Clicking anywhere on a line - or anywhere on the strip - opens Interfaces > Link Health.
 *
 * Everything shown comes from /var/db/linkhealth/status.json, which the API serves verbatim; this
 * widget never re-derives a verdict from the raw numbers, it only prints what the collector and
 * the verdict engine already decided. A clean or idle port is drawn in grey on purpose: the widget
 * earns its place on the dashboard by being boring until something is actually wrong.
 *
 * The colours, the sort order and the way a rate is written are kept the same as on the full page
 * in Interfaces > Link Health. The two read the same document, and a port that is second from the
 * top in one of them has no business being fifth in the other.
 */

export default class LinkHealth extends BaseTableWidget {
    constructor(config) {
        super(config);

        /* the collector rewrites status.json once a minute; half that keeps the dashboard within
           one window of the truth and still costs one small read of an already generated file */
        this.tickTimeout = 30;

        /* below this width the flex table stacks its cells, and three stacked lines per port is
           not a compact list any more - see onWidgetResize() for which cell gives way */
        this.opticsBreakpoint = 450;
        this.showOptics = true;

        /* dataChanged() only tells us the file is the same as last time, which is not the same
           thing as the list still being on screen - see _update() */
        this.listed = false;

        /* The front panel is a drawing of the metal, so it cannot change while the firewall is
           running: it is read once and kept, and the tick keeps costing exactly one status call.
           The colours on it are taken from that status document rather than from the copy of the
           state the faceplate carries, so the strip and the list under it can never disagree
           about the same port.

           A layout that could not be read is not retried forever. Three attempts cover a web
           service that is being restarted while the dashboard is open; past that the machine is
           telling us it has no faceplate to give, and the widget has a list to draw. */
        this.faceplate = null;
        this.faceplateTries = 0;
        this.drawn = undefined;
    }

    getGridOptions() {
        return {
            // trigger overflow-y:scroll after 650px height
            sizeToContent: 650
        };
    }

    getMarkup() {
        const $container = $('<div></div>');

        /* one line above the list for what is true of the whole file rather than of any one port:
           the collector has stopped, or it has not written anything yet */
        $container.append($(`
            <div id="linkhealth-notice" class="text-muted"
                 style="display: none; padding: 0.5em; text-align: center;"></div>
        `));

        /* The strip sits under the notice and over the list: a warning that the readings have
           stopped belongs above the picture those readings colour in. It is empty and hidden
           until a layout arrives, so a firewall with no faceplate entry loses no height at all. */
        $container.append($('<div id="linkhealth-faceplate" style="display: none;"></div>'));

        $container.append(this.createTable('linkhealth-table', {headerPosition: 'none'}));

        return $container;
    }

    async onMarkupRendered() {
        /* Translations reach the widget after the constructor has run, so the table of verdict
           states is built here. The ranks are the sort order of the list, and they are exactly
           the ranks the full page sorts by - fail, warn, watch, ok, idle, down, disabled - for
           the reason the page gives: the four judged states come before the three that are not a
           judgement, and a port that is merely dark sinks to the bottom because a dark port is
           usually a socket nobody has plugged into. On the reference machine thirteen of the
           twenty ports are down on an ordinary day, and a widget that floated all thirteen above
           the two it has actually measured would be a list of empty sockets.

           Icon and text colour are deliberately not the same for "ok" - a green dot says the port
           was judged and found clean, while grey text keeps it from competing with the ports that
           need reading. "watch" is blue rather than amber, as it is on the page: a few corrupted
           frames and a downshifted link are different findings and must not look alike.

           `fill` is how much of the socket is coloured in on the front-panel strip. The outline is
           always drawn at the full contextual colour - see _socket() for why that matters on the
           dark theme - so this only decides how loud the inside of it is. A faceplate is mostly
           empty by design, and twenty equally solid blocks would make the one that matters
           impossible to find, so a dark socket is left hollow, a clean one is tinted, and only a
           port with something wrong is filled in. */
        this.states = {
            fail: {
                rank: 0, icon: 'fa-triangle-exclamation', fill: 0.6,
                iconColour: 'text-danger', textColour: 'text-danger', label: this.translations.state_fail
            },
            warn: {
                rank: 1, icon: 'fa-triangle-exclamation', fill: 0.6,
                iconColour: 'text-warning', textColour: 'text-warning', label: this.translations.state_warn
            },
            watch: {
                rank: 2, icon: 'fa-circle', fill: 0.6,
                iconColour: 'text-info', textColour: 'text-info', label: this.translations.state_watch
            },
            ok: {
                rank: 3, icon: 'fa-circle', fill: 0.3,
                iconColour: 'text-success', textColour: 'text-muted', label: this.translations.state_ok
            },
            idle: {
                rank: 4, icon: 'fa-circle', fill: 0.18,
                iconColour: 'text-muted', textColour: 'text-muted', label: this.translations.state_idle
            },
            down: {
                rank: 5, icon: 'fa-circle', fill: 0,
                iconColour: 'text-muted', textColour: 'text-muted', label: this.translations.state_down
            },
            disabled: {
                rank: 6, icon: 'fa-circle', fill: 0,
                iconColour: 'text-muted', textColour: 'text-muted', label: this.translations.state_disabled
            }
        };

        /* The dashboard stores getMarkup() as a string (it serialises the panel to outerHTML
           before gridstack parses it again), so a handler bound while the markup is being built
           never reaches the page. This runs once the markup is in the document. The handler is
           delegated from the table, which stays put, because updateTable() throws the rows away
           and builds new ones on every refresh. */
        $('#linkhealth-table')
            .off('click.linkhealth')
            .on('click.linkhealth', '.flextable-row', (event) => {
                if ($(event.target).closest('a').length > 0) {
                    /* the port name is a real link, so middle click and keyboard still work;
                       let the browser follow it instead of navigating twice */
                    return;
                }
                window.location.href = '/ui/linkhealth';
            });
    }

    async onWidgetTick() {
        let status = null;

        try {
            status = await this.ajaxCall('/api/linkhealth/service/status');
        } catch (error) {
            /* An exception that escapes this method replaces the widget with the dashboard's red
               "Failed to load widget" box, which then says nothing at all about the ports. A web
               service being restarted, or a session that has just expired, is not worth that: the
               last list stays on screen with a line saying it is no longer being refreshed. */
            this._setNotice(this.translations.unreachable, 'text-warning');
            return;
        }

        await this._readFaceplate();

        try {
            this._update(status);
        } catch (error) {
            /* one malformed record must cost the list one line at most, never the whole widget */
            console.error('LinkHealth: could not render the port list', error);
            this._setNotice(this.translations.unreachable, 'text-warning');
        }
    }

    async _readFaceplate() {
        /* Read once, keep forever. Sockets are not added to a running firewall, so a second call
           would spend a tick to be told the same thing, and the tick budget is meant for the
           reading that does change.

           A call that was answered with something other than a layout - the model has no drawing
           yet, configd could not be reached, the backend is older than this file - is kept as the
           answer all the same. It means "there is no strip to draw", and asking a second time
           would get the same sentence back. Only a call that was never answered is retried. */
        if (this.faceplate !== null || this.faceplateTries >= 3) {
            return;
        }

        this.faceplateTries += 1;

        try {
            const layout = await this.ajaxCall('/api/linkhealth/service/faceplate');
            this.faceplate = (layout !== null && typeof layout === 'object') ? layout : {available: false};
        } catch (error) {
            /* the strip is the extra, the list is the widget: a web service being restarted may
               cost the front panel and must never cost the port list */
        }
    }

    onWidgetResize(elem, width, height) {
        /* The optical reading is the cell that gives way when the widget is made narrow: it is
           corroboration, and the design is explicit that it never carries the verdict. The verdict
           and the port name stay whatever the width. */
        this.showOptics = width > this.opticsBreakpoint;
        const changed = super.onWidgetResize(elem, width, height);
        this._applyOpticsVisibility();

        return changed;
    }

    /* Rendering */

    _update(status) {
        /* The API hands the collector's document back verbatim, and that document has no top
           level "status" key. This envelope is therefore the controller itself saying it never
           reached the backend - configd stopped, or the script died - which is a different thing
           from a collector that has simply not written its first sweep yet. */
        if (status && status.status === 'failed') {
            this._setNotice(this.translations.unreachable, 'text-warning');
            return;
        }

        const ports = Array.isArray(status && status.ports) ? status.ports : [];

        if (ports.length === 0) {
            this._setNotice(this.translations.nodata, 'text-muted');
            this._tooltips('hide');
            this.updateTable('linkhealth-table', []);
            this._faceplate([]);
            this.listed = false;
            return;
        }

        this._setNotice(this._stalenessNotice(status), 'text-warning');

        /* A sweep that found no ports empties the table, and the sweep after it may well carry
           the same ports as the last one that did - identical to what dataChanged() remembers.
           Draw whenever the file changed or the list is not on screen, whichever comes first.

           The layout arrives a moment after the first list does, so `drawn` also remembers which
           faceplate document the strip was built from. It starts as undefined and the document
           starts as null, which is what makes the very first pass draw. */
        if (!this.dataChanged('linkhealth-ports', ports) && this.listed && this.drawn === this.faceplate) {
            return;
        }

        this._tooltips('hide');

        const rows = ports
            .slice()
            .sort((a, b) => this._compare(a, b))
            .map((port) => this._row(port));

        this.updateTable('linkhealth-table', rows);

        /* the rows are built by the base class, so the pointer is set here rather than inline */
        $('#linkhealth-table').children('.flextable-row').css('cursor', 'pointer');
        this._applyOpticsVisibility();

        this._faceplate(ports);

        this._tooltips('init');
        this.listed = true;
        this.drawn = this.faceplate;
    }

    _row(port) {
        const state = this._state(port);
        const style = this._style(port);
        const link = port.link || {};

        /* The whole point of the plugin is that a fault is reported as PortA3 and not as igb2, so
           the chassis label leads and the kernel name follows in small grey text - the kernel name
           is still what ifconfig, the interface pages and the logs will call it. The bay, the
           configured name and the chip go into the tooltip, where they cost no space.

           min-width on both the row and the link is what lets the label be cut with an ellipsis:
           a flex item refuses to shrink below its own content until it is told it may. */
        const identity = [port.bay, port.name, port.chip].filter((part) => !!part).join(' - ');
        const name = `
            <div style="display: flex; align-items: center; gap: 6px; min-width: 0;">
                <i class="fa fa-fw ${style.icon} ${style.iconColour} linkhealth-tip"
                   style="font-size: 11px;" data-toggle="tooltip" title="${this._escape(style.label)}"></i>
                <a href="/ui/linkhealth" class="linkhealth-tip" data-toggle="tooltip"
                   title="${this._escape(this._isolate(identity))}"
                   style="font-weight: bold; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
                   >${this._escape(this._label(port))}</a>
                <span class="text-muted" dir="ltr" style="font-size: 11px;">${this._escape(port['if'] || '')}</span>
            </div>`;

        const verdict = [`<span class="${style.textColour}">${this._escape(style.label)}</span>`];

        /* A rate is a judgement, and three of the states are explicitly not one. An idle port has
           not been cleared of anything - the design says it is never green and never red - so the
           handful of parts per million a few hundred frames can produce stays off the line, and a
           dark port has no window to have a rate over at all. */
        if (state !== 'idle' && state !== 'down' && state !== 'disabled') {
            const rate = this._errorRate(this._ppm(port));
            if (rate !== '') {
                verdict.push(`<span dir="ltr" class="${style.textColour}">${rate}</span>`);
            }
        }

        if (link.downshift === true) {
            /* The one rich signal every driver can give, including the ones with no error
               counters at all: the port settled below the fastest rung its own PHY advertises.
               That is what a broken pair in a cable looks like from this side. */
            const negotiated = this._speed(link.speed_mbps);
            const fastest = this._speed(link.max_speed_mbps);
            if (negotiated !== '' && fastest !== '') {
                verdict.push(`
                    <span dir="ltr" class="text-warning linkhealth-tip" data-toggle="tooltip"
                          title="${this._escape(this.translations.downshift)}"
                          ><i class="fa fa-arrow-down"></i> ${this._escape(negotiated)} / ${this._escape(fastest)}</span>
                `);
            }
        }

        const judgement = `
            <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap; min-width: 0;">
                ${verdict.join('')}
            </div>`;

        return [name, judgement, `<div class="linkhealth-optics">${this._optics(port)}</div>`];
    }

    _optics(port) {
        const caps = Array.isArray(port.caps) ? port.caps : [];
        const optics = port.optics || {};

        /* A panel is rendered if and only if its capability flag is set: a copper port, or an SFP
           cage on a driver that implements no SIOCGI2C, leaves this cell empty rather than showing
           a dash that could be mistaken for a reading of zero. */
        if (!caps.includes('OPTICS_DOM') || optics.present !== true) {
            return '';
        }

        /* the data contract types rx_dbm as a JSON number, so anything else - a null left behind
           for a module that answers its inventory but no diagnostics, an empty string - means
           there is no reading, and it must not become a very believable 0.00 dBm */
        const rx = optics.rx_dbm;
        if (typeof rx !== 'number' || !Number.isFinite(rx)) {
            return '';
        }

        /* Receive power never decides anything here. During the fault this plugin was written for,
           3.81% of frames failed CRC while both transceivers read fully inside their own limits,
           so the colour can only ever repeat an advisory the verdict engine already wrote down -
           and `_worst()` skips those when it picks the state, which is why an amber reading can
           sit on a green port. What the reading is really for is the tooltip: the two ends of that
           fibre held different modules. */
        const module = caps.includes('OPTICS_INVENTORY')
            ? [optics.type, optics.vendor, optics.pn, optics.sn].filter((part) => !!part).join(' - ')
            : '';

        return `
            <span dir="ltr" class="${this._opticsColour(port)} linkhealth-tip" data-toggle="tooltip"
                  title="${this._escape(this._isolate(module))}"
                  >${this._escape(rx.toFixed(2))} ${this._escape(this.translations.unit_dbm)}</span>`;
    }

    _opticsColour(port) {
        /* What the collector actually writes about a module. There is no `alarm` or `warning`
           boolean anywhere in the document - `ifconfig -v` prints no such flag and the collector
           invents none - so the module's condition reaches a consumer the one way the design
           allows: as an advisory reason on the port's verdict, carrying the same severity
           vocabulary every other reason uses. A cell that read a flag nobody writes would have
           stayed grey through a transceiver sitting at 80 C.

           The severities are coloured the way the full page colours a reason of the same
           severity, so one finding is one colour in both places, and a module with nothing to say
           stays grey. Only the colour crosses over: the sentence behind it is written by the
           collector in English, the page prints it as it arrives, and an English sentence has no
           business appearing in a tooltip on an Arabic dashboard. The reading is a hint that the
           port is worth opening, and the page is where the words are. */
        const colours = {fail: 'text-danger', warn: 'text-warning', watch: 'text-info', info: 'text-info'};
        const rank = {fail: 0, warn: 1, watch: 2, info: 3};
        const reasons = (port.verdict && Array.isArray(port.verdict.reasons)) ? port.verdict.reasons : [];

        let worst = null;
        reasons.forEach((reason) => {
            if (!reason || reason.advisory !== true || String(reason.code || '').indexOf('optics_') !== 0) {
                return;
            }
            const severity = String(reason.severity || '');
            if (!Object.prototype.hasOwnProperty.call(rank, severity)) {
                return;
            }
            if (worst === null || rank[severity] < rank[worst]) {
                worst = severity;
            }
        });

        return worst === null ? 'text-muted' : colours[worst];
    }

    _faceplate(ports) {
        const $strip = $('#linkhealth-faceplate');
        if ($strip.length === 0) {
            return;
        }

        const layout = this.faceplate;
        const rows = (layout && Array.isArray(layout.rows)) ? layout.rows : [];

        /* available:false is the backend saying there is no drawing for this model, which is the
           ordinary case for any appliance nobody has contributed a layout for yet. It is not an
           error and it gets no message: the widget simply has one part fewer. */
        if (!layout || layout.available !== true || rows.length === 0 || ports.length === 0) {
            $strip.hide().empty();
            return;
        }

        /* The interface name is the strongest key there is - it is what the alert, the page and
           the kernel all agree on - so the join is made on it, with the bay and printed label as
           the fallback for a layout written against a chassis table that has since been edited.
           A socket that matches neither is drawn as absent rather than guessed at. */
        const byInterface = {};
        const byLabel = {};
        ports.forEach((port) => {
            if (port['if']) {
                byInterface[port['if']] = port;
            }
            byLabel[this._socketKey(port.bay, port.label)] = port;
        });

        const types = (layout.port_types !== null && typeof layout.port_types === 'object')
            ? layout.port_types : {};
        const unconfirmed = layout.confirmed !== true;

        const blocks = [];
        rows.forEach((row, bay) => {
            const items = Array.isArray(row.items) ? row.items : [];
            items.forEach((item, position) => {
                const port = byInterface[item['if']]
                    || byLabel[this._socketKey(row.bay, item.label)]
                    || null;
                /* the wider gap is the only thing that says where one bay ends and the next
                   begins; a bay is a real thing a person can point at, and the module bay on the
                   reference machine enumerates before the faceplate it sits next to */
                blocks.push(this._socket(row, item, port, types, unconfirmed,
                                         bay > 0 && position === 0));
            });
        });

        if (blocks.length === 0) {
            $strip.hide().empty();
            return;
        }

        /* An inline row of boxes is laid out along the writing direction, so in the Arabic GUI a
           flex row would put the first socket at the right edge and run the front panel backwards.
           The drawing is of metal, and metal does not mirror: the row of sockets is pinned to ltr
           so the blocks stay in the order they are in on the box, exactly as the page pins its
           own drawing and nothing around it.

           The direction sits on the row and not on the link, because the link carries the only
           words here - the label a screen reader reads out - and those are translated, so they
           have to be announced in the direction of the page and not in the direction of the
           metal. The whole strip is one link rather than a click handler, so the middle mouse
           button and the keyboard reach the page the same way the port names in the list below
           already do. */
        $strip.html(`
            <a href="/ui/linkhealth" aria-label="${this._escape(this.translations.faceplate)}"
               style="display: block; padding: 2px 0 6px; text-decoration: none;"
               ><span dir="ltr" style="display: flex; align-items: center; gap: 2px;
                                       overflow: hidden;">${blocks.join('')}</span></a>
        `).show();
    }

    _socketKey(bay, label) {
        /* A bay is free text and so is a printed label, so the two cannot simply be pasted
           together with a separator and trusted: "faceplate" + "1G SFP/Port9" and "faceplate, 1G
           SFP" + "Port9" would collide under most of them. JSON quotes and escapes both halves,
           which is exactly the property wanted and costs nothing at twenty sockets. */
        return JSON.stringify([bay || '', label || '']);
    }

    _socket(row, item, port, types, unconfirmed, newBay) {
        const state = port ? this._state(port) : 'absent';
        const absent = (state === 'absent');
        const style = this._styleFor(state);

        /* The width hints are the layout's own arbitrary units, which is exactly what a flex grow
           factor wants: the cages come out wider than the copper jacks in the same proportion as
           on the metal, whatever width the dashboard column happens to be. The rounding is the
           other half of it - a cage reads as a slot and a jack as a square - and it is the only
           shape that survives being ten pixels tall. */
        const hint = types[item.type] || types.unknown || {};
        const width = Number(hint.width) > 0 ? Number(hint.width) : 34;
        const radius = hint.shape === 'cage' ? '5px' : (hint.shape === 'rj45' ? '1px' : '2px');

        /* currentColor rather than a hex value: the theme decides what "danger" looks like, and
           these blocks have to stay legible on the light and the dark dashboards alike.

           The outline is drawn at full strength and only the inside of the socket is faded. That
           is the same split the page makes on its own drawing - stroke at full opacity, fill at
           0.18 - and it is not a matter of taste. `opacity` blends the whole block towards
           whatever is behind it, and the two dashboards sit at opposite ends of that: the widget
           panel is #FBFBFB on the light theme and #101218 on the dark one, so a muted block at
           the old 0.3 came out around 1.4:1 against the dark background and the strip quietly
           lost most of its sockets - on the theme this firewall is actually set to. The same
           colour at full strength is 4.2:1 there, and a one pixel inset shadow reads on both.

           An absent socket keeps the outline, loses even the tint, and has that outline dashed,
           which is how the page draws one: the drawing has a jack the machine never mentioned,
           and anything solid there would be a claim about a port nobody is measuring. */
        const tint = (!absent && style.fill > 0)
            ? `<span style="display: block; height: 100%; background-color: currentColor;
                            opacity: ${style.fill};"></span>`
            : '';
        const edge = absent
            ? 'border: 1px dashed currentColor;'
            : 'box-shadow: inset 0 0 0 1px currentColor;';

        /* The printed label and the bay come from the drawing, because that is what they are; the
           interface name and the configured name are read off the live port instead, so a port
           that was renamed since the layout was fetched is named in the tooltip the way the rest
           of the firewall is naming it now. */
        const label = absent ? this.translations.state_absent : style.label;
        const identity = [item.label, port ? port['if'] : item['if'], port ? port.name : '', row.bay]
            .filter((part) => !!part)
            .join(' - ');
        const tip = [identity, label, unconfirmed ? this.translations.unconfirmed : '']
            .filter((part) => !!part)
            .join(' - ');

        /* margin-inline-start, and it resolves against the row above rather than against the
           page: the row is pinned to ltr, so the wider gap opens on the side the next bay
           actually starts on whichever language the GUI is in. */
        return `
            <span class="${style.iconColour} linkhealth-tip" data-toggle="tooltip"
                  title="${this._escape(this._isolate(tip))}"
                  style="flex: ${width} 0 0; min-width: 3px; height: 10px; box-sizing: border-box;
                         border-radius: ${radius}; overflow: hidden;
                         ${edge}${newBay ? ' margin-inline-start: 6px;' : ''}"
                  >${tint}</span>`;
    }

    _tooltips(action) {
        /* A tooltip whose element is thrown away while it is on screen leaves itself behind in
           the body, because that is where it was attached to escape the panel's overflow. Both
           halves of the widget build their rows from scratch on every refresh, so both are hidden
           before the rebuild and initialised again after it. */
        const $tips = $('#linkhealth-faceplate, #linkhealth-table').find('.linkhealth-tip');
        if ($tips.length === 0) {
            return;
        }

        if (action === 'hide') {
            $tips.tooltip('hide');
            return;
        }

        $tips.tooltip({container: 'body'});
    }

    _setNotice(text, colour) {
        const $notice = $('#linkhealth-notice');
        if ($notice.length === 0) {
            return;
        }

        if (!text) {
            $notice.hide().empty();
            return;
        }

        /* .text() rather than .html(): the only variable part is a formatted timestamp */
        $notice.attr('class', colour || 'text-muted').text(text).show();
    }

    _applyOpticsVisibility() {
        const $cells = $('#linkhealth-table').find('.linkhealth-optics').parent();
        if ($cells.length === 0) {
            return;
        }

        $cells.toggle(this.showOptics);
        /* the flex table divides a row between the cells it can see, so the column widths have to
           be worked out again once one of them is taken out of the count */
        this.refreshStyles('linkhealth-table');
    }

    /* Reading the status file */

    _label(port) {
        /* unknown hardware falls back to the interface name, which the collector already does;
           this only keeps the list readable if a chassis table ever leaves the label out */
        return port.label || port['if'] || '';
    }

    _state(port) {
        const state = port.verdict ? port.verdict.state : '';
        return typeof state === 'string' ? state : '';
    }

    _ppm(port) {
        const ppm = Number(port.window ? port.window.error_ppm : NaN);
        return Number.isFinite(ppm) ? ppm : 0;
    }

    _style(port) {
        return this._styleFor(this._state(port));
    }

    _styleFor(state) {
        /* Taken by name rather than by port because the front-panel strip has one state the ports
           list never carries: "absent", which is the drawing describing a socket the machine says
           nothing about. */
        if (this.states && Object.prototype.hasOwnProperty.call(this.states, state)) {
            return this.states[state];
        }

        /* A state this widget has not been taught about is not a severity, so it sinks to the
           bottom of the list exactly as it does on the page - which also ranks an unknown state 9
           - and it is printed as it arrived rather than being dressed up as healthy. */
        return {
            rank: 9,
            icon: 'fa-circle',
            fill: 0,
            iconColour: 'text-muted',
            textColour: 'text-muted',
            label: state || ''
        };
    }

    _compare(a, b) {
        const rank = this._style(a).rank - this._style(b).rank;
        if (rank !== 0) {
            return rank;
        }

        /* within one state the dirtier port goes first, so the worst line of the list is the top
           one even when several ports share a verdict */
        const ppm = this._ppm(b) - this._ppm(a);
        if (ppm !== 0) {
            return ppm;
        }

        /* Numeric collation, as the page uses for the same tie: a chassis label is a word with a
           number stuck on the end, and plain string order puts Port10 between Port1 and Port2.
           Eighteen of the twenty ports on the reference machine share one state, so this is the
           comparison that actually decides the order of most of the list. */
        return String(this._label(a)).localeCompare(String(this._label(b)), undefined, {numeric: true});
    }

    _stalenessNotice(status) {
        const generated = Number(status.generated);
        if (!Number.isFinite(generated) || generated <= 0) {
            return '';
        }

        const window_seconds = Number(status.window_seconds) > 0 ? Number(status.window_seconds) : 60;
        const age = (Date.now() / 1000) - generated;

        /* The collector rewrites the file once per window. Five missed windows - and never less
           than five minutes, so a long window does not make this fire on a healthy box - means it
           is no longer running, and a list of stale green ports is worse than no list at all. */
        if (age <= Math.max(300, window_seconds * 5)) {
            return '';
        }

        return String(this.translations.stale || '')
            .replace('%s', this._isolate(new Date(generated * 1000).toLocaleString()));
    }

    /* Formatting */

    _escape(value) {
        /* Port labels come from the chassis table, names and descriptions from config.xml, and the
           module strings straight off the transceiver, so all of it is text somebody else wrote.
           The flex table takes HTML strings, which is why every value goes through here.

           The quotes are escaped along with the angle brackets because several of these values are
           written into a title="..." attribute. Building this out of a detached element and asking
           jQuery for its .html() would leave a quote alone - correct for a text node, and an open
           door once the same string is put inside an attribute. */
        const text = value === undefined || value === null ? '' : String(value);
        const replacements = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};

        return text.replace(/[&<>"']/g, (character) => replacements[character]);
    }

    _isolate(value) {
        /* A tooltip is plain text inside a panel that is right to left in the Arabic GUI, and it
           cannot be given a direction of its own the way a span can. "SFP+ 10G - FINISAR CORP. -
           FTLX8571D3BCL" is three left-to-right runs joined by neutral characters, so the layout
           puts the last one first and the module reads backwards. U+2068 and U+2069 wrap it in an
           isolate that takes its direction from the first letter inside it, which is the same
           thing the page gets from unicode-bidi: plaintext on its .lh-tech spans. */
        const text = value === undefined || value === null ? '' : String(value);

        return text === '' ? '' : `⁨${text}⁩`;
    }

    _speed(mbps) {
        const speed = Number(mbps);
        if (!Number.isFinite(speed) || speed <= 0) {
            return '';
        }

        if (speed >= 1000) {
            /* 2.5G and 5G links exist, so the decimal is kept when it carries something */
            return `${(speed / 1000).toFixed(speed % 1000 === 0 ? 0 : 1)}G`;
        }

        return `${speed}M`;
    }

    _errorRate(ppm) {
        /* The rate over the window, never a counter since boot: error_ppm is what the verdict
           engine measured between two readings, and the cumulative counters in the document are
           left where they are. A port on the reference machine has carried 5,945 input errors
           since boot while being perfectly clean for hours, which is the whole reason this line
           prints a window and not a total.

           A clean port prints nothing at all. Zero is the ordinary reading on a healthy machine,
           and a column of zeroes is exactly the noise this widget is meant not to make. */
        if (!Number.isFinite(ppm) || ppm <= 0) {
            return '';
        }

        /* Above one percent a percentage is what a person reads; below it parts per million keeps
           the small numbers visible. The page draws the line in the same place, and the same
           measurement has to be written the same way in both. */
        if (ppm >= 10000) {
            return `${this._escape((ppm / 10000).toFixed(2))}%`;
        }

        const grouped = String(Math.round(ppm)).replace(/\B(?=(\d{3})+(?!\d))/g, ',');

        return `${this._escape(grouped)} ${this._escape(this.translations.unit_ppm)}`;
    }
}
