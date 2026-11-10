"""Command-line entry points for runs, resumes, and comparisons."""
import argparse
from time import perf_counter

from .experiment_t002 import CONDITIONS as T002_CONDITIONS, run_t002
from .experiment_t003 import CONDITIONS as T003_CONDITIONS, run_t003
from .experiments import run_experiment
from .simulation import advance, new_world
from .storage import load_snapshot, write_run


def main():
    parser = argparse.ArgumentParser(prog="python3 -m soccer_sim")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="start a new run")
    run.add_argument("--seed", type=int, required=True)
    conditions = T002_CONDITIONS | T003_CONDITIONS
    run.add_argument("--condition", choices=conditions, default="baseline")
    resume = commands.add_parser("resume", help="advance a day-boundary checkpoint")
    resume.add_argument("--snapshot", required=True)
    for command in (run, resume):
        command.add_argument("--days", type=int, required=True, help="additional simulated days")
        command.add_argument("--out", required=True)
    experiment = commands.add_parser("experiment", help="run the paired comparisons")
    suites = {"t002": run_t002, "t003": run_t003}
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
            world = new_world(args.seed, conditions[args.condition])
        else:
            world = load_snapshot(args.snapshot)
        start = perf_counter()
        advance(world, args.days)
        state_hash = write_run(args.out, world, perf_counter() - start)
        print(f"day {world.day}, {len(world.events)} events, SHA-256 {state_hash}")
    except (OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
