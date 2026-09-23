"""Run the local, single-process foundation API."""

import uvicorn


def main() -> None:
    print("Starting Neova foundation on http://127.0.0.1:8000", flush=True)
    print("Local API only: customer conversations are not implemented.", flush=True)
    uvicorn.run("neova.app:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
