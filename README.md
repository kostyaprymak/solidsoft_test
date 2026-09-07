# Payment endpoint

A local take-home application using Python, Flask, SQLAlchemy ORM, Alembic and
PostgreSQL. It starts a payment for an owned cart, records attempts, prevents duplicate
charges, and checks out the cart only after confirmed success.

## Run

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/getting-started/installation/),
and Docker with Compose:

```sh
uv sync --locked
docker compose up -d --wait
uv run alembic upgrade head
uv run flask --app app seed-db
export DEMO_API_TOKEN=local-alice-secret
export DEMO_BOB_API_TOKEN=local-bob-secret
uv run flask --app app run
```

`seed-db` inserts sample data once, in a transaction; duplicates fail without
replacing existing data. Migration upgrades are safe to repeat. `DATABASE_URL`
overrides `postgresql+psycopg://shop:shop@localhost:55432/shop`. The development
server listens on `127.0.0.1:5000`. Stop the database with `docker compose stop`.

Pay Alice's sample cart:

```sh
curl -i http://127.0.0.1:5000/carts/c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1/payments \
  -H "Authorization: Bearer $DEMO_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: checkout-1' \
  -d '{"payment_method_id":"11111111-2222-3333-4444-555555555555"}'
```

The first successful response is HTTP 201:

```json
{
  "id": "<payment UUID>",
  "cart_id": "c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1",
  "payment_method_id": "11111111-2222-3333-4444-555555555555",
  "amount": "70.00",
  "currency": "USD",
  "status": "succeeded",
  "failure_code": null
}
```

Bob's sample cart is `c2c2c2c2-c2c2-c2c2-c2c2-c2c2c2c2c2c2`, his payment method is
`22222222-2222-3333-4444-555555555555`, and its quote is 89.99 USD. Use Bob's bearer
token for that cart. Neither user can pay with the other's cart or payment method.

Clients persist an `Idempotency-Key` (1–128 letters, digits, `.`, `_`, `:`, `-`) and
reuse it after transport errors. Keys are scoped by cart. Replays return the existing
attempt; changing the selected method with the same key is rejected. A new key is
allowed only after a definitive decline. Request JSON accepts only `payment_method_id`.

| HTTP | Meaning |
| --- | --- |
| 200 / 201 | Existing / newly successful payment |
| 202 | Pending attempt; ordinary replay does not call the provider |
| 400 / 415 | Invalid request / JSON content type required |
| 401 / 404 | Invalid identity / missing or another user's cart or method |
| 409 | Ineligible cart, conflicting key, or another live attempt |
| 402 | Definitive decline; cart stays active and can be edited |
| 502 | Invalid quote, malformed provider response, or provider adapter bug |
| 503 | Unavailable total service or database |

## Recover pending payments

The mock persists charge results in `mock_charges`, in an independent transaction.
This simulates the external provider's durable idempotency store; it has no foreign
key to the payment attempt. No real card or network call is involved.

- Normal tokens succeed.
- `tok_test_decline` produces a definitive decline.
- `tok_test_timeout` commits a successful mock charge but loses its first response.
  Its payment remains pending until reconciled.

Recover one attempt using the payment UUID returned by the endpoint:

```sh
uv run flask --app app reconcile-payment <payment-uuid>
```

The command resubmits the **original** amount, currency, token snapshot and payment
UUID as the provider idempotency key. It never creates a new attempt. The mock returns
its stored result, or records a single charge if the process crashed before charging.
Recovery works across process restarts, concurrently, and after database finalization
fails. A completed payment is a no-op. Continued provider unavailability leaves the
payment pending and returns a nonzero command exit status. It is never assumed declined.

For a real provider, the adapter must bound network timeouts and guarantee safe key
reuse. If a provider's key-retention window expires, it must query/reconcile provider
state instead of blindly submitting again. Verified, idempotent webhooks and provider
status lookup are required integration work before live operation. This project uses
an explicit operator recovery command, not an unattended worker or distributed
exactly-once guarantee.

## Payment and cart consistency

1. Lock the owned cart, check eligibility and key reuse, get a server-side quote, and
   commit a pending attempt before calling the provider.
2. Call the provider outside the database transaction. Its key is the payment UUID.
3. Finalize the payment and cart checkout atomically. Concurrent finalizers reuse the
   already-confirmed result.

