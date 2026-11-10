"""League file compatibility and separate source provenance (T018).

This is a static import contract for trusted local source, not a Python safety
analyzer. The two old writer imports below are allowed only because league code
does not call those writers. Arbitrary calls, reflection, and source changes
during a process are outside this contract. External data/model dependencies
require a new contract before use.
"""
import ast
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sys


LEAGUE_SOURCE_SCOPE = "league-runtime-files-v1"
LEAGUE_RUNTIME_FILES = (
    "__init__.py", "fingerprints.py", "league_economy.py",
    "league_observation.py", "money.py", "match_2d.py", "storage.py",
    "advice.py", "observation.py", "policy.py", "simulation.py", "state.py",
)

# Exact lexical scope and imported symbol; nested/class names cannot match.
_WRITER_IMPORTS = {
    ("storage.py", ("write_run",), "experiments", (("measure", None),)),
    ("match_2d.py", ("write_run",), "experiment_t008", (("measure_match_run", None),)),
}


def _fingerprint(scope, files):
    encoded = json.dumps(files, allow_nan=False, ensure_ascii=True,
                         separators=(",", ":"), sort_keys=True).encode()
    return {"scope": scope, "sha256": hashlib.sha256(encoded).hexdigest(), "files": files}


class _ImportContract(ast.NodeVisitor):
    def __init__(self, filename):
        self.filename = filename
        self.scope = ()

    def fail(self, message):
        raise ValueError(f"league runtime import contract: {self.filename}: {message}")

    def visit_FunctionDef(self, node):
        previous = self.scope
        self.scope += (node.name,)
        self.generic_visit(node)
        self.scope = previous

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        previous = self.scope
        self.scope += ("class:" + node.name,)
        self.generic_visit(node)
        self.scope = previous

    def internal(self, module, node):
        aliases = tuple((alias.name, alias.asname) for alias in node.names)
        exception = (self.filename, self.scope, module, aliases)
        # The exceptions retain the original relative import spelling as well.
        if (isinstance(node, ast.ImportFrom) and node.level == 1
                and node.module == module and exception in _WRITER_IMPORTS):
            return
        filename = "__init__.py" if not module else module.replace(".", "/") + ".py"
        if filename not in LEAGUE_RUNTIME_FILES:
            self.fail(f"unregistered internal dependency {module or 'soccer_sim'}")

    def absolute(self, module, node):
        root = module.split(".")[0]
        if root == "soccer_sim":
            self.internal(module.removeprefix("soccer_sim").lstrip("."), node)
        elif root == "importlib":
            self.fail("unsupported dynamic import via importlib")
        elif root not in sys.stdlib_module_names and root != "__future__":
            self.fail(f"unsupported external dependency {module}")

    def visit_Import(self, node):
        for alias in node.names:
            self.absolute(alias.name, node)

    def visit_ImportFrom(self, node):
        if any(alias.name == "*" for alias in node.names):
            self.fail("unsupported wildcard import")
        if node.module == "builtins" and any(
                alias.name in ("__import__", "eval", "exec") for alias in node.names):
            self.fail("unsupported dynamic code/import from builtins")
        if node.level:
            if node.level != 1:
                self.fail("relative import outside soccer_sim")
            if node.module:
                self.internal(node.module, node)
            else:
                for alias in node.names:
                    self.internal(alias.name, node)
        elif node.module == "soccer_sim":
            for alias in node.names:
                self.internal(alias.name, node)
        else:
            self.absolute(node.module, node)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load) and node.id in ("__import__", "eval", "exec"):
            self.fail(f"unsupported dynamic code/import via {node.id}")

    def visit_Attribute(self, node):
        if node.attr in ("__import__", "eval", "exec", "import_module"):
            self.fail(f"unsupported dynamic code/import via {node.attr}")
        self.generic_visit(node)


@lru_cache(maxsize=48)
def _validate_runtime_imports(filename, source):
    # Content and lexical filename are both keys: edits must be rechecked, and
    # a writer exception must never carry over to another file. Cache only None,
    # not an AST. File existence, reads, and hashes are still checked every time.
    try:
        tree = ast.parse(source, filename=filename)
    except (SyntaxError, ValueError) as error:
        raise ValueError(f"invalid league runtime source: {filename}") from error
    _ImportContract(filename).visit(tree)


def league_runtime_fingerprint(package_dir=None):
    """Validate the fixed import boundary, then hash all twelve complete files."""
    package = Path(package_dir) if package_dir is not None else Path(__file__).parent
    files = {}
    for filename in sorted(LEAGUE_RUNTIME_FILES):
        path = package / filename
        if not path.is_file():
            raise ValueError(f"league runtime source missing: {filename}")
        source = path.read_bytes()
        _validate_runtime_imports(filename, source)
        files[filename] = hashlib.sha256(source).hexdigest()
    return _fingerprint(LEAGUE_SOURCE_SCOPE, files)


def source_provenance(package_dir=None, project_root=None):
    """Return manifest-only provenance; CLI entry is not its dependency closure."""
    package = Path(package_dir) if package_dir is not None else Path(__file__).parent
    root = Path(project_root) if project_root is not None else package.parent
    runtime = league_runtime_fingerprint(package)
    package_files = {path.relative_to(package).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in sorted(package.rglob("*.py"))}
    cli = package / "__main__.py"
    if not cli.is_file():
        raise ValueError("CLI provenance source missing: __main__.py")
    analysis_paths = set(package.glob("experiment*.py"))
    analysis_paths.update((root / "experiments").rglob("*.py"))
    analysis = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(analysis_paths)}
    return {
        "runtime": runtime,
        "package": _fingerprint("package-python-files-v1", package_files),
        "cli_entry": _fingerprint("cli-entry-file-v1", {"__main__.py": hashlib.sha256(cli.read_bytes()).hexdigest()}),
        "analysis": _fingerprint("analysis-python-files-v1", analysis),
    }
