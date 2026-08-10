"""Test fixtures. Forces an in-memory SQLite database before the app imports its
engine, so tests never touch Postgres."""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import pytest  # noqa: E402
from fastapi import Request  # noqa: E402
from iridium_iam_client import IamClient  # noqa: E402

from neptune.config import settings  # noqa: E402
from neptune.db.base import (  # noqa: E402
    MacroSession,
    SecuritiesSession,
    SessionLocal,
    engine,
    init_db,
    init_macro_db,
    init_securities_db,
    macro_engine,
    securities_engine,
)


@pytest.fixture(autouse=True)
def _seed_demo_for_tests():
    """Production no longer seeds the fake demo book by default; the test suite exercises it
    (the golden AAA/BBB/CCC/DDD positions), so force it on for every test."""
    prev = settings.seed_demo_positions
    settings.seed_demo_positions = True
    yield
    settings.seed_demo_positions = prev


@pytest.fixture()
def session():
    """A session against a freshly created in-memory portfolio schema.

    The in-memory DB is shared across the process (StaticPool), so drop + recreate to
    isolate each test (otherwise firm/entity/book rows leak between tests)."""
    from neptune.db.base import Base
    from neptune.db import models  # noqa: F401  (register mappers)

    Base.metadata.drop_all(bind=engine)
    init_db(engine)
    with SessionLocal() as s:
        yield s


@pytest.fixture()
def securities_session():
    """A session against a freshly created in-memory securities (market-data) schema.

    The in-memory DB is shared across the process (StaticPool), so drop + recreate to
    isolate each test (otherwise projected ``securities`` rows leak between tests)."""
    from neptune.db.base import SecuritiesBase
    from neptune.securities import models  # noqa: F401  (register mappers)

    SecuritiesBase.metadata.drop_all(bind=securities_engine)
    init_securities_db(securities_engine)
    with SecuritiesSession() as s:
        yield s


@pytest.fixture()
def macro_session():
    """A session against a freshly created in-memory macro schema (own engine/DB), dropped +
    recreated per test so series/observations/vintages don't leak between tests."""
    from neptune.db.base import MacroBase
    from neptune.macro import models  # noqa: F401  (register mappers)

    MacroBase.metadata.drop_all(bind=macro_engine)
    init_macro_db(macro_engine)
    with MacroSession() as s:
        yield s


# JANUS auth bypass for the API test suite ---------------------------------------------------
#
# api/main.py gates every route but /health behind api/auth.py's IamClient: a router-level
# Depends(auth.get_claims) (design doc §15.1/§15.5) plus per-route Depends(auth.require_role(...))
# on the 25 mutating routes. This test suite exercises Neptune's own business logic against a
# real request/response cycle (TestClient(app)) -- it is not the place to exercise JANUS's token
# verification, which is iridium-iam-client's own test suite's job (see that package's tests/).
# So every request gets a fixed set of claims carrying every Neptune role, satisfying every
# require_role(...) gate the same way a real ADMIN's token would.
#
# Two patch points, not one, because require_role()'s closures call `self.get_claims(request)`
# directly as a plain method call rather than through FastAPI's Depends() sub-dependency
# injection (see iridium_iam_client.iam_client.IamClient.require_role) -- overriding only
# app.dependency_overrides[auth.get_claims] would satisfy the router-level global gate but leave
# every require_role(...)-gated route still trying (and failing) to verify a real bearer token:
#   - monkeypatch the IamClient class's get_claims method itself, so `self.get_claims(request)`
#     resolves the patched implementation on every fresh attribute lookup (Python re-resolves
#     instance.method from the class each call; it isn't cached on the bound method object)
#   - override app.dependency_overrides[auth.get_claims], because the module-level
#     `auth.get_claims = iam_client.get_claims` in api/auth.py captured one bound-method object
#     at import time -- patching the class afterward doesn't change what that already-bound
#     reference points to, and it's the exact object every router-level Depends(auth.get_claims)
#     was registered with.
FAKE_JANUS_CLAIMS = {
    "sub": "00000000-0000-0000-0000-000000000001",
    "typ": "user",
    "roles": ["ADMIN", "CIO", "PM", "ANALYST"],
    "org": "test-org",
    "sid": "00000000-0000-0000-0000-0000000000ff",
    "jti": "00000000-0000-0000-0000-0000000000fe",
    "ver": 1,
}


async def _fake_get_claims_method(self, request: Request):
    return dict(FAKE_JANUS_CLAIMS)


async def _fake_get_claims_dependency(request: Request):
    return dict(FAKE_JANUS_CLAIMS)


# NOTE: both fakes are defined at MODULE level, not nested inside the fixture below, and
# `Request` is imported at module level too -- both load-bearing. This file has `from __future__
# import annotations` (line 3), so every annotation here -- including a nested closure's -- is a
# STRING at runtime, resolved by FastAPI via the function's __globals__ (always its DEFINING
# MODULE's globals, never an enclosing function's locals, regardless of nesting). A `Request`
# imported only inside the fixture body would live in that fixture's local scope, invisible to
# forward-ref resolution -- it fails silently (an unresolved typing.ForwardRef('Request'), no
# exception), so FastAPI stops recognizing the parameter as "inject the raw Request" and instead
# parses it as an ordinary required query parameter named "request", 422-ing the request before
# the fake body ever runs. Keeping both fakes and the import at module level sidesteps this
# entirely and is simplest; the nested-closure form works too as long as the import is hoisted to
# module scope.
@pytest.fixture(autouse=True)
def _bypass_janus_auth(monkeypatch):
    """api/main.py gates every route but /health behind api/auth.py's IamClient: a router-level
    Depends(auth.get_claims) (design doc §15.1/§15.5) plus per-route
    Depends(auth.require_role(...)) on the 25 mutating routes. This test suite exercises
    Neptune's own business logic against a real request/response cycle (TestClient(app)) -- it
    is not the place to exercise JANUS's token verification, which is iridium-iam-client's own
    test suite's job. So every request gets a fixed set of claims carrying every Neptune role,
    satisfying every require_role(...) gate the same way a real ADMIN's token would.

    Two patch points, not one, because require_role()'s closures call `self.get_claims(request)`
    directly as a plain method call rather than through FastAPI's Depends() sub-dependency
    injection (see iridium_iam_client.iam_client.IamClient.require_role) -- overriding only
    app.dependency_overrides[auth.get_claims] would satisfy the router-level global gate but
    leave every require_role(...)-gated route still trying (and failing) to verify a real bearer
    token:
      - monkeypatch the IamClient class's get_claims method itself, so `self.get_claims(request)`
        resolves the patched implementation on every fresh attribute lookup (Python re-resolves
        instance.method from the class each call; it isn't cached on a bound-method object)
      - override app.dependency_overrides[auth.get_claims], because the module-level
        `auth.get_claims = iam_client.get_claims` in api/auth.py captured one bound-method
        object at import time -- patching the class afterward doesn't change what that
        already-bound reference points to, and it's the exact object every router-level
        Depends(auth.get_claims) was registered with.
    """
    from neptune.api import auth
    from neptune.api.main import app

    monkeypatch.setattr(IamClient, "get_claims", _fake_get_claims_method)
    app.dependency_overrides[auth.get_claims] = _fake_get_claims_dependency
    try:
        yield
    finally:
        app.dependency_overrides.pop(auth.get_claims, None)
