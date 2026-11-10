"""Small local gate or the complete suite; stdlib only."""
import argparse
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
parser = argparse.ArgumentParser()
parser.add_argument("--suite", choices=("fast", "all"), default="fast")
args = parser.parse_args()
loader = unittest.TestLoader()
if args.suite == "fast":
    suite = loader.loadTestsFromNames([
        "test_t013", "test_t011_r1.TestLeagueEconomicMechanism", "test_t011_r1.TestLeagueOfferBoundary"])
else:
    suite = loader.discover(str(ROOT / "tests"))
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