A partial unique index permits at most one pending/succeeded attempt per cart.
Database triggers serialize cart-item writes on the same cart lock and reject inserts,
updates, deletes and item moves while payment is pending or the cart is checked out.
Changing ownership or abandoning a cart with a live payment is also rejected. These
protections apply to ordinary SQL writers, not just this Flask endpoint. Schema owners
can still bypass them administratively; cart services should handle the resulting
constraint errors as conflicts.

Amounts use Decimal and NUMERIC(12,2). Quotes and provider response shapes are validated.
The endpoint never accepts client prices or card tokens. The saved provider token is
snapshotted on the payment for safe recovery if the saved method later changes. Tokens
are not returned or logged. Diagnostic logs contain payment IDs and exception classes,
not provider payloads, exception messages or SQL parameters. Database access must be
restricted appropriately for these existing provider tokens; no raw card data is stored.

## Integration assumptions

- Existing identity and total calculation belong to the surrounding shop.
  `IDENTITY_RESOLVER`, `TOTAL_SERVICE` and `PAYMENT_PROVIDER` are injectable, typed
  contracts in `shop/contracts.py`; the endpoint does not hardcode a user or cart.
- The demo maps configured bearer tokens to users. No configured identity means
  authentication fails closed. `DEMO_IDENTITIES_JSON` adds arbitrary token-to-user-UUID
  mappings; `DEMO_API_TOKEN` and `DEMO_BOB_API_TOKEN` are sample-user conveniences.
  Replace the resolver with the shop's verified authentication for deployment.
- Demo quotes are fixtures, **not a calculator**: defaults cover the two seeded carts.
  `DEMO_QUOTES_JSON` replaces the mapping, using cart UUIDs as keys and
  `["amount", "CURRENCY"]` as values. Updating demo cart contents requires updating
  its quote fixture. An injected real total service supplies the current quote while
  the cart is locked. Missing quotes fail closed rather than inventing a total.
- Quotes are positive, finite, two-decimal Decimal amounts with three uppercase currency
  letters. This task assumes currencies using two decimal places. Zero-value checkout
  is outside scope.
- Inventory reservation, shipping, orders, refunds and fulfillment are outside this
  payment-only task. The endpoint does not decrement stock or implement tax calculation.

## Tests and code checks

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run alembic check
```

Tests use PostgreSQL and real Alembic migrations in disposable schemas, including
upgrade/downgrade round trips. Set `TEST_DATABASE_URL` for a separate database. The
role needs schema creation and pgcrypto installation privileges (Compose provides
these). The suite covers simultaneous starts, concurrent/restarted recovery, provider
parameters and malformed responses, multi-user ownership, cart edits racing payment,
and rollback after a provider succeeds.

## Migrations

- `001_base`: the supplied shop schema.
- `002_payments`: payment attempts and duplicate-charge constraints.
- `003_recovery`: token snapshots, the durable mock ledger, and cart guards.

```sh
uv run alembic current
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "describe schema change"
```

Review generated revisions: Alembic does not automatically detect every check
constraint or trigger change. Revisions are independent of live ORM models.
Downgrades remove the corresponding tables/columns and data. The shared pgcrypto
extension is retained. Use migrations, not `Base.metadata.create_all()`, to provision.

For an unversioned database from the original SQL scripts, verify its schema first.
Stamp `001_base` if it has only the supplied base schema, or `002_payments` if it also
has the matching original payments table, then run `upgrade head`. Stamping records
history without validating or creating tables; never stamp an empty or mismatched DB.
Existing sample data must not be reseeded. New Bob demo rows are included on fresh seeds.

Legacy payment attempts have no trustworthy original token snapshot. The migration
leaves those snapshots null instead of guessing from today's saved card. The recovery
command refuses such pending rows: reconcile them with the original provider records.
Already successful/failed attempts retain their results and replay normally.

## Layout

`app.py` is the Flask entry point. `shop/` contains the application factory, routes,
ORM models, payment orchestration, typed contracts, demo adapters, database setup and
seed data. `migrations/` owns schema history; `tests/` verifies behavior against PostgreSQL.

Primary references: [Flask testing](https://flask.palletsprojects.com/en/stable/testing/),
[SQLAlchemy transactions](https://docs.sqlalchemy.org/en/20/core/connections.html),
[Alembic](https://alembic.sqlalchemy.org/en/latest/), and
[Stripe idempotency](https://docs.stripe.com/api/idempotent_requests).
