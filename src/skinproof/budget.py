"""Hard spend limits for an agent run. Enforced in code, never in the prompt.

UnitBudget guards YouCam units: reserve() runs BEFORE the paid call and raises
BudgetExceeded if the estimate would not fit; commit() records what was really
spent (0 for a cache hit). LLMBudget caps the OpenRouter dollars per run.
"""
from __future__ import annotations

DEFAULT_MAX_UNITS = 40
DEFAULT_LLM_USD = 0.10


class BudgetExceeded(Exception):
    def __init__(self, message: str, *, kind: str = "units"):
        super().__init__(message)
        self.kind = kind


class UnitBudget:
    def __init__(self, max_units: float = DEFAULT_MAX_UNITS, max_llm_usd: float = DEFAULT_LLM_USD):
        self.max_units = float(max_units)
        self.max_llm_usd = float(max_llm_usd)
        self.spent = 0.0
        self.reserved = 0.0
        self.pending: str | None = None
        self.llm_cost = 0.0
        self.ledger: list[dict] = []

    @property
    def left(self) -> float:
        return max(0.0, self.max_units - self.spent - self.reserved)

    def reserve(self, feature: str, estimate: float) -> None:
        if self.pending is not None:
            raise RuntimeError(f"previous reservation for {self.pending} was never committed")
        estimate = float(estimate)
        if estimate < 0:
            raise ValueError("estimate must be >= 0")
        if self.spent + estimate > self.max_units + 1e-9:
            raise BudgetExceeded(
                f"{feature} needs about {estimate:g} units but only {self.left:g} of "
                f"{self.max_units:g} are left in this run's budget.")
        self.reserved, self.pending = estimate, feature

    def commit(self, actual: float | None = None) -> float:
        """Record real spend for the pending reservation (None = the estimate, 0 = cache hit)."""
        if self.pending is None:
            raise RuntimeError("commit() without reserve()")
        amount = self.reserved if actual is None else float(actual)
        self.spent += amount
        self.ledger.append({"feature": self.pending, "estimate": self.reserved, "actual": amount})
        self.reserved, self.pending = 0.0, None
        return amount

    def release(self) -> None:
        """Call failed before spending anything."""
        self.reserved, self.pending = 0.0, None

    # LLM dollars
    def check_llm(self) -> None:
        if self.llm_cost >= self.max_llm_usd:
            raise BudgetExceeded(
                f"LLM cap reached: ${self.llm_cost:.4f} of ${self.max_llm_usd:.2f}.", kind="llm")

    def add_llm_cost(self, cost: float | None) -> None:
        self.llm_cost += float(cost or 0)
