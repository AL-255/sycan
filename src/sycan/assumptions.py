"""Assumption engine — symbolic constraints applied to MNA solutions.

An :class:`Assumption` carries two pieces of information:

* :meth:`Assumption.apply` — how to *transform* a symbolic solution
  expression to fold the assumption in. ``Limit`` takes a sympy
  ``limit()``; ``MuchGreater`` rewrites the small quantity as
  :math:`\\epsilon` times the big one and takes :math:`\\epsilon \\to 0`;
  ``Region`` is a no-op on the expression (it doesn't change the
  equations — it just makes a claim about the operating point that
  the checker will verify after solving).
* :meth:`Assumption.check` — given a fully-solved operating point,
  produce a :class:`CheckResult` describing whether the assumption
  actually held. ``Limit`` claims aren't verifiable from a numeric
  solution and pass trivially; ``Region`` checks the device's actual
  ``V_GS`` / ``V_DS`` (or ``V_BE`` / ``V_BC``) against the requested
  region's defining inequalities.

The unified :func:`sycan.solve` entry point composes assumptions
attached to the :class:`~sycan.Circuit` with any passed directly to
the solver and applies them after the matrix solve.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Optional, Union

from sycan import cas as cas

if TYPE_CHECKING:
    from sycan.circuit import Circuit
    from sycan.mna import Component


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    """Outcome of post-solve verification of one :class:`Assumption`.

    ``passed`` is the boolean verdict. ``description`` reproduces the
    assumption's textual form. ``detail`` carries human-readable
    extra context — typically the failing inequality or the numeric
    value that broke the claim. ``measured`` is an optional dict of
    symbolic / numeric quantities the check evaluated (e.g.
    ``{"V_GS": 0.7, "V_TH": 0.5}``) so callers can render their own
    diagnostic.
    """

    passed: bool
    description: str
    detail: Optional[str] = None
    measured: dict[str, cas.Expr] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.passed


# ---------------------------------------------------------------------------
# Assumption hierarchy
# ---------------------------------------------------------------------------

class Assumption(ABC):
    """Base class. Subclasses override :meth:`apply` / :meth:`check`."""

    @abstractmethod
    def describe(self) -> str: ...

    def apply(self, value: Any) -> Any:
        """Transform a symbolic expression (or dict of them) under the
        assumption. Default is a no-op for assumptions that don't
        change the equations — e.g. region assumptions.
        """
        return value

    def check(
        self,
        solution: Mapping[cas.Symbol, cas.Expr],
        circuit: "Circuit",
    ) -> CheckResult:
        """Verify the assumption against a solved operating point.

        Default returns *passed* with a "no runtime check" note — used
        by assumptions like ``Limit`` whose claims can't be evaluated
        against the post-substitution solution.
        """
        return CheckResult(True, self.describe(), "no runtime check")

    # Convenience for use in dict / set keying when needed.
    def __hash__(self) -> int:
        return id(self)


def _apply_to_dict_or_expr(value, fn):
    """Helper: apply ``fn`` to a dict's values or to a single expr."""
    if isinstance(value, dict):
        return {k: _apply_to_dict_or_expr(v, fn) for k, v in value.items()}
    if isinstance(value, cas.Basic):
        return fn(value)
    return value


def _safe_limit(expr, sym, target):
    """``cas.limit`` with fallback substitution.

    Sympy's :func:`limit` raises on a handful of pathological forms.
    For most circuit-analysis expressions it works; when it fails we
    fall back to substituting a finite ``target``, or to leaving the
    expression unchanged if the target is infinite (so the user sees
    the un-limited form rather than a crash).
    """
    try:
        return cas.limit(expr, sym, target)
    except (NotImplementedError, ValueError, RecursionError):
        if target in (cas.oo, -cas.oo, getattr(cas, "zoo", None)):
            return expr
        try:
            return expr.subs(sym, target)
        except Exception:
            return expr


