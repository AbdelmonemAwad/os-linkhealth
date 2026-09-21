# Adding your appliance: port labels and front panel

The whole point of this plugin is that an alert says **"PortA3"** — the label printed next to the
jack you are about to unplug — instead of `igb2`. That translation lives in one data file,
`chassis.json`, and adding a new appliance to it means writing one JSON block. There is no code to
write and nothing else to touch; that block is the entire contribution path.

There is a second, optional block in a second file, `faceplates.json`, which says how those
sockets are **arranged** on the front of the box. It is what lets the page draw the panel instead
of listing it. Sections 1 to 6 below are the labels and are the part that matters; section 7 is
the drawing, and an appliance with labels and no layout is a perfectly good contribution that gets
the list without the picture.

Until your box is in either file it falls back to interface names. Nothing breaks, the verdicts
are exactly the same, you just have to count jacks yourself.

This page walks through it with the ex-Sophos XG330 as the worked example, because that is the
machine everything here was measured on.

---

## 1. Work out which box this is

Detection uses the motherboard identity the BIOS reports:

```sh
kenv | grep '^smbios.planar'
```

On the reference machine:

```
smbios.planar.location="Default string"
smbios.planar.maker="Sophos"
smbios.planar.product="XG"
smbios.planar.serial="Default string"
smbios.planar.tag="Default string"
smbios.planar.version="330r2"
```

Three of those are used — `maker`, `product` and `version` — and on this board they are literally
`Sophos`, `XG` and `330r2`. Note how little the vendor bothered to fill in: everything else says
`Default string`. That is normal on appliance hardware, and it is why the model number usually
hides in `version` rather than in `product`.

Where the board leaves one of the three empty, the same field from the system block is used
instead, so it is worth reading both:

```sh
kenv | grep -E '^smbios\.(planar|system)\.(maker|product|version)'
```

On the reference machine the two blocks agree exactly — `Sophos` / `XG` / `330r2` in both — which
is the ordinary case; they differ mostly on boards sold under one name and built under another.

Run it on your own box and write the three values down. If all three say `Default string` or
something equally useless, stop here and say so in the issue — an appliance that does not identify
itself needs a different approach, and I would rather know about it than guess. Note that
`Default string` is a *value*, not an empty field: nothing falls back for it, and matching on it
would claim every other appliance whose vendor was equally careless.

## 2. List the physical ports

A physical port is one that has a media ladder; virtual interfaces do not. The root shell on
OPNsense is csh, so run any loop under `sh`:

```sh
sh -c 'for i in $(ifconfig -l); do
    ifconfig -m $i | grep -q "supported media" && printf "%-8s %s\n" "$i" "$(ifconfig $i | sed -n "s/.*status: //p")"
done'
```

On the reference machine that prints `igb0` through `igb17` and `ix0`, `ix1` — twenty ports — and
leaves out `lo0`, `enc0`, `pflog0`, `pfsync0`, `bridge0` and the WireGuard interfaces, which is
exactly right: they carry no cable (measured on the reference machine; the rule that virtual
interfaces print no `supported media` block held for every one of them).

The driver is the interface name with the trailing digits removed, and the unit is those digits:
`igb8` is driver `igb`, unit 8. Both go into the entry.

## 3. Map printed labels to unit numbers

This is the part nobody can do for you, and the part that is worth doing carefully, because
guessing it produces alerts that point at the wrong cable — which is worse than no labels at all.

Plug a live cable into one jack at a time and watch which interface comes up:

```sh
ifconfig igb8 | grep -E 'media:|status:'
```

```
	media: Ethernet autoselect (1000baseT <full-duplex>)
	status: active
```

Work along the faceplate, jack by jack, writing down the printed label and the interface that
lit up. Do the module bays as well, and note which bay each block of ports belongs to — the bay
name goes into the entry and is shown on the page, so "FleXi module, bay A" is worth the typing.

