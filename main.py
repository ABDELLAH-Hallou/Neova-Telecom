"""Single-process launcher for foundation API and graph."""

import sys

from uvicorn import run

from .app import app


def main():
    """Start FastAPI app on loopback."""
    # Validate environment before starting server
    try:
        import neova.config  # noqa: F401
    except RuntimeError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    print("Starting Néova Telecom Foundation on http://127.0.0.1:8000")
    print("This is foundation mode: no customer-facing answers or booking.")
    run(
        "neova.app:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
