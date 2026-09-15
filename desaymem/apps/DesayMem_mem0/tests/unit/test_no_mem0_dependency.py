from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
_IMPORT = re.compile(r"^(?:from mem0(?:\s|\.| import)|import mem0(?:\s|$|,))")


def test_pyproject_has_no_mem0ai_dependency():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "mem0ai" not in text.lower()


def test_source_does_not_import_mem0():
    hits = []
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            if _IMPORT.match(stripped):
                hits.append(f"{path}:{stripped}")
    assert hits == []
