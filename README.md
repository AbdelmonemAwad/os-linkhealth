# Link Health for OPNsense

A plugin that watches every physical port on the firewall and tells you **which cable or
transceiver is going bad** — by the name printed on the chassis, not by the driver name the
kernel uses.

I wrote it after spending an evening on a 10G fibre that was quietly corrupting frames. Every
page in the GUI said the link was up at 10G. The transceiver read perfectly healthy: temperature
fine, voltage fine, receive power comfortably inside its own limits, no alarm flag, no warning
flag. The only place the fault existed was in a hardware error counter that nobody looks at,
climbing by a few thousand frames a minute. That is the whole reason this plugin exists, and it
is why the verdict is built on the *change* in error counters over a window and never on optical
telemetry.

Tested on OPNsense 26.7.4_1 / FreeBSD 15.1, on one machine: an ex-Sophos XG330 with eighteen
`igb` ports and two `ix` ports. Everything below that says **measured here** was measured on that
machine. Everything else comes from the FreeBSD driver sources, and is marked where it matters.

## What it does

- Reads the hardware error counters of every physical port once a minute and judges each port on
  the **delta over the window**, never on the cumulative value. One port on the reference machine
  has carried 5,945 input errors since boot while being completely clean for hours (measured
  here); a tool that reads the raw counter paints that port red forever.
- Notices **downshift**: a port negotiating below the fastest rung its own PHY advertises. This is
  the classic symptom of a broken pair in a copper cable, and it is readable on every driver,
  including the ones with no error counters at all.
- Names the port the way the chassis does. An alert says `PortA3`, not `igb2`.
- **Draws the front of the appliance** — one socket per socket, in the order and the bays the
  layout file records, each coloured with its verdict — so a fault has a position on the box and
  not only a name in a list. It is a drawing and not a photograph, for the licensing reason given
  below; and an arrangement nobody has yet checked against the metal is a claim, so until
  somebody has watched a socket blink the page says so in as many words.
- **Blinks a socket on command**, so the printed label and the interface name can be tied together
  by looking at the machine rather than by trusting a table.
- Shows the transceiver's identity and readings where the driver provides them — as
  corroboration, never as the verdict.
- Mails about a fault once, not once a minute, and mails again when the port recovers.
- Offers a "test now" button for ports that are too quiet to judge.

## What it does not do

Please read **[docs/limits.md](docs/limits.md)** before you expect something from it. The short
version: no cable length or distance-to-fault, no readings from the transceiver at the far end,
no transceiver readings at all on `em`/`igb`/`igc` ports even when they drive an SFP cage, no
per-cable verdict on appliances that put many jacks behind one internal switch chip, no photograph
of your appliance, and no verdict derived from packet loss.

It also does not graph anything over time and does not identify neighbours. Both jobs already
have good tools on OPNsense, and this plugin leaves them alone.

## The fault that produced this plugin

A 10G fibre run between the firewall and a switch, both ends lit, link `active`, media
`10Gbase-SR`, full duplex. Measured here:

| What was read | What it said |
| --- | --- |
| Link state | active, 10Gbase-SR, full duplex |
| Received frames failing CRC | **3.81% of all received frames** — 38,100 ppm |
| Module temperature | 53.8 °C, normal |
| Supply voltage | 3.30 V, normal |
| Receive power | −2.70 dBm, well inside the 10GBASE-SR range |
| Transmit bias | 7.91 mA, normal |
| Module alarm flag | not set |
| Module warning flag | not set |

A plugin that trusted the optics would have reported a healthy link, because by every optical
measure the link *was* healthy. The error counter was the only witness.

What the optics did give was the module's **identity**, and that is what found the fault in
seconds once it was on screen: the two ends of the one fibre held **different modules**. So the
plugin reads optics for identity and for advisory warnings, and builds the verdict out of counter
deltas. It also remembers each module's serial number, so a transceiver that is swapped or
removed is reported as a change rather than as a mystery.

## Verdict states

One port, one window (60 seconds by default). `ppm` is errored frames per million received
frames, over that window alone.

