"""Linear system-modelling blocks.

Behavioural building blocks for signal-flow / loop-filter modelling
that stamp into the existing MNA framework as VCVS-style elements:
high-impedance differential inputs, voltage-forced differential
output, and a single auxiliary branch current per block.

Intended use cases include sigma-delta modulators, behavioural
filters, and control-system loop analysis where the device-level
realisation is irrelevant.
"""
from sycan.components.blocks.gain import Gain
from sycan.components.blocks.integrator import Integrator
from sycan.components.blocks.quantizer import Quantizer
from sycan.components.blocks.summer import Summer
from sycan.components.blocks.transfer_function import TransferFunction

__all__ = [
    "Gain",
    "Integrator",
    "Quantizer",
    "Summer",
    "TransferFunction",
]
