# What Link Health cannot tell you

This page exists so you can find out in two minutes that something is impossible, instead of
finding out in an hour that it is missing.

None of the items below are on a roadmap. They are not unimplemented; they are things the
operating system, the driver or the hardware does not expose, and no amount of work inside this
plugin changes that. Where the number does exist somewhere else, the section says where to go
and get it.

Facts marked **measured here** were measured on the one machine this plugin was developed on: an
ex-Sophos XG330 running OPNsense 26.7.4_1 / FreeBSD 15.1, with eighteen `igb` ports and two `ix`
ports. Everything else comes from the FreeBSD driver sources.

---

## No cable test, no distance to fault

If you have used `ethtool --cable-test` on Linux, or the Cable Test button in a managed switch's
web interface, you have seen a NIC report "pair B open at 14 m". That is a real measurement — the
PHY sends a pulse down each pair and times the reflection — and it is genuinely useful.

FreeBSD's `igb` and `ix` drivers expose no interface for it at all. There is no ioctl to call and
no sysctl to read; the capability is simply not wired up in the driver. So this plugin cannot tell
you the length of a cable, which pair is broken, or how far along the run the break is.

**Where the number lives:** in a managed switch at the other end of the cable, under a name like
Cable Diagnostics or Cable Test. That is the only place it exists on this side of the link.

**What this plugin gives you instead:** downshift detection. A port negotiating 100M on a link
whose PHY advertises 1G is almost always a broken pair — the same fault the cable test finds, in
less detail, but visible on every driver and with no downtime.

---

## No readings from the transceiver at the far end

The plugin reads the transceiver seated in *this* firewall. It cannot read the one in the switch,
the server or the media converter at the other end of the fibre.

This matters more than it sounds. Receive power on our side tells you how much light arrived; it
does not tell you how much light the far end transmitted, so a dying laser over there looks like a
dirty connector over here. The two are told apart by reading both ends — and the other end belongs
to another device.

**Where the number lives:** in the switch or host at the far end, which almost certainly displays
its own optics. Read both ends when you are diagnosing a fibre; that comparison is the point.

---

## No transceiver data at all on `em`, `igb` and `igc` — SFP cages included

This is the one that surprises people, so it is worth being blunt about it.

Those drivers implement no `SIOCGI2C`, which is the call that reads a module's EEPROM and its
diagnostic page. Without it there is no module type, no vendor, no part number, no serial number,
no temperature, no voltage, no receive power — nothing. This is a property of the driver, not of
the module and not of this plugin.

It applies to SFP cages driven by those chips exactly as it applies to copper ports. An Intel I210
*Fiber* port with a module seated in it reports no module block whatsoever: verified on `igb16`
and `igb17` of the reference machine (measured here), and confirmed in the driver source.

So on an appliance like this one you will see optics boxes on the two `ix` ports and on nothing
else, including the two ports with a fibre visibly plugged into them. Do not report this as a bug;
report it to Intel.

**What this plugin gives you instead:** those ports still have full per-cause hardware counters
and full link-state and media-ladder information, which is what the verdict is made of anyway. A
bad fibre on an `igb` SFP cage is caught exactly as a bad fibre on an `ix` port is caught — the
fault that started this whole plugin was found by counters, while the optics that *were* readable
insisted everything was fine.

---

## No per-cable verdict behind an internal switch chip

Some appliances present eight or sixteen faceplate jacks that all sit behind one internal switch
chip and reach the CPU as a single MAC. The operating system sees one interface. Every counter it
can read is the sum over all of those jacks, and the link state it reports is the state of the
internal link, not of any cable you can touch.

On such hardware this plugin can judge that port — the one the OS shows — and nothing finer. If
jack 5 of eight is corrupting frames, the verdict names the whole group, because that is all the
information that exists. It cannot point at jack 5.

**How to tell whether this applies to you:** count the physical jacks on the front of the box and
compare with the number of interfaces that have a media ladder. `ifconfig -l` is not the list you
want — it includes `lo0`, `enc0`, `pflog0`, `pfsync0`, the bridges and every VPN interface. This
is, and the root shell is csh, so it is wrapped in `sh`:

```sh
sh -c 'for i in $(ifconfig -l); do ifconfig -m $i | grep -q "supported media" && echo $i; done' | wc -l
```

If the faceplate has more jacks than that, the extra jacks are behind a switch chip. On the
reference machine it prints 20 against 20 jacks, which is why that machine is judged per cable.

**Where the number lives:** the internal switch chip usually has no management interface at all.
If yours does, that is where per-jack counters would be.

---

## No photograph of your appliance, and a drawing is only a claim

The front panel on the page is **drawn**, from a handful of numbers and short strings: how many
sockets, in what order, in which bay, and what is printed beside each one. **No vendor image is
shipped with this plugin, and none is fetched at run time** — there is no image file anywhere in
the package, and the firewall makes no request to anybody to get one.

That is a licensing conclusion, not a design preference. A vendor's photograph or product render
is that vendor's copyrighted work and a plugin under a BSD licence cannot redistribute it. The one
bulk source that looks like an answer, the NetBox device-type library, puts a CC0 label over
images that are demonstrably vendor marketing art, logos included; CC0 waives only what the
uploader owned and explicitly declines to clear anyone else's rights, that project records no
provenance for any of its images, and it has no entry for this appliance in any case. Port counts,
order, bays and printed labels are facts, and facts are not owned — so the panel is drawn from
those, never traced from an image, and never given a logo.