| State | When | What the page says |
| --- | --- | --- |
| `down` | the link is not active | "no cable, or nothing answering at the other end" |
| `disabled` | the port is administratively down | "port is switched off in the configuration" |
| `idle` | the first window for the port, or it is still gathering frames towards `min_frames` (default 1000), or an hour of gathering did not reach it | "measuring: 84 of 1,000 frames so far, over 3 min" |
| `ok` | no rule fired | "clean — 51,092 frames, no errors" |
| `watch` | 10 ppm or more | "a few frames are being corrupted (14 per million)" |
| `warn` | 100 ppm or more, **or** a downshift, **or** collisions on a full-duplex link, **or** 3 or more flaps in an hour | "negotiated 100 Mbit/s where the port supports 1 Gbit/s — usually a broken pair in the cable" |
| `fail` | 1000 ppm or more (0.1%), **or** collisions above the duplex threshold, **or** 10 or more flaps in an hour | "3.81% of received frames were corrupted (CRC errors: 38,142)" |

`idle` is deliberately neither green nor red. A port with no traffic has not been proved good and
has not been proved bad, and saying otherwise is the fastest way to make the page worthless. That
is what the load test is for.

A small window is not thrown away, though — it is added to the one before it, and the verdict
waits until there are enough frames to mean something. This machine has gigabit ports carrying a
couple of hundred frames a minute, because nearly everything goes over the 10G link, and judged
one minute at a time they would read "not enough traffic" for ever and never be judged at all. So
a busy port is still judged every minute, since one minute already clears the bar, and a quiet one
is judged more slowly rather than not at all. After an hour of gathering
(`max_accumulate_seconds`) the port is judged on whatever it did see, and the page says how little
that was.

A port whose link goes away is `down`, not `fail`. It may well be somebody unplugging a laptop, a
switch rebooting, or an ISP doing whatever ISPs do at four in the morning, and a firewall that
mails about every dark port teaches you to delete its mail unread. A link that keeps going away
*is* judged — that is the flap rule above — and the frames that did arrive are judged whatever the
link does afterwards.

The thresholds sit far below the real fault (38,100 ppm) and well above counter noise: a clean
port on the reference machine measures exactly 0 (measured here).

Optics can only add an **advisory reason** to a verdict, never a state of their own: receive power
outside the IEEE range for the media type — 10GBASE-SR, -LR and -ER, 1000BASE-SX and -LX,
25GBASE-SR, with a deliberately wide fallback for anything else — or within 2 dB of its floor,
module temperature above 70 °C and again above 80 °C, supply voltage outside 3.1–3.5 V, a module
whose serial number changed since the last poll, or a module that vanished. The module's own alarm
thresholds are read but **clamped to the IEEE range**, because vendors set them far looser than the
standard — one module on the reference machine declares its low-power alarm 6.2 dB below the spec
floor (measured here).

Nothing here ever alerts on a dark port, on counters accumulated before the plugin started, on an
ISP-side WAN link change, or on ICMP loss.

## What a port can tell you

Support is detected **per port**, as seven independent flags. There are no tiers; a "tier" would
only be a label.

| Flag | Meaning | How it is detected |
| --- | --- | --- |
| `LINK` | link state is readable | always |
| `NETSTAT` | aggregate in/out error counters | always (`netstat -i -b -n -W`, read once for the whole machine) |
| `MEDIA_LADDER` | the list of media the PHY advertises, which is what makes downshift detectable | `ifconfig -m` printed a `supported media` block |
| `COUNTERS` | per-cause hardware counters (CRC, alignment, symbol, length, …) | the driver is in `drivers.json` **and** one probe OID answers |
| `IDENTIFY_LED` | this socket's own LED can be made to blink, which is what turns a drawn layout from a claim into something somebody has checked | `/dev/led/<interface>` exists, which is the driver's own answer and the whole test |
| `OPTICS_INVENTORY` | module type, vendor, part number, serial number | `ifconfig -v` printed a `plugged:` line |
| `OPTICS_DOM` | temperature, voltage, receive power, transmit bias | `ifconfig -v` also printed a `module temperature:` line |

Two rules follow, and they are worth knowing before you go looking for a missing box on the page:

- **One algorithm runs at every level.** `NETSTAT` alone is enough to raise "this port is
  corrupting frames". `COUNTERS` only adds *why* — CRC against alignment against length.
- **A panel appears if and only if its flag is set.** A port with no optics shows no optics box,
  not an empty one.

### How support varies by driver

The rows below are named the way `ifconfig -l` names your ports — by the prefix of the interface
name, which is also how `drivers.json` is keyed. That is not always the module name: FreeBSD's
`mlx5en` gives you `mce0`, and `mlx4en` gives you `mlxen0`.

