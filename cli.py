"""Command line interface.

    mmd run          decide what to do based on the ET clock, then do it
    mmd build        force a rebuild for a session
    mmd lock         freeze the current session's list
    mmd score        grade a session after the close
    mmd feed         regenerate site/feed.json only
    mmd phase        print the current phase and exit
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date

from . import clock, collect, publish, score
from .config import Config


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_phase(_: argparse.Namespace) -> int:
    cycle = clock.resolve()
    print(json.dumps({**cycle.as_dict(),
                      "seconds_to_lock": clock.seconds_to_lock(),
                      "should_build": cycle.should_build}, indent=2))
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    target = date.fromisoformat(args.session) if args.session else clock.resolve().target_session
    payload = collect.build_predictions(target, Config.load())
    collect.write(payload, target, lock=args.lock)
    publish.build_feed()
    print(f"{target}: {len(payload['predictions'])} rows, locked={args.lock}")
    return 0


def cmd_lock(args: argparse.Namespace) -> int:
    """Freeze whatever is on disk. Used by the 08:25 guard job."""
    target = date.fromisoformat(args.session) if args.session else clock.resolve().target_session
    from .config import session_dir
    path = session_dir(target) / "predictions.json"
    if not path.exists():
        print(f"nothing to lock for {target}", file=sys.stderr)
        return 2
    payload = json.loads(path.read_text())
    if payload.get("locked"):
        print(f"{target} already locked at {payload['locked_at_et']}")
        publish.build_feed()
        return 0
    payload["locked"] = True
    payload["locked_at_et"] = clock.now_et().isoformat()
    path.write_text(json.dumps(payload, indent=2) + "\n")
    publish.build_feed()
    print(f"locked {target} with {len(payload['predictions'])} rows")
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    target = date.fromisoformat(args.session) if args.session else clock.resolve().display_session
    card = score.score_session(target)
    publish.build_feed()
    print(json.dumps({k: v for k, v in card.items() if k != "rows"}, indent=2))
    return 0


def cmd_feed(_: argparse.Namespace) -> int:
    feed = publish.build_feed()
    print(f"phase={feed['cycle']['phase']} rows={len((feed.get('list') or {}).get('predictions', []))}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """The only command cron calls. Decides its own behaviour from the clock."""
    cycle = clock.resolve()
    log = logging.getLogger("mmd.run")
    log.info("phase=%s target=%s", cycle.phase.value, cycle.target_session)

    if cycle.should_build:
        lock = cycle.phase is clock.Phase.FINAL and clock.seconds_to_lock() <= args.lock_within
        try:
            payload = collect.build_predictions(cycle.target_session, Config.load())
            collect.write(payload, cycle.target_session, lock=lock)
        except Exception as exc:  # noqa: BLE001
            log.error("build failed: %s", exc)
            # A failed refresh must not take the site down — republish what we have.
            publish.build_feed()
            return 1

    elif cycle.should_score:
        try:
            score.score_session(cycle.display_session)
        except Exception as exc:  # noqa: BLE001
            log.error("scoring failed: %s", exc)

    else:
        log.info("phase %s is read-only; refreshing feed only", cycle.phase.value)

    publish.build_feed()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mmd")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="phase-aware run for cron")
    p.add_argument("--lock-within", type=int, default=3000,
                   help="seconds before 08:25 at which a build also locks")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("build")
    p.add_argument("--session")
    p.add_argument("--lock", action="store_true")
    p.set_defaults(fn=cmd_build)

    p = sub.add_parser("lock")
    p.add_argument("--session")
    p.set_defaults(fn=cmd_lock)

    p = sub.add_parser("score")
    p.add_argument("--session")
    p.set_defaults(fn=cmd_score)

    sub.add_parser("feed").set_defaults(fn=cmd_feed)
    sub.add_parser("phase").set_defaults(fn=cmd_phase)

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