**If the plugin is already installed, do it the other way round instead**, which is faster and
needs no cable at all: make the machine tell you where a port is, by blinking it.

```sh
configctl linkhealth identify igb8 30
```

That blinks `igb8`'s own LED for thirty seconds — half a second lit, half a second dark, slow
enough that it cannot be confused with traffic — and returns immediately. Walk to the front of the
box, read the label printed beside the socket that is blinking, and write down the pair. Then do
the next one. `configctl linkhealth stopidentify` ends it early.

It works on a port with nothing plugged into it, which is the whole advantage: the ports you most
need to identify are the empty ones. It does not touch traffic, the link does not drop, and the
LED is handed back to the driver when the time is up (measured on the reference machine, on all
three counts). One port blinks at a time; asking for a second while the first is running is
answered with "busy".

If the command reports that the port has no identification LED, its driver does not register a
`/dev/led/` node and you are back to plugging cables in. `ls /dev/led` shows which of your ports
can be blinked; [limits.md](limits.md) has the detail.

**Do not skip to the obvious answer.** On the XG330 the obvious answer is wrong: the FleXi module
in bay A takes `igb0`–`igb7`, and the jack printed `Port1` on the faceplate is `igb8` (measured on
the reference machine, and corroborated by my own interface names, which were assigned years
earlier from the same physical evidence).

While you are there, note the friendly names OPNsense gives the interfaces — but do not take them
from the kernel. After an interface is renamed the kernel keeps the old description string until
the interface is reconfigured; on the reference machine the kernel's descriptions and the
configuration's names are currently swapped relative to each other (measured here). The plugin
reads the name from `/conf/config.xml` for that reason, and `chassis.json` has nothing to do with
it: your entry supplies the *printed* label, never the friendly name.

## 4. Write the entry

Entries live inside the `models` object of `chassis.json` — in the repository at
`src/opnsense/scripts/linkhealth/chassis.json`, on an installed firewall at
`/usr/local/opnsense/scripts/linkhealth/chassis.json`. The file also has a `version` and a
`comment` at the top; leave both alone and add your model beside the others:

```json
{
  "version": 1,
  "comment": ["..."],
  "models": {
    "sophos-xg330": {
      "display": "Sophos XG 330 (rev 2)",
      "verified": true,
      "verified_by": "read from the machine this plugin was written on, 2026-09-21",
      "match": {"maker": "^Sophos$", "product": "^XG$", "version": "^330"},
      "groups": [
        {"driver": "igb", "units": [0, 7],   "label": "PortA%d", "start": 1,
         "bay": "FleXi module, bay A",
         "note": "This bay enumerates before the faceplate ports.",
         "verify": {"chip": "I350", "subdevice": "0x0008"}},
        {"driver": "igb", "units": [8, 15],  "label": "Port%d",  "start": 1, "bay": "faceplate",
         "verify": {"chip": "I211"}},
        {"driver": "igb", "units": [16, 17], "label": "Port%d",  "start": 9,
         "bay": "faceplate, 1G SFP",
         "note": "Intel I210 Fiber. The driver has no transceiver access, so these cages report no module data even with a module seated - the plugin shows link, media and error counters for them and nothing optical.",
         "verify": {"chip": "I210"}},
        {"driver": "ix",  "units": [0, 1],   "label": "SFP+%d",  "start": 1,
         "bay": "faceplate, 10G SFP+",
         "note": "Intel X520. Full transceiver telemetry.",
         "verify": {"chip": "X520"}}
      ]
    }
  }
}
```

