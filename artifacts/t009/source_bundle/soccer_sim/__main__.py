"""Command-line entry points for runs, resumes, and comparisons."""
import argparse
from time import perf_counter

from .experiment_t002 import CONDITIONS as T002_CONDITIONS, run_t002
from .experiment_t003 import CONDITIONS as T003_CONDITIONS, run_t003
from .experiment_t004 import CONDITIONS as T004_CONDITIONS, run_t004
from .experiment_t005 import CONDITIONS as T005_CONDITIONS, run_t005
from .experiment_t006 import CONDITIONS as T006_CONDITIONS, run_t006
from .experiment_t007 import CONDITIONS as T007_CONDITIONS, run_t007
from .experiment_t008 import CONDITIONS as T008_CONDITIONS, run_t008
from .experiment_t009 import CONDITIONS as T009_CONDITIONS, run_t009
from . import match_2d, multi_coach, multi_storage, selection_loop
from .experiments import run_experiment
from .simulation import advance, new_world
from .storage import load_snapshot, write_run


def main():
    parser = argparse.ArgumentParser(prog="python3 -m soccer_sim")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="start a new run")
    run.add_argument("--seed", type=int, required=True)
    conditions = (
        T002_CONDITIONS
        | T003_CONDITIONS
        | T004_CONDITIONS
        | T005_CONDITIONS
        | T006_CONDITIONS
        | T007_CONDITIONS
        | T008_CONDITIONS
        | T009_CONDITIONS
    )
    run.add_argument("--condition", choices=conditions, default="baseline")
    resume = commands.add_parser("resume", help="advance a day-boundary checkpoint")
    resume.add_argument("--snapshot", required=True)
    for command in (run, resume):
        command.add_argument("--days", type=int, required=True, help="additional simulated days")
        command.add_argument("--out", required=True)
    experiment = commands.add_parser("experiment", help="run the paired comparisons")
    suites = {
        "t002": run_t002,
        "t003": run_t003,
        "t004": run_t004,
        "t005": run_t005,
        "t006": run_t006,
        "t007": run_t007,
        "t008": run_t008,
        "t009": run_t009,
    }
    experiment.add_argument("--suite", choices=("t001", *suites), default="t001")
    experiment.add_argument("--out", required=True)
    args = parser.parse_args()

    try:
        if args.command == "experiment":
            if args.suite in suites:
                report = suites[args.suite](args.out)
                print(f"{args.suite.upper()}: {report['run_count']} runs across "
                      f"{len(report['sets'])} seed sets")
                return
            report = run_experiment(args.out)
            for name, condition in report["conditions"].items():
                metrics = condition["aggregate"]
                print(f"{name}: {metrics['runs']} runs, "
                      f"mean gain {metrics['mean_skill_gain']:.6f}, "
                      f"{metrics['accepted_count']}/{metrics['proposal_count']} proposals accepted")
            return
        if args.command == "run":
            if args.condition in T009_CONDITIONS:
                world = selection_loop.create_selection_world(args.seed, config=conditions[args.condition])
            elif args.condition in T008_CONDITIONS:
                world = match_2d.create_match_world(args.seed, config=conditions[args.condition])
            elif args.condition in T007_CONDITIONS:
                world = multi_coach.new_world(args.seed, conditions[args.condition])
            else:
                world = new_world(args.seed, conditions[args.condition])
        else:
            import json
            from pathlib import Path
            kind = json.loads(Path(args.snapshot).read_bytes()).get('world_kind')
            if kind not in (None, multi_coach.WORLD_KIND, match_2d.MATCH_WORLD_KIND, selection_loop.SELECTION_WORLD_KIND):
                raise ValueError('unknown world kind')
            if kind == selection_loop.SELECTION_WORLD_KIND:
                world = selection_loop.load_snapshot(args.snapshot)
            elif kind == match_2d.MATCH_WORLD_KIND:
                world = match_2d.load_snapshot(args.snapshot)
            elif kind == multi_coach.WORLD_KIND:
                world = multi_storage.load_snapshot(args.snapshot)
            else:
                world = load_snapshot(args.snapshot)
        start = perf_counter()
        if isinstance(world, selection_loop.SelectionWorldState):
            selection_loop.run_selection_world(days=args.days, world=world)
            state_hash = selection_loop.write_run(args.out, world, perf_counter() - start)
            print(f"day {world.day}, {len(world.events)} events, SHA-256 {state_hash}")
            return
        if isinstance(world, match_2d.MatchWorldState):
            match_2d.run_match(ticks=args.days, world=world)
            state_hash = match_2d.write_run(args.out, world, perf_counter() - start)
            print(f"tick {world.tick}, {len(world.events)} events, SHA-256 {state_hash}")
            return
        if isinstance(world, multi_coach.MultiWorld):
            multi_coach.advance(world, args.days)
            state_hash = multi_storage.write_run(args.out, world, perf_counter() - start)
        else:
            advance(world, args.days)
            state_hash = write_run(args.out, world, perf_counter() - start)
        print(f"day {world.day}, {len(world.events)} events, SHA-256 {state_hash}")
    except (OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
