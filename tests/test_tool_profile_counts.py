import re
from pathlib import Path

from src import server
from src.cad_understanding.plan import DANGEROUS_OPS, SAFE_PLAN_OPS


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PROFILE_COUNTS = {"lean": 116, "core": 213, "full": 324}


def _readme_profile_counts(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    counts = {}
    for profile in ("lean", "core", "full"):
        match = re.search(rf"\| `{profile}` \| (\d+) \|", text)
        assert match, f"{path.name} is missing the {profile} tool count"
        counts[profile] = int(match.group(1))
    return counts


def test_registered_tool_profiles_match_both_readmes():
    registered = {tool.name for tool in server._registered_tools()}
    names = registered | set(server._DISABLED_TOOL_NAMES)
    core_hidden = set(server._CORE_HIDDEN_TOOLS)
    lean_hidden = core_hidden | set(server._LEAN_EXTRA_HIDDEN_TOOLS)

    assert core_hidden <= names
    assert lean_hidden <= names
    counts = {
        "full": len(names),
        "core": len(names - core_hidden),
        "lean": len(names - lean_hidden),
    }

    assert counts == EXPECTED_PROFILE_COUNTS
    assert "Registers 324 tools" in (ROOT / "src" / "server.py").read_text(encoding="utf-8")
    assert _readme_profile_counts(ROOT / "README.md") == EXPECTED_PROFILE_COUNTS
    assert _readme_profile_counts(ROOT / "README.zh-CN.md") == EXPECTED_PROFILE_COUNTS
    assert "send_command" in names
    assert "run_lisp" not in names
    assert "send_command" in DANGEROUS_OPS
    assert "send_command" not in SAFE_PLAN_OPS
    assert "run_lisp" not in SAFE_PLAN_OPS
    assert "run_lisp" not in DANGEROUS_OPS