| Key | What it is |
| --- | --- |
| the table name (`sophos-xg330`) | your key for the entry; lower case, vendor and model, no spaces. It appears in `status.json` as `chassis.table`, so keep it recognisable. |
| `display` | the appliance's name in words. This is what the page and the alert mail print, so write it the way somebody standing in front of the box would say it out loud. |
| `verified` | `true` only if you checked it against the metal — pressed identify and watched which socket blinked, or plugged a cable into one jack at a time and watched which interface came up. If you worked it out from a manual, write `false` and say so; the page can then be honest about it. |
| `verified_by` | optional, one line: what you checked it against and when. |
| `match` | the three `smbios.planar` values from step 1, each a **regular expression** searched against what the board reports. Anchor them. `"^330"` matches this board's `330r2` and not `1330`; unanchored `"XG"` would also match `XGS` and `XG-something`, which is why the shipped entry says `"^XG$"`. A value with a `.`, a `+` or brackets in it is a pattern, not text — escape it. |
| `groups` | the port blocks, below. An entry with `"groups": []` names the hardware and labels nothing, which is a legitimate half-contribution. |
| `hint` | optional, one sentence shown when the entry labels no ports, telling whoever is reading the page what to do about it. |
| `driver` | the interface name without its unit digits — `igb`, `ix`, `ixl`, `cxgbe`, … — matched against the whole prefix, so `ix` does not catch `ixl`. |
| `units` | first and last unit number of the block, inclusive. A single-port group is `[3, 3]`. |
| `label` | the label printed on the chassis, with **exactly one** `%d` where the number goes. `"PortA%d"` gives `PortA1`. A label with no `%d` in it, or with a stray `%`, is a formatting error, not a label. |
| `start` | the number printed on the **first** jack of the group. The XG330's SFP cages are `igb16`/`igb17` but are printed `Port9` and `Port10`, so that group starts at 9. |
| `bay` | the human name of the bay, shown on the page and used to group the port list. |
| `note` | optional, one sentence, shown on every port of the group. Use it for something a person would otherwise report as a bug — as above, where a fibre port legitimately shows no optics. |
| `verify` | what the card under this group has to actually be. Every group in the shipped file carries one, and it is what makes a wrong label impossible rather than merely unlikely. |

Within a group, unit *n* gets the label `label % (start + n - units[0])`. That is the only
arithmetic involved.

### `verify`, and why every group should have one

A model table is a claim about a machine nobody can see from here. `verify` is the group saying
what it expects to find, and the labels are printed **only** if the hardware agrees. A bay holding
a different card, or a cousin model that shares an SMBIOS string and is wired differently, fails
the check and those ports quietly keep their interface names — which is the right outcome, because
a wrong label sends somebody to the wrong cable at two in the morning.

Two fields, both optional, and a group with neither is never checked at all:

- **`chip`** — matched as a case-insensitive substring of the description the driver prints for
  that unit. Read it off your own machine, one group at a time:

  ```sh
  sysctl -n dev.igb.0.%desc
  ```

  On the reference machine that answers `Intel(R) I350 (Copper)`, and the entry stores only the
  distinctive part, `I350`. Do not paste the whole string in: the wording varies between driver
  versions and you would be pinning your table to one of them.

- **`subdevice`** — the PCI subdevice id, as the `0x…` string `pciconf -l` prints, or a list of
  them if the same board ships with more than one:

  ```sh
  pciconf -l | grep '^igb0@'
  ```

  ```
  igb0@pci0:5:0:0:	class=0x020000 rev=0x01 hdr=0x00 vendor=0x8086 device=0x1521 subvendor=0x15bb subdevice=0x0008
  ```

  Add it where the chip alone does not settle the question. An I350 sitting in the XG330's module
  bay could be any FleXi card built around that chip, with its own port count and its own order;
  `0x0008` is the one this table describes, and anything else in that bay gets interface names
  rather than somebody else's labels.

A group that fails its check reports why: `labelled` comes back `false` and `label_refused`
carries the sentence the page shows, naming the card it actually found.

**Order matters.** The models are tried in the order they appear in the file and the first entry
whose every stated field matches wins, so a specific model must sit **above** any broader entry
that would also match it. That is why `sophos-xg330` comes before `sophos-xg-family`, whose
`match` is only `{"maker": "^Sophos$"}`: put it after, and every Sophos box would stop at the
family entry and never reach yours. `generic` is the fallback and is skipped while anything else
matches.

