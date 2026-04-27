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

import math
import re
import xml.etree.ElementTree as _ET
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple, Union

# Type alias for axis-aligned bounding boxes: (x_min, y_min, x_max, y_max).
BBox = Tuple[float, float, float, float]


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


# ---------------------------------------------------------------------------
# Geometric bounding box. The SVG ``viewBox`` attribute is just the
# canvas the editor was set up with — for hand-edited Inkscape glyphs
# it routinely fails to enclose the actual drawing (e.g. ``npn.svg``
# declares ``0 0 70 40`` but its base lead lives at x = -20). For
# autodraw we want a tight box around everything that's *actually* on
# the page: drawing primitives plus port markers. ``geometric_bbox``
# computes that, ``load_glyph`` then uses it instead of the canvas.
# ---------------------------------------------------------------------------
_AFFINE_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_TRANSFORM_OP_RE = re.compile(r'([A-Za-z]+)\s*\(([^)]*)\)')
_NUM_SPLIT_RE = re.compile(r'[,\s]+')

# Subtrees that don't contribute to the visible drawing.
_NON_RENDERING_TAGS = frozenset({
    "defs", "clipPath", "mask", "symbol", "marker",
    "metadata", "title", "desc", "style", "script",
})


def _strip_ns(tag: str) -> str:
    """Drop the ``{namespace}`` prefix ElementTree puts on tag names."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def _parse_floats(s: str) -> list[float]:
    """Parse a whitespace/comma-separated list of floats; ignore junk."""
    out: list[float] = []
    for tok in _NUM_SPLIT_RE.split(s.strip()):
        if not tok:
            continue
        try:
            out.append(float(tok))
        except ValueError:
            pass
    return out


def _compose_affine(M, N):
    """Return ``M @ N`` (apply N first, then M) for 2D affine transforms.

    Each transform is stored as ``(a, b, c, d, e, f)`` mapping
    ``(x, y) -> (a*x + c*y + e, b*x + d*y + f)`` — the same packing
    SVG's ``matrix()`` uses.
    """
    a1, b1, c1, d1, e1, f1 = M
    a2, b2, c2, d2, e2, f2 = N
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _apply_affine_pt(M, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = M
    return (a * x + c * y + e, b * x + d * y + f)


def _apply_affine_bbox(M, bb: BBox) -> BBox:
    """Transform an axis-aligned bbox by ``M`` and return the AAB of
    the result. Uses all four corners so rotations/skews are handled
    correctly."""
    xmin, ymin, xmax, ymax = bb
    pts = [
        _apply_affine_pt(M, xmin, ymin),
        _apply_affine_pt(M, xmax, ymin),
        _apply_affine_pt(M, xmax, ymax),
        _apply_affine_pt(M, xmin, ymax),
    ]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _parse_transform(s: str):
    """Parse an SVG ``transform="..."`` attribute. Supports translate,
    scale, rotate, and matrix; unknown ops are skipped (the result
    becomes a conservative under-transform but never crashes)."""
    M = _AFFINE_IDENTITY
    for op, args in _TRANSFORM_OP_RE.findall(s):
        nums = _parse_floats(args)
        op = op.lower()
        if op == "translate":
            tx = nums[0] if nums else 0.0
            ty = nums[1] if len(nums) > 1 else 0.0
            local = (1.0, 0.0, 0.0, 1.0, tx, ty)
        elif op == "scale":
            sx = nums[0] if nums else 1.0
            sy = nums[1] if len(nums) > 1 else sx
            local = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif op == "rotate":
            theta = math.radians(nums[0] if nums else 0.0)
            cs = math.cos(theta)
            sn = math.sin(theta)
            R = (cs, sn, -sn, cs, 0.0, 0.0)
            if len(nums) >= 3:
                cx, cy = nums[1], nums[2]
                T1 = (1.0, 0.0, 0.0, 1.0, cx, cy)
                T2 = (1.0, 0.0, 0.0, 1.0, -cx, -cy)
                local = _compose_affine(T1, _compose_affine(R, T2))
            else:
                local = R
        elif op == "matrix" and len(nums) >= 6:
            local = tuple(nums[:6])  # type: ignore[assignment]
        else:
            continue
        M = _compose_affine(M, local)
    return M


# Path command tokeniser. Splits the ``d`` attribute into a stream of
# command letters and signed numbers — handles compact Inkscape output
# like ``M-12,16.110082 0,11.006883V2`` that crams numbers together.
_PATH_TOKEN_RE = re.compile(
    r'([MmLlHhVvCcSsQqTtAaZz])'
    r'|([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)'
)


def _path_d_bbox(d: str) -> Optional[BBox]:
    """Bounding box of the geometry described by an SVG path ``d``.

    Curve commands (C/S/Q/T/A) are bounded by their *control* points,
    which over-approximates the true curve extent — fine for autodraw,
    which only needs a box that contains the visible drawing.
    """
    if not d:
        return None
    tokens = [
        m.group(1) or m.group(2)
        for m in _PATH_TOKEN_RE.finditer(d)
    ]
    if not tokens:
        return None

    pts: list[tuple[float, float]] = []
    pen_x = pen_y = 0.0
    sub_x = sub_y = 0.0  # subpath start, restored by Z
    cmd: Optional[str] = None
    i = 0
    n = len(tokens)

    def take(k: int) -> Optional[list[float]]:
        nonlocal i
        if i + k > n:
            return None
        try:
            out = [float(t) for t in tokens[i:i + k]]
        except ValueError:
            return None
        i += k
        return out

    while i < n:
        t = tokens[i]
        if t and t[0].isalpha():
            cmd = t
            i += 1
            # Per SVG spec a leading relative ``m`` is treated as
            # absolute; subsequent coordinate pairs after any moveto
            # are implicit linetos with the same case as the moveto.
            continue
        if cmd is None:
            i += 1
            continue
        rel = cmd.islower()
        c = cmd.lower()

        if c == "m":
            args = take(2)
            if args is None:
                break
            x, y = args
            if rel and pts:  # leading m on empty path is treated as M
                x += pen_x
                y += pen_y
            pen_x, pen_y = x, y
            sub_x, sub_y = x, y
            pts.append((x, y))
            cmd = "l" if rel else "L"  # implicit-lineto chain
        elif c == "l":
            args = take(2)
            if args is None:
                break
            x, y = args
            if rel:
                x += pen_x
                y += pen_y
            pen_x, pen_y = x, y
            pts.append((x, y))
        elif c == "h":
            args = take(1)
            if args is None:
                break
            (x,) = args
            if rel:
                x += pen_x
            pen_x = x
            pts.append((pen_x, pen_y))
        elif c == "v":
            args = take(1)
            if args is None:
                break
            (y,) = args
            if rel:
                y += pen_y
            pen_y = y
            pts.append((pen_x, pen_y))
        elif c == "z":
            pen_x, pen_y = sub_x, sub_y
        elif c == "c":
            args = take(6)
            if args is None:
                break
            cp1x, cp1y, cp2x, cp2y, ex, ey = args
            if rel:
                cp1x += pen_x; cp1y += pen_y
                cp2x += pen_x; cp2y += pen_y
                ex += pen_x; ey += pen_y
            pts.extend([(cp1x, cp1y), (cp2x, cp2y), (ex, ey)])
            pen_x, pen_y = ex, ey
        elif c == "s":
            args = take(4)
            if args is None:
                break
            cp2x, cp2y, ex, ey = args
            if rel:
                cp2x += pen_x; cp2y += pen_y
                ex += pen_x; ey += pen_y
            pts.extend([(cp2x, cp2y), (ex, ey)])
            pen_x, pen_y = ex, ey
        elif c == "q":
            args = take(4)
            if args is None:
                break
            cpx, cpy, ex, ey = args
            if rel:
                cpx += pen_x; cpy += pen_y
                ex += pen_x; ey += pen_y
            pts.extend([(cpx, cpy), (ex, ey)])
            pen_x, pen_y = ex, ey
        elif c == "t":
            args = take(2)
            if args is None:
                break
            ex, ey = args
            if rel:
                ex += pen_x; ey += pen_y
            pts.append((ex, ey))
            pen_x, pen_y = ex, ey
        elif c == "a":
            # rx ry x-axis-rotation large-arc-flag sweep-flag x y
            args = take(7)
            if args is None:
                break
            rx, ry, _xr, _laf, _sf, ex, ey = args
            if rel:
                ex += pen_x; ey += pen_y
            r = max(abs(rx), abs(ry))
            # Conservative: enclose start, end, and a circle of radius
            # ``r`` around each. This is an over-bound but never
            # under-bounds the arc.
            pts.extend([
                (pen_x - r, pen_y - r), (pen_x + r, pen_y + r),
                (ex - r, ey - r), (ex + r, ey + r),
            ])
            pen_x, pen_y = ex, ey
        else:
            i += 1
            continue

    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _primitive_bbox(tag: str, attrib: dict) -> Optional[BBox]:
    """Bbox of a single primitive element (no transform applied)."""

    def f(name: str, default: float = 0.0) -> float:
        try:
            return float(attrib.get(name, default))
        except (TypeError, ValueError):
            return default

    if tag == "line":
        x1, y1 = f("x1"), f("y1")
        x2, y2 = f("x2"), f("y2")
        return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    if tag == "rect":
        x, y = f("x"), f("y")
        w, h = f("width"), f("height")
        if w <= 0 or h <= 0:
            return None
        return (x, y, x + w, y + h)
    if tag == "circle":
        cx, cy, r = f("cx"), f("cy"), f("r")
        if r <= 0:
            return (cx, cy, cx, cy)
        return (cx - r, cy - r, cx + r, cy + r)
    if tag == "ellipse":
        cx, cy = f("cx"), f("cy")
        rx, ry = f("rx"), f("ry")
        if rx <= 0 or ry <= 0:
            return (cx, cy, cx, cy)
        return (cx - rx, cy - ry, cx + rx, cy + ry)
    if tag in ("polyline", "polygon"):
        nums = _parse_floats(attrib.get("points", ""))
        if len(nums) < 2:
            return None
        xs = nums[0::2]
        ys = nums[1::2]
        if not xs or not ys:
            return None
        return (min(xs), min(ys), max(xs), max(ys))
    if tag == "path":
        return _path_d_bbox(attrib.get("d", ""))
    return None


def _gbbox_walk(
    node: _ET.Element, transform, extents: list[BBox],
) -> None:
    """Depth-first walk that accumulates per-primitive bboxes (already
    transformed into the document's coordinate system) into
    ``extents``. Skips non-rendering subtrees and treats port-marker
    circles as their centre point so port positions inform the bbox
    without inflating it by the marker radius."""
    tag = _strip_ns(node.tag)
    if tag in _NON_RENDERING_TAGS:
        return

    local = node.attrib.get("transform", "").strip()
    cur = (
        _compose_affine(transform, _parse_transform(local))
        if local else transform
    )

    if tag not in ("g", "svg"):
        node_id = node.attrib.get("id", "")
        if node_id.startswith("port-") and tag == "circle":
            try:
                cx = float(node.attrib.get("cx", 0.0))
                cy = float(node.attrib.get("cy", 0.0))
                bb: Optional[BBox] = (cx, cy, cx, cy)
            except (TypeError, ValueError):
                bb = None
        else:
            bb = _primitive_bbox(tag, node.attrib)
        if bb is not None:
            extents.append(_apply_affine_bbox(cur, bb))

    for child in node:
        _gbbox_walk(child, cur, extents)


def geometric_bbox(inner: str) -> Optional[BBox]:
    """Compute the tight bounding box of all visible primitives plus
    port markers in a glyph's inner SVG content.

    Returns ``(x_min, y_min, x_max, y_max)`` in the SVG's own user
    units, or ``None`` if nothing was renderable. Port markers are
    included as their centre points (so wire-attachment positions
    always lie inside the box without inflating it by the marker
    radius). Curve commands and arcs are bounded by control points,
    which is a conservative super-set of the true extent.
    """
    if not inner.strip():
        return None
    try:
        # Wrap with a fresh root so callers can pass just the body of
        # an SVG file. The xmlns declaration keeps ElementTree from
        # flagging unknown elements/attributes.
        root = _ET.fromstring(
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'xmlns:xlink="http://www.w3.org/1999/xlink">'
            f'{inner}</svg>'
        )
    except _ET.ParseError:
        return None

    extents: list[BBox] = []
    _gbbox_walk(root, _AFFINE_IDENTITY, extents)
    if not extents:
        return None
    return (
        min(b[0] for b in extents),
        min(b[1] for b in extents),
        max(b[2] for b in extents),
        max(b[3] for b in extents),
    )


# Inkscape and friends round-trip coordinates through float64, so an
# editor "30" routinely shows up in the SVG as "30.000004" or similar.
# Naïve floor/ceil pushes those values across the integer boundary and
# silently inflates the bbox by a whole grid step. Anything closer than
# this tolerance to an integer grid step is treated as already aligned.
# 1e-4 is comfortably below the 0.05 the autodraw test layer tolerates,
# but loose enough to cover noise that's already compounded across a
# bbox-vs-anchor subtraction (each operand can already carry ~1e-6).
_GRID_SNAP_TOL = 1e-4


def _floor_step(value: float) -> int:
    """``floor(value)`` but treats ``value`` as integer when it's
    within :data:`_GRID_SNAP_TOL` of one (handles float-print noise
    in glyph SVGs)."""
    rounded = round(value)
    if abs(value - rounded) <= _GRID_SNAP_TOL:
        return rounded
    return math.floor(value)


def _ceil_step(value: float) -> int:
    """:func:`math.ceil` with the same float-noise tolerance as
    :func:`_floor_step`."""
    rounded = round(value)
    if abs(value - rounded) <= _GRID_SNAP_TOL:
        return rounded
    return math.ceil(value)


def _anchor_aligned_bbox(
    bb: BBox,
    anchor: tuple[float, float],
    grid: float,
) -> BBox:
    """Shift ``bb``'s top-left to the largest grid-aligned point that
    is still ``≤ (xmin, ymin)`` *and* congruent to ``anchor`` modulo
    ``grid``, then ceil the bottom-right out to a grid multiple of the
    same offset.

    The point: if every port is placed at integer-grid offsets from
    the anchor (which is true when the user designs ports at multiples
    of ``grid``), then re-anchoring to the returned origin makes every
    port's coordinate a multiple of ``grid``. The bbox still contains
    the original ``bb`` — it can only grow, never shrink.
    """
    if grid <= 0:
        return bb
    xmin, ymin, xmax, ymax = bb
    ax, ay = anchor
    # ``vx ≡ ax (mod grid)`` and ``vx ≤ xmin`` ⇒ ``vx`` is the unique
    # element of ``ax + grid·ℤ`` that lies in ``(xmin - grid, xmin]``.
    vx = _floor_step((xmin - ax) / grid) * grid + ax
    vy = _floor_step((ymin - ay) / grid) * grid + ay
    # Bottom-right: ceil out so the canvas footprint is itself a grid
    # multiple, which keeps every component's bbox edge on a grid line
    # (the column placer relies on this for clean inter-column wiring).
    vx2 = vx + _ceil_step((xmax - vx) / grid) * grid
    vy2 = vy + _ceil_step((ymax - vy) / grid) * grid
    return (vx, vy, vx2, vy2)


def _pick_anchor_port(
    ports: dict[str, tuple[float, float]],
) -> Optional[tuple[float, float]]:
    """Pick the top-most port — the natural "wiring origin" for column
    layout — breaking ties by left-most. Returns ``None`` when the
    glyph has no port markers at all (no anchor available, fall back
    to the raw geometric bbox)."""
    if not ports:
        return None
    return min(ports.values(), key=lambda xy: (xy[1], xy[0]))


def load_glyph(
    path: Path,
    default_w: float,
    default_h: float,
    *,
    snap_grid: float = 10.0,
) -> Optional[dict]:
    """Read a glyph SVG. Returns a dict with the keys::

        {
            "viewbox":     "x y w h",         # bbox in inner-svg coords
            "svg_viewbox": "x y w h",         # original SVG viewBox attribute
            "inner":       "<svg body>",
            "bbox_w":      float,             # bbox width on the canvas
            "bbox_h":      float,             # bbox height on the canvas
            "ports":       {port: (x, y)},    # relative to the bbox origin
        }

    or ``None`` if the file is missing / malformed. ``default_w`` /
    ``default_h`` are last-resort fallbacks when neither a viewBox nor
    width/height attributes are present *and* the geometric scan finds
    nothing renderable.

    The reported bbox starts from the *tight geometric bbox* of the
    drawing primitives + port markers — *not* the SVG ``viewBox``
    attribute, which is frequently the editor canvas and may not
    match the drawing. The geometric bbox is then shifted to align
    with the port grid so that every port coordinate becomes a
    multiple of ``snap_grid`` after re-anchoring (see below).

    ``snap_grid`` (default ``10``) is the routing-grid pitch that the
    autodraw layer uses. When non-zero, the bbox origin is snapped so
    that the topmost port (the natural "wiring terminal" for column
    layout) lands at a grid-aligned offset; provided the user designed
    every port at grid-multiple spacings from it, the rest follow. The
    bbox dimensions are also expanded out to grid multiples so that
    column edges stay on the grid.

    Pass ``snap_grid=0`` to disable snapping entirely and get the
    raw geometric bbox — useful for diagnosing port-placement bugs.
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

    # Parse the file's declared viewBox / width / height — kept for
    # display in the inspector and as a fallback when geometric_bbox
    # finds nothing.
    svg_vb_x = svg_vb_y = 0.0
    svg_vb_w = float(default_w)
    svg_vb_h = float(default_h)
    svg_vb_str = f"0 0 {default_w} {default_h}"
    vb_match = _VIEWBOX_RE.search(open_tag)
    if vb_match:
        svg_vb_str = vb_match.group(1).strip()
        try:
            tokens = svg_vb_str.replace(",", " ").split()
            if len(tokens) >= 4:
                svg_vb_x, svg_vb_y, svg_vb_w, svg_vb_h = (
                    float(t) for t in tokens[:4]
                )
        except ValueError:
            pass
    else:
        wm = _WIDTH_RE.search(open_tag)
        hm = _HEIGHT_RE.search(open_tag)
        if wm and hm:
            try:
                svg_vb_w = float(wm.group(1))
                svg_vb_h = float(hm.group(1))
                svg_vb_str = f"0 0 {svg_vb_w} {svg_vb_h}"
            except ValueError:
                pass

    inner_start = m_open.end()
    m_close = _SVG_CLOSE_RE.search(text, inner_start)
    inner_end = m_close.start() if m_close else len(text)
    inner = text[inner_start:inner_end].strip()

    ports = parse_port_markers(inner)

    # Compute the tight geometric bbox of drawing + port markers. Fall
    # back to the SVG-declared viewBox only if the geometric scan fails
    # entirely (empty file / unparseable XML / no recognised primitives).
    geom = geometric_bbox(inner)
    if geom is None:
        gx, gy = svg_vb_x, svg_vb_y
        gw, gh = svg_vb_w, svg_vb_h
    else:
        # Anchor the bbox to the topmost port's grid congruence: that
        # forces every port (which the user designs at grid-multiple
        # spacings from each other) to land on a routing-grid line
        # after the re-anchor step below. Glyphs without ports fall
        # through to the raw geometric bbox — there's no anchor to
        # reference and they wouldn't carry wires anyway.
        anchor = _pick_anchor_port(ports)
        if anchor is not None and snap_grid > 0:
            gx, gy, gx2, gy2 = _anchor_aligned_bbox(
                geom, anchor, snap_grid,
            )
        else:
            gx, gy, gx2, gy2 = geom
        gw = gx2 - gx
        gh = gy2 - gy

    # Re-anchor ports to the bbox origin so the autodraw layer can
    # treat them as offsets in 0..gw / 0..gh space. With the anchor
    # alignment above this also makes every offset a multiple of
    # ``snap_grid``; snap explicitly to absorb the inevitable float
    # noise from Inkscape's coordinate round-tripping.
    def _snap_to_grid(v: float) -> float:
        if snap_grid <= 0:
            return v
        rounded = round(v / snap_grid) * snap_grid
        return rounded if abs(v - rounded) <= _GRID_SNAP_TOL else v

    ports = {
        p: (_snap_to_grid(x - gx), _snap_to_grid(y - gy))
        for p, (x, y) in ports.items()
    }
    geom_vb_str = f"{gx:g} {gy:g} {gw:g} {gh:g}"

    return {
        "viewbox": geom_vb_str,
        "svg_viewbox": svg_vb_str,
        "inner": inner,
        "bbox_w": float(gw),
        "bbox_h": float(gh),
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
# Designator label placement.
# ---------------------------------------------------------------------------
def _label_rect(
    text_x: float, text_y: float, anchor: str, text: str, font_size: float,
) -> tuple[float, float, float, float]:
    """Approximate the visual bounding box of an SVG ``<text>`` element.

    Sans-serif glyph width averages ~0.6em; ascender + descender fit
    into roughly one em with a ~0.8/0.2 split around the baseline. Good
    enough for collision testing.
    """
    w = max(font_size * 0.6, 0.6 * font_size * len(text))
    if anchor == "middle":
        x0, x1 = text_x - w / 2.0, text_x + w / 2.0
    elif anchor == "end":
        x0, x1 = text_x - w, text_x
    else:  # "start"
        x0, x1 = text_x, text_x + w
    y0 = text_y - font_size * 0.8
    y1 = text_y + font_size * 0.2
    return (x0, y0, x1, y1)


def _rect_overlap_area(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    ow = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    oh = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return ow * oh


def _emit_designator_labels(
    placed: Sequence,
    polylines: Sequence[tuple[str, list[tuple[float, float]]]],
    canvas_w: float,
    canvas_h: float,
    *,
    label_fs: int,
    port_fs: int,
    short_port: callable,
    have_glyphs: dict,
    extra_obstacles: Sequence[tuple[float, float, float, float]] = (),
) -> list[str]:
    """Pick a low-overlap position next to each component for its label.

    Builds an obstacle list of every component bbox, every wire / rail
    segment (as a thin rect), and every pin label (also a rect). For
    each placed component, scores a small set of cardinal + corner
    candidate anchors by total overlap area against the obstacle list
    plus already-placed designator rects, and emits the label at the
    best candidate. Ties — including the all-zero "fully clear" tie —
    fall to candidate priority order (right, left, above, below, then
    corners), which matches the conventional schematic placement of
    component designators.

    Components rendered as the labelled-rect placeholder (kind not in
    ``have_glyphs``) keep their designator at the bbox centre — the
    rect's interior is empty and the label is the only thing
    identifying the part, so moving it outside hurts more than it
    helps.
    """
    obstacles: list[tuple[float, float, float, float]] = []

    # Component bboxes — keep labels off the glyph ink.
    for p in placed:
        bw, bh = p.desc.bbox_w, p.desc.bbox_h
        obstacles.append(
            (p.cx - bw / 2.0, p.cy - bh / 2.0,
             p.cx + bw / 2.0, p.cy + bh / 2.0)
        )

    # Pin label rects — match the positions used in emit_svg's port loop.
    for p in placed:
        for port, (px, py) in p.pin_pos.items():
            side = p.pin_side[port]
            text = short_port(port)
            if side == "left":
                tx, ty, anc = px - 4, py - 2, "end"
            elif side == "bot":
                tx, ty, anc = px + 4, py + 11, "start"
            else:  # top, right
                tx, ty, anc = px + 4, py - 2, "start"
            obstacles.append(_label_rect(tx, ty, anc, text, port_fs))

    # Caller-supplied obstacles (e.g. back-annotation rects, computed
    # before this pass so designators dodge them).
    obstacles.extend(extra_obstacles)

    # Wire / rail segments — thin axis-aligned rects with a small pad.
    wire_pad = 2.0
    for _cls, pts in polylines:
        if not pts:
            continue
        for (x1, y1), (x2, y2) in zip(pts[:-1], pts[1:]):
            obstacles.append((
                min(x1, x2) - wire_pad, min(y1, y2) - wire_pad,
                max(x1, x2) + wire_pad, max(y1, y2) + wire_pad,
            ))

    out: list[str] = []
    placed_label_rects: list[tuple[float, float, float, float]] = []

    for p in placed:
        d = p.desc
        if not getattr(d, "label", ""):
            continue
        bw, bh = d.bbox_w, d.bbox_h
        cx, cy = p.cx, p.cy
        margin = 4.0
        half = label_fs * 0.4  # baseline offset to vertically centre

        # Rect-placeholder components (no glyph for their kind) get the
        # designator at the bbox centre: the rect is otherwise empty,
        # and the label is the only mark identifying the part.
        if d.kind not in have_glyphs:
            tx, ty, anc = cx, cy + half, "middle"
            r = _label_rect(tx, ty, anc, d.label, label_fs)
            out.append(
                f'<text class="lab" x="{tx:.1f}" y="{ty:.1f}" '
                f'text-anchor="{anc}">{html_escape(d.label)}</text>'
            )
            placed_label_rects.append(r)
            continue

        # Candidates in priority order: E, W, N, S, then four corners.
        candidates = [
            (cx + bw / 2 + margin, cy + half, "start"),
            (cx - bw / 2 - margin, cy + half, "end"),
            (cx, cy - bh / 2 - margin, "middle"),
            (cx, cy + bh / 2 + margin + label_fs * 0.8, "middle"),
            (cx + bw / 2 + margin, cy - bh / 2 + half, "start"),
            (cx + bw / 2 + margin, cy + bh / 2 + half, "start"),
            (cx - bw / 2 - margin, cy - bh / 2 + half, "end"),
            (cx - bw / 2 - margin, cy + bh / 2 + half, "end"),
        ]

        best: Optional[tuple[float, float, str, tuple[float, float, float, float]]] = None
        best_score = float("inf")
        for tx, ty, anc in candidates:
            r = _label_rect(tx, ty, anc, d.label, label_fs)
            # Reject candidates that fall off the canvas.
            if r[0] < 0 or r[2] > canvas_w or r[1] < 0 or r[3] > canvas_h:
                continue
            score = 0.0
            for ob in obstacles:
                score += _rect_overlap_area(r, ob)
            for ob in placed_label_rects:
                score += _rect_overlap_area(r, ob)
            if score < best_score:
                best_score = score
                best = (tx, ty, anc, r)

        if best is None:
            # Every candidate is off-canvas — fall back to bbox centre.
            tx, ty, anc = cx, cy + half, "middle"
            best = (tx, ty, anc, _label_rect(tx, ty, anc, d.label, label_fs))

        out.append(
            f'<text class="lab" x="{best[0]:.1f}" y="{best[1]:.1f}" '
            f'text-anchor="{best[2]}">{html_escape(d.label)}</text>'
        )
        placed_label_rects.append(best[3])

    return out


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
    solder_dots: Optional[Sequence[tuple[float, float]]] = None,
    back_annotation: Optional[dict[str, Sequence[str]]] = None,
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
        ".solder{fill:#0a4;stroke:none}"
        ".lab{fill:#222}"
        ".plab{fill:#444;font-size:%dpx}"
        ".rlab{fill:#a00;font-weight:bold}"
        ".bann{fill:#e07b00;font-weight:300;font-size:%dpx}"
        "</style>" % (port_fs, max(8, port_fs))
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

    # Solder dots at Steiner T-junctions of routed wires. Painted
    # *after* the polylines so they cover the wire join cleanly.
    if solder_dots:
        for x, y in solder_dots:
            parts.append(
                f'<circle class="solder" cx="{x:.1f}" cy="{y:.1f}" r="2.5" />'
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

    # Designator labels: deferred to after every other primitive so we
    # can pick a position around each component bbox that overlaps the
    # least with neighbouring geometry. Drawing them at the bbox centre
    # (the original behaviour) made them collide with the glyph ink on
    # any component whose body fills the bbox (triodes, op-amps, sources).
    # Back annotations: positions are computed *before* designator
    # placement so the designator picker treats annotation rects as
    # obstacles and routes around them. The actual text elements are
    # appended at the very end of the SVG, so they remain the topmost
    # layer in z-order.
    annotation_emits: list[str] = []
    annotation_rects: list[tuple[float, float, float, float]] = []
    if back_annotation:
        ann_fs = max(8, port_fs)
        line_h = ann_fs + 1
        margin = 4.0
        by_label = {p.desc.label: p for p in placed if getattr(p.desc, "label", "")}
        for name, lines in back_annotation.items():
            p = by_label.get(name)
            if p is None or not lines:
                continue
            x = p.cx + p.desc.bbox_w / 2.0 + margin
            n = len(lines)
            # Stack centred vertically on the bbox, baseline at top of
            # each line — the topmost text sits ~ann_fs above centre
            # for a 1-line annotation, two lines straddle centre, etc.
            top_y = p.cy - (n - 1) * line_h / 2.0 + ann_fs * 0.3
            for i, line in enumerate(lines):
                ty = top_y + i * line_h
                text = str(line)
                annotation_emits.append(
                    f'<text class="bann" '
                    f'data-bann-comp="{html_escape(name)}" '
                    f'data-bann-line="{i}" '
                    f'x="{x:.1f}" y="{ty:.1f}">'
                    f'{html_escape(text)}</text>'
                )
                annotation_rects.append(
                    _label_rect(x, ty, "start", text, ann_fs)
                )

    parts.extend(
        _emit_designator_labels(
            placed, polylines, canvas_w, canvas_h,
            label_fs=label_fs, port_fs=port_fs, short_port=short_port,
            have_glyphs=have_glyphs,
            extra_obstacles=annotation_rects,
        )
    )

    parts.extend(annotation_emits)

    parts.append("</svg>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Glyph inspector — visualises what ``load_glyph`` actually parsed out of
# each ``res/<kind>.svg`` file. Generates a self-contained HTML page where
# every glyph is rendered next to its bounding box and port markers as the
# loader sees them; opening it lets you sanity-check that the bbox really
# spans the drawn symbol and that ports land where they should.
# ---------------------------------------------------------------------------
def _viewer_glyph_card(kind: str, glyph: dict) -> str:
    """Render one glyph card for the inspector page."""
    # Geometric bbox — the authoritative box autodraw reserves.
    vb_tokens = glyph["viewbox"].replace(",", " ").split()
    try:
        gx = float(vb_tokens[0])
        gy = float(vb_tokens[1])
    except (ValueError, IndexError):
        gx = gy = 0.0
    bw = float(glyph["bbox_w"])
    bh = float(glyph["bbox_h"])
    inner = glyph["inner"]
    ports = glyph["ports"]

    # Original SVG viewBox attribute — kept around so the inspector can
    # show it as a *secondary* outline, making any mismatch with the
    # geometric bbox immediately visible.
    svg_vb_str = glyph.get("svg_viewbox", glyph["viewbox"])
    svg_vb_tokens = svg_vb_str.replace(",", " ").split()
    svg_vb: Optional[tuple[float, float, float, float]]
    try:
        svg_vb = tuple(float(t) for t in svg_vb_tokens[:4])  # type: ignore[assignment]
        if len(svg_vb) != 4:  # pragma: no cover — defensive
            svg_vb = None
    except (ValueError, IndexError):
        svg_vb = None

    # Inspector frame: render in geometric-bbox coords. The inner
    # content uses original SVG coords, so translate by (-gx, -gy)
    # to align it with the bbox's 0,0 corner — same trick autodraw's
    # emit_svg uses.
    pad = max(2.0, min(bw, bh) * 0.12)
    frame_x = -pad
    frame_y = -pad
    frame_w = bw + 2 * pad
    frame_h = bh + 2 * pad

    if gx or gy:
        body = (
            f'<g transform="translate({-gx:g},{-gy:g})">'
            f'{inner}</g>'
        )
    else:
        body = inner

    overlays: list[str] = []
    # Geometric (tight) bbox.
    overlays.append(
        f'<rect class="bbox" x="0" y="0" '
        f'width="{bw:g}" height="{bh:g}" />'
    )
    # Original SVG viewBox, mapped into geometric-bbox coords.
    if svg_vb is not None:
        sx, sy, sw, sh = svg_vb
        overlays.append(
            f'<rect class="svg-vb" x="{sx - gx:g}" y="{sy - gy:g}" '
            f'width="{sw:g}" height="{sh:g}" />'
        )

    label_size = max(2.5, min(bw, bh) / 14.0)
    for port, (px, py) in sorted(ports.items()):
        overlays.append(
            f'<g class="port">'
            f'<circle cx="{px:g}" cy="{py:g}" r="1.6" />'
            f'<text x="{px:g}" y="{py - 2.5:g}" '
            f'font-size="{label_size:g}" text-anchor="middle">'
            f'{html_escape(port)}</text>'
            f'</g>'
        )

    annotated = (
        f'<svg class="glyph" xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{frame_x:g} {frame_y:g} {frame_w:g} {frame_h:g}" '
        f'preserveAspectRatio="xMidYMid meet">'
        f'{body}{"".join(overlays)}'
        f'</svg>'
    )

    if ports:
        port_rows = "".join(
            f"<tr><td>{html_escape(p)}</td>"
            f"<td>{x:.3f}</td><td>{y:.3f}</td></tr>"
            for p, (x, y) in sorted(ports.items())
        )
    else:
        port_rows = (
            '<tr><td colspan="3" class="none">no port markers</td></tr>'
        )

    return (
        f'<section class="card">'
        f'<h2>{html_escape(kind)}</h2>'
        f'<div class="frame">{annotated}</div>'
        f'<dl class="meta">'
        f'<dt>bbox</dt>'
        f'<dd>{bw:g} &times; {bh:g} <span class="muted">'
        f'@ ({gx:g}, {gy:g})</span></dd>'
        f'<dt>geom viewBox</dt>'
        f'<dd>{html_escape(glyph["viewbox"])}</dd>'
        f'<dt>SVG viewBox</dt>'
        f'<dd>{html_escape(svg_vb_str)}</dd>'
        f'</dl>'
        f'<table class="ports">'
        f'<thead><tr><th>port</th><th>x</th><th>y</th></tr></thead>'
        f'<tbody>{port_rows}</tbody>'
        f'</table>'
        f'</section>'
    )


def _viewer_html(
    cards: list[str], missing: list[str], res_dir_str: str
) -> str:
    if missing:
        items = "".join(f"<li>{html_escape(m)}</li>" for m in missing)
        missing_block = (
            f'<aside class="missing">'
            f"<h3>No SVG found for these kinds "
            f"(autodraw falls back to a labelled rect):</h3>"
            f"<ul>{items}</ul>"
            f"</aside>"
        )
    else:
        missing_block = ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>sycan glyph inspector</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    margin: 1.5em auto; max-width: 1200px; padding: 0 1em;
    line-height: 1.4;
  }}
  h1 {{ margin-bottom: 0.2em; }}
  p.sub {{ color: #888; margin-top: 0; font-size: 0.9em; }}
  code {{ font-family: "JetBrains Mono", Menlo, Consolas, monospace; }}

  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 16px;
    margin-top: 1em;
  }}
  .card {{
    border: 1px solid #bbb; border-radius: 8px;
    padding: 12px; background: #fff;
  }}
  .card h2 {{
    margin: 0 0 8px;
    font-family: "JetBrains Mono", Menlo, Consolas, monospace;
    font-size: 1em;
  }}
  .frame {{
    background: #fafafa; border: 1px solid #ddd;
    border-radius: 4px; padding: 8px;
    aspect-ratio: 1; display: flex;
    align-items: center; justify-content: center;
  }}
  .glyph {{ width: 100%; height: 100%; }}
  .glyph .bbox {{
    fill: rgba(0, 128, 255, 0.10);
    stroke: rgba(0, 128, 255, 0.85);
    stroke-width: 1.4;
    stroke-dasharray: 2 1.5;
    vector-effect: non-scaling-stroke;
  }}
  .glyph .svg-vb {{
    fill: none;
    stroke: rgba(160, 160, 160, 0.7);
    stroke-width: 1;
    stroke-dasharray: 1 1.5;
    vector-effect: non-scaling-stroke;
  }}
  .glyph .port circle {{
    fill: rgba(255, 80, 0, 0.9);
    stroke: white;
    vector-effect: non-scaling-stroke;
  }}
  .glyph .port text {{
    font-family: "JetBrains Mono", Menlo, Consolas, monospace;
    fill: rgba(180, 30, 0, 1);
    paint-order: stroke;
    stroke: white; stroke-width: 1.5; stroke-linejoin: round;
  }}

  dl.meta {{
    display: grid; grid-template-columns: max-content 1fr;
    gap: 2px 8px; margin: 8px 0; font-size: 0.85em;
    font-family: "JetBrains Mono", Menlo, Consolas, monospace;
  }}
  dl.meta dt {{ color: #888; }}
  dl.meta dd {{ margin: 0; word-break: break-all; }}
  dl.meta .muted {{ color: #888; }}

  table.ports {{
    width: 100%; border-collapse: collapse; font-size: 0.85em;
    font-family: "JetBrains Mono", Menlo, Consolas, monospace;
  }}
  table.ports th, table.ports td {{
    padding: 2px 6px; text-align: left;
    border-bottom: 1px solid #eee;
  }}
  table.ports th {{ color: #888; font-weight: normal; }}
  table.ports td.none {{
    color: #888; font-style: italic; text-align: center;
  }}

  aside.missing {{
    margin-top: 1.5em; padding: 12px;
    border: 1px solid #d99; border-radius: 6px; background: #fee;
  }}
  @media (prefers-color-scheme: dark) {{
    .card {{ background: #1a1a1a; border-color: #444; }}
    .frame {{ background: #0d0d0d; border-color: #333; }}
    .glyph .port text {{
      fill: rgba(255, 150, 100, 1); stroke: black;
    }}
    table.ports th, table.ports td {{ border-color: #333; }}
    aside.missing {{ background: #2a1010; border-color: #844; }}
  }}
</style>
</head>
<body>
<h1>sycan glyph inspector</h1>
<p class="sub">Loaded from <code>{html_escape(res_dir_str)}</code>.
<b style="color:rgb(0,128,255)">Blue dashed</b> rectangle = the
<i>geometric</i> bounding box (<code>bbox_w</code> &times;
<code>bbox_h</code>) computed from drawing primitives + port markers.
<b style="color:#888">Grey dotted</b> rectangle = the SVG file's own
<code>viewBox</code> attribute, mapped into the same frame so any
mismatch is visible. <span style="color:rgb(255,80,0)">Orange dots</span>
= port markers as returned by <code>load_glyph</code>, expressed
relative to the geometric-bbox origin.</p>
<div class="grid">
{"".join(cards)}
</div>
{missing_block}
</body>
</html>
"""


def view_glyphs(
    res_dir: Optional[Union[str, Path]] = None,
    *,
    default_w: float = 70.0,
    default_h: float = 60.0,
    output: Optional[Union[str, Path]] = None,
    open_browser: bool = True,
) -> Path:
    """Generate an HTML inspector for the glyphs under ``res_dir``.

    Each card shows the glyph rendered in a 0..bbox_w / 0..bbox_h frame,
    the parsed bounding box (translucent blue rectangle), and a labelled
    dot for every port marker that :func:`load_glyph` found. This is
    exactly the data that :func:`load_glyphs` returns to autodraw, so
    the page is the visual ground truth for what the placer is using.

    Parameters
    ----------
    res_dir:
        Directory of glyph SVGs. Defaults to ``<repo>/res/`` relative
        to this file.
    default_w, default_h:
        Fallback dimensions for SVGs with neither viewBox nor explicit
        width/height attributes (mirrors :func:`load_glyph`).
    output:
        Path to write the HTML to. If ``None`` (default), a temp file
        is created.
    open_browser:
        Launch the system default browser on the generated file.

    Returns
    -------
    The path to the HTML file that was written.
    """
    import os
    import tempfile
    import webbrowser

    if res_dir is None:
        res_dir = Path(__file__).resolve().parent.parent.parent / "res"
    res_dir = Path(res_dir)

    cards: list[str] = []
    missing: list[str] = []
    for kind in KIND_GLYPHS:
        glyph = load_glyph(res_dir / f"{kind}.svg", default_w, default_h)
        if glyph is None:
            missing.append(kind)
            continue
        cards.append(_viewer_glyph_card(kind, glyph))

    html = _viewer_html(cards, missing, str(res_dir))

    if output is None:
        fd, tmp = tempfile.mkstemp(prefix="sycan-glyphs-", suffix=".html")
        os.close(fd)
        out_path = Path(tmp)
    else:
        out_path = Path(output)
    out_path.write_text(html, encoding="utf-8")

    if open_browser:
        webbrowser.open(out_path.as_uri())

    return out_path


# ---------------------------------------------------------------------------
# Bode plot (independent of the schematic stack).
# ---------------------------------------------------------------------------
def bode_svg(
    omegas,
    mag_db,
    phase_deg,
    title: str = "",
    *,
    width: int = 720,
    height: int = 460,
    mag_range: tuple[float, float] = (-80.0, 10.0),
) -> str:
    """Return an inline SVG with stacked magnitude/phase Bode panels.

    ``omegas`` is the angular-frequency axis (assumed log-spaced and
    monotonically increasing). ``mag_db`` and ``phase_deg`` must be the
    same length as ``omegas``. The phase y-axis auto-ranges to the
    nearest 90° and a dashed -3 dB reference line is overlaid on the
    magnitude panel.
    """
    omegas = list(omegas)
    mag_db = list(mag_db)
    phase_deg = list(phase_deg)
    if not (len(omegas) == len(mag_db) == len(phase_deg)):
        raise ValueError("omegas, mag_db, phase_deg must have equal length")

    ML, MR, MT, MB, GAP = 64, 20, 28, 36, 32
    panel_h = (height - MT - MB - GAP) // 2
    plot_w = width - ML - MR

    log_w = [math.log10(w) for w in omegas]
    x0, x1 = log_w[0], log_w[-1]

    def xpx(lw: float) -> float:
        return ML + (lw - x0) / (x1 - x0) * plot_w

    y1_min, y1_max = mag_range
    def y1px(v: float) -> float:
        v = max(min(v, y1_max), y1_min)
        return MT + panel_h * (1 - (v - y1_min) / (y1_max - y1_min))

    p_lo = 90 * math.floor(min(phase_deg) / 90)
    p_hi = 90 * math.ceil(max(phase_deg) / 90)
    if p_hi <= p_lo:
        p_hi = p_lo + 90
    def y2px(v: float) -> float:
        return MT + panel_h + GAP + panel_h * (1 - (v - p_lo) / (p_hi - p_lo))

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'font-family="system-ui, sans-serif" font-size="11">',
    ]
    if title:
        out.append(
            f'<text x="{width/2}" y="16" text-anchor="middle" font-size="13" '
            f'font-weight="bold">{html_escape(title)}</text>'
        )

    # Frame each panel.
    out.append(
        f'<rect x="{ML}" y="{MT}" width="{plot_w}" height="{panel_h}" '
        'fill="none" stroke="#888"/>'
    )
    out.append(
        f'<rect x="{ML}" y="{MT+panel_h+GAP}" width="{plot_w}" '
        f'height="{panel_h}" fill="none" stroke="#888"/>'
    )

    # Decade gridlines and x-axis labels.
    for d in range(int(math.floor(x0)), int(math.ceil(x1)) + 1):
        x = xpx(d)
        out.append(
            f'<line x1="{x:.1f}" y1="{MT}" x2="{x:.1f}" '
            f'y2="{MT+panel_h}" stroke="#e0e0e0"/>'
        )
        out.append(
            f'<line x1="{x:.1f}" y1="{MT+panel_h+GAP}" x2="{x:.1f}" '
            f'y2="{MT+2*panel_h+GAP}" stroke="#e0e0e0"/>'
        )
        out.append(
            f'<text x="{x:.1f}" y="{height-MB+12}" '
            f'text-anchor="middle">10^{d}</text>'
        )

    # Magnitude y gridlines (every 20 dB).
    v = int(y1_min)
    while v <= int(y1_max):
        y = y1px(v)
        out.append(
            f'<line x1="{ML}" y1="{y:.1f}" x2="{ML+plot_w}" '
            f'y2="{y:.1f}" stroke="#eee"/>'
        )
        out.append(f'<text x="{ML-6}" y="{y+3:.1f}" text-anchor="end">{v}</text>')
        v += 20

    # Phase y gridlines: round 1/4 of span up to a 45° multiple.
    step = max(45, int(round((p_hi - p_lo) / 4 / 45)) * 45) or 45
    v = int(p_lo)
    while v <= int(p_hi):
        y = y2px(v)
        out.append(
            f'<line x1="{ML}" y1="{y:.1f}" x2="{ML+plot_w}" '
            f'y2="{y:.1f}" stroke="#eee"/>'
        )
        out.append(f'<text x="{ML-6}" y="{y+3:.1f}" text-anchor="end">{v}</text>')
        v += step

    # -3 dB reference line.
    y_3dB = y1px(-3)
    out.append(
        f'<line x1="{ML}" y1="{y_3dB:.1f}" x2="{ML+plot_w}" '
        f'y2="{y_3dB:.1f}" stroke="#bbb" stroke-dasharray="3,3"/>'
    )

    # Curves.
    pts_m = " ".join(
        f"{xpx(lw):.1f},{y1px(m):.1f}" for lw, m in zip(log_w, mag_db)
    )
    pts_p = " ".join(
        f"{xpx(lw):.1f},{y2px(p):.1f}" for lw, p in zip(log_w, phase_deg)
    )
    out.append(
        f'<polyline points="{pts_m}" fill="none" '
        'stroke="#1f77b4" stroke-width="1.6"/>'
    )
    out.append(
        f'<polyline points="{pts_p}" fill="none" '
        'stroke="#d62728" stroke-width="1.6"/>'
    )

    # Axis labels.
    out.append(f'<text x="{ML}" y="{MT-8}" font-style="italic">|H| [dB]</text>')
    out.append(
        f'<text x="{ML}" y="{MT+panel_h+GAP-8}" font-style="italic">∠H [deg]</text>'
    )
    out.append(
        f'<text x="{width/2}" y="{height-4}" text-anchor="middle">ω [rad/s]</text>'
    )
    out.append('</svg>')
    return "".join(out)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="Inspect sycan glyph SVGs in a browser."
    )
    ap.add_argument(
        "res_dir", nargs="?", default=None,
        help="Glyph directory (default: <repo>/res/).",
    )
    ap.add_argument(
        "-o", "--output", default=None,
        help="Write HTML to this path (default: temp file).",
    )
    ap.add_argument(
        "--no-open", action="store_true",
        help="Don't open the file in a browser.",
    )
    args = ap.parse_args()
    path = view_glyphs(
        args.res_dir,
        output=args.output,
        open_browser=not args.no_open,
    )
    print(path)