The consequence is the part worth knowing: **a drawn layout is a claim until somebody confirms
it.** Nothing in the firmware carries the silk-screen names — SMBIOS type 41, the one table that
could, lists the onboard video and a single onboard LAN on the reference machine and none of its
twenty ports (measured here) — so the arrangement in the file came from a person who looked, or
from a person who reasoned, and the file records which. A layout that nobody has checked says so
on the page in words, and it keeps saying so until somebody presses identify, watches a socket
blink and reports what they saw.

So: if the drawing puts a socket in the wrong place, it is wrong about a *position*. It cannot be
wrong about *which port an alert is for*. The layout file names bays and printed labels and never
an interface; the binding from a printed label to `igb2` lives in `chassis.json` and nowhere else.
A bad drawing misleads your eye for as long as it takes to press the button, and that is the worst
it can do.

**What to do about it:** press identify on a socket, see which one blinks, and send the answer in.
[contributing-chassis.md](contributing-chassis.md) says what to write down.

---

## No identification LED on every driver

Identify works through `led(4)`. A driver that supports it registers one `/dev/led/<interface>`
node per port, and writing a blink pattern to that node drives the NIC's identification LED
through the controller's own LED register. A driver that does not register the node offers nothing
to write to, and that port simply has no identify button — there is no fallback, because the only
other way to make a port noticeable is to disturb its traffic, which this plugin will not do to
find out where a socket is.

FreeBSD's `e1000` driver (`em`, `igb`, `igc`) and its `ixgbe` driver (`ix`) both register the
node. On the reference machine all twenty ports have one — `igb0` through `igb17` and `ix0`,
`ix1` (measured here). Other drivers vary, and this has not been tested on any of them: check your
own hardware with

```sh
ls /dev/led
```

Anything in that list that looks like an interface name can be identified; anything missing from
it cannot. The list also holds LEDs that are nothing to do with networking — the reference machine
carries `ahci0.0.fault` and `ahci0.0.locate` for a disk bay — and this plugin only ever writes to
a node whose name is one of its own physical ports.

**Two things it is not.** It is not a link test: the blink is driven by a register write and says
nothing about the cable. And it is not disruptive: the link does not drop, a transfer in progress
keeps going, and it works on a port with no cable in it at all (measured here, on all three
counts). One port blinks at a time and the LED is always handed back when the time is up.

---

## No verdict from packet loss

Ping loss is not evidence of a bad cable, and this plugin will not treat it as such.

The reason is easy to demonstrate. Flood the switch at the far end of one of these links with
pings and it answers with about 0.6% loss while the port's frame error counters stay at exactly
zero (measured here). Nothing is wrong
with that cable. What is happening is the switch's management CPU deprioritising ICMP addressed to
itself so it can keep forwarding — the device protecting itself, which is correct behaviour.

The reverse also happens: the real fault on the reference machine corrupted 3.81% of received
frames while ping to the same neighbour stayed comfortable, because TCP and the upper layers were
quietly absorbing the retransmissions.

Loss measured by ping tells you about a path, a load and a remote CPU, all at once. The frame
error counter on the port tells you about one cable. Only the second one is a verdict.

**Where loss is still useful:** the on-demand load test uses a flood of ICMP deliberately — but it
reads the *counter delta* the flood produces, and reports the loss figure alongside it as context.
The loss number never decides the verdict by itself.

---

## And a few smaller ones

- **A quiet port is judged slowly, not never.** A window carrying fewer than 1000 received frames
  (the default) is not thrown away — it is added to the one before it, and the verdict waits until
  there are enough frames to mean something. Until then the state is `idle`, deliberately neither
  green nor red, and the page says how far along the count is. After an hour of that the port is
  judged on whatever it did see and says how little that was. A port that carries almost nothing
  at all never reaches a rate worth trusting whatever you do, which is what the load test is for.
- **Counters from before the plugin started are ignored.** Everything is judged on the change
  over one window, so a port that accumulated errors months ago reads clean today. That is
  intentional: one port on the reference machine has carried 5,945 input errors since boot while
  being perfectly clean for hours (measured here).
- **The first window after a reboot judges nothing.** The counters start again from zero, so
  there is nothing to subtract from; the page says so in words and no mail goes out. The same
  applies to any counter that comes back lower than it was, which means it wrapped or was reset:
  that window is thrown away rather than reported as a spike.
- **Flaps are counted from the system log**, because the kernel announces every link transition
  there with a time of day. If those messages are filtered out of the log, or the port has been
  flapping for longer than the log goes back, the flap count is only as complete as the log is.
  The drivers' own `local_faults` / `remote_faults` are not used for this, for the reason in the
  next bullet.
- **`local_faults` and `remote_faults` are not error counters.** They count link transitions,
  administrative ones included: the two healthy 10G ports on the reference machine carry 997 and
  832 remote faults and 22 and 41 local ones, with nothing wrong on either (measured here). They
  are shown beside the other counters and no rule is built on them.
- **Virtual interfaces are skipped entirely.** `lagg`, `vlan`, `bridge`, the VPN interfaces and
  the rest carry no cable of their own, so there is nothing to judge, and judging them would
  double-count the physical members that do. Look at the members instead.
- **A NIC in a virtual machine has no cable behind it either.** `vtnet`, `vmx`, `hn` and `xn`
  stay on the page — a firewall running as a guest should not be shown an empty one — with link
  state and the driver's error totals, and the page says plainly that there is no cable to
  diagnose. A "bad cable" verdict on a virtio NIC would be a statement about the host's software
  switch, which is not something this plugin can see.
- **Only one appliance has been tested.** The counter maps for every driver other than `igb` and
  `ix` are written from the driver sources and have never been run against the hardware. If a
  driver's tree differs from what the map expects, the port falls back to link state, aggregate
  errors and the media ladder — still useful, just less specific. A report of what your hardware
  shows is welcome.
