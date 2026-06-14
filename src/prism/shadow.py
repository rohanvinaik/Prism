"""Shadow fingerprint — the emergent shared orientation of the trilogy.

NOT a tool, and nothing to invoke. A one-file convention: each trilogy tool
writes its own *layer* into a shared file as a side effect of its normal run,
and the repo-aware tools read the union back and surface it inside their OWN
output — in the same channel the agent is already reasoning over. The composite
has genuine meaning only once all three layers have been contributed; it
*converges* as the natural SetupAtlas → Prism → LintGate sweep proceeds.

Vendored verbatim into each trilogy tool (stdlib only — a shared package
dependency would be its own bloat). Best-effort throughout: fingerprint I/O
must never block or break the host tool.

Layout under ~/.claude/shadow/:
  environment.json        global, machine-wide  (intent / SetupAtlas layer)
  <repo_hash>.json        per-repo              (operation / Prism, implementation / LintGate)
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time

_DIR = os.path.expanduser("~/.claude/shadow")
_GLOBAL = "environment"

# The descending pipeline: intent -> operation -> implementation.
LAYERS = ("intent", "operation", "implementation")
_LAYER_TOOL = {"intent": "SetupAtlas", "operation": "Prism", "implementation": "LintGate"}


def _repo_path(repo: str) -> str:
    h = hashlib.sha256(os.path.abspath(repo).encode()).hexdigest()[:12]
    return os.path.join(_DIR, h + ".json")


def _global_path() -> str:
    return os.path.join(_DIR, _GLOBAL + ".json")


def _atomic_merge(path: str, layer: str, payload: dict) -> None:
    os.makedirs(_DIR, exist_ok=True)
    doc: dict = {}
    if os.path.isfile(path):
        try:
            with open(path) as f:
                doc = json.load(f)
        except (OSError, ValueError):
            doc = {}
    doc.setdefault("sections", {})
    doc["sections"][layer] = payload
    fd, tmp = tempfile.mkstemp(dir=_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(doc, f, indent=2)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def write(layer: str, headline: str, data: dict, repo: str | None = None) -> None:
    """Contribute this tool's layer. ``repo=None`` writes the global env layer."""
    try:
        payload = {
            "tool": _LAYER_TOOL.get(layer, "?"),
            "updated_at": time.time(),
            "headline": (headline or "").strip(),
            "data": data,
        }
        _atomic_merge(_global_path() if repo is None else _repo_path(repo), layer, payload)
    except OSError:
        pass


def compose(repo: str) -> dict:
    """Merge the global env layer with this repo's layers (newest wins per layer)."""
    sections: dict = {}
    for path in (_global_path(), _repo_path(repo)):
        if not os.path.isfile(path):
            continue
        try:
            with open(path) as f:
                doc = json.load(f)
        except (OSError, ValueError):
            continue
        for name, payload in (doc.get("sections") or {}).items():
            sections[name] = payload
    return sections


def _flags(sections: dict) -> list[str]:
    """Cross-layer observations — the part no single tool can emit alone."""
    out: list[str] = []
    op = (sections.get("operation") or {}).get("data") or {}
    im = (sections.get("implementation") or {}).get("data") or {}
    for r in op.get("dependency_redundant") or []:
        out.append(f"dependency redundancy — {r}")
    if op.get("lockfile") and op.get("lockfile_stale"):
        out.append("lockfile stale relative to manifest")
    if "operation" in sections and not op.get("lockfile"):
        out.append("no lockfile — dependencies unpinned")
    blocking = im.get("blocking")
    if blocking and op.get("git_clean") is False:
        out.append(f"uncommitted working tree atop {blocking} unresolved blocker(s)")
    return out


def _ago(ts) -> str:
    if not ts:
        return ""
    try:
        secs = time.time() - float(ts)
    except (TypeError, ValueError):
        return ""
    if secs < 90:
        return ", now"
    if secs < 5400:
        return f", {int(secs // 60)}m"
    if secs < 172800:
        return f", {int(secs // 3600)}h"
    return f", {int(secs // 86400)}d"


def render(repo: str) -> str:
    """Compact, informational composite for in-tool surfacing. '' when empty."""
    sections = compose(repo)
    if not sections:
        return ""
    present = [layer for layer in LAYERS if layer in sections]
    n = len(present)
    icon = "●" if n == 3 else ("◐" if n == 2 else "○")
    lines = [f"{icon} Shadow fingerprint — {n}/3 layers ({'converged' if n == 3 else 'partial'})"]
    for layer in LAYERS:
        sec = sections.get(layer)
        tool = _LAYER_TOOL[layer]
        if not sec:
            lines.append(f"  · {layer:<15}({tool}): not yet contributed")
        else:
            lines.append(f"  · {layer:<15}({tool}{_ago(sec.get('updated_at'))}): {sec.get('headline', '')}")
    flags = _flags(sections)
    if flags:
        lines.append("  cross-layer flags (only the composite sees these):")
        lines.extend(f"    ⚠ {f}" for f in flags)
    return "\n".join(lines)
