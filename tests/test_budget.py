"""Concurrent global budgets and conservative durable billing behavior."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from livingmeta.budget import BudgetExhausted, BudgetLedger, RunCancelled, microusd, token_cost
from livingmeta.config import Settings
from livingmeta.db import Project, Reservation, Run, User, Wallet, initialize_database, make_database
from livingmeta.domain import Protocol


@pytest.fixture
def budget_database(tmp_path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'budget.sqlite'}", private_directory=tmp_path / "private")
    engine, sessions = make_database(settings)
    initialize_database(engine)
    with sessions() as db:
        user = User(github_id="synthetic", login="synthetic", name="Synthetic owner", is_owner=True)
        db.add(user)
        db.flush()
        project = Project(name="Synthetic budget project", owner_id=user.id, protocol=Protocol().model_dump())
        db.add(project)
        db.flush()
        runs = [Run(project_id=project.id, budget_microusd=microusd(100), document_ids=[]) for _ in range(8)]
        db.add_all(runs)
        db.commit()
        run_ids = [run.id for run in runs]
    yield sessions, run_ids
    engine.dispose()


def test_prices_and_ceiling_never_round_spend_down():
    assert microusd(0.0000001) == 1
    assert token_cost("gpt-6.1-sol", 1, 1) == 12
    with pytest.raises(ValueError, match="Unknown model"):
        token_cost("unreviewed-model", 1, 1)
    with pytest.raises(ValueError, match="negative"):
        token_cost("gpt-6.1-sol", -1, 1)


def test_global_wallet_is_shared_across_distinct_runs(budget_database):
    sessions, run_ids = budget_database
    first, second = BudgetLedger(sessions, run_ids[0]), BudgetLedger(sessions, run_ids[1])
    reservation = first.reserve("gpt-6.1-sol", 0, 8_000_000)
    with pytest.raises(BudgetExhausted, match="shared"):
        second.reserve("gpt-6.1-sol", 0, 3_000_000)
    first.settle(reservation, None, None)
    with sessions() as db:
        wallet = db.get(Wallet, "initial-evaluation")
        assert wallet.spent_microusd == microusd(80) and wallet.reserved_microusd == 0
    with pytest.raises(BudgetExhausted):
        second.reserve("gpt-6.1-sol", 0, 3_000_000)


def test_concurrent_reservations_cannot_overspend_shared_allowance(budget_database):
    sessions, run_ids = budget_database
    barrier = Barrier(len(run_ids))
    def reserve(run_id):
        ledger = BudgetLedger(sessions, run_id)
        barrier.wait()
        try:
            return ledger.reserve("gpt-6.1-sol", 0, 3_000_000)
        except BudgetExhausted:
            return None
    with ThreadPoolExecutor(max_workers=len(run_ids)) as pool:
        outcomes = list(pool.map(reserve, run_ids))
    assert sum(result is not None for result in outcomes) == 3
    with sessions() as db:
        wallet = db.get(Wallet, "initial-evaluation")
        assert wallet.reserved_microusd == microusd(90)
        assert wallet.spent_microusd + wallet.reserved_microusd <= wallet.limit_microusd


def test_known_and_unknown_usage_and_settlement_are_idempotent(budget_database):
    sessions, run_ids = budget_database
    ledger = BudgetLedger(sessions, run_ids[0])
    reservation = ledger.reserve("gpt-6.1-sol", 1000, 1000)
    expected = token_cost("gpt-6.1-sol", 100, 50)
    ledger.settle(reservation, 100, 50)
    ledger.settle(reservation, 1000, 1000)
    ledger.release_unsent(reservation)
    with sessions() as db:
        item = db.get(Reservation, reservation)
        wallet, run = db.get(Wallet, "initial-evaluation"), db.get(Run, run_ids[0])
        assert item.status == "settled" and item.actual_microusd == expected
        assert item.usage["usage_known"] is True
        assert wallet.spent_microusd == run.spent_microusd == expected
        assert wallet.reserved_microusd == run.reserved_microusd == 0
    unknown = ledger.reserve("gpt-6-astra", 1000, 1000)
    ledger.settle(unknown, None, None)
    with sessions() as db:
        item = db.get(Reservation, unknown)
        assert item.actual_microusd == item.reserved_microusd
        assert item.usage["usage_known"] is False and item.usage["input_tokens"] is None


def test_unsent_release_reclaims_budget_only_once(budget_database):
    sessions, run_ids = budget_database
    ledger = BudgetLedger(sessions, run_ids[0])
    reservation = ledger.reserve("gpt-6.1-sol", 1000, 1000)
    ledger.release_unsent(reservation)
    ledger.release_unsent(reservation)
    ledger.settle(reservation, None, None)
    with sessions() as db:
        assert db.get(Reservation, reservation).status == "released"
        assert db.get(Wallet, "initial-evaluation").reserved_microusd == 0
        assert db.get(Wallet, "initial-evaluation").spent_microusd == 0


def test_cancelled_run_cannot_reserve_and_inflight_usage_still_settles(budget_database):
    sessions, run_ids = budget_database
    ledger = BudgetLedger(sessions, run_ids[0])
    reservation = ledger.reserve("gpt-6.1-sol", 1000, 1000)
    with sessions() as db:
        db.get(Run, run_ids[0]).cancel_requested = True
        db.commit()
    with pytest.raises(RunCancelled):
        ledger.reserve("gpt-6.1-sol", 1000, 1000)
    ledger.settle(reservation, None, None)
    with sessions() as db:
        assert db.get(Wallet, "initial-evaluation").reserved_microusd == 0
        assert db.get(Wallet, "initial-evaluation").spent_microusd > 0


def test_run_budget_is_not_reset_by_a_new_ledger(budget_database):
    sessions, run_ids = budget_database
    with sessions() as db:
        db.get(Run, run_ids[0]).budget_microusd = microusd(1)
        db.commit()
    ledger = BudgetLedger(sessions, run_ids[0])
    reservation = ledger.reserve("gpt-6.1-sol", 0, 100_000)
    ledger.settle(reservation, None, None)
    with pytest.raises(BudgetExhausted, match="run budget"):
        BudgetLedger(sessions, run_ids[0]).reserve("gpt-6.1-sol", 1, 1)


def test_actual_usage_exceeding_reservation_is_recorded_and_blocks_next_call(budget_database):
    sessions, run_ids = budget_database
    with sessions() as db:
        db.get(Run, run_ids[0]).budget_microusd = microusd(1)
        db.commit()
    ledger = BudgetLedger(sessions, run_ids[0])
    reservation = ledger.reserve("gpt-6.1-sol", 1, 1)
    ledger.settle(reservation, 0, 100_000)
    with sessions() as db:
        assert db.get(Reservation, reservation).usage["exceeded_reservation"] is True
        assert db.get(Run, run_ids[0]).spent_microusd == microusd(1)
    with pytest.raises(BudgetExhausted):
        ledger.reserve("gpt-6.1-sol", 1, 1)


def test_concurrent_duplicate_settlement_charges_only_once(budget_database):
    sessions, run_ids = budget_database
    ledger = BudgetLedger(sessions, run_ids[0])
    reservation = ledger.reserve("gpt-6.1-sol", 1000, 1000)
    barrier = Barrier(6)
    def settle(_):
        barrier.wait()
        ledger.settle(reservation, 100, 50)
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(settle, range(6)))
    with sessions() as db:
        wallet = db.get(Wallet, "initial-evaluation")
        assert wallet.spent_microusd == token_cost("gpt-6.1-sol", 100, 50)
        assert wallet.reserved_microusd == 0
        assert db.get(Run, run_ids[0]).reserved_microusd == 0
