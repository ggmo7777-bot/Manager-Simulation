"""T015 checks: fixed fixture is unchanged vs frozen T014; sampled-population generalization.

New output directories only. Author of this script: Claude Sonnet 5.5 (not the T001-T014 authors).
"""
import argparse
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import statistics
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from soccer_sim import league_economy as le

SEEDS = tuple(range(100, 130))
EQUIV_SEEDS = (100, 101, 102, 103, 104)
INSTITUTIONS = ("closed_no_solidarity", "open_no_solidarity", "open_solidarity")
METRICS = ("academy_retained_skill_gain", "mean_skill_gain", "solidarity_dividends_paid_units",
           "academy_clubs_points", "buyer_clubs_points", "total_transfers",
           "academy_facility_days", "buyer_facility_days")


def frozen_t014():
    directory = ROOT / "artifacts/t014/source_bundle"
    manifest = json.loads((directory / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == expected, name
    package = directory / "soccer_sim"
    spec = importlib.util.spec_from_file_location("_t015_reference", package / "__init__.py",
                                                 submodule_search_locations=[str(package)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return importlib.import_module("_t015_reference.league_economy")


def strip(summary):
    return {k: v for k, v in summary.items() if k not in ("identity", "condition")}


def equivalence(old):
    results = []
    for condition in (*le.T011_ALL_CONDITIONS,):
        for seed in EQUIV_SEEDS:
            past = old.run_league_world(seed=seed, days=84, config=old.config_for_condition(condition))
            now = le.run_league_world(seed=seed, days=84, config=le.config_for_condition(condition))
            same_events = past.events == now.events
            same_summary = strip(old.summarize_league_world(past)) == strip(le.summarize_league_world(now))
            results.append({"condition": condition, "seed": seed,
                            "events_equal": same_events, "summary_equal": same_summary})
            assert same_events and same_summary, (condition, seed)
    return results


def mean_ci(values):
    m = statistics.fmean(values)
    sd = statistics.stdev(values)
    half = 2.045 * sd / len(values) ** 0.5  # t(0.975, 29); exploratory, not a tuned threshold
    return {"mean": round(m, 6), "sd": round(sd, 6), "ci95": [round(m - half, 6), round(m + half, 6)],
            "positive": sum(v > 0 for v in values), "negative": sum(v < 0 for v in values), "n": len(values)}


def run(out):
    out.mkdir(parents=True)
    start = perf_counter()
    old = frozen_t014()
    equiv = equivalence(old)
    table = {}
    for mode, suffix in (("fixed", ""), ("sampled", le.POPULATION_SUFFIX)):
        for institution in INSTITUTIONS:
            for seed in SEEDS:
                world = le.run_league_world(seed=seed, days=84, config=le.config_for_condition(institution + suffix))
                summary = le.summarize_league_world(world)
                table[mode, institution, seed] = {m: summary[m] for m in METRICS}
        print(mode, "complete", flush=True)
    rows = [{"population": m, "institution": i, "seed": s, **v} for (m, i, s), v in table.items()]
    effects = {}
    for mode in ("fixed", "sampled"):
        for metric in METRICS:
            for a, b in (("open_solidarity", "open_no_solidarity"), ("open_no_solidarity", "closed_no_solidarity")):
                diffs = [table[mode, a, s][metric] - table[mode, b, s][metric] for s in SEEDS]
                effects[f"{mode}|{metric}|{a}-{b}"] = mean_ci(diffs) if len(set(diffs)) > 1 else {"all_equal": diffs[0], "n": len(diffs)}
    # Spread of the initial population itself (what the fixture could not express).
    spread = {}
    for mode, suffix in (("fixed", ""), ("sampled", le.POPULATION_SUFFIX)):
        levels = []
        for seed in SEEDS:
            w = le.run_league_world(seed=seed, days=0, config=le.config_for_condition("open_solidarity" + suffix))
            levels += [w.players[p].composite_skill() for p in le.PLAYER_IDS]
        spread[mode] = {"min": min(levels), "max": max(levels), "sd": round(statistics.pstdev(levels), 6), "n": len(levels)}
    result = {"identity": le.league_identity(), "equivalence_runs": len(equiv), "equivalence_all_equal": True,
              "seeds": list(SEEDS), "rows": rows, "paired_effects": effects, "population_spread": spread,
              "elapsed_seconds": round(perf_counter() - start, 3)}
    (out / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"equivalence_runs": len(equiv), "elapsed": result["elapsed_seconds"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    run(parser.parse_args().out)