| Interface | Per-cause counters | Transceiver data | Always available |
| --- | --- | --- | --- |
| `em`, `lem`, `igb`, `igc` | yes — `mac_stats.{crc_errs, alignment_errs, symbol_errors, sequence_errors, recv_length_errors, recv_errs, …}` | **none, ever** — including their SFP cages | link state, aggregate errors, media ladder |
| `ix`, `ixv` | yes — different names: `mac_stats.{crc_errs, byte_errs, ill_errs, rec_len_errs, rx_errs, short_discards, …}` | full, when a module with DDM is seated | link state, aggregate errors, media ladder |
| `ixl`, `iavf`, `ice`, `cxgbe`, `bnxt`, `mce`, `mlxen` | yes — each with its own tree, and each of those trees written from the driver source rather than from a machine | full (these drivers implement `SIOCGI2C`) | link state, aggregate errors, media ladder |
| `bge`, `bce` | yes — CamelCase names, two levels deep | none | link state, aggregate errors, media ladder |
| `re`, `rge`, `axgbe`, `qlnxe`, USB adapters (`ue`, `axge`, `ure`), most consumer NICs | none | none | link state, aggregate errors, media ladder |
| anything not in `drivers.json` | none | only if `ifconfig -v` prints the block | link state, aggregate errors, media ladder |
| guest NICs: `vtnet`, `vmx`, `hn`, `xn` | none | none | link state and aggregate errors — kept on the page, but there is no cable behind them to diagnose |
| virtual: `lagg`, `vlan`, `bridge`, `wg`, `ovpn`, `lo`, … | not applicable | not applicable | skipped entirely — they carry no cable of their own, and judging them would double-count the members that do |

The two optics columns are decided by what `ifconfig -v` prints, not by the counter map: a driver
`drivers.json` has never heard of still gets its transceiver panel if it prints a `plugged:` line,
and a driver listed there gets none if it does not.

The drivers do not share a vocabulary. `igb` and `ix` have exactly one error counter name in
common — `crc_errs` — and nothing else; `dev.igb.0.mac_stats.byte_errs` is simply an unknown OID
(measured here). That is why the counter map is a data file keyed by driver, holding the full
relative path of each counter, and not a list of names assumed to be universal.

Each counter also carries a class, because not every counter that increments is damage:

- **cable** — CRC, alignment, symbol, illegal-byte, runt, jabber, fragment. A physical problem,
  and the only class the error rate is built from.
- **aggregate** — the driver's own sum of receive errors (`recv_errs`, `rx_errs`). Used only when
  the driver offers no cable counter at all, and never added on top of one: the sum already
  contains the causes, so adding them would count every error twice.
- **duplex** — collisions: `late_coll`, `excess_coll`, `collision_count`, `coll_ext_errs`. On a
  link that negotiated full duplex these mean the two ends disagree about duplex, which is judged
  on its own much stricter threshold rather than mixed into the error rate.
- **flap** — `local_faults`, `remote_faults`. These count link transitions, administrative ones
  included: the two healthy 10G ports on the reference machine carry 997 and 832 remote faults,
  and 22 and 41 local ones, with nothing wrong on either (measured here). They are reported, never
  alerted on the way CRC is.
- **load** — `missed_packets`, `recv_no_buff`, `short_discards`. The host could not keep up. That
  is a capacity story, not a cable story.
- **ignore** — counted by the hardware, meaningless here. `checksum_errs` is the example: a
  provably clean port carries 15,973 of them (measured here).

The flow-control counters (`xon_*`, `xoff_*`) are simply not in the map. They say the link is busy
enough to ask the other end to pause, which is neither damage nor a fault worth a line on a page.

### The same appliance has both kinds of port

This is the normal case, not an edge case. The reference machine has twenty physical ports:

- `ix0`, `ix1` — 10G SFP+, full per-cause counters **and** full transceiver telemetry.
- `igb0`–`igb15` — copper, full per-cause counters, no transceiver anything (there is none).
- `igb16`, `igb17` — **1G SFP cages driven by `igb`**: physically optical, with a module seated,
  and still no transceiver data whatsoever, because the driver implements no way to read it
  (measured here, and confirmed in the driver source).

So on one page you will see optics boxes on two ports, no optics box on the other eighteen, and
two of those eighteen will be ports you can see a fibre plugged into. That is expected. The
verdict for those two cages is built from counters and link state, exactly as it is for copper,
and it is just as capable of catching a bad fibre — the fault described above was caught that way.

## The page

Screenshots are not included yet, so here it is in words.

