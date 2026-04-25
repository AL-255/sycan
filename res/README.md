# `res/` — component glyph library

`autodraw(..., res_dir="res")` reads one SVG per component kind from
this folder. At startup, it parses each glyph's `viewBox` and any
**port markers** inside, then uses *those* dimensions and port
positions to drive the placement, the SA cost, and the routing.

This means you are **not** locked into a fixed component size: a
transmission line can be 200 px wide, a resistor 30 px tall, a triode
80 × 80, etc. The placer adapts column widths and row pitches to fit.

## Glyph file structure

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 W H">
  <!-- Your schematic symbol here. -->
  ...

  <!-- Port markers: invisible <circle>s with id="port-<port>" -->
  <circle id="port-drain"  cx="X1" cy="Y1" r="0" fill="none" stroke="none"/>
  <circle id="port-gate"   cx="X2" cy="Y2" r="0" fill="none" stroke="none"/>
  <circle id="port-source" cx="X3" cy="Y3" r="0" fill="none" stroke="none"/>
</svg>
```

* The `viewBox` defines the component's bounding box. `W` and `H`
  may be any positive numbers — they're the dimensions used for
  placement and bbox-blocking during routing.
* A **port marker** is an element whose `id` attribute starts with
  `port-`. The string after the prefix is the port name (must match
  the component's port: `drain`, `gate`, `source`, `n_plus`, etc.).
* Coordinates can be specified via `cx`/`cy` (preferred), `x`/`y`,
  or `data-x`/`data-y`, in viewBox space. The autodraw code translates
  them to top-left = (0, 0) of the bounding box.
* `r="0"` (or any small radius) keeps the marker invisible. Setting
  `fill="none"` and `stroke="none"` is belt-and-braces.

If a port name is missing from the glyph, autodraw falls back to a
canonical position on the box edge:

| Port               | Default position                         |
| ------------------ | ---------------------------------------- |
| `spine_top`        | top centre   `(W/2, 0)`                  |
| `spine_bot`        | bottom centre `(W/2, H)`                 |
| 1st side port      | left centre   `(0, H/4)`                 |
| 2nd side port      | right centre  `(W, H/4)`                 |
| more side ports    | alternating, stepping by `H/2`           |

## Supported kinds

| File           | Component(s) it stands in for          | Spine top → bottom |
| -------------- | -------------------------------------- | ------------------ |
| `nmos.svg`     | `NMOS_L1`, `NMOS_subthreshold`         | drain → source     |
| `pmos.svg`     | `PMOS_L1`, `PMOS_subthreshold`         | source → drain     |
| `npn.svg`      | NPN BJT                                | collector → emitter|
| `pnp.svg`      | PNP BJT                                | emitter → collector|
| `triode.svg`   | vacuum triode                          | plate → cathode    |
| `diode.svg`    | Shockley diode                         | anode → cathode    |
| `vsrc.svg`     | independent voltage source             | n_plus → n_minus   |
| `isrc.svg`     | independent current source             | n_plus → n_minus   |
| `res.svg`      | resistor                               | n_plus → n_minus   |
| `ind.svg`      | inductor                               | n_plus → n_minus   |
| `cap.svg`      | capacitor                              | n_plus → n_minus   |
| `tline.svg`    | transmission line                      | n_in_p → n_out_p   |
| `ccsrc.svg`    | controlled sources (E, G, F, H)        | n_plus → n_minus   |
| `port.svg`     | named port marker                      | n_plus → n_minus   |
| `gnd.svg`      | explicit ground tie                    | node               |

## Pin orientation

The glyph is rendered in its native orientation when the netlist's
spine endpoint mapping matches the canonical (top-port-on-top) order.
When the placer chooses to flip a component vertically (so its
canonical bottom port ends up at the top of the column), autodraw
applies an SVG `scale(1, -1)` to the `<use>` reference. Symmetric
side-port mirroring uses `scale(-1, 1)`. So **draw the glyph in its
natural orientation** and let autodraw handle flips.

## Pin labels and stubs

Pin tip dots and short port-glyph labels (D, S, G, +, -, ...) are
drawn by autodraw *outside* the glyph's bounding region. The glyph
itself only needs to contain the device body. If you want decorative
pin-stub lines reaching out to the box edge, draw them inside the
glyph; autodraw won't add an extra stub when a glyph is present.

The placeholder glyphs in this folder are intentionally minimal — a
dashed outline plus a sketch of the device. Replace each with the
real schematic symbol you want; autodraw will pick up the new
dimensions, port markers, and shape on the next call.