@dataclass
class Limit(Assumption):
    """``symbol → target`` — fold into the result via :func:`cas.limit`.

    The classic use is ``Limit(A, cas.oo)`` to push an op-amp's
    open-loop gain to infinity, which collapses the closed-loop
    expression to the ideal form. ``target`` may be any sympy expr;
    ``cas.oo``, ``-cas.oo``, and ``0`` are the common cases.
    """
    symbol: cas.Symbol
    target: cas.Expr

    def describe(self) -> str:
        return f"{self.symbol} → {self.target}"

    def apply(self, value: Any) -> Any:
        return _apply_to_dict_or_expr(
            value, lambda e: _safe_limit(e, self.symbol, self.target)
        )


@dataclass
class MuchGreater(Assumption):
    """``big >> small`` — fold by sending the small quantity to zero
    relative to the big one.

    The default strategy depends on which side is a free symbol:

    * If ``big`` is a :class:`~sympy.Symbol`, take ``big → ∞``.
    * Else if ``small`` is a :class:`~sympy.Symbol`, take ``small → 0``.
    * Otherwise rewrite ``small = ε · big`` and take ``ε → 0``.
    """
    big: cas.Expr
    small: cas.Expr

    def describe(self) -> str:
        return f"{self.big} >> {self.small}"

    def apply(self, value: Any) -> Any:
        big = cas.sympify(self.big)
        small = cas.sympify(self.small)

        def transform(expr):
            if big.is_symbol:
                return _safe_limit(expr, big, cas.oo)
            if small.is_symbol:
                return _safe_limit(expr, small, 0)
            # General path: replace small with ε·big, then ε→0.
            eps = cas.Dummy("epsilon")
            with_eps = expr.xreplace({small: eps * big})
            return _safe_limit(with_eps, eps, 0)

        return _apply_to_dict_or_expr(value, transform)


@dataclass
class MuchLess(Assumption):
    """``small << big`` — sugar for :class:`MuchGreater(big, small)`."""
    small: cas.Expr
    big: cas.Expr

    def describe(self) -> str:
        return f"{self.small} << {self.big}"

    def apply(self, value: Any) -> Any:
        return MuchGreater(big=self.big, small=self.small).apply(value)


@dataclass
class Approximate(Assumption):
    """``symbol ≈ value`` — pure substitution, no limit.

    Used when a free symbol should be pinned to a concrete value (e.g.
    ``Approximate(R_load, 50)``) without changing solver behaviour.
    """
    symbol: cas.Symbol
    value: cas.Expr

    def describe(self) -> str:
        return f"{self.symbol} ≈ {self.value}"

    def apply(self, value: Any) -> Any:
        return _apply_to_dict_or_expr(
            value, lambda e: e.subs(self.symbol, cas.sympify(self.value))
        )


@dataclass
class Region(Assumption):
    """``component`` operates in a named region.

    Recognised regions per device:

    * MOSFET (any flavour): ``"saturation"``, ``"triode"``, ``"cutoff"``
    * BJT: ``"forward-active"`` (alias ``"active"``), ``"saturation"``,
      ``"cutoff"``, ``"reverse-active"``
    * Diode: ``"forward"``, ``"reverse"``

    ``apply`` is a no-op — region assumptions don't transform the
    symbolic solution. The :meth:`check` method evaluates the device's
    actual operating point against the region's defining inequalities
    and returns the verdict in a :class:`CheckResult`.
    """
    component: str
    region: str

    def describe(self) -> str:
        return f"{self.component} in {self.region}"

    def check(
        self,
        solution: Mapping[cas.Symbol, cas.Expr],
        circuit: "Circuit",
    ) -> CheckResult:
        comp = _find_component(circuit, self.component)
        if comp is None:
            return CheckResult(
                False, self.describe(),
                f"component {self.component!r} not found in circuit"
            )
        return _check_region(comp, self.region, solution)


# ---------------------------------------------------------------------------
# Region-check helpers
# ---------------------------------------------------------------------------

def _find_component(circuit: "Circuit", name: str):
    """Return the named component, scanning the flat hierarchy first.

    Falls back to the top-level component list so callers can look up
    a :class:`~sycan.SubCircuit` by its (unflattened) instance name —
    grouped designs benefit from being able to reference either the
    wrapper or one of its leaves.
    """
    try:
        for c in circuit.flat_components():
            if getattr(c, "name", None) == name:
                return c
    except Exception:
        # ``flat_components`` may fail mid-hierarchy if the circuit is
        # partially built; fall through to the top-level scan.
        pass
    for c in circuit.components:
        if getattr(c, "name", None) == name:
            return c
    return None


