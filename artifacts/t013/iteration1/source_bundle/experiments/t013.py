"""T013 corrected rules: 7 x 30 paired worlds + 30 perfect-fatigue controls."""
import argparse
from collections import Counter
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from random import Random
from statistics import mean
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from soccer_sim import league_economy as le
from soccer_sim.experiment_t002 import summarize_differences
from soccer_sim.storage import canonical, digest, runtime, write_json
from audit_t013 import ledger_audit, admission_audit

SEEDS = tuple(range(100, 130))
METRICS = ("total_transfers", "academy_retained_skill_gain", "academy_facility_days",
           "academy_clubs_points", "academy_clubs_cash", "talents_appearances", "mean_skill_gain")


def run(out):
    identity = le.league_identity()
    rows, results, resume = [], {}, {}
    totals = Counter()
    start = perf_counter()
    for condition in (*le.T011_ALL_CONDITIONS, "open_solidarity_perfect_fatigue"):
        cfg = le.config_for_condition(condition if condition in le.T011_ALL_CONDITIONS else "open_solidarity")
        if condition.endswith("perfect_fatigue"):
            cfg = replace(cfg, self_fatigue_noise=0.0, coach_fatigue_noise=0.0,
                          scout_fatigue_noise=0.0, match_fatigue_noise=0.0)
        results[condition] = {}
        for seed in SEEDS:
            w = le.run_league_world(seed=seed, days=84, config=cfg)
            raw = le._snapshot_payload_unchecked(w)
            ledger, admission = ledger_audit(raw), admission_audit(raw)
            totals.update(admission["counts"])
            summary = le.summarize_league_world(w)
            results[condition][seed] = summary
            rows.append({"condition": condition, "seed": seed, "days": 84, "config": cfg.to_dict(),
                         "summary": summary, "ledger_audit": ledger, "admission_audit": admission,
                         "snapshot_sha256": digest(raw)})
            if seed == 100:
                target = out / "representative" / condition
                le.save_league_artifacts(w, target / "full")
                if condition in le.T011_MAIN_CONDITIONS:
                    half = le.run_league_world(seed=seed, days=42, config=cfg)
                    le.save_league_artifacts(half, target / "half")
                    continued = le.load_league_snapshot(target / "half/snapshot.json")
                    le.advance_league_days(continued, 42)
                    le.save_league_artifacts(continued, target / "resumed")
                    equal = {f: (target / "full" / f).read_bytes() == (target / "resumed" / f).read_bytes()
                             for f in ("snapshot.json", "events.jsonl")}
                    assert all(equal.values())
                    resume[condition] = equal
        print(f"{condition}: 30 worlds, independent ledgers/requests passed", flush=True)
    old = json.loads((ROOT / "artifacts/t011-r1/experiments/comparison.json").read_text())
    references = {c: {r["seed"]: r["summary"] for r in old["runs"]
                      if r["set"] == "confirmation" and r["condition"] == c} for c in le.T011_MAIN_CONDITIONS}
    rng = Random(13013)
    resamples = [tuple(rng.randrange(30) for _ in range(30)) for _ in range(2000)]
    def contrast(a, b):
        return {m: summarize_differences({s: a[s][m] - b[s][m] for s in SEEDS}, resamples) for m in METRICS}
    effects = {"solidarity_minus_no_solidarity": contrast(results["open_solidarity"], results["open_no_solidarity"]),
               "measured_minus_perfect_fatigue": contrast(results["open_solidarity"], results["open_solidarity_perfect_fatigue"])}
    for c in le.T011_MAIN_CONDITIONS:
        effects[f"T013_minus_R1_{c}"] = contrast(results[c], references[c])
    assert le.league_identity() == identity, "Source changed during run"
    write_json(out / "summary.json", {"task": "T013", "identity": identity, "runtime": runtime(),
        "run_count": len(rows), "seeds": SEEDS, "means": {c: {m: mean(v[s][m] for s in SEEDS)
        for m in METRICS} for c, v in results.items()}, "effects": effects, "admission_totals": dict(totals),
        "representative_resume": resume, "elapsed_seconds": perf_counter() - start,
        "limits": "Fixed initial cohort; version contrast is not a single-factor causal estimate. Money remains float."})
    write_json(out / "comparison.json", {"identity": identity, "runs": rows})
    (out / "runs.jsonl").write_bytes(b"".join(canonical(r) + b"\n" for r in rows))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    run(args.out)
