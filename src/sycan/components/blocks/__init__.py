"""Linear system-modelling blocks and hierarchical subcircuits.

Behavioural building blocks for signal-flow / loop-filter modelling
that stamp into the existing MNA framework as VCVS-style elements:
high-impedance differential inputs, voltage-forced differential
output, and a single auxiliary branch current per block.

Intended use cases include sigma-delta modulators, behavioural
filters, and control-system loop analysis where the device-level
realisation is irrelevant.

The :class:`SubCircuit` element provides hierarchical design support:
any :class:`~sycan.circuit.Circuit` can be wrapped, named, and
instantiated multiple times in a parent circuit. :class:`OPAMP` is
the first concrete subcircuit — an ideal differential VCVS-style
op-amp.
"""
from sycan.components.blocks.gain import Gain
from sycan.components.blocks.integrator import Integrator
from sycan.components.blocks.opamp import OPAMP
from sycan.components.blocks.quantizer import Quantizer
from sycan.components.blocks.subcircuit import SubCircuit
from sycan.components.blocks.summer import Summer
from sycan.components.blocks.transfer_function import TransferFunction

__all__ = [
    "Gain",
    "Integrator",
    "OPAMP",
    "Quantizer",
    "SubCircuit",
    "Summer",
    "TransferFunction",
]
