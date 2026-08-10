"""JANUS (iridium-iam) authentication/authorization for Neptune's API.

Neptune is a real server holding its own database credential — unlike a desktop app's login
screen, which a user with shell access to the machine can bypass by reading the config file
(design doc §5), a check added HERE is genuine enforcement (design doc §15.1/§15.5: "already a
server holding credentials, so every check added is genuine enforcement on day one"). Before
this module, every one of Neptune's 43 routes had, at most, a DB-session dependency — nothing
checking who the caller is, or whether they're allowed to do what they're asking.

One `IamClient` instance, module-level singleton (mirrors `SessionLocal`'s own module-level
pattern in `db/base.py`) — its JWKS cache and revocation poller are process-lifetime state, wired
into `main.py`'s existing `lifespan()` via `iam_client.lifespan(app)`.
"""
from __future__ import annotations

from iridium_iam_client import IamClient, IamClientConfig

from neptune.config import settings

iam_client = IamClient(
    IamClientConfig(
        issuer=settings.janus_issuer,
        audience="iridium:neptune",
        product_code="neptune",
    )
)

# Re-exported at module level so main.py reads `auth.get_claims` / `auth.require_role(...)` —
# matching the "one auth.py, one thing to import" shape design doc §15.5 asks for.
get_claims = iam_client.get_claims
require_role = iam_client.require_role
require_scope = iam_client.require_scope
