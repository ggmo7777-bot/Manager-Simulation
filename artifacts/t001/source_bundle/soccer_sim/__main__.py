"""Command-line entry points for runs, resumes, and comparisons."""
import argparse
from time import perf_counter

from .experiments import run_experiment
from .simulation import advance, new_world
from .storage import load_snapshot, write_run


def main():
    parser = argparse.ArgumentParser(prog="python3 -m soccer_sim")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="start a new run")
    run.add_argument("--seed", type=int, required=True)
    resume = commands.add_parser("resume", help="advance a day-boundary checkpoint")
    resume.add_argument("--snapshot", required=True)
    for command in (run, resume):
        command.add_argument("--days", type=int, required=True, help="additional simulated days")
        command.add_argument("--out", required=True)
    experiment = commands.add_parser("experiment", help="run the paired comparisons")
    experiment.add_argument("--out", required=True)
    args = parser.parse_args()

    try:
        if args.command == "experiment":
            report = run_experiment(args.out)
            for name, condition in report["conditions"].items():
                metrics = condition["aggregate"]
                print(f"{name}: {metrics['runs']} runs, "
                      f"mean gain {metrics['mean_skill_gain']:.6f}, "
                      f"{metrics['accepted_count']}/{metrics['proposal_count']} proposals accepted")
            return
        world = new_world(args.seed) if args.command == "run" else load_snapshot(args.snapshot)
        start = perf_counter()
        advance(world, args.days)
        state_hash = write_run(args.out, world, perf_counter() - start)
        print(f"day {world.day}, {len(world.events)} events, SHA-256 {state_hash}")
    except (OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
