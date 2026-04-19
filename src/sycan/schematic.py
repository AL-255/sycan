"""Render a SPICE netlist as a Circuitikz schematic via lcapy.

This writes a standalone ``.tex`` file (no pdflatex invocation), which
can later be compiled with ``pdflatex`` + the ``circuitikz`` package to
obtain a PDF/PNG/SVG. The output extension is forced to ``.tex`` so
lcapy does not try to run LaTeX.

Layout hints and ideal wires (SPICE ``W`` elements) may be carried
directly in the testbench netlist: our SPICE parser strips the ``;``
layout annotations as inline comments while lcapy consumes them.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

_GND_RE = re.compile(r"^gnd(\d*)$", re.IGNORECASE)


def _rewrite_gnd(line: str) -> str:
    """``GND[n] node`` → ``Wgnd[n] node 0; down=0.3`` so lcapy can draw it.

    A short fixed length keeps the ground connection visually tight.
    """
    core, sep, _hints = line.partition(";")
    tokens = core.split()
    if len(tokens) < 2:
        return line
    m = _GND_RE.match(tokens[0])
    if not m:
        return line
    return f"Wgnd{m.group(1)} {tokens[1]} 0; down=0.3"


def _strip_for_lcapy(netlist: str) -> str:
    """Remove SPICE bookkeeping lcapy does not understand.

    - first line (SPICE title) is dropped
    - ``.end`` ends parsing; other dot-directives are skipped
    - ``*`` comments are skipped
    - ``+`` continuations are folded into the preceding element

    ``;``-suffixed layout hints are preserved for lcapy.
    """
    out: list[str] = []
    for i, raw in enumerate(netlist.splitlines(), 1):
        if i == 1:
            continue
        stripped = raw.strip()
        if not stripped or stripped.startswith("*"):
            continue
        if stripped.lower() == ".end":
            break
        if stripped.startswith("."):
            continue
        if stripped.startswith("+"):
            if out:
                out[-1] = f"{out[-1]} {stripped[1:].strip()}"
            continue
        out.append(_rewrite_gnd(stripped))
    if any("0" in line.split(";", 1)[0].split() for line in out):
        # Tack on a dangling wire so lcapy draws a ground symbol at node 0.
        out.append("Wgndsym 0 0_gndsym; down=0.3, ground")
    return "\n".join(out)


def draw(netlist: str, filename: str | Path) -> Path:
    """Render ``netlist`` to a Circuitikz ``.tex`` file.

    Returns the path written. The caller may compile the output with::

        pdflatex <filename>

    to obtain a PDF (or use `dvisvgm` / `pdf2svg` for SVG).
    """
    from lcapy import Circuit as LCircuit

    path = Path(filename)
    if path.suffix.lower() not in {".tex", ".schtex"}:
        path = path.with_suffix(".tex")
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        cct = LCircuit(_strip_for_lcapy(netlist))
        cct.draw(str(path))
    except Exception as e:
        # Fall back so a missing hint does not break ``pytest --draw``.
        path.write_text(
            "% lcapy could not lay out this circuit.\n"
            f"% Error: {type(e).__name__}: {e}\n"
            "%\n"
            "% Add '; right' / '; down' hints and split grounds\n"
            "% (0_1, 0_2, ... connected by W wires) in the testbench netlist.\n"
            "%\n"
            "% Original netlist:\n"
            + "\n".join(f"% {line}" for line in netlist.splitlines())
            + "\n"
        )
    return path


def render_png(tex_path: str | Path, dpi: int = 200) -> Path | None:
    """Compile a Circuitikz ``.tex`` to PNG alongside it.

    Uses ``pdflatex`` to produce a PDF, then ``pdftoppm`` to rasterize.
    If the source is a stub (lcapy layout failure) or either tool is
    missing, returns ``None`` and leaves no PNG behind.
    """
    tex = Path(tex_path)
    if not tex.exists():
        return None
    if tex.read_text(errors="ignore").lstrip().startswith("% lcapy could not"):
        return None
    if shutil.which("pdflatex") is None or shutil.which("pdftoppm") is None:
        return None

    png = tex.with_suffix(".png")
    # pdflatex writes its aux/log files into -output-directory.
    subprocess.run(
        [
            "pdflatex",
            "-interaction=nonstopmode",
            "-halt-on-error",
            f"-output-directory={tex.parent}",
            tex.name,
        ],
        cwd=tex.parent,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pdf = tex.with_suffix(".pdf")
    subprocess.run(
        ["pdftoppm", "-png", "-singlefile", "-r", str(dpi), pdf.name, png.stem],
        cwd=tex.parent,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Clean pdflatex droppings.
    for ext in (".aux", ".log", ".pdf"):
        tex.with_suffix(ext).unlink(missing_ok=True)
    return png
