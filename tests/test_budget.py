import pytest

from skinproof.budget import BudgetExceeded, UnitBudget


def test_reserve_commit():
    b = UnitBudget(40)
    b.reserve("a", 12)
    assert b.left == 28
    b.commit(12)
    assert b.spent == 12 and b.left == 28


def test_refuses_before_call():
    b = UnitBudget(20)
    b.reserve("a", 12); b.commit(12)
    with pytest.raises(BudgetExceeded):
        b.reserve("b", 12)
    assert b.spent == 12 and b.pending is None


def test_cache_hit_costs_zero_and_release():
    b = UnitBudget(40)
    b.reserve("a", 12); b.commit(0)
    assert b.spent == 0
    b.reserve("b", 8); b.release()
    assert b.left == 40


def test_unfinished_reservation_blocks():
    b = UnitBudget(40)
    b.reserve("a", 1)
    with pytest.raises(RuntimeError):
        b.reserve("b", 1)


def test_llm_cap():
    b = UnitBudget(40, max_llm_usd=0.10)
    b.check_llm()
    b.add_llm_cost(0.06); b.check_llm()
    b.add_llm_cost(0.05)
    with pytest.raises(BudgetExceeded) as e:
        b.check_llm()
    assert e.value.kind == "llm"