def _node_voltage(solution: Mapping[cas.Symbol, cas.Expr], node: str):
    """``V(node)`` if present in ``solution``, with ``"0"`` mapped to 0."""
    if node == "0":
        return cas.Integer(0)
    sym = cas.Symbol(f"V({node})")
    return solution.get(sym)


def _diff(solution, n_plus: str, n_minus: str):
    a = _node_voltage(solution, n_plus)
    b = _node_voltage(solution, n_minus)
    if a is None or b is None:
        return None
    return cas.simplify(a - b)


def _truthy(expr) -> Optional[bool]:
    """Best-effort boolean evaluation of a (possibly symbolic) inequality.

    Returns ``True`` / ``False`` when sympy can decide, or ``None``
    when the inequality still has free symbols. The caller decides how
    to surface "indeterminate" — :class:`Region.check` reports it as a
    failed check with an explanatory detail rather than crashing.
    """
    if expr is True or expr is False:
        return bool(expr)
    try:
        truth = bool(expr)
        return truth
    except TypeError:
        return None


def _check_mosfet_region(comp, region: str, solution) -> CheckResult:
    """Evaluate MOSFET operating region from solved V_GS / V_DS.

    Internally uses the same polarity-aware effective voltages the
    device's :meth:`_I_D_expr` uses (``V_GS_eff = pol·V_GS``), so the
    check stays consistent with the conduction equation. The result's
    ``measured`` dict carries the computed ``V_GS_eff``, ``V_DS_eff``,
    ``V_TH``, and overdrive ``V_OV`` so callers can render a precise
    diagnostic on failure.
    """
    name = comp.name
    region_lc = region.lower().replace("-", "_")
    polarity = getattr(comp, "polarity", None)
    if polarity not in ("N", "P"):
        return CheckResult(
            False, f"{name} in {region}",
            f"component {name!r} has no MOSFET polarity"
        )
    pol = 1 if polarity == "N" else -1
    V_GS = _diff(solution, comp.gate, comp.source)
    V_DS = _diff(solution, comp.drain, comp.source)
    if V_GS is None or V_DS is None:
        return CheckResult(
            False, f"{name} in {region}",
            "node voltages not available in solution",
        )
    V_GS_eff = pol * V_GS
    V_DS_eff = pol * V_DS
    V_TH = comp.V_TH
    V_OV = cas.simplify(V_GS_eff - V_TH)
    measured = {
        "V_GS_eff": V_GS_eff,
        "V_DS_eff": V_DS_eff,
        "V_TH": V_TH,
        "V_OV": V_OV,
    }

    on = _truthy(V_OV > 0)
    sat = _truthy(V_DS_eff > V_OV) if on is True else None
    cutoff_ok = _truthy(V_GS_eff <= V_TH)

    if region_lc in ("saturation", "sat"):
        if on is False:
            return CheckResult(
                False, f"{name} in saturation",
                f"device is cutoff: V_GS_eff={V_GS_eff} ≤ V_TH={V_TH}",
                measured,
            )
        if on is None:
            return CheckResult(
                False, f"{name} in saturation",
                "on/off condition could not be decided symbolically; "
                "supply numeric values or further assumptions",
                measured,
            )
        if sat is True:
            return CheckResult(True, f"{name} in saturation",
                               None, measured)
        if sat is False:
            return CheckResult(
                False, f"{name} in saturation",
                f"device is in triode: V_DS_eff={V_DS_eff} ≤ V_OV={V_OV}",
                measured,
            )
        return CheckResult(
            False, f"{name} in saturation",
            "V_DS vs V_OV could not be decided symbolically",
            measured,
        )

    if region_lc == "triode":
        if on is False or on is None:
            return CheckResult(
                False, f"{name} in triode",
                f"device not on or undecidable: V_OV={V_OV}",
                measured,
            )
        in_triode = _truthy(V_DS_eff < V_OV)
        if in_triode is True:
            return CheckResult(True, f"{name} in triode", None, measured)
        if in_triode is False:
            return CheckResult(
                False, f"{name} in triode",
                f"device is in saturation: V_DS_eff={V_DS_eff} ≥ V_OV={V_OV}",
                measured,
            )
        return CheckResult(False, f"{name} in triode",
                           "V_DS vs V_OV could not be decided", measured)

    if region_lc == "cutoff":
        if cutoff_ok is True:
            return CheckResult(True, f"{name} in cutoff", None, measured)
        if cutoff_ok is False:
            return CheckResult(
                False, f"{name} in cutoff",
                f"device is on: V_GS_eff={V_GS_eff} > V_TH={V_TH}",
                measured,
            )
        return CheckResult(False, f"{name} in cutoff",
                           "V_GS vs V_TH could not be decided", measured)

    return CheckResult(False, f"{name} in {region}",
                       f"unknown MOSFET region {region!r}; "
                       "valid: 'saturation', 'triode', 'cutoff'")


