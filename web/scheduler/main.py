from __future__ import annotations

import argparse
import logging
import sys

from web.log_config import configure_logging

configure_logging(level="INFO")
log = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SWPPP Scheduler — run due weekly and rain-event reports"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print what would be generated without writing any files or DB rows",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Override the safety gate that blocks runs with more than 10 unfiled dates",
    )
    args = parser.parse_args()

    from web.auth.db import connect
    from web.scheduler.run_due_reports import run_due_reports

    with connect() as conn:
        summary = run_due_reports(conn, dry_run=args.dry_run, force=args.force)

    log.info(
        "Done: projects_processed=%d reports_filed=%d failures=%d skipped=%d",
        summary["projects_processed"],
        summary["reports_filed"],
        summary["failures"],
        summary["skipped"],
    )

    if summary["failures"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
