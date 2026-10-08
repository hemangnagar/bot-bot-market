"""Fail if any 6-line window of this repo's source appears verbatim in an upstream repo.

Upstream corpora: the pinned clones under .upstream/ and the installed git packages
(edshield, decision_gate) in the venv. Blank lines and whitespace are normalised.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOW = 6
EXTS = {".py", ".js", ".mjs", ".ts", ".sql", ".yaml", ".yml", ".json", ".md"}
OURS = ["registry", "providers", "settlement", "agents", "runner", "report", "scripts", "tests"]


def lines_of(path: Path) -> list[str]:
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return []
    return [ln.strip() for ln in text.splitlines() if ln.strip() and len(ln.strip()) > 12]


def windows(lines: list[str]) -> set[tuple[str, ...]]:
    return {tuple(lines[i:i + WINDOW]) for i in range(0, max(0, len(lines) - WINDOW + 1))}


def upstream_files() -> list[Path]:
    files: list[Path] = []
    up = ROOT / ".upstream"
    if up.exists():
        files += [p for p in up.rglob("*") if p.suffix in EXTS and ".git" not in p.parts and "node_modules" not in p.parts]
    for mod in ("edshield", "decision_gate"):
        spec = importlib.util.find_spec(mod)
        if spec and spec.origin:
            files += [p for p in Path(spec.origin).parent.rglob("*") if p.suffix in EXTS]
    return files


def main() -> int:
    corpus: dict[tuple[str, ...], Path] = {}
    for f in upstream_files():
        for w in windows(lines_of(f)):
            corpus.setdefault(w, f)
    if not corpus:
        print("no upstream corpus found (run `make setup`); nothing to compare")
        return 0
    hits = []
    for d in OURS:
        for f in (ROOT / d).rglob("*"):
            if f.suffix in EXTS and f.is_file():
                for w in windows(lines_of(f)):
                    if w in corpus:
                        hits.append((f, corpus[w], w[0][:60]))
    for ours, theirs, first in hits:
        print(f"COPIED? {ours.relative_to(ROOT)} <- {theirs}: {first}")
    print(f"checked {len(corpus)} upstream windows; {len(hits)} overlapping windows")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
