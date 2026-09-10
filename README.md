# Payment endpoint

A Flask and PostgreSQL service that starts payments for owned carts, records attempts, prevents duplicate live payments, and checks out carts after confirmed payment.

## Demo setup

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/getting-started/installation/), and Docker with Compose.

```sh
uv sync --locked
docker compose up -d --wait
export DATABASE_URL=postgresql+psycopg://shop:shop@localhost:55432/shop
uv run alembic upgrade head
export APP_CONFIG=demo
export DEMO_API_TOKEN=local-alice-secret
export DEMO_BOB_API_TOKEN=local-bob-secret
uv run flask --app app run
```

Demo mode idempotently loads fixture data and supplies local identity, quote, and mock payment adapters. It uses the same `DATABASE_URL` as production.

Pay Alice's sample cart:

```sh
curl -i http://127.0.0.1:5000/carts/c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1/payments \
  -H "Authorization: Bearer $DEMO_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: checkout-1' \
  -d '{"payment_method_id":"11111111-2222-3333-4444-555555555555"}'
```

Demo fixtures:

| User | Cart | Payment method | Amount |
| --- | --- | --- | --- |
| Alice | `c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1` | `11111111-2222-3333-4444-555555555555` | 70.00 USD |
| Bob | `c2c2c2c2-c2c2-c2c2-c2c2-c2c2c2c2c2c2` | `22222222-2222-3333-4444-555555555555` | 89.99 USD |

`DEMO_IDENTITIES_JSON` can add token-to-user UUID mappings. `DEMO_QUOTES_JSON` can replace fixture quotes using `{"cart-uuid": ["amount", "CURRENCY"]}`.

The mock provider succeeds normally. `tok_test_decline` declines, while `tok_test_timeout` records one successful mock charge, simulates a lost response, and leaves the payment pending. Mock charge results are stored in PostgreSQL so demo recovery survives application and CLI process restarts.

## API contract

`POST /carts/<cart-uuid>/payments` requires:

- `Authorization: Bearer <token>`
- `Idempotency-Key`: 1–128 letters, digits, `.`, `_`, `:`, or `-`
- JSON containing only `payment_method_id`

The amount and currency come from the configured total service. The endpoint never accepts client prices or provider tokens.

A successful new payment returns HTTP 201:

```json
{
  "id": "<payment UUID>",
  "cart_id": "<cart UUID>",
  "payment_method_id": "<payment method UUID>",
  "amount": "70.00",
  "currency": "USD",
  "status": "succeeded",
  "failure_code": null
}
```

| HTTP | Meaning |
| --- | --- |
| 200 | Existing successful payment |
| 201 | New successful payment |
| 202 | Payment outcome remains pending |
| 400 / 415 | Invalid request or content type |
| 401 | Invalid identity |
| 402 | Definitive decline; cart remains active |
| 404 | Cart or payment method not found for this user |
| 409 | Ineligible cart, conflicting key, or another live payment |
| 502 | Invalid dependency response or provider adapter failure |
| 503 | Total service or database unavailable |

Idempotency keys are scoped by cart. Retrying the same key returns the existing attempt without charging again. Reusing a key with another payment method is rejected.

A cart permits only one pending or successful payment. Database constraints and triggers also prevent cart changes while payment is pending or after checkout.

## Recovery

Recover a pending payment with its payment UUID:

```sh
uv run flask --app app reconcile-payment <payment-uuid>
uv run flask --app app reconcile-payment <payment-uuid>  # completed: safe no-op
```

Recovery reuses the stored amount, currency, provider token snapshot, and payment UUID as the provider idempotency key. A completed payment is a no-op; continued provider unavailability returns a nonzero exit status.

The demo provider durably retains idempotency results in the `mock_charges` table for this workflow. Production providers must provide equivalent durable idempotency across process restarts; the demo provider does not perform real card or network operations and is not a production integration.

## Production configuration

`APP_CONFIG=production` is the default. Production requires:

- `DATABASE_URL`
- `IDENTITY_RESOLVER`: callable receiving the Authorization header and returning a user UUID or `None`
- `TOTAL_SERVICE`: callable receiving a cart UUID and returning a valid `Quote` or matching mapping
- `PAYMENT_PROVIDER`: callable accepting `token`, `amount`, `currency`, and `idempotency_key`

The deployment composition root must pass the three callables to `create_app`. Missing or non-callable integrations fail startup.

Quotes must contain a positive, finite, two-decimal `Decimal` amount and a three-letter uppercase currency. Provider results are validated before persistence.

Provider adapters must use bounded network timeouts and safely reuse idempotency keys. Production deployments should also implement verified webhooks or provider status lookup.

## Development checks

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run alembic check
```

Tests use PostgreSQL and run real Alembic migrations in disposable schemas. Override the database with `TEST_DATABASE_URL`; the role must be able to create schemas and install `pgcrypto`.

Apply migrations with:

```sh
uv run alembic upgrade head
```

## Project layout

- `app.py`: Flask CLI entry point
- `shop/app.py`: application factory and global error handling
- `shop/database.py`: shared engine and session setup
- `shop/models.py`: shared users, products, carts, and cart items
- `shop/payments/`: payment routes, service, repository, models, schemas, errors, guards, CLI wiring, and demo fixtures
- `migrations/`: production database migrations
- `tests/`: API, persistence, recovery, and concurrency tests