**Interfaces → Link Health** lists every physical port, in chassis order, grouped by bay — so on
the reference machine the eight ports of the FleXi module come first under "FleXi module, bay A",
then "faceplate", then "faceplate, 1G SFP" for the two cages, then "faceplate, 10G SFP+". The bay
names are whatever `chassis.json` says for your appliance; those four are what the shipped XG330
entry says. Each port shows its
chassis label (`PortA3`), the friendly name it has in the firewall's configuration
(`LAN_Port9_SFP_1G`), and the interface name (`igb2`), because you need all three to talk about
the same port with a switch on the other side.

Each port carries its verdict as a short coloured state with a sentence under it, in words rather
than numbers first: "3.8% of received frames failed CRC" is the headline, the ppm figure is the
detail. Ports whose state has just changed are the ones you want to see, and `idle` ports are
visibly neither good nor bad.

Opening a port shows what that port actually has, and nothing it does not:

- the link line — media, duplex, negotiated speed, the fastest speed the PHY advertises, and
  whether that is a downshift;
- the window — frames in and out, errors in and out, and the error rate in ppm;
- the per-cause counters, if the driver has them;
- the transceiver, if the driver can read it: type, vendor, part number, serial number, date, and
  then temperature, voltage, receive power, transmit bias, and transmit power on the modules that
  report it — the Finisar module in the reference machine reports bias but no transmit power
  (measured here), which is ordinary and not a fault;
- link flaps counted over the last hour;
- which neighbours are reachable through that port, taken from the bridge's MAC address table
  rather than from the ARP table — on a bridged firewall every host appears to be "on bridge0",
  and the per-port truth lives only in the bridge table (measured here);
- a **test now** button, for ports too quiet to judge, and an **identify** button that blinks the
  socket so you can read the label printed beside it.

There is also a dashboard widget: the same verdicts in one compact list, under a strip of the
front panel — one small block per socket, in layout order, coloured the same way the list below it
is. The strip costs the widget one line of height and one extra read when the dashboard is opened;
a layout does not change while a firewall is running, so it is read once and kept. On an appliance
with no layout there is simply no strip, and nothing says so.

The GUI on the reference machine is used in **Arabic, right to left**. Nothing on the page is
positioned with a hard-coded left offset, and every visible string goes through the framework's
translation function, so the existing translation pipeline carries it like any other page.

## The front panel, and why it is drawn rather than photographed

The page draws the front of the appliance from its layout: how many sockets, in what order, in
which bay, and what is printed beside each one. It comes out as inline SVG generated at render
time — a few kilobytes of markup, no image files, nothing to ship and nothing to license. Each
socket is coloured with its verdict and carries its printed label, so "PortA3 is failing" is a
place on the box rather than a line in a table.

The reason it is a drawing takes two sentences. A vendor's photograph or product render is that
vendor's copyrighted work and a plugin under a BSD licence cannot redistribute it, and the one
bulk source that looks like an answer — the NetBox device-type library — puts a CC0 label over
images that are plainly vendor marketing art, complete with the vendors' logos, records no
provenance for any of them, and has no entry for this appliance in any case. How many sockets a
device has, in what order, in which bay, and what is printed beside them are facts, and facts are
not owned — so the panel is drawn from those, never traced from an image, and never given a logo.

The drawing is **not mirrored** in the Arabic layout, and that is deliberate rather than an
oversight. An inline SVG's geometry does not turn with the page — a rectangle at x=0 stays against
the same edge — while text anchors do, which is measured rather than assumed; so the drawing pins
its own direction, every label is centred, only the block's placement on the page follows the
language, and the whole thing is captioned "as you face the front of the appliance". The metal
does not mirror when you change the GUI language, so neither does the picture of it.

That raises the fair question: if you drew it, how do you know it is right? You do not, and the
plugin does not pretend otherwise. A layout nobody has checked against the metal says so on the
page, in those words, and stays that way until somebody checks it. The check is one button.

### Identify: making a socket blink

Every port whose driver offers it gets an **identify** button. Press it and that socket's own LED
blinks slowly — half a second lit, half a second dark, which cannot be mistaken for traffic — for
30 seconds by default and 300 at most, and you can stop it early. Three things make this the right
mechanism, all three verified on the reference machine:

- **It touches nothing that carries traffic.** The link does not drop and a transfer in progress
  keeps going. It drives the NIC's identification LED through the controller's own LED register,
  by way of `led(4)`, and nothing about the interface changes.
