"""T018 compatibility boundary, source provenance, and typed replay failures."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from soccer_sim import fingerprints as fp
from soccer_sim import league_economy as le
from soccer_sim.storage import digest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "soccer_sim"


class TestLeagueFingerprint(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / "soccer_sim"
        shutil.copytree(PACKAGE, self.package, ignore=shutil.ignore_patterns("__pycache__"))
        (self.root / "experiments").mkdir()
        (self.root / "experiments" / "analysis.py").write_text("# analysis\n")

    def append(self, filename, source):
        with (self.package / filename).open("a") as stream:
            stream.write("\n" + source + "\n")

    def test_runtime_is_exact_twelve_file_map_and_hashes_itself(self):
        result = fp.league_runtime_fingerprint(self.package)
        self.assertEqual(result["scope"], "league-runtime-files-v1")
        self.assertEqual(len(result["files"]), 12)
        self.assertEqual(set(result["files"]), set(fp.LEAGUE_RUNTIME_FILES))
        self.assertEqual(result["sha256"], digest(result["files"]))
        for filename, expected in result["files"].items():
            self.assertEqual(expected, hashlib.sha256((self.package / filename).read_bytes()).hexdigest())

    def test_every_runtime_file_changes_hash_even_with_comment_only(self):
        before = fp.league_runtime_fingerprint(self.package)
        for filename in fp.LEAGUE_RUNTIME_FILES:
            with self.subTest(filename=filename):
                path = self.package / filename
                original = path.read_bytes()
                self.append(filename, "# runtime compatibility includes whole files")
                after = fp.league_runtime_fingerprint(self.package)
                self.assertNotEqual(before["sha256"], after["sha256"])
                self.assertEqual([name for name in before["files"]
                                  if before["files"][name] != after["files"][name]], [filename])
                path.write_bytes(original)

    def test_nonruntime_provenance_changes_do_not_change_identity(self):
        before = fp.source_provenance(self.package, self.root)
        changes = (
            ("soccer_sim/__main__.py", {"package", "cli_entry"}),
            ("soccer_sim/experiment_t018_added.py", {"package", "analysis"}),
            ("soccer_sim/experiments.py", {"package", "analysis"}),
            ("experiments/analysis.py", {"analysis"}),
            ("experiments/nested/new.py", {"analysis"}),
        )
        for filename, changed in changes:
            with self.subTest(filename=filename):
                path = self.root / filename
                original = path.read_bytes() if path.exists() else None
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((original or b"") + b"\n# provenance only\n")
                after = fp.source_provenance(self.package, self.root)
                self.assertEqual({key for key in before if before[key] != after[key]}, changed)
                if original is None:
                    path.unlink()
                else:
                    path.write_bytes(original)

    def test_missing_each_runtime_file_is_rejected(self):
        for filename in fp.LEAGUE_RUNTIME_FILES:
            with self.subTest(filename=filename):
                path = self.package / filename
                original = path.read_bytes()
                path.unlink()
                with self.assertRaisesRegex(ValueError, "source missing"):
                    fp.league_runtime_fingerprint(self.package)
                path.write_bytes(original)

    def test_unregistered_dependency_import_spellings_are_rejected(self):
        cases = (
            "from . import experiment_t011", "from .experiment_t011 import CONDITIONS",
            "import soccer_sim.experiment_t011 as analysis",
            "from soccer_sim import experiment_t011",
            "from soccer_sim.experiment_t011 import CONDITIONS",
            "from .new_runtime_module import value", "from .. import other",
            "import third_party_model", "from .state import *",
        )
        path = self.package / "policy.py"
        original = path.read_bytes()
        for source in cases:
            with self.subTest(source=source):
                self.append("policy.py", source)
                with self.assertRaisesRegex(ValueError, "import contract"):
                    fp.league_runtime_fingerprint(self.package)
                path.write_bytes(original)

    def test_dynamic_import_spellings_are_rejected(self):
        cases = (
            "import importlib", "from importlib import import_module as load",
            "__import__('soccer_sim.experiment_t011')",
            "loader = __import__", "exec('import soccer_sim.experiment_t011')",
            "eval('1')", "from builtins import __import__ as load",
            "import builtins\nbuiltins.__import__('soccer_sim.experiment_t011')",
        )
        path = self.package / "policy.py"
        original = path.read_bytes()
        for source in cases:
            with self.subTest(source=source):
                self.append("policy.py", source)
                with self.assertRaisesRegex(ValueError, "unsupported dynamic"):
                    fp.league_runtime_fingerprint(self.package)
                path.write_bytes(original)

    def test_writer_exception_cannot_escape_exact_scope_module_or_symbol(self):
        # Both existing imports pass in their actual writer functions.
        fp.league_runtime_fingerprint(self.package)
        cases = (
            ("policy.py", "def write_run():\n    from .experiments import measure"),
            ("storage.py", "def other():\n    from .experiments import measure"),
            ("storage.py", "def outer():\n    def write_run():\n        from .experiments import measure"),
            ("storage.py", "class Other:\n    def write_run(self):\n        from .experiments import measure"),
            ("storage.py", "def write_run():\n    from .experiment_t008 import measure_match_run"),
            ("storage.py", "def write_run():\n    from .experiments import measure as other"),
            ("storage.py", "def write_run():\n    from .experiments import other"),
            ("match_2d.py", "def other():\n    from .experiment_t008 import measure_match_run"),
        )
        for filename, source in cases:
            with self.subTest(filename=filename, source=source):
                path = self.package / filename
                original = path.read_bytes()
                self.append(filename, source)
                with self.assertRaisesRegex(ValueError, "unregistered internal"):
                    fp.league_runtime_fingerprint(self.package)
                path.write_bytes(original)

    def test_bad_runtime_source_is_reported_as_contract_error(self):
        self.append("policy.py", "def broken(")
        with self.assertRaisesRegex(ValueError, "invalid league runtime source"):
            fp.league_runtime_fingerprint(self.package)


class TestLeagueSave17(unittest.TestCase):
    def test_identity_separates_runtime_from_manifest_only_provenance(self):
        world = le.run_league_world(seed=100, days=0)
        with tempfile.TemporaryDirectory() as tmp:
            le.save_league_artifacts(world, tmp)
            manifest = json.loads((Path(tmp) / "manifest.json").read_text())
            snap = json.loads((Path(tmp) / "snapshot.json").read_text())
            identity = snap["identity"]
            self.assertEqual(identity["save"], 17)
            self.assertEqual(identity["source_scope"], fp.LEAGUE_SOURCE_SCOPE)
            self.assertEqual(identity["source_sha256"], manifest["source_provenance"]["runtime"]["sha256"])
            self.assertEqual(identity, manifest["identity"])
            self.assertNotIn("source_provenance", snap)
            self.assertEqual(identity["rules"], "t015-sampled-population-v1")
            self.assertEqual(identity["policy"], "t013-independent-transfer-score-v1")

    def test_save_resume_and_recorded_branch_resume_remain_exact(self):
        world = le.run_league_world(seed=100, days=7,
                                   config=le.config_for_condition("open_solidarity_sampled_population"))
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "parent"
            le.save_league_artifacts(world, parent)
            original = (parent / "snapshot.json").read_bytes()
            resumed = le.load_league_snapshot(parent / "snapshot.json")
            le.advance_league_days(world, 7)
            le.advance_league_days(resumed, 7)
            self.assertEqual(le.snapshot_payload(world), le.snapshot_payload(resumed))
            child = le.fork_league_world(resumed, kind="player_trust", player_id="p3",
                                        coach_id="c1", value=0.05, reason="T018 typed replay")
            le.advance_league_days(child, 7)
            child_dir = Path(tmp) / "child"
            le.save_league_artifacts(child, child_dir)
            loaded = le.load_league_snapshot(child_dir / "snapshot.json")
            le.advance_league_days(child, 7)
            le.advance_league_days(loaded, 7)
            self.assertEqual(le.snapshot_payload(child), le.snapshot_payload(loaded))
            self.assertEqual((parent / "snapshot.json").read_bytes(), original)

    def test_old_unknown_tampered_and_wrongly_typed_identity_is_rejected(self):
        raw = le.snapshot_payload(le.run_league_world(seed=100, days=0))
        cases = (("save", 16), ("save", True), ("source_scope", "unknown"),
                 ("source_scope", None), ("source_sha256", "0" * 64))
        for key, value in cases:
            with self.subTest(key=key, value=value):
                altered = deepcopy(raw)
                altered["identity"][key] = value
                with self.assertRaisesRegex(ValueError, "snapshot.identity"):
                    le._replay_snapshot_payload(altered)
        del raw["identity"]["source_scope"]
        with self.assertRaisesRegex(ValueError, "snapshot.identity"):
            le._replay_snapshot_payload(raw)

    def test_provenance_failure_precedes_output_creation(self):
        world = le.run_league_world(seed=100, days=0)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "absent"
            with patch.object(le, "source_provenance", side_effect=ValueError("missing source")):
                with self.assertRaisesRegex(ValueError, "missing source"):
                    le.save_league_artifacts(world, out)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
