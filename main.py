"""Run the local, single-process Neova Telecom Customer Agent API."""

import uvicorn


def main() -> None:
    print("Starting Neova Telecom Customer Agent on http://127.0.0.1:8000", flush=True)
    uvicorn.run("neova.app:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