- **It works on a port with no cable in it** — which is exactly the port whose name you want,
  because you are about to plug something into it.
- **The LED is always handed back.** Writing `0` to the node returns it to the driver, and that
  write happens whether the blink finished, was stopped, or failed, so nothing is left blinking to
  lie to the next person who walks in.

One port blinks at a time, enforced by a lock: two blinking sockets would defeat the whole point.
Asking for a second one while the first is running is answered with "busy", not with a queue.

This is also the honest answer to "which socket is `igb10`?". Nothing in the firmware carries the
silk-screen names — SMBIOS type 41, the one table that could, lists the onboard video and a single
onboard LAN on this appliance and none of its twenty ports (measured here) — so every table of
printed names, including the one shipped here, is a claim until somebody looks at the metal. This
turns looking into one button, and a confirmed layout is one somebody has watched blink.

`led(4)` support is a driver property, not a plugin one: `em`, `igb`, `igc` and `ix` register a
node per port, and on the reference machine all twenty ports have one (measured here). Other
drivers have not been tested and may register nothing, in which case those ports simply have no
button — `ls /dev/led` on your own firewall is the whole answer, and
[docs/limits.md](docs/limits.md) says what to make of it.

## The load test

A port with no traffic yields no verdict, so the page offers "test now". It floods ICMP at a
neighbour of your choosing with jumbo payloads and then diffs the counters — the same method that
proved both the fault and the fix on the reference machine (measured here).

It is deliberately fenced in: one test at a time on the whole firewall, enforced by a lock the
worker holds; a hard cap on both the packet count and the duration (20,000 packets and 180
seconds by default, with an 8,972-byte payload where the MTU allows a jumbo frame and 1,472 bytes
where it does not); and the target has to be a neighbour already in the bridge table for that
port, so a typo cannot aim it at the internet. The
GUI says plainly, before you press it, that the test saturates the link while it runs. Do not
start one on a link carrying something you care about.

## Alerts

Mail goes out directly over SMTP, reusing the mail settings already configured for Monit — the
same approach my other plugin uses and which is proven on this machine (measured here). Monit
itself is not the transport: its alerts are plain text truncated at 512 bytes, on a 120-second
cycle, with no per-port cooldown. A `check program` script ships for people who want Monit to
watch as well; it is **off by default**, because running both mails every fault twice.

What keeps the mailbox usable:

- only `warn` and `fail` are mailed at all — a dark port, a disabled port and a port too quiet to
  judge are shown on the page and never put in your inbox;
- a state has to persist for **two consecutive windows** before it alerts, so one noisy minute is
  not a mail;
- **one mail per port per cooldown** (6 hours by default), except that a port getting worse —
  `warn` to `fail` — is mailed immediately rather than waiting the cooldown out;
- a **recovery mail** when a port is clean again, or when it goes so quiet that it can no longer
  be judged, because either way the fault you were told about is no longer being measured;
- the **first window after a reboot only establishes the baseline** and never alerts. A reboot is
  detected from `kern.boottime`, so counters that climb past their previous values after a restart
  do not look like an error storm.

The mail carries the chassis label, the configured name and the interface name of every port it
mentions, and the hosts reachable behind it, so it can be acted on from a phone without opening
the GUI. When the firewall's language is a right-to-left one — Arabic here — the message is laid
out right to left as well, because a mail that reads backwards is a mail nobody reads.

## Installation

The plugin is not in the official repository. To install it by hand, copy this repository to the
firewall and run the installer as root:

```sh
fetch -o /tmp/linkhealth.tar.gz https://github.com/AbdelmonemAwad/os-linkhealth/archive/refs/heads/main.tar.gz
tar -xzf /tmp/linkhealth.tar.gz -C /root
sh /root/os-linkhealth-main/install/install.sh
```

Then open **Interfaces → Link Health**. Give it two minutes before you judge the page: the first
run only records a baseline, and a verdict needs a window to compare against.

