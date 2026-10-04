"""Production-database concurrency checks isolated in disposable test schemas."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from livingmeta.budget import BudgetExhausted, BudgetLedger, microusd, token_cost
from livingmeta.config import Settings
from livingmeta.db import Base, Project, Reservation, Run, User, Wallet, make_database
from livingmeta.domain import Protocol


@pytest.fixture
def postgres_database(tmp_path):
    url = os.environ.get("LIVINGMETA_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set LIVINGMETA_TEST_POSTGRES_URL to a dedicated livingmeta_test PostgreSQL database")
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql" or parsed.database != "livingmeta_test":
        pytest.fail("PostgreSQL concurrency tests require a dedicated database named livingmeta_test")
    settings = Settings(_env_file=None, database_url=url, private_directory=tmp_path / "private")
    engine, _ = make_database(settings)
    schema = f"livingmeta_test_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    isolated = engine.execution_options(schema_translate_map={None: schema})
    sessions = sessionmaker(isolated, expire_on_commit=False)
    try:
        Base.metadata.create_all(isolated)
        with sessions() as db:
            owner = User(github_id="synthetic-concurrency", login="synthetic", name="Synthetic owner", is_owner=True)
            db.add(owner)
            db.flush()
            project = Project(name="Synthetic concurrent budget", owner_id=owner.id, protocol=Protocol().model_dump())
            db.add(project)
            db.flush()
            runs = [Run(project_id=project.id, budget_microusd=microusd(100), document_ids=[]) for _ in range(8)]
            db.add_all(runs)
            db.commit()
            run_ids = [run.id for run in runs]
        yield sessions, run_ids
    finally:
        # Only this fixture's randomly named schema is removed, never an existing schema.
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()


def test_postgres_concurrent_first_wallet_reservations_preserve_global_limit(postgres_database):
    sessions, run_ids = postgres_database
    barrier = Barrier(len(run_ids))
    def reserve(run_id):
        barrier.wait()
        try:
            return BudgetLedger(sessions, run_id).reserve("gpt-6.1-sol", 0, 3_000_000)
        except BudgetExhausted:
            return None
    with ThreadPoolExecutor(max_workers=len(run_ids)) as pool:
        reservations = list(pool.map(reserve, run_ids))
    assert sum(value is not None for value in reservations) == 3
    with sessions() as db:
        wallet = db.get(Wallet, "initial-evaluation")
        assert wallet.reserved_microusd == microusd(90)
        assert wallet.spent_microusd + wallet.reserved_microusd <= wallet.limit_microusd
        assert len(list(db.scalars(select(Wallet)))) == 1
        assert len(list(db.scalars(select(Reservation)))) == 3
        assert sum(run.reserved_microusd for run in db.scalars(select(Run))) == wallet.reserved_microusd


def test_postgres_duplicate_settlement_is_atomic_across_workers(postgres_database):
    sessions, run_ids = postgres_database
    ledger = BudgetLedger(sessions, run_ids[0])
    reservation = ledger.reserve("gpt-6.1-sol", 1000, 1000)
    barrier = Barrier(6)
    def settle(_):
        barrier.wait()
        ledger.settle(reservation, 100, 30)
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(settle, range(6)))
    with sessions() as db:
        expected = token_cost("gpt-6.1-sol", 100, 30)
        run, wallet = db.get(Run, run_ids[0]), db.get(Wallet, "initial-evaluation")
        assert run.spent_microusd == wallet.spent_microusd == expected
        assert run.reserved_microusd == wallet.reserved_microusd == 0
        assert db.get(Reservation, reservation).actual_microusd == expected
