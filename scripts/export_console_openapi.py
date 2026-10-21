"""Export the console contract without settings, environment files, pools or providers."""

from __future__ import annotations

import json
from pathlib import Path

from csa.console.auth import build_identity_router
from csa.console.onboarding import build_onboarding_router
from csa.console.router import build_router
from csa.hr.api import build_hr_router
from fastapi import FastAPI

OUTPUT = Path(__file__).resolve().parents[1] / "docs" / "console_openapi.json"


def contract() -> dict:
    app = FastAPI(title="CSAV2 console", version="1.0.0", docs_url=None, redoc_url=None)
    app.include_router(build_identity_router())
    app.include_router(build_onboarding_router())
    app.include_router(build_router())
    app.include_router(build_hr_router())
    return app.openapi()


def main() -> None:
    OUTPUT.write_text(json.dumps(contract(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("Exported docs/console_openapi.json (no runtime connections)")


if __name__ == "__main__":
    main()