## 5. Why groups, and not a flat list

A flat list of twenty names, in interface order, would be shorter to write and wrong in four
separate ways:

1. **Numbering restarts in every bay.** The XG330 goes `PortA1…PortA8`, then `Port1…Port8`, then
   `Port9`, `Port10`, then `SFP+1`, `SFP+2`. There is no single sequence to flatten, and three
   different ports would be "number 1".
2. **The module bay enumerates before the faceplate.** `igb0` is not the first jack on the front
   of the box; it is the first jack on a card in a slot. A flat list hides that inversion behind
   an ordinal, so a mistake in it looks like a typo instead of like a wrong bay. It also means the
   unit numbers of the faceplate ports depend on how many ports the fitted module has — a group
   can express that by being edited in one place, a flat list cannot. (I have only ever had one
   module in this machine, so that last part is reasoning from how it enumerates, not something I
   have tested with a second module.)
3. **The bay name is real information.** "faceplate, 10G SFP+" and "FleXi module, bay A" are how
   people talk about these ports out loud, and the page groups by them. A flat list has nowhere
   to put them.
4. **Notes belong to a bay, not to a port.** The "no transceiver telemetry on this driver" note is
   true of both SFP cages for the same reason; written per port it would be duplicated, and
   duplicated text drifts.

Groups also keep the entry honest about what you actually verified. If you mapped only the
faceplate and not the module bay, you can ship the faceplate group and leave the rest to fall back
to interface names, which is a perfectly good contribution.

## 6. Test it

1. **Check the file parses** before anything reads it. A broken `chassis.json` is the one way to
   make the port list disappear:

   ```sh
   python3 -c 'import json; json.load(open("/usr/local/opnsense/scripts/linkhealth/chassis.json", encoding="utf-8")); print("ok")'
   ```

   The installer runs the same check, so a file that fails here would have stopped an install too.

2. **Wait for the next collector run** — it is once a minute — and read the result. `status.json`
   is `root:wheel` and mode 0640, so read it as root:

   ```sh
   python3 -c 'import json; d=json.load(open("/var/db/linkhealth/status.json")); print(d["chassis"]); [print(p["if"], p["label"], p["labelled"], p.get("bay"), p.get("label_refused", "")) for p in d["ports"]]'
   ```

   `chassis["table"]` should be your key and `chassis["display"]` your name for the box. If it
   says `generic`, nothing matched: re-read the three `kenv` values and check for a stray anchor
   or a capital letter. If it names a *broader* entry than yours — a Sophos box stopping at
   `sophos-xg-family` — your entry is below that one in the file and the first match won; move it
   above.

3. **Check every physical port got a label.** `labelled` is `false` on any port no group claimed,
   and such a port keeps its interface name. It is also `false` when a group *did* claim the unit
   but its `verify` block did not match the card — the last column, `label_refused`, then carries
   the sentence saying which card was found instead, and the page shows it. An empty column on
   every line is what you want. Watch for the opposite mistake as well: two groups
   covering the same unit, where the first one silently wins and the second is never reached.
   Compare the list against the ports you wrote down in step 2.

4. **Check it against the box itself.** Press **identify** on a port on the page, walk round to
   the front, and confirm that the socket blinking is the one whose printed label the page just
   showed you. Do this at least once, and once per group if you can bear it: it is the only check
   that catches an off-by-one across a whole group, and an off-by-one is precisely what sends
   somebody to the wrong jack at two in the morning. Write down which ones you watched — that
   sentence is what step 8 asks you for, and it is what turns a layout from a claim into a
   confirmed one.

   Unplugging a cable and watching a port go `down` proves the same thing, and is what to do on
   hardware whose driver has no identification LED. It costs a link, which is why it is second
   choice.

