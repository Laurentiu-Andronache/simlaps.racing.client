"""Parse one or more ACE logs and print a session/lap summary.

The script intentionally requires an input path.  It must not implicitly read
the operator's personal ACE log directory.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Sequence, TextIO


def _add_project_root_to_import_path() -> None:
    """Make direct execution work regardless of the caller's working directory."""

    project_root = Path(__file__).resolve().parents[1]
    project_root_string = str(project_root)
    if project_root_string not in sys.path:
        sys.path.insert(0, project_root_string)


_add_project_root_to_import_path()

from src.core.log_parser import LogParser  # noqa: E402
from src.models import SessionData  # noqa: E402


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse an ACE log file or all matching logs in a directory."
    )
    parser.add_argument(
        "input",
        type=Path,
        help="path to an ACE log file or directory containing ACE logs",
    )
    parser.add_argument(
        "-p",
        "--pattern",
        default="*.txt",
        help="glob pattern for directory input (default: %(default)s)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="write the report to this file instead of stdout",
    )
    return parser


def _log_files(input_path: Path, pattern: str) -> list[Path]:
    """Validate *input_path* and return the files that should be parsed."""

    input_path = input_path.expanduser()
    if not input_path.exists():
        raise ValueError(f"input path does not exist: {input_path}")
    if input_path.is_file():
        return [input_path]
    if not input_path.is_dir():
        raise ValueError(
            f"input path is not a regular file or directory: {input_path}"
        )

    try:
        files = sorted(path for path in input_path.glob(pattern) if path.is_file())
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid pattern {pattern!r}: {exc}") from exc
    if not files:
        raise ValueError(f"no files matching {pattern!r} found in {input_path}")
    return files


def _print_session(session: SessionData, output: TextIO) -> None:
    """Print the existing session summary format."""

    print(f"\nTrack: {session.track}", file=output)
    print(f"Car:   {session.car}", file=output)
    print(f"Type:  {session.session_type}", file=output)
    print(f"Laps:  {len(session.laps)}", file=output)
    print(file=output)
    print(
        f"{'Lap':>3}  {'Time':>10}  {'State':>12}  {'Valid':>5}  "
        f"{'S1':>7}  {'S2':>7}  {'S3':>7}  {'Compound':>8}",
        file=output,
    )
    print("-" * 80, file=output)

    for lap in session.laps:
        time_str = lap.lap_time_str if lap.lap_time_str else "--:--.---"
        s1 = f"{lap.sector1_ms}" if lap.sector1_ms is not None else "-"
        s2 = f"{lap.sector2_ms}" if lap.sector2_ms is not None else "-"
        s3 = f"{lap.sector3_ms}" if lap.sector3_ms is not None else "-"
        compound = lap.tyre_compound or "-"
        print(
            f"{lap.lap_number:>3}  {time_str:>10}  {lap.lap_state.value:>12}  "
            f"{'Y' if lap.is_valid else 'N':>5}  {s1:>7}  {s2:>7}  "
            f"{s3:>7}  {compound:>8}",
            file=output,
        )


async def _parse_logs(log_files: Sequence[Path], output: TextIO) -> int:
    """Parse *log_files*, returning a nonzero status if any file fails."""

    had_error = False
    for log_file in log_files:
        print(f"\n{'=' * 80}", file=output)
        print(f"FILE: {log_file.name}", file=output)
        print(f"{'=' * 80}", file=output)

        parser = LogParser(log_path=str(log_file))
        try:
            sessions = await parser.parse_file()
        except Exception as exc:  # the next file should still be inspected
            had_error = True
            print(f"ERROR parsing {log_file.name}: {exc}", file=output)
            continue

        if not sessions:
            print("No sessions found.", file=output)
            continue

        for session in sessions:
            _print_session(session, output)
    return 1 if had_error else 0


def _output_stream(output_path: Path, input_files: Sequence[Path]) -> TextIO:
    """Open and validate the optional report output path."""

    output_path = output_path.expanduser()
    if output_path.exists() and not output_path.is_file():
        raise ValueError(f"output path is not a regular file: {output_path}")
    if not output_path.parent.is_dir():
        raise ValueError(f"output directory does not exist: {output_path.parent}")
    output_resolved = output_path.resolve()
    if any(output_resolved == input_file.resolve() for input_file in input_files):
        raise ValueError("output path must not overwrite an input log")
    try:
        return output_path.open("w", encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"could not open output path {output_path}: {exc}") from exc


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    try:
        log_files = _log_files(args.input, args.pattern)
        if args.output is None:
            return asyncio.run(_parse_logs(log_files, sys.stdout))

        with _output_stream(args.output, log_files) as output:
            return asyncio.run(_parse_logs(log_files, output))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
