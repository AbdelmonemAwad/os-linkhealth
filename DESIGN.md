# os-linkhealth — design contract

A plugin that watches every physical port on an OPNsense firewall and says **which cable or
transceiver is going bad**, names it the way the chassis names it, and mails about it once.

Everything below is the contract the code is written against. It was derived from measurements on
one machine (an ex-Sophos XG330 running OPNsense 26.7.4_1 / FreeBSD 15.1) and from the FreeBSD
driver sources for hardware we cannot test. Claims are marked **V** (verified on that machine),
**D** (documented in a primary source) or **R** (reported by users). Nothing unmarked is a fact.

---

## 1. What it measures, and what it refuses to promise

The primary signal is the **delta of hardware error counters over a window**, never a cumulative
value. On the reference machine one port has carried 5,945 input errors since boot while being
completely clean for hours (**V**) — a tool that reads the raw counter paints that port red
forever.

The second signal is **downshift**: a port negotiating below the fastest rung its own PHY
advertises. It is the one rich signal available on *every* driver, including those with no error
counters at all (**D**), and it catches the classic broken pair — the owner has a switch port
sitting at 100M on a 1G cable (**R**, from the switch's own UI).

Optical telemetry (DOM) is **corroboration only, never the verdict**. During a real fault on the
reference machine — 3.81% of received frames failing CRC — both transceivers read fully inside
their own limits, with no alarm or warning flag set (**V**). A plugin that trusted DOM would have
reported a healthy link. What DOM *does* give is the module's identity, and that is what would have
found this fault in seconds: the two ends of one fibre held different modules.

**We do not promise:**

- **No TDR, no distance-to-fault.** FreeBSD's `igb`/`ix` expose no cable-test interface, unlike
  Linux's `ethtool --cable-test` (**D**). A switch that offers Cable Test in its own UI is the only
  place that number exists.
- **No far-end optics.** We read our own transceiver, never the one at the other end of the fibre.
- **No optics at all on `em`/`igb`/`igc` ports**, including SFP cages driven by them. `SIOCGI2C` —
  the call that reads a module's EEPROM — is implemented by **iflib** for every iflib driver, but
  `em`/`igb`/`igc` supply no `ifdi_i2c_req` method behind it, so the call is answered by iflib's
  own `null_i2c_req` and returns `ENOTSUP` before it ever reaches the cage (**D**:
  `sys/net/ifdi_if.m`, and `sys/dev/e1000/if_em.c` contains the string `i2c` zero times).
  Measured here with the box's own ioctl number: every one of the eighteen `igb` ports returns
  errno 45, I350 copper and I210 Fiber alike, while both `ix` ports return live EEPROM bytes
  (**V**). So it is a property of **those drivers on FreeBSD**, not of the I210, not of the cage
  and not of the module — and it is also why this plugin cannot tell you whether a module is
  seated in such a cage at all.
- **No per-cable verdict behind an internal switch chip.** Appliances that present many faceplate
  jacks through one MAC can only be judged as one port.
- **No verdict from packet loss alone.** The owner's switch answers floods of pings with 0.6% loss
  and zero frame errors — that is its CPU protecting itself, not a bad cable (**V**).

## 2. Capabilities, not tiers

Capability is detected **per port** as seven independent flags. A "tier" is only a label we print.

| Flag | Meaning | How it is detected |
|---|---|---|
| `LINK` | link state is readable | always true |
| `NETSTAT` | aggregate in/out error counters | always true (`netstat -I <if> -b`) |
| `MEDIA_LADDER` | the list of media the PHY advertises | `ifconfig -m` printed a `supported media` block |
| `COUNTERS` | per-cause hardware counters | the driver appears in `drivers.json` **and** one probe OID answers |
| `OPTICS_INVENTORY` | module type/vendor/PN/SN | `ifconfig -v` printed a `plugged:` line |
| `OPTICS_DOM` | temperature, voltage, power, bias | `ifconfig -v` also printed a `module temperature:` line |
| `IDENTIFY_LED` | a light on this socket can be made to blink | `/dev/led/<if>` exists **and** no verified chassis group says the light is not wired |

Rules that follow from this:

- One alerting algorithm runs at every level. `NETSTAT` alone is enough to raise "this port is
  corrupting frames"; `COUNTERS` only adds *why* (CRC vs alignment vs length).
- A panel is rendered **if and only if** its flag is set. A port with no optics shows no optics box,
  not an empty one.
- Unknown drivers fall through to `LINK`+`NETSTAT`+`MEDIA_LADDER` and are still useful.

### 2b. Two ways to point at a socket, and why one flag is not enough

`IDENTIFY_LED` is the only flag whose probe can pass while the thing it promises does not happen,
and that is not a detail — it is the case we met.

**V, 2026-09-21, reference appliance.** `/dev/led/ix0`, `/dev/led/ix1`, `/dev/led/igb16` and
`/dev/led/igb17` all exist, all accept `echo 1` and a pattern like `f0` with no error, and **no
light comes on**. The copper faceplate ports on the same machine blink correctly. The cage lights
on this board are not wired to the controller pins the driver drives, and nothing in any interface
can be asked about that: the node's existence says the *driver* offered one, never that the *board*
connected one.

So the chassis table carries the answer, under the same discipline as its labels. A group may say
`"identify_led": false` with an `identify_led_note`, believed only when that group's `verify` block
matches the card actually present — a table cannot make claims about a card it does not describe.
Where it says so, the port does not advertise `IDENTIFY_LED` and carries the reason instead, and
the page prints the reason where the button would have been. A dead button teaches the wrong thing
about what this page knows.

That leaves the socket still needing to be found, so there is a second way, on any port that has a
link and something visible behind it: **beat the activity light**. One second of ~500 small pings,
one second of silence, repeated — a rhythm ordinary traffic does not have. It is about a quarter of
a megabit, three orders of magnitude below what the load test deliberately does, and it takes the
same lock as the blink so two sockets can never identify themselves at once.

It has its own limit, and the limit is physical: an activity LED stretches its pulses, so a port
already carrying enough traffic shows a continuously lit light that no added traffic can change.
**V:** captured with `tcpdump` on this machine, `ix1` ran at 7,230–19,313 packets/s with a longest
inter-packet gap of 20 ms and **no quiet interval at all** at 200 ms or above — the "second of
silence" this feature is named for does not exist on that port, and the burst adds about a thousand
packets on top of nineteen thousand. On `ix0`, measured in the same minutes, 100 packets/s with a
longest gap of 1.05 s and quiet for 300 ms or more over half the time: that is where the beat
reads. A beat offered on a saturated port is the same failure as a dead button.

**Not yet implemented:** `can_flicker()` asks only for an active link and a known neighbour, so the
button is still offered on a port where it cannot work. The gate must not be built on `netstat`
counters — on `ix` the `if_data` counters are refreshed by the iflib admin task twice a second, so
a 108 ms sample of a saturated `ix1` reported it 78% idle (**V**). The per-queue driver counters
(`dev.<drv>.<unit>.queue*.rx_packets`) move per packet and are what the gate must read.

Where neither light can work, the honest fallback is the one the page already names: pull the cable
and watch which row goes down.

### Discovery calls (the whole sweep)

| Call | Gives | Cost on 20 ports (**V**) |
|---|---|---|
| `ifconfig -vm` | link, media, **supported media ladder**, description, driver name, SFP block | 0.39–0.41 s |
| `sysctl dev.<tree>` once per driver tree | every per-port counter | 0.11 s |
| `netstat -i -b -n -W` (text, not JSON) | Ipkts/Ierrs/Opkts/Oerrs/bytes | negligible |

`-v` and `-m` are disjoint: `-v` prints the SFP block but no media ladder, `-m` prints the ladder
but no SFP block (**V**). Both are required. `netstat --libxo json` is **not** usable: it emits two
keys named `dropped-packets` in one object, so a standard parser silently drops one of them (**V**).

A one-minute sweep costs about 0.64% of one core on an i5-6500 (**V**).

## 3. Per-driver counter maps (`drivers.json`)

The drivers do **not** share a vocabulary. `igb` and `ix` have exactly one error counter name in
common — `crc_errs` — and nothing else (**V**: `sysctl dev.igb.0.mac_stats.byte_errs` → unknown
oid). The map is a versioned data file keyed by driver, storing the **full relative path** of each
counter plus its severity class, because the trees differ in shape as well as in names.

| Driver | Counters | Optics |
|---|---|---|
| `em`, `igb`, `igc` | full, `mac_stats.{crc_errs, alignment_errs, symbol_errors, sequence_errors, recv_length_errors, recv_errs, missed_packets, …}` | none, ever |
| `ix` | full, different names: `mac_stats.{crc_errs, byte_errs, ill_errs, rec_len_errs, rx_errs, short_discards, …}` | full when a DDM module is seated |
| `ixl`, `ice`, `cxgbe`, `mlx5en`, `mlx4en`, `bnxt`, `axgbe`, `qlnxe` | full, each with its own tree | full (they implement `SIOCGI2C`) |
| `bge` | full, CamelCase, two levels deep | none |
| `re`, `rge`, most consumer NICs | none | none |
| virtual (`vtnet`, `vmx`, `lagg`, `bridge`, `vlan`) | none — skipped entirely | none |

Counter classes, as shipped in `drivers.json`:

- `cable` — CRC, alignment, symbol, illegal-byte, length, jabber, fragment, runt: a physical
  problem, and the only class that decides a verdict.
- `aggregate` — the driver's own sum of receive errors. Used **instead of** the cable counters when
  the hardware offers nothing finer, never added to them, or every error is counted twice.
- `duplex` — collisions. On a link that negotiated full duplex these mean the two ends disagree,
  which is a cabling and negotiation fault, so they are judged on their own much lower bar.
- `flap` — `local_faults`, `remote_faults`: these count **link transitions**, including
  administrative ones. Both healthy ports on the reference machine carry hundreds (**V**). Shown,
  never alerted on the way CRC is.
- `load` — `recv_no_buff`, `missed_packets`, `short_discards`: the host could not keep up. A
  capacity story, not a cable story.
- `ignore` — `checksum_errs` is **not** a fault counter: a provably clean port carries 15,973 of
  them (**V**), and OPNsense carries a bug report of its own about it (opnsense/src#191, from
  FreeBSD bug 222979).

## 4. Verdict rules

Evaluated per port, per window. `frames` = frames received in the window.

| State | Condition | Sentence shown |
|---|---|---|
| `down` | link not active | "link is down" |
| `disabled` | administratively down | "port is disabled" |
| `idle` | `frames < min_frames` (default 1000) | "not enough traffic to judge" — **never green, never red** |
| `ok` | error rate 0 and no other rule fires | "clean" |
| `watch` | rate ≥ 10 ppm | "a few frames are being corrupted (N ppm)" |
| `warn` | rate ≥ 100 ppm, or a downshift, or ≥ 3 flaps in an hour | "PortA3 is negotiating 100M on a 1G link" |
| `fail` | rate ≥ 1000 ppm (0.1%), or ≥ 10 flaps in an hour | "PortA3 — 3.8% of received frames failed CRC" |

A dark port is never a fault. A firewall with eighteen sockets has most of them empty by design,
and a tool that painted them red would be turned off within a day.

The real fault measured 38,100 ppm; a clean port measured 0 (**V**). The thresholds sit far below
the former and well above counter noise.

Optics add **advisory** reasons only. They are carried in the port's reason list and shown in the
GUI, but `_worst()` skips them when it decides the state, so a transceiver can never raise or lower
a verdict on its own: receive power outside the
10GBASE-SR range, module temperature above 70 °C, a module whose serial number changed since the
last poll (someone swapped a transceiver), or a module that vanished. Vendor DDM thresholds are
read from the module but **clamped to the IEEE range**, because vendors set them far looser — one
module on the reference machine declares its low-power alarm 6.2 dB below the spec floor (**V**).

Never alerted on: a dark port, historical counters, an ISP-side WAN link change, ICMP loss.

## 5. Port labelling (`chassis.json`)

The whole point is that an alert says **"PortA3"**, not `igb2`. Detection is
`kenv smbios.planar.{maker,product,version}` — on the reference machine literally `Sophos` / `XG` /
`330r2` (**V**).

A model entry describes **groups**, never a flat list, because module bays enumerate before the
faceplate. On the XG330 the FleXi module takes `igb0`–`igb7` while the printed `Port1` is `igb8`
(**V**, and corroborated by the owner's own interface names).

```json
{
  "version": 1,
  "models": {
    "sophos-xg330": {
      "display": "Sophos XG 330 (rev 2)",
      "verified": true,
      "match": {"maker": "^Sophos$", "product": "^XG$", "version": "^330"},
      "groups": [
        {"driver": "igb", "units": [0, 7],   "label": "PortA%d", "start": 1, "bay": "FleXi module, bay A",
         "verify": {"chip": "I350", "subdevice": "0x0008"}},
        {"driver": "igb", "units": [8, 15],  "label": "Port%d",  "start": 1, "bay": "faceplate",
         "verify": {"chip": "I211"}},
        {"driver": "igb", "units": [16, 17], "label": "Port%d",  "start": 9, "bay": "faceplate, 1G SFP",
         "verify": {"chip": "I210"},
         "note": "I210 Fiber: no transceiver telemetry on this driver"},
        {"driver": "ix",  "units": [0, 1],   "label": "SFP+%d",  "start": 1, "bay": "faceplate, 10G SFP+",
         "verify": {"chip": "X520"}}
      ]
    }
  }
}
```

Every group states what the card under it must be. The label is printed only when the chip the
driver reports - and the PCI subdevice id where it is distinctive - matches (**V**: proven in both
directions on the reference machine, including a deliberately wrong fingerprint that correctly
refused to label the ports). Nothing in firmware carries the silk-screen names: SMBIOS type 41, the
one table that could, lists only the onboard video and a single onboard LAN on this appliance and
none of its twenty ports (**V**). A wrong label is worse than no label.

Unknown hardware falls back to the interface name, and any port can be relabelled by hand in the
GUI. A user with a different appliance contributes one JSON block — that is the whole contribution
path, and it is documented in the README.

The friendly name (`LAN_Port9_SFP_1G`) comes from `/conf/config.xml`, **not** from the kernel's
interface description: after a rename the kernel keeps the old string until the interface is
reconfigured, and on the reference machine the two are currently swapped relative to the config
(**V**).

## 6. Data contract

The collector writes `/var/db/linkhealth/status.json` (root:wheel, 0640) and the API serves it
verbatim. Every consumer — GUI, widget, alerting, exporter — reads this and nothing else.

```json
{
  "generated": 1790054400,
  "boottime": 1790040000,
  "window_seconds": 60,
  "chassis": {"vendor": "Sophos", "model": "XG 330r2", "table": "sophos-xg330", "source": "smbios"},
  "ports": [{
    "if": "ix0",
    "label": "SFP+1",
    "name": "LAN_NAS_10G",
    "confkey": "opt2",
    "bay": "SFP+ 10G",
    "driver": "ix", "unit": 0,
    "chip": "Intel(R) X520 82599ES (SFI/SFP+)",
    "caps": ["LINK", "NETSTAT", "MEDIA_LADDER", "COUNTERS", "OPTICS_INVENTORY", "OPTICS_DOM"],
    "serves": ["192.168.1.10"],
    "link": {"state": "active", "media": "10Gbase-SR", "duplex": "full",
             "speed_mbps": 10000, "max_speed_mbps": 10000, "downshift": false},
    "window": {"rx_frames": 51092, "tx_frames": 50004, "rx_bytes": 0, "tx_bytes": 0,
               "rx_errors": 0, "tx_errors": 0, "error_ppm": 0},
    "counters": {"crc_errs": 0, "byte_errs": 0, "ill_errs": 0},
    "optics": {"present": true, "type": "10G Base-SR (LC)", "vendor": "FINISAR CORP.",
               "pn": "FTLX8571D3BCL-FC", "sn": "ABC1234", "date": "2018-08-07",
               "temp_c": 53.8, "vcc_v": 3.30, "rx_dbm": -2.70, "tx_bias_ma": 7.91,
               "alarm": false, "warning": false},
    "flaps": {"window_seconds": 3600, "count": 0, "last": null},
    "verdict": {"state": "ok", "since": 1790054400,
                "reasons": [{"code": "clean", "severity": "info", "text": "clean"}]},
    "last_test": {"when": 1790050000, "target": "192.168.1.10", "frames": 50000,
                  "errors": 0, "error_ppm": 0, "loss_pct": 0.0}
  }]
}
```

`serves` is resolved from the bridge address table (`ifconfig bridge0 addr`), **not** from `arp`:
on a bridged firewall every host appears "on bridge0" and the per-port truth lives only in the
bridge's MAC table (**V**).

## 7. State, alerting and schedule

State lives in `/var/db/linkhealth/`: `baseline.json` (previous counters + `kern.boottime`, so a
reboot is detected even when counters climb past their old values), `status.json`, `alerts.json`
(per-port cooldown and last state).

- The collector runs every minute from `/usr/local/etc/cron.d/linkhealth` (7-field crontab format,
  with the user column). All scheduling logic lives in the script, as in os-netreport (**V**).
- Mail is sent **directly** with `smtplib`, reusing the Monit SMTP settings from `config.xml`, the
  way os-netreport already does and which is proven on this box (**V**). Monit is *not* the
  transport: its alerts are plain text truncated at 512 bytes with a 120 s cycle and no per-port
  cooldown. An optional `check program` script ships for people who want Monit to watch too, default
  off, because running both mails every fault twice.
- Anti-storm: a state must persist for two consecutive windows before it alerts; one mail per port
  per cooldown (default 6 h); a recovery mail when a port returns to `ok`; and the first window
  after a reboot only establishes the baseline and never alerts.
- Writes are throttled: `status.json` is rewritten each cycle (a few KB), history is appended at
  most every 5 minutes. The reference machine's SSD has 31% of its write endurance consumed (**V**),
  so this plugin has no business writing megabytes a day.

## 8. On-demand load test

A port with no traffic yields no verdict, so the GUI offers "test now": flood ICMP at a chosen
neighbour with jumbo payloads, then diff the counters — the same method that proved both the fault
and the fix on the reference machine (**V**). Rules: one test at a time, enforced by a lock held by
the **worker** (not the starter, whose lock dies with it); a hard cap on count and duration; the
target must be a neighbour already in the bridge table for that port; and the GUI warns that the
test saturates the link while it runs.

## 9. Integration instead of reinvention

- `/var/tmp/node_exporter/linkhealth.prom` is written each cycle (temp file + rename) when that
  directory exists, exporting **cumulative** counters as Prometheus wants them. The owner already
  has `os-node_exporter` installed (**V**).
- Neighbour identity is left to `os-lldpd` if it is installed; we do not implement LLDP.
- Long-term graphing is left to whatever already collects it. This plugin owns the verdict, not the
  time series.

## 10. Layout

```
Makefile, pkg-descr, README.md, docs/limits.md
install/{install.sh,uninstall.sh}
src/etc/cron.d/linkhealth
src/etc/rc.syshook.d/start/62-linkhealth
src/opnsense/service/conf/actions.d/actions_linkhealth.conf
src/opnsense/scripts/linkhealth/{linkhealth.py,collector.py,verdict.py,alerts.py,loadtest.py,
                                 state.py,exporters.py,chassis.py,drivers.json,chassis.json,
                                 thresholds.json,merge_ui_translations.py,i18n/}
src/opnsense/mvc/app/models/OPNsense/LinkHealth/{LinkHealth.xml,LinkHealth.php,Menu/Menu.xml,ACL/ACL.xml}
src/opnsense/mvc/app/controllers/OPNsense/LinkHealth/{IndexController.php,forms/general.xml,
                                 Api/SettingsController.php,Api/ServiceController.php}
src/opnsense/mvc/app/views/OPNsense/LinkHealth/index.volt
src/opnsense/www/js/widgets/{LinkHealth.js,Metadata/LinkHealth.xml}
```

Menu: **Interfaces → Link Health**, order 235 (next to Overview at 230).
ACL: `ui/linkhealth/*` and `api/linkhealth/*`.

The widget is two files and nothing else: core registers widgets by globbing
`/usr/local/opnsense/www/js/widgets/Metadata/*.xml` (**V**). Shipping them inside the plugin is what
makes them survive a firmware update — the hand-staged copies in `/conf/custom_widgets` on the
reference machine are read by no OPNsense code at all (**V**).

The GUI is used in Arabic with a right-to-left layout on the reference machine, so the view must not
position anything with a hard-coded `left`, and every string goes through `gettext()` so the
existing translation pipeline can carry it.