The GUI lets you rename any single port by hand, and that override survives all of this. Use it
for a one-off, but please still send the entry in — the next person with your appliance should not
have to repeat step 3.

## 7. Draw the front panel (optional)

`chassis.json` says what each port is **called**. `faceplates.json` says where each one **is** —
which row, and in what order along it — which is what the page needs to draw the panel instead of
listing it. It lives beside the other file, at
`src/opnsense/scripts/linkhealth/faceplates.json` in the repository and
`/usr/local/opnsense/scripts/linkhealth/faceplates.json` on an installed firewall, and it has the
same shape: a `version`, a `comment`, and a `models` object you add one entry to.

```json
{
  "version": 1,
  "comment": ["..."],
  "models": {
    "sophos-xg330": {
      "display": "Sophos XG 330 (rev 2)",
      "confirmed": false,
      "confirmed_note": "Port order and bay grouping are corroborated by three independent witnesses on the reference machine: the owner's own interface names, the chip and PCI-subdevice boundaries, and two contiguous MAC blocks. The physical ARRANGEMENT below - which row, and left-to-right position - is drawn from the owner's description and has not been checked against the metal.",
      "units": 1,
      "orientation": "front",
      "rows": [
        {
          "bay": "FleXi module, bay A",
          "note": "An expansion bay. Its ports enumerate before the faceplate ports, which is why PortA1 is igb0 while the printed Port1 is igb8.",
          "items": [
            {"label": "PortA1", "type": "rj45"},
            {"label": "PortA2", "type": "rj45"}
          ]
        },
        {
          "bay": "faceplate, 10G SFP+",
          "items": [
            {"label": "SFP+1", "type": "sfp+"},
            {"label": "SFP+2", "type": "sfp+"}
          ]
        }
      ]
    }
  }
}
```

| Key | What it is |
| --- | --- |
| the table name (`sophos-xg330`) | **the same key you used in `chassis.json`**. That is the whole join between the two files; get it wrong and the page finds no drawing for your appliance and says so. |
| `display` | the appliance's name in words, as in the other file. |
| `confirmed` | `true` only if somebody stood in front of the machine and watched the sockets blink in this order. `false` otherwise, and the page then tells its reader the layout is unchecked. Default to `false`; it costs nothing and claims nothing. |
| `confirmed_note` | one paragraph, shown on the page: what you checked, how, and — just as useful — what you did **not** check. The shipped XG330 entry says its port order has three independent witnesses and its physical arrangement has none, which is exactly the kind of sentence to write. |
| `units` | how many rack units tall the appliance is. Recorded for completeness; no code reads it yet, so do not spend time on it. |
| `orientation` | which face these rows describe. It defaults to `front`, and `front` is the only value anything has needed so far. |
| `rows` | the rows of sockets, top to bottom as you face that side of the box. |
| `bay` | the bay this row of sockets sits in, spelled as the `bay` of the matching group in `chassis.json`. |
| `note` | optional, one sentence about the row, shown on the page. |
| `items` | the sockets of that row, **in the order they are printed on the metal**, reading the way you would read the front of the box. |
| `label` | the printed label of one socket, spelled exactly as `chassis.json` produces it — `PortA1`, not `PortA%d` and not `porta1`. |
| `type` | one of `rj45`, `sfp`, `sfp+`, `qsfp`, `console`, `usb`, `unknown`. It picks the width and the shape the socket is drawn with; `port_types` at the bottom of the file holds those hints and you should not need to touch it. |

**An item names a bay and a printed label, and never an interface.** That is deliberate and it is
the safety property of the whole feature: the binding from a printed label to `igb2` lives in
`chassis.json` and in no other file. So a mistake here puts a box in the wrong place on a picture.
It cannot send an alert to the wrong port.

