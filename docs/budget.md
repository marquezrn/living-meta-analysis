# Agent account usage and optional legacy API budget

The default local edition invokes an existing ChatGPT-authenticated Codex client.
It strips API-key environment variables, forces ChatGPT sign-in, and pauses on
authentication or usage-limit failures. It never purchases credits, changes plans,
or switches billing modes. Existing account allowances and any account-level credit
arrangements still apply. Available token usage is recorded, while unknown monetary
cost remains null. A network connection is needed for cloud inference.

Local preparation, deterministic validation, descriptive synthesis, export and HTML
viewing use no model. Optional metadata monitoring also uses no model. Bounded
attempts, concurrency and --max-jobs limit execution; they are not a dollar budget
for account-authenticated inference. See [Codex authentication](https://learn.chatgpt.com/docs/auth).

The following ledger is retained only for the explicitly enabled legacy hosted/API
edition. It is not loaded by local commands.

## Legacy USD 100 evaluation ledger

The application enforces one shared initial allowance of USD 100, across all projects
and runs. Run-specific allowances cannot exceed it. PostgreSQL row locks and SQLite
immediate transactions serialize micro-dollar reservations. The wallet is durable;
resume, restart and new projects do not reset it.

The reviewed standard token prices on October 3, 2026 are:

| API model | Input USD per million tokens | Output USD per million tokens |
| --- | ---: | ---: |
| gpt-6.1-sol | 2 | 10 |
| gpt-6-astra | 10 | 50 |

Prices are fixed in `budget.py` with their review date. Unknown models fail closed.
Cached input is conservatively billed at the standard input rate in this ledger.
Review [official model pricing](https://developers.openai.com/api/docs/models) before
changing models or commencing an evaluation after a pricing change.

Before a call, the provider reserves a conservative text/schema/image token bound
and the full maximum output allowance. It accepts bounded image files only within
the current private source-artifact directory. A request cannot begin if its reserve
does not fit both the remaining run budget and the shared wallet.

Successful calls settle reported input/output usage. Unknown usage or an interrupted
sent request consumes the full reservation and is labelled `usage_known=false`.
A request known not to have been sent releases its reservation. Duplicate settlements
are idempotent. SDK automatic retries are disabled; a future resumed attempt requires
a new reservation while preserving earlier charges. Cancellation does not refund a
call already sent.

The ledger is an application guard based on reviewed prices and conservative token
bounds. Provider bills remain authoritative. If usage exceeds its reservation, the
actual amount is recorded and further calls in that run are blocked for review.
Use a dedicated provider project and inspect its usage dashboard alongside the ledger.
Accounted unknown-usage reserves are upper-bound costs, not fabricated precise invoices.

Hosting, storage and any database subscriptions are outside the OpenAI allowance.
Weekly metadata monitoring receives no OpenAI key and never invokes the provider.
