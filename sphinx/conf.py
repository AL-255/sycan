"""Sphinx configuration for the sycan API documentation."""
from __future__ import annotations

import os
import sys
from importlib import metadata

# Allow autodoc to import the package without a prior `pip install .`
# (CI installs the package via `uv sync`, which makes this a no-op there
# but keeps `sphinx-build` working from a fresh checkout).
sys.path.insert(0, os.path.abspath("../src"))

project = "sycan"
author = "AL-255"
copyright = "2026, AL-255"

try:
    release = metadata.version("sycan")
except metadata.PackageNotFoundError:
    release = "0.0.0"
version = ".".join(release.split(".")[:2])

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "myst_parser",
]

autosummary_generate = True
autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
}
autodoc_typehints = "description"
autodoc_member_order = "bysource"

napoleon_google_docstring = True
napoleon_numpy_docstring = True

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "sympy": ("https://docs.sympy.org/latest/", None),
}

myst_enable_extensions = ["colon_fence", "deflist"]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
source_suffix = {".rst": "restructuredtext", ".md": "markdown"}

html_theme = "furo"
html_static_path = ["_static"]
html_title = f"sycan {release}"
html_logo = "_static/sycan_s.png"
html_favicon = "_static/sycan_s.png"