def _check_bjt_region(comp, region: str, solution) -> CheckResult:
    """Evaluate BJT operating region from solved V_BE / V_BC."""
    name = comp.name
    region_lc = region.lower().replace("-", "_")
    polarity = comp.polarity
    pol = 1 if polarity == "NPN" else -1
    V_BE = _diff(solution, comp.base, comp.emitter)
    V_BC = _diff(solution, comp.base, comp.collector)
    if V_BE is None or V_BC is None:
        return CheckResult(False, f"{name} in {region}",
                           "node voltages not available in solution")
    V_BE_eff = pol * V_BE
    V_BC_eff = pol * V_BC
    measured = {"V_BE_eff": V_BE_eff, "V_BC_eff": V_BC_eff}

    be_fwd = _truthy(V_BE_eff > 0)
    bc_fwd = _truthy(V_BC_eff > 0)

    if region_lc in ("forward_active", "active", "fwd_active"):
        if be_fwd is True and bc_fwd is False:
            return CheckResult(True, f"{name} in forward-active",
                               None, measured)
        return CheckResult(
            False, f"{name} in forward-active",
            f"expected V_BE>0 and V_BC<0, got V_BE_eff={V_BE_eff}, "
            f"V_BC_eff={V_BC_eff}",
            measured,
        )

    if region_lc in ("reverse_active", "rev_active"):
        if be_fwd is False and bc_fwd is True:
            return CheckResult(True, f"{name} in reverse-active",
                               None, measured)
        return CheckResult(
            False, f"{name} in reverse-active",
            f"expected V_BE<0 and V_BC>0, got V_BE_eff={V_BE_eff}, "
            f"V_BC_eff={V_BC_eff}",
            measured,
        )

    if region_lc == "saturation":
        if be_fwd is True and bc_fwd is True:
            return CheckResult(True, f"{name} in saturation",
                               None, measured)
        return CheckResult(
            False, f"{name} in saturation",
            f"expected both junctions forward, got V_BE_eff={V_BE_eff}, "
            f"V_BC_eff={V_BC_eff}",
            measured,
        )

    if region_lc == "cutoff":
        if be_fwd is False and bc_fwd is False:
            return CheckResult(True, f"{name} in cutoff", None, measured)
        return CheckResult(
            False, f"{name} in cutoff",
            f"expected both junctions reverse, got V_BE_eff={V_BE_eff}, "
            f"V_BC_eff={V_BC_eff}",
            measured,
        )

    return CheckResult(False, f"{name} in {region}",
                       f"unknown BJT region {region!r}; valid: "
                       "'forward-active', 'reverse-active', "
                       "'saturation', 'cutoff'")


def _check_diode_region(comp, region: str, solution) -> CheckResult:
    name = comp.name
    region_lc = region.lower()
    V_AK = _diff(solution, comp.anode, comp.cathode)
    if V_AK is None:
        return CheckResult(False, f"{name} in {region}",
                           "node voltages not available")
    measured = {"V_AK": V_AK}
    fwd = _truthy(V_AK > 0)
    if region_lc == "forward":
        if fwd is True:
            return CheckResult(True, f"{name} forward", None, measured)
        return CheckResult(False, f"{name} forward",
                           f"reverse-biased: V_AK={V_AK}", measured)
    if region_lc == "reverse":
        if fwd is False:
            return CheckResult(True, f"{name} reverse", None, measured)
        return CheckResult(False, f"{name} reverse",
                           f"forward-biased: V_AK={V_AK}", measured)
    return CheckResult(False, f"{name} in {region}",
                       f"unknown diode region {region!r}; "
                       "valid: 'forward', 'reverse'")


