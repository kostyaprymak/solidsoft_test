import json
import os


def production() -> dict:
    return {
        "DATABASE_URL": os.getenv("DATABASE_URL"),
        "IDENTITY_RESOLVER": None,
        "TOTAL_SERVICE": None,
        "PAYMENT_PROVIDER": None,
        "DATA_LOADER": None,
    }


def demo() -> dict:
    from shop.payments.demo.adapters import (
        ALICE,
        BOB,
        DemoIdentityResolver,
        DemoTotalService,
        MockProvider,
    )
    from shop.payments.demo.seed import DEMO_QUOTES, seed_demo

    identities = json.loads(os.getenv("DEMO_IDENTITIES_JSON", "{}"))
    for name, user in [("DEMO_API_TOKEN", ALICE), ("DEMO_BOB_API_TOKEN", BOB)]:
        if token := os.getenv(name):
            identities[token] = str(user)
    quotes = json.loads(os.getenv("DEMO_QUOTES_JSON", json.dumps(DEMO_QUOTES)))
    return {
        "DATABASE_URL": os.getenv("DATABASE_URL"),
        "IDENTITY_RESOLVER": DemoIdentityResolver(identities),
        "TOTAL_SERVICE": DemoTotalService(quotes),
        "PAYMENT_PROVIDER": MockProvider,
        "DATA_LOADER": seed_demo,
    }


PROFILES = {"production": production, "demo": demo}


def load(name: str) -> dict:
    try:
        return PROFILES[name]()
    except KeyError as error:
        raise RuntimeError(f"Unknown APP_CONFIG: {name}") from error
