"""Transactional micro-dollar reservations shared by all paid workers."""

from decimal import Decimal, ROUND_CEILING

from sqlalchemy import select, text

from .db import Reservation, Run, Wallet

PRICES = {"gpt-6.1-sol": (Decimal("2"), Decimal("10")),
          "gpt-6-astra": (Decimal("10"), Decimal("50"))}
PRICE_DATE = "2026-10-03"


class BudgetExhausted(RuntimeError):
    pass


class RunCancelled(RuntimeError):
    pass


def microusd(value: float) -> int:
    return int((Decimal(str(value)) * 1_000_000).to_integral_value(rounding=ROUND_CEILING))


def token_cost(model: str, input_tokens: int, output_tokens: int) -> int:
    if model not in PRICES:
        raise ValueError("Unknown model pricing; configure a reviewed price before paid execution")
    rates = PRICES[model]
    if min(input_tokens, output_tokens) < 0:
        raise ValueError("Token counts cannot be negative")
    return int((rates[0] * input_tokens + rates[1] * output_tokens).to_integral_value(rounding=ROUND_CEILING))


class BudgetLedger:
    def __init__(self, session_factory, run_id: str, total_limit_usd: float = 100):
        self.sessions = session_factory
        self.run_id = run_id
        self.total_limit = microusd(total_limit_usd)

    def _begin(self, db):
        if db.bind.dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))

    def reserve(self, model: str, input_tokens: int, max_output_tokens: int) -> str:
        amount = token_cost(model, input_tokens, max_output_tokens)
        with self.sessions() as db:
            self._begin(db)
            if db.bind.dialect.name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert
                db.execute(insert(Wallet).values(id="initial-evaluation", limit_microusd=self.total_limit,
                    spent_microusd=0, reserved_microusd=0).on_conflict_do_nothing(index_elements=["id"]))
            wallet = db.scalar(select(Wallet).where(Wallet.id == "initial-evaluation").with_for_update())
            if wallet is None:
                wallet = Wallet(id="initial-evaluation", limit_microusd=self.total_limit,
                                spent_microusd=0, reserved_microusd=0)
                db.add(wallet)
                db.flush()
            run = db.scalar(select(Run).where(Run.id == self.run_id).with_for_update())
            if run is None:
                raise ValueError("Unknown run")
            if run.cancel_requested:
                raise RunCancelled("Cancellation requested")
            if any(r.usage.get("exceeded_reservation") for r in db.scalars(select(Reservation).where(
                    Reservation.run_id == run.id, Reservation.status == "settled"))):
                raise BudgetExhausted("Provider usage exceeded a reservation; review pricing and bounds before further paid calls")
            wallet.limit_microusd = min(wallet.limit_microusd, self.total_limit)
            if amount + run.spent_microusd + run.reserved_microusd > run.budget_microusd:
                raise BudgetExhausted("The remaining run budget cannot cover the next bounded call")
            if amount + wallet.spent_microusd + wallet.reserved_microusd > wallet.limit_microusd:
                raise BudgetExhausted("The shared initial evaluation allowance is exhausted")
            reservation = Reservation(run_id=run.id, model=model, reserved_microusd=amount,
                                      status="held", usage={"price_date": PRICE_DATE,
                                      "reserved_input_tokens": input_tokens,
                                      "reserved_output_tokens": max_output_tokens})
            db.add(reservation)
            run.reserved_microusd += amount
            wallet.reserved_microusd += amount
            db.commit()
            return reservation.id

    def settle(self, reservation_id: str, input_tokens: int | None = None,
               output_tokens: int | None = None, **usage):
        with self.sessions() as db:
            self._begin(db)
            wallet = db.scalar(select(Wallet).where(Wallet.id == "initial-evaluation").with_for_update())
            reservation = db.scalar(select(Reservation).where(Reservation.id == reservation_id).with_for_update())
            if reservation is None or reservation.status != "held":
                return
            run = db.scalar(select(Run).where(Run.id == reservation.run_id).with_for_update())
            known = input_tokens is not None and output_tokens is not None
            amount = token_cost(reservation.model, input_tokens, output_tokens) if known else reservation.reserved_microusd
            reservation.actual_microusd = amount
            reservation.status = "settled"
            reservation.usage = {**reservation.usage, **usage, "input_tokens": input_tokens,
                                 "output_tokens": output_tokens, "usage_known": known,
                                 "exceeded_reservation": amount > reservation.reserved_microusd}
            run.reserved_microusd -= reservation.reserved_microusd
            run.spent_microusd += amount
            wallet.reserved_microusd -= reservation.reserved_microusd
            wallet.spent_microusd += amount
            db.commit()

    def release_unsent(self, reservation_id: str):
        with self.sessions() as db:
            self._begin(db)
            wallet = db.scalar(select(Wallet).where(Wallet.id == "initial-evaluation").with_for_update())
            r = db.scalar(select(Reservation).where(Reservation.id == reservation_id).with_for_update())
            if r is None or r.status != "held":
                return
            run = db.scalar(select(Run).where(Run.id == r.run_id).with_for_update())
            run.reserved_microusd -= r.reserved_microusd
            wallet.reserved_microusd -= r.reserved_microusd
            r.status = "released"
            db.commit()
