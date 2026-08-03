from __future__ import annotations

import argparse
import json

from app.database_health import run_database_health


def main() -> int:
    parser = argparse.ArgumentParser(description="Run non-destructive SQLite health checks.")
    parser.add_argument("--db", dest="db_file", help="Explicit SQLite file to inspect.")
    parser.add_argument("--full", action="store_true", help="Also run integrity_check.")
    args = parser.parse_args()
    result = run_database_health(args.db_file, full_check=args.full)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] in {"healthy", "degraded"} else 1


if __name__ == "__main__":
    raise SystemExit(main())