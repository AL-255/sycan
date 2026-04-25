"""SVG glue used by :mod:`sycan.autodraw`.

This module is the only place that knows about SVG syntax: parsing
``res/<kind>.svg`` glyph files (viewBox + ``<circle id="port-X">`` port
markers) and serialising the final schematic. The autodraw layer
hands a list of placed components, the routed polylines, and the
loaded glyphs in, and gets a ready-to-write SVG string back.

Keeping this code in its own file means ``autodraw.py`` can stay
focused on the layout algorithm.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union


# ---------------------------------------------------------------------------
# Component kinds that the SVG layer knows how to look up under res/.
# ---------------------------------------------------------------------------
KIND_GLYPHS: tuple[str, ...] = (
    "nmos", "pmos", "npn", "pnp", "triode", "diode",
    "vsrc", "isrc", "res", "ind", "cap", "tline",
    "ccsrc", "port", "gnd",
)


# ---------------------------------------------------------------------------
# Regex helpers — small, dependency-free SVG attribute scraping. Glyph
# files are expected to be the lightweight hand-edited kind; we don't
# need a real XML parser.
# ---------------------------------------------------------------------------
_SVG_OPEN_RE = re.compile(r"<svg\b[^>]*>", re.IGNORECASE)
_SVG_CLOSE_RE = re.compile(r"</svg\s*>", re.IGNORECASE)
_VIEWBOX_RE = re.compile(r'viewBox\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
_WIDTH_RE = re.compile(r'\bwidth\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
_HEIGHT_RE = re.compile(r'\bheight\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)

# Port markers — any element whose ``id="port-<port>"`` is set.
_PORT_ID_RE = re.compile(
    r'<([a-zA-Z][a-zA-Z0-9]*)\s+([^>]*?)\bid\s*=\s*["\']port-([A-Za-z0-9_]+)["\']'
    r'([^>]*)>',
    re.IGNORECASE,
)
_CX_ATTR_RE = re.compile(r'\bcx\s*=\s*["\']([+\-\d.eE]+)["\']')
_CY_ATTR_RE = re.compile(r'\bcy\s*=\s*["\']([+\-\d.eE]+)["\']')
_X_ATTR_RE = re.compile(r'\bx\s*=\s*["\']([+\-\d.eE]+)["\']')
_Y_ATTR_RE = re.compile(r'\by\s*=\s*["\']([+\-\d.eE]+)["\']')
_DATA_X_RE = re.compile(r'\bdata-x\s*=\s*["\']([+\-\d.eE]+)["\']')
_DATA_Y_RE = re.compile(r'\bdata-y\s*=\s*["\']([+\-\d.eE]+)["\']')


def parse_port_markers(inner: str) -> dict[str, tuple[float, float]]:
    """Extract ``{port: (x, y)}`` from a glyph's inner SVG content.

    Recognised forms (any tag, any glyph file):

    * ``<circle id="port-NAME" cx="X" cy="Y" r="0" />``
    * ``<rect   id="port-NAME" x="X"  y="Y"  ... />``
    * any element with ``id="port-NAME" data-x="X" data-y="Y"``

    Coordinates are in the glyph's own viewBox space, with ``(0, 0)``
    being the top-left of the viewBox.
    """
    out: dict[str, tuple[float, float]] = {}
    for m in _PORT_ID_RE.finditer(inner):
        attrs = m.group(2) + m.group(4)
        port = m.group(3)
        for x_re, y_re in (
            (_CX_ATTR_RE, _CY_ATTR_RE),
            (_DATA_X_RE, _DATA_Y_RE),
            (_X_ATTR_RE, _Y_ATTR_RE),
        ):
            xm = x_re.search(attrs)
            ym = y_re.search(attrs)
            if xm and ym:
                try:
                    out[port] = (float(xm.group(1)), float(ym.group(1)))
                except ValueError:
                    pass
                break
    return out


def load_glyph(path: Path,
               default_w: float, default_h: float) -> Optional[dict]:
    """Read a glyph SVG. Returns a dict with the keys::

        {
            "viewbox": "<viewbox attr value>",
            "inner": "<everything between <svg> and </svg>>",
            "bbox_w": float,        # width  from viewBox
            "bbox_h": float,        # height from viewBox
            "ports": {port: (x, y), ...},  # in viewBox coords, top-left origin
        }

    or ``None`` if the file is missing / malformed. ``default_w`` /
    ``default_h`` are only used as last-resort fallbacks when neither
    a ``viewBox`` nor ``width`` / ``height`` attributes are present.
    """
    if not path.exists():
        return None
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return None
    m_open = _SVG_OPEN_RE.search(text)
    if not m_open:
        return None
    open_tag = m_open.group()

    vb_x = vb_y = 0.0
    vb_w = float(default_w)
    vb_h = float(default_h)
    vb_str = f"0 0 {default_w} {default_h}"
    vb_match = _VIEWBOX_RE.search(open_tag)
    if vb_match:
        vb_str = vb_match.group(1).strip()
        try:
            tokens = vb_str.replace(",", " ").split()
            if len(tokens) >= 4:
                vb_x, vb_y, vb_w, vb_h = (float(t) for t in tokens[:4])
        except ValueError:
            pass
    else:
        wm = _WIDTH_RE.search(open_tag)
        hm = _HEIGHT_RE.search(open_tag)
        if wm and hm:
            try:
                vb_w = float(wm.group(1))
                vb_h = float(hm.group(1))
                vb_str = f"0 0 {vb_w} {vb_h}"
            except ValueError:
                pass

    inner_start = m_open.end()
    m_close = _SVG_CLOSE_RE.search(text, inner_start)
    inner_end = m_close.start() if m_close else len(text)
    inner = text[inner_start:inner_end].strip()

    ports = parse_port_markers(inner)
    if vb_x or vb_y:
        ports = {p: (x - vb_x, y - vb_y) for p, (x, y) in ports.items()}

    return {
        "viewbox": vb_str,
        "inner": inner,
        "bbox_w": vb_w,
        "bbox_h": vb_h,
        "ports": ports,
    }


def load_glyphs(
    res_dir: Optional[Union[str, Path]],
    default_w: float, default_h: float,
) -> dict[str, dict]:
    """Load every available ``res/<kind>.svg`` glyph (see :func:`load_glyph`).

    Returns ``{kind: glyph_info}``. Missing kinds are absent; those
    components fall back to the default rect with canonical pin
    positions in the autodraw layer.
    """
    if res_dir is None:
        return {}
    root = Path(res_dir)
    if not root.exists() or not root.is_dir():
        return {}
    glyphs: dict[str, dict] = {}
    for kind in KIND_GLYPHS:
        loaded = load_glyph(root / f"{kind}.svg", default_w, default_h)
        if loaded is not None:
            glyphs[kind] = loaded
    return glyphs


def html_escape(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))


# ---------------------------------------------------------------------------
# Final SVG serialisation.
# ---------------------------------------------------------------------------
def emit_svg(
    placed: Sequence,                  # list of _Placed (autodraw)
    polylines: Sequence[tuple[str, list[tuple[float, float]]]],
    canvas_w: float,
    canvas_h: float,
    rail_top_y: float,
    rail_bot_y: float,
    *,
    label_fs: int = 11,
    port_fs: int = 9,
    glyphs: Optional[dict[str, dict]] = None,
    short_port: Optional[callable] = None,
) -> str:
    """Serialise a routed schematic to SVG.

    ``placed`` is an iterable of ``_Placed``-like objects (anything with
    ``.cx``, ``.cy``, ``.pin_pos``, ``.pin_side``, and a ``.desc`` that
    carries ``label``, ``kind``, ``bbox_w``, ``bbox_h``, ``mirror``,
    ``flip``). ``polylines`` is the list of routed wires/rails as
    ``(net_class, [(x, y), ...])`` pairs (a class starting with
    ``"rail"`` is drawn in the rail style).

    ``glyphs`` maps a kind to the dict produced by :func:`load_glyph`;
    components whose kind is in ``glyphs`` render as ``<use>``
    references, the rest fall back to a labelled ``<rect>``.

    ``short_port`` is an optional callable mapping a port name to its
    short label glyph (e.g., ``"drain" → "D"``); defaults to using the
    first two characters of the port name.
    """
    if short_port is None:
        short_port = lambda p: p[:2]

    parts: list[str] = []
    # Declare the Inkscape / sodipodi namespaces on the root <svg> so
    # glyph files exported by Inkscape (which sprinkle attrs like
    # ``inkscape:label`` and elements like ``<sodipodi:namedview>``)
    # parse cleanly when their inner content is inlined.
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
        f'xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd" '
        f'viewBox="0 0 {canvas_w:.0f} {canvas_h:.0f}" '
        f'width="{canvas_w:.0f}" height="{canvas_h:.0f}" '
        f'font-family="sans-serif" font-size="{label_fs}">'
    )
    parts.append(
        "<style>"
        ".comp{fill:#fff;stroke:#222;stroke-width:1.4}"
        ".pin{stroke:#222;stroke-width:1.2;fill:none}"
        ".pinpad{fill:#222}"
        ".wire{stroke:#0a4;stroke-width:1.2;fill:none}"
        ".rail{stroke:#a00;stroke-width:1.6;fill:none}"
        ".net{stroke:#0a4;stroke-width:1.2;fill:none}"
        ".lab{fill:#222}"
        ".plab{fill:#444;font-size:%dpx}"
        ".rlab{fill:#a00;font-weight:bold}"
        "</style>" % port_fs
    )

    # Glyph defs are intentionally inlined per-instance below (as <g>
    # transforms wrapping the glyph's inner content). The pure <use>
    # path is more concise but ImageMagick's SVG renderer doesn't
    # follow inline <symbol> references reliably, so we inline.

    # Wires / rails.
    for cls, pts in polylines:
        if not pts:
            continue
        stroke_class = "rail" if cls.startswith("rail") else "wire"
        d_pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        parts.append(
            f'<polyline class="{stroke_class}" '
            f'data-net="{cls}" points="{d_pts}" />'
        )

    # Rail labels.
    parts.append(
        f'<text class="rlab" x="{8}" y="{rail_top_y - 6:.0f}">VDD</text>'
    )
    parts.append(
        f'<text class="rlab" x="{8}" y="{rail_bot_y + 14:.0f}">VSS / GND</text>'
    )

    have_glyphs = glyphs or {}
    for p in placed:
        d = p.desc
        bw, bh = d.bbox_w, d.bbox_h
        x = p.cx - bw / 2.0
        y = p.cy - bh / 2.0
        if d.kind in have_glyphs:
            info = have_glyphs[d.kind]
            # Parse the glyph's viewBox so we can map "glyph space" to
            # the component's bbox on the canvas.
            vb_tokens = info["viewbox"].replace(",", " ").split()
            try:
                vx = float(vb_tokens[0]); vy = float(vb_tokens[1])
                vw = float(vb_tokens[2]); vh = float(vb_tokens[3])
            except (ValueError, IndexError):
                vx = vy = 0.0
                vw = bw; vh = bh
            sx_fit = bw / vw if vw > 0 else 1.0
            sy_fit = bh / vh if vh > 0 else 1.0
            # Compose: optional mirror/flip around component center,
            # then place the glyph inner content at (x, y) scaled to
            # (bw, bh), then strip the glyph's own viewBox offset.
            parts_t: list[str] = []
            if d.mirror or d.flip:
                msx = -1 if d.mirror else 1
                msy = -1 if d.flip else 1
                parts_t.append(
                    f"translate({p.cx:.3f},{p.cy:.3f}) "
                    f"scale({msx},{msy}) "
                    f"translate({-p.cx:.3f},{-p.cy:.3f})"
                )
            parts_t.append(
                f"translate({x:.3f},{y:.3f}) "
                f"scale({sx_fit:.6f},{sy_fit:.6f})"
            )
            if vx or vy:
                parts_t.append(f"translate({-vx:.3f},{-vy:.3f})")
            transform = " ".join(parts_t)
            parts.append(
                f'<g data-comp="{d.kind}" data-name="{d.label}" '
                f'data-x="{x:.3f}" data-y="{y:.3f}" '
                f'data-bbox-w="{bw}" data-bbox-h="{bh}" '
                f'transform="{transform}">{info["inner"]}</g>'
            )
        else:
            parts.append(
                f'<rect class="comp" data-comp="{d.kind}" '
                f'data-name="{d.label}" '
                f'x="{x:.1f}" y="{y:.1f}" width="{bw}" height="{bh}" rx="3" />'
            )
        parts.append(
            f'<text class="lab" x="{p.cx:.1f}" y="{p.cy + 4:.1f}" '
            f'text-anchor="middle">{html_escape(d.label)}</text>'
        )

        for port, (px, py) in p.pin_pos.items():
            side = p.pin_side[port]
            outside = (
                px < p.cx - bw / 2.0 - 0.5
                or px > p.cx + bw / 2.0 + 0.5
                or py < p.cy - bh / 2.0 - 0.5
                or py > p.cy + bh / 2.0 + 0.5
            )
            if outside and d.kind not in have_glyphs:
                if side == "top":
                    parts.append(
                        f'<line class="pin" x1="{p.cx:.1f}" '
                        f'y1="{(p.cy - bh / 2.0):.1f}" '
                        f'x2="{px:.1f}" y2="{py:.1f}" />'
                    )
                elif side == "bot":
                    parts.append(
                        f'<line class="pin" x1="{p.cx:.1f}" '
                        f'y1="{(p.cy + bh / 2.0):.1f}" '
                        f'x2="{px:.1f}" y2="{py:.1f}" />'
                    )
                elif side == "left":
                    parts.append(
                        f'<line class="pin" x1="{(p.cx - bw / 2.0):.1f}" '
                        f'y1="{p.cy:.1f}" x2="{px:.1f}" y2="{py:.1f}" />'
                    )
                else:
                    parts.append(
                        f'<line class="pin" x1="{(p.cx + bw / 2.0):.1f}" '
                        f'y1="{p.cy:.1f}" x2="{px:.1f}" y2="{py:.1f}" />'
                    )

            parts.append(
                f'<circle class="pinpad" cx="{px:.1f}" cy="{py:.1f}" r="2" />'
            )
            if side == "top":
                parts.append(
                    f'<text class="plab" x="{px + 4:.1f}" y="{py - 2:.1f}">'
                    f'{short_port(port)}</text>'
                )
            elif side == "bot":
                parts.append(
                    f'<text class="plab" x="{px + 4:.1f}" y="{py + 11:.1f}">'
                    f'{short_port(port)}</text>'
                )
            elif side == "left":
                parts.append(
                    f'<text class="plab" x="{px - 4:.1f}" y="{py - 2:.1f}" '
                    f'text-anchor="end">{short_port(port)}</text>'
                )
            else:
                parts.append(
                    f'<text class="plab" x="{px + 4:.1f}" y="{py - 2:.1f}">'
                    f'{short_port(port)}</text>'
                )

    parts.append("</svg>")
    return "\n".join(parts)