def _check_region(comp, region: str, solution) -> CheckResult:
    """Dispatch to the right region checker based on device class."""
    from sycan.components.active import (
        BJT, Diode,
        NMOS_3T, NMOS_4T, NMOS_L1, NMOS_subthreshold,
        PMOS_3T, PMOS_4T, PMOS_L1, PMOS_subthreshold,
    )
    if isinstance(comp, (NMOS_L1, NMOS_3T, NMOS_4T, NMOS_subthreshold,
                         PMOS_L1, PMOS_3T, PMOS_4T, PMOS_subthreshold)):
        return _check_mosfet_region(comp, region, solution)
    if isinstance(comp, BJT):
        return _check_bjt_region(comp, region, solution)
    if isinstance(comp, Diode):
        return _check_diode_region(comp, region, solution)
    return CheckResult(False, f"{comp.name} in {region}",
                       f"no region check for {type(comp).__name__}")


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def apply_assumptions(
    value: Any,
    assumptions: Iterable[Assumption],
) -> Any:
    """Run every assumption's :meth:`apply` over ``value`` in order.

    ``value`` is typically the solution dict returned from a solver, but
    any sympy expression works. Assumption order is significant: later
    ``apply`` calls see the output of earlier ones.
    """
    for a in assumptions:
        value = a.apply(value)
    return value


def check_assumptions(
    circuit: "Circuit",
    solution: Mapping[cas.Symbol, cas.Expr],
    assumptions: Optional[Iterable[Assumption]] = None,
) -> list[CheckResult]:
    """Verify every assumption against the solved operating point.

    Returns a list of :class:`CheckResult` in the same order as the
    input assumptions. If ``assumptions`` is ``None``, the circuit's
    own attached :attr:`~sycan.Circuit.assumptions` are used.
    """
    if assumptions is None:
        assumptions = getattr(circuit, "assumptions", [])
    return [a.check(solution, circuit) for a in assumptions]


def violations(results: Iterable[CheckResult]) -> list[CheckResult]:
    """Filter a list of check results down to the ones that failed."""
    return [r for r in results if not r.passed]


def format_check_report(results: Iterable[CheckResult]) -> str:
    """Pretty-print a list of :class:`CheckResult` for terminal output.

    Each line is one assumption. Passing checks render with ``OK``;
    failing ones include the ``detail`` field so the user can see
    which inequality was violated without inspecting the measured
    quantities themselves.
    """
    lines: list[str] = []
    for r in results:
        tag = "OK  " if r.passed else "FAIL"
        line = f"[{tag}] {r.description}"
        if not r.passed and r.detail:
            line += f"  — {r.detail}"
        lines.append(line)
    return "\n".join(lines)


# Convenience factory functions kept in the module namespace so users can
# write ``from sycan.assumptions import limit, much_greater, region``
# instead of constructing dataclasses directly.

def limit(symbol: cas.Symbol, target: cas.Expr) -> Limit:
    """Factory: ``limit(A, oo)`` is ``Limit(A, oo)``."""
    return Limit(symbol=symbol, target=target)


def much_greater(big, small) -> MuchGreater:
    """Factory: ``much_greater(A, B)`` is ``MuchGreater(A, B)`` — A >> B."""
    return MuchGreater(big=big, small=small)


def much_less(small, big) -> MuchLess:
    """Factory: ``much_less(small, big)`` is ``MuchLess(small, big)``."""
    return MuchLess(small=small, big=big)


def approximate(symbol: cas.Symbol, value) -> Approximate:
    """Factory: ``approximate(R, 50)`` is ``Approximate(R, 50)``."""
    return Approximate(symbol=symbol, value=value)


def region(component_name: str, region_name: str) -> Region:
    """Factory: ``region("M1", "saturation")`` is ``Region("M1", ...)``."""
    return Region(component=component_name, region=region_name)
