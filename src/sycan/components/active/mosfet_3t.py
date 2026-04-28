"""Three-terminal MOSFET — backward-compat shim.

The real implementation lives in :mod:`sycan.components.active.mosfet_4t`,
where ``NMOS_3T`` / ``PMOS_3T`` are wrappers around the four-terminal
``NMOS_4T`` / ``PMOS_4T`` model with the bulk tied to the source.
This module re-exports them so existing imports (`from
sycan.components.active.mosfet_3t import NMOS_3T, PMOS_3T`) keep
working unchanged.
"""
from sycan.components.active.mosfet_4t import (
    NMOS_3T,
    PMOS_3T,
    _MOSFET_4T as _MOSFET_3T,  # legacy alias for the abstract base
)

__all__ = ["NMOS_3T", "PMOS_3T"]
