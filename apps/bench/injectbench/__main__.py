"""InjectBench CLI.

    python -m injectbench                      run everything, print the report
    python -m injectbench --family exfiltration  run one family
    python -m injectbench --case deputy-01     run one case, verbosely
    python -m injectbench --json out.json      write machine-readable results
    python -m injectbench --publish http://localhost:8080   post to a leaderboard
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="injectbench",
        description="An open benchmark of prompt-injection attacks against agents.",
    )
    parser.add_argument("--family", help="run only cases in this family")
    parser.add_argument("--case", help="run only this case id, with detail")
    parser.add_argument("--label", default="local", help="label for the run")
    parser.add_argument("--json", type=Path, help="write full results as JSON")
    parser.add_argument("--publish", help="base URL of a Membrane proxy to post to")
    parser.add_argument("--no-benign", action="store_true",
                        help="skip the false-positive corpus")
    parser.add_argument("--strict", action="store_true",
                        help="exit non-zero unless every target is met")
    parser.add_argument("--list", action="store_true", help="list the corpus and exit")
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    from .harness import run_all
    from .report import render, targets_met, write_json
    from .schema import load_benign, load_cases

    cases = load_cases()
    benign = [] if args.no_benign else load_benign()

    if args.list:
        by_family: dict[str, int] = {}
        for case in cases:
            by_family[case.family] = by_family.get(case.family, 0) + 1
        print(f"\n  {len(cases)} attack cases in {len(by_family)} families\n")
        for family, count in sorted(by_family.items()):
            print(f"    {family:<30} {count:>3}")
            for case in cases:
                if case.family == family:
                    print(f"      · {case.id:<16} {case.title}")
        print(f"\n  {len(benign)} benign documents\n")
        return 0

    if args.family:
        cases = [c for c in cases if c.family == args.family]
    if args.case:
        cases = [c for c in cases if c.id == args.case]
        benign = []
    if not cases:
        print("no cases matched", file=sys.stderr)
        return 2

    report = await run_all(label=args.label, cases=cases, benign=benign)

    if args.case:
        result = report.cases[0]
        print(json.dumps(result.to_dict(), indent=2))
        return 0

    print(render(report))

    if args.json:
        path = write_json(report, args.json)
        print(f"  results written to {path}\n")

    if args.publish:
        from .report import publish

        try:
            posted = await publish(report, args.publish)
            print(f"  published as {posted['run_id']}\n")
        except Exception as exc:
            print(f"  could not publish: {exc}\n", file=sys.stderr)

    if args.strict and not all(targets_met(report).values()):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    # The report uses box drawing and block characters; a Windows console
    # defaults to a codepage that cannot encode them.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover
            pass
    args = _parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
