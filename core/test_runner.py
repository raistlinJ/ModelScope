"""
Test runner backend for ModelScope's test suite visualization.

Invokes pytest as a subprocess and parses its terminal output into structured
TestItem / TestRunResult objects.  No extra pytest plugins are required.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path


def _pytest_cmd() -> list[str]:
    """
    Return the command prefix to invoke pytest.

    Streamlit ships in a pipx venv that has no pytest or project deps.
    We need the Python that *both* has pytest AND can import the project
    modules (requests, paramiko, etc.).  Priority:

      1. python3 -m pytest  (system Python — has all deps on this machine)
      2. python  -m pytest
      3. sys.executable -m pytest  (current interpreter; may lack deps)
    """
    for py in ("python3", "python", sys.executable):
        py_path = py if py == sys.executable else shutil.which(py)
        if not py_path:
            continue
        try:
            r = subprocess.run(
                [py_path, "-m", "pytest", "--version"],
                capture_output=True, timeout=5,
            )
            if r.returncode == 0:
                return [py_path, "-m", "pytest"]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return [sys.executable, "-m", "pytest"]

# ── Status constants ──────────────────────────────────────────────────────────
_STATUS_RE = re.compile(
    r"^(tests/.+?)\s+(PASSED|FAILED|ERROR|SKIPPED|XFAILED|XPASSED)"
)
_FAIL_SUMMARY_RE = re.compile(
    r"^FAILED\s+(tests/[^\s]+)\s+-\s+(.+)$"
)
_SUMMARY_RE = re.compile(
    r"=+\s*((?:\d+ \w+(?:,\s*)?)+)\s+in\s+([\d.]+)s\s*=+"
)

CATEGORY_ORDER = ["regression", "smoke", "unit", "functional", "other"]

CATEGORY_LABELS = {
    "regression": "Regression Guards",
    "smoke":      "Smoke Tests",
    "unit":       "Unit Tests",
    "functional": "Functional Tests",
    "other":      "Other Tests",
}

CATEGORY_COLOURS = {
    "regression": "#ef4444",
    "smoke":      "#f59e0b",
    "unit":       "#0e7490",
    "functional": "#6d28d9",
    "other":      "#64748b",
}


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class TestItem:
    """One test result from a pytest run."""
    node_id:       str
    module:        str
    class_:        str
    name:          str
    status:        str           # PASSED | FAILED | ERROR | SKIPPED | XFAILED
    duration_ms:   float = 0.0
    error_summary: str   = ""

    @property
    def passed(self) -> bool | None:
        if self.status == "PASSED":
            return True
        if self.status in ("FAILED", "ERROR"):
            return False
        return None

    @property
    def category(self) -> str:
        m = self.module.lower()
        if "regression" in m:
            return "regression"
        parts = self.module.replace("\\", "/").split("/")
        if len(parts) >= 3:
            return parts[1]   # tests/subdir/file.py → subdir is the category
        return "other"

    @property
    def short_name(self) -> str:
        return self.node_id.rsplit("::", 1)[-1]

    @property
    def display_path(self) -> str:
        """Module path without the 'tests/' prefix for compact display."""
        return self.module.replace("tests/", "").replace("tests\\", "")


@dataclass
class TestRunResult:
    """Summary of a complete pytest invocation."""
    items:            list[TestItem] = field(default_factory=list)
    total_duration_s: float          = 0.0
    timestamp:        str            = ""
    ran_at:           float          = 0.0
    exit_code:        int            = 0
    raw_output:       str            = ""
    error_msg:        str            = ""

    @property
    def passed(self) -> int:
        return sum(1 for i in self.items if i.status == "PASSED")

    @property
    def failed(self) -> int:
        return sum(1 for i in self.items if i.status in ("FAILED", "ERROR"))

    @property
    def skipped(self) -> int:
        return sum(1 for i in self.items if i.status
                   in ("SKIPPED", "XFAILED", "XPASSED"))

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def pass_pct(self) -> float:
        return (self.passed / self.total * 100) if self.total else 0.0

    def by_category(self) -> dict[str, list[TestItem]]:
        out: dict[str, list[TestItem]] = {}
        for item in self.items:
            out.setdefault(item.category, []).append(item)
        return out

    def by_module(self, category: str | None = None) -> dict[str, list[TestItem]]:
        out: dict[str, list[TestItem]] = {}
        src = self.items if category is None else [
            i for i in self.items if i.category == category
        ]
        for item in src:
            out.setdefault(item.module, []).append(item)
        return out


# ── Public API ────────────────────────────────────────────────────────────────

def run_tests(
    root: str | None = None,
    test_path: str | None = None,
    timeout: int = 120,
) -> TestRunResult:
    """
    Invoke pytest and return a structured TestRunResult.

    Parameters
    ----------
    root:       Repository root directory (defaults to this file's grandparent).
    test_path:  Pytest target (default: 'tests').
    timeout:    Hard wall-clock limit in seconds.
    """
    if root is None:
        root = str(Path(__file__).parent.parent)

    target  = test_path or "tests"
    ran_at  = time.time()
    t0      = time.monotonic()

    cmd = _pytest_cmd() + [
        target,
        "-v", "--tb=line", "--no-header",
        "--color=no",
        "-p", "no:cacheprovider",
        f"--rootdir={root}",
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=root,
        )
        raw       = proc.stdout + "\n" + proc.stderr
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ran_at))
        return TestRunResult(
            error_msg=f"pytest timed out after {timeout}s",
            ran_at=ran_at,
            timestamp=ts,
        )
    except Exception as exc:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ran_at))
        return TestRunResult(error_msg=str(exc), ran_at=ran_at, timestamp=ts)

    elapsed = time.monotonic() - t0
    items   = _parse(raw)
    ts      = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ran_at))

    return TestRunResult(
        items=items,
        total_duration_s=elapsed,
        timestamp=ts,
        ran_at=ran_at,
        exit_code=exit_code,
        raw_output=raw,
    )


# ── Parser ────────────────────────────────────────────────────────────────────

def _parse(raw: str) -> list[TestItem]:
    """Extract per-test results from verbose pytest text output."""
    errors: dict[str, str] = {}
    for line in raw.splitlines():
        m = _FAIL_SUMMARY_RE.match(line.strip())
        if m:
            errors[m.group(1)] = m.group(2).strip()

    items: list[TestItem] = []
    for line in raw.splitlines():
        stripped = line.strip()
        m = _STATUS_RE.match(stripped)
        if not m:
            continue
        node_id = m.group(1)
        status  = m.group(2)

        # Duration is sometimes appended: "PASSED [ 5%]   0.12s"
        dur_ms = 0.0
        dur_m  = re.search(r"([\d.]+)s\s*$", stripped)
        if dur_m:
            dur_ms = float(dur_m.group(1)) * 1000

        parts  = node_id.split("::")
        module = parts[0]
        class_ = parts[1] if len(parts) == 3 else ""
        name   = parts[-1]

        items.append(TestItem(
            node_id=node_id,
            module=module,
            class_=class_,
            name=name,
            status=status,
            duration_ms=dur_ms,
            error_summary=errors.get(node_id, ""),
        ))

    return items