The layout of this repository matches a plugin directory in
[opnsense/plugins](https://github.com/opnsense/plugins), so `Makefile` and `pkg-descr` are only
used when it is built as a package there.

### Removing it

```sh
sh /root/os-linkhealth-main/install/uninstall.sh
```

Settings stay in `config.xml` and the state in `/var/db/linkhealth`, so a reinstall picks up where
it left off.

## How it works

- `src/opnsense/scripts/linkhealth/` holds the collector, the verdict engine, the alerting and the
  load test. Cron runs the collector every minute from `/usr/local/etc/cron.d/linkhealth`; all the
  scheduling logic lives in the script.
- The whole sweep is three calls for the ports themselves: `ifconfig -vm` for link, media ladder,
  description, driver name and the SFP block; one `sysctl dev.<tree>` per driver tree that is
  actually present, for the counters; and `netstat -i -b -n -W` for the aggregates. One more,
  `ifconfig <bridge> addr`, reads the bridge's MAC table so a port can be described by what sits
  behind it. `-v` and `-m` are disjoint — `-v` prints the SFP block but no media ladder, `-m`
  prints the ladder but no SFP block — so both are needed (measured here).
- The aggregates are read from netstat's text output rather than from `--libxo json`, and the
  reason is narrow enough to be worth stating exactly: add `-d` and the JSON object for an
  interface carries the key `dropped-packets` **twice** — once for input drops and once for
  output — and a standard JSON parser keeps only the last of them without a word (measured here;
  without `-d` the JSON parses cleanly). Text output has no such trap and has not changed shape in
  years.
- It is cheap. `ifconfig -vm` over 20 ports takes about 0.4 s, the sysctl sweep of both driver
  trees well under a tenth of a second, and a run every minute costs about 0.64% of one core on an
  i5-6500 (measured here).
- Everything any consumer reads — the GUI, the widget, the alerting, the exporter — comes from
  `/var/db/linkhealth/status.json` (root:wheel, 0640) and from nothing else. The API serves that
  file verbatim. Beside it, `baseline.json` holds the previous counters and the boot time,
  `alerts.json` the per-port cooldown and last state, and `history.json` a rolling two days of
  error rate and link speed, one sample every five minutes.
- Writes are throttled on purpose. `status.json` is a few kilobytes and is rewritten each cycle;
  `history.json` is appended at most every five minutes, and never grows, because it keeps the
  last 576 samples and drops the rest. The reference machine's SSD has 31% of its write endurance
  already consumed (measured here), and a monitoring plugin has no business writing megabytes a
  day.
- If `/var/tmp/node_exporter/` exists, `linkhealth.prom` is written there each cycle (temp file
  then rename), exporting **cumulative** counters the way Prometheus wants them — a rate belongs
  to the page and to the mail, a counter belongs to the scraper. That directory is the default
  `node_exporter_textfile_dir` of the `os-node_exporter` plugin and it creates the directory
  itself when the service starts, so nothing is exported until you actually run node_exporter
  (measured here).
- Neighbour identity is left to `os-lldpd` if it is installed; this plugin does not implement LLDP.
  Long-term graphing is left to whatever already collects it. This plugin owns the verdict, not
  the time series.

## Port labels, and adding your appliance

The chassis is identified from `kenv smbios.planar.{maker,product,version}`, falling back to the
`smbios.system.*` field where the board left the planar one empty — on the reference machine both
blocks say `Sophos` / `XG` / `330r2` (measured here) — and the labels come from the `models`
object in `chassis.json`.

The entries describe **groups of ports per bay**, never a flat list, because module bays enumerate
before the faceplate: on the XG330 the FleXi module takes `igb0`–`igb7` while the port printed
`Port1` on the faceplate is `igb8` (measured here).

Unknown hardware falls back to the interface name and still works — you lose the printed labels,
nothing else — and any single port can be renamed by hand in the GUI.

If your appliance is not in the file, adding it is one JSON block and that is the entire
contribution path. A second, optional block in `faceplates.json` says how the sockets are arranged
on the front, which is what the drawn panel needs; a machine with labels and no layout is a
perfectly good contribution, and gets the list without the picture. Both are written up in
**[docs/contributing-chassis.md](docs/contributing-chassis.md)**, with the XG330 entries as the
worked example — including what to say about how much of it you confirmed by watching a socket
blink, which is the part that decides whether the page presents your layout as checked or as a
claim.

## Known limitations

The full list, with the reasoning, is in **[docs/limits.md](docs/limits.md)**. It is worth five
minutes before filing a bug: most of what people expect from a "cable tester" cannot be done from
FreeBSD at all, and the file says exactly which parts and why.

One more that belongs here rather than there: all of the measurements behind the thresholds come
from a single appliance with `igb` and `ix` ports. The counter maps for every other driver are
written from the driver sources and have not been run against real hardware. If you have one of
them, I would like to hear what the page shows.

## License

BSD 2-Clause, the same as OPNsense, and the same header as the rest of my plugins.
