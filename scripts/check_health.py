from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from urllib.request import urlopen


def main() -> None:
    parser = argparse.ArgumentParser(description="Check HTTP server and database availability")
    parser.add_argument("--url", default="http://127.0.0.1:8000/api/health")
    args = parser.parse_args()
    try:
        with urlopen(args.url, timeout=3) as response:
            if response.status != 200 or json.load(response).get("status") != "ok":
                raise RuntimeError("Unexpected health response")
        print(f"{datetime.now(timezone.utc).isoformat()} OK HTTP and database")
    except Exception as exc:
        print(f"{datetime.now(timezone.utc).isoformat()} FAIL {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