**The label is what the join is made on, so spell it exactly.** A socket is matched to its live
state on the pair (`bay`, `label`) first, and on the `label` alone if that misses — which means a
bay you have spelled differently in the two files is forgiven, and a label you have spelled
differently is not. A label that matches nothing comes back as `absent`, and the page and the
widget both show that socket as unaccounted for instead of colouring it in.

A few rules that follow from the file being a drawing and not a photograph:

- **Draw from the metal, not from an image.** Count the sockets, read the labels, note the rows.
  Do not trace a vendor photograph or product render, and do not copy a layout out of a datasheet
  picture: those are the vendor's copyrighted work and cannot be redistributed here.
  [limits.md](limits.md) has the reasoning in full. What you are contributing is a list of facts —
  counts, order, bays, printed names — and facts are not owned.
- **No logos, no model badges, no decoration.** The plugin draws sockets and the names beside
  them. There is nothing else on the picture and nothing else belongs in the file.
- **A partial row is better than a guessed one.** If the machine has a console port and two USB
  sockets and you are not sure where they sit, leave them out. A drawing that is short a socket is
  honest; one with a socket in an invented place is not.

Check it parses before anything reads it, exactly as with the other file:

```sh
python3 -c 'import json; json.load(open("/usr/local/opnsense/scripts/linkhealth/faceplates.json", encoding="utf-8")); print("ok")'
```

Then read what the backend makes of it, which is the same document the page and the widget draw
from:

```sh
configctl linkhealth faceplate | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("reason") or (d["display"] + " confirmed: " + str(d["confirmed"]))); [print(r["bay"]) or [print("   ", i["label"], i["type"], i["if"], i["state"]) for i in r["items"]] for r in d.get("rows", [])]'
```

Before your entry is found, that prints one line — `no faceplate drawing for this model yet` —
because a document that carries no drawing carries no rows and no `confirmed` flag either. Seeing
that line is how you know the key in `faceplates.json` does not match the one `chassis.json`
chose, which is by far the most common way to get section 7 wrong.

On the reference machine that begins:

```
Sophos XG 330 (rev 2) confirmed: False
FleXi module, bay A
    PortA1 rj45 igb0 idle
    PortA2 rj45 igb1 ok
    PortA3 rj45 igb2 down
```

Every socket should name an interface and carry a real verdict. A socket whose state comes back
`absent` is one the live data does not account for — the drawing claiming a jack the machine never
mentioned — and it nearly always means a `label` that does not match what `chassis.json` produces,
character for character. Both the page and the dashboard widget show such a socket as unaccounted
for rather than colouring it in, so it does not need any JSON to spot; this command is the quickest
way to see *which* label is wrong.

## 8. Send it in

Open a pull request that adds your block to `src/opnsense/scripts/linkhealth/chassis.json` — above
any broader entry that would also match your board — plus your `faceplates.json` entry if you drew
one, and put in the description:

- the `kenv | grep -E '^smbios\.(planar|system)\.(maker|product|version)'` output, verbatim;
- the model as it is printed on the front of the box, and how many jacks it has;
- the port list from step 2;
- the `sysctl -n dev.<driver>.<unit>.%desc` line and the `pciconf -l` line for the first port of
  each group, which is where your `verify` blocks came from;
- which groups you verified by watching a socket blink or by plugging a cable in, and which you
  inferred;
- if you drew a faceplate: **which sockets you watched blink, and what you saw** — "I pressed
  identify on igb8 through igb15 one at a time and read Port1 through Port8 in that order, left to
  right" is the whole sentence, and it is what lets the entry ship with `confirmed: true`. Say
  which rows you did not check just as plainly; a layout that is confirmed in one bay and reasoned
  in another is normal, and the `confirmed_note` is where that goes.

Those last two lines matter more than they look. They are the difference between a label somebody
can trust and a label somebody has to re-check, and saying "I inferred the module bay" costs you
nothing. A drawing is a claim until a person has watched the metal answer; writing down what you
watched is what settles it.
