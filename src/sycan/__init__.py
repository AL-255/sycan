"""sycan: symbolic circuit analysis."""
from importlib.metadata import PackageNotFoundError, version as _pkg_version

from sycan.circuit import Circuit, print_hierarchy
from sycan.components.active import (
    BJT,
    Diode,
    NJFET,
    NMOS_3T,
    NMOS_4T,
    NMOS_L1,
    NMOS_subthreshold,
    PJFET,
    PMOS_3T,
    PMOS_4T,
    PMOS_L1,
    PMOS_subthreshold,
    Triode,
)
from sycan.components.blocks import (
    Gain,
    Integrator,
    OPAMP,
    OPAMP1,
    Quantizer,
    SubCircuit,
    Summer,
    TransferFunction,
)
from sycan.components.rf import TLINE
from sycan.network_params import (
    abcd_to_s,
    abcd_to_y,
    abcd_to_z,
    s_to_abcd,
    s_to_t,
    s_to_y,
    s_to_z,
    t_to_s,
    y_to_abcd,
    y_to_s,
    y_to_z,
    z_to_abcd,
    z_to_s,
    z_to_y,
)
from sycan.components.basic import (
    BehavioralCurrent,
    BehavioralVoltage,
    CCCS,
    CCVS,
    Capacitor,
    CurrentSource,
    GND,
    Inductor,
    MutualCoupling,
    Port,
    Resistor,
    VCCS,
    VCVS,
    VSwitch,
    Varactor,
    VoltageSource,
)
from sycan.check import ERCFinding, ERCReport, check_circuit
from sycan.headroom import HeadroomResult, solve_headroom
from sycan.mna import (
    Component,
    NoiseSource,
    NoiseSpec,
    StampContext,
    TransientResult,
    build_mna,
    build_residuals,
    k_B,
    q,
    solve,
    solve_ac,
    solve_dc,
    solve_dc_sweep,
    solve_impedance,
    solve_noise,
    solve_pz,
    solve_sensitivity,
    solve_tf,
    solve_transient,
)
from sycan.components.basic.voltage_source import (
    waveform_laplace,
    waveform_time,
)
from sycan.assumptions import (
    Approximate,
    Assumption,
    CheckResult,
    Limit,
    MuchGreater,
    MuchLess,
    Region,
    apply_assumptions,
    check_assumptions,
    format_check_report,
    violations,
)
from sycan.mna import T as T_kelvin  # avoid shadowing trans-line letter elsewhere
from sycan.autodraw import autodraw
from sycan.polynomials import bessel, butterworth, chebyshev1
from sycan.schematic import draw
from sycan.spice import parse, parse_file, parse_value, to_spice, write_file
from sycan.svg_util import bode_svg

__all__ = [
    "BJT",
    "BehavioralCurrent",
    "BehavioralVoltage",
    "CCCS",
    "CCVS",
    "Capacitor",
    "Circuit",
    "Component",
    "CurrentSource",
    "Diode",
    "ERCFinding",
    "ERCReport",
    "GND",
    "Gain",
    "HeadroomResult",
    "Inductor",
    "Integrator",
    "MutualCoupling",
    "NJFET",
    "NMOS_3T",
    "NMOS_4T",
    "NMOS_L1",
    "NMOS_subthreshold",
    "NoiseSource",
    "NoiseSpec",
    "OPAMP",
    "OPAMP1",
    "PJFET",
    "PMOS_3T",
    "PMOS_4T",
    "PMOS_L1",
    "PMOS_subthreshold",
    "Port",
    "Quantizer",
    "Resistor",
    "StampContext",
    "SubCircuit",
    "Summer",
    "T_kelvin",
    "TLINE",
    "TransferFunction",
    "TransientResult",
    "Triode",
    "k_B",
    "q",
    "abcd_to_s",
    "abcd_to_y",
    "abcd_to_z",
    "s_to_abcd",
    "s_to_t",
    "s_to_y",
    "s_to_z",
    "t_to_s",
    "y_to_abcd",
    "y_to_s",
    "y_to_z",
    "z_to_abcd",
    "z_to_s",
    "z_to_y",
    "VCCS",
    "VCVS",
    "VSwitch",
    "Varactor",
    "VoltageSource",
    "autodraw",
    "bessel",
    "check_circuit",
    "bode_svg",
    "build_mna",
    "build_residuals",
    "butterworth",
    "chebyshev1",
    "draw",
    "parse",
    "parse_file",
    "parse_value",
    "solve_ac",
    "solve_dc",
    "solve_dc_sweep",
    "solve_headroom",
    "solve_impedance",
    "solve_noise",
    "solve_pz",
    "solve_sensitivity",
    "solve_tf",
    "solve_transient",
    "solve",
    "waveform_laplace",
    "waveform_time",
    "main",
    "print_hierarchy",
    "to_spice",
    "write_file",
    # Assumption engine.
    "Approximate",
    "Assumption",
    "CheckResult",
    "Limit",
    "MuchGreater",
    "MuchLess",
    "Region",
    "apply_assumptions",
    "check_assumptions",
    "format_check_report",
    "violations",
]


try:
    __version__ = _pkg_version("sycan")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the ``sycan`` console script."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="sycan",
        description="SYmbolic Circuit ANalysis",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"sycan {__version__}",
    )
    sub = parser.add_subparsers(dest="command")

    parse_cmd = sub.add_parser(
        "parse",
        help="Parse a SPICE netlist and print the resulting Circuit",
    )
    parse_cmd.add_argument("netlist", help="Path to a SPICE netlist file")

    args = parser.parse_args(argv)

    if args.command == "parse":
        circuit = parse_file(args.netlist)
        print(circuit)
        return 0

    parser.print_help()
    return 0
