"""Project-type fingerprinting.

Classifies a project as one of {library, cli, mcp_server, unknown} from
manifest metadata. The classification gates type-specific recommendations
in recommend.py — an MCP server has different hygiene needs than a plain
library, and pretending the matrix is uniform produces noise.

Resists enumerating every conceivable project shape. Three types ship now;
add more only when behavioral data shows the recommendation set actually
diverges meaningfully.
"""

from pathlib import Path

PROJECT_TYPES = ("library", "cli", "mcp_server", "unknown")


def _read_pyproject(root: Path) -> str:
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return ""
    try:
        return pyproject.read_text()
    except OSError:
        return ""


def _has_mcp_signal(pyproject_text: str, root: Path) -> bool:
    """Detect MCP server: dependency on mcp/fastmcp, or scripts mentioning mcp."""
    lower = pyproject_text.lower()
    if "fastmcp" in lower or '"mcp"' in lower or "'mcp'" in lower or "mcp[" in lower:
        return True
    if "fastmcp" in lower or "mcp.server" in lower:
        return True
    # Scripts pattern: scan registered scripts for "mcp" in name or target
    if "[project.scripts]" in pyproject_text:
        in_scripts = False
        for line in pyproject_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("["):
                in_scripts = stripped == "[project.scripts]"
                continue
            if in_scripts and "mcp" in stripped.lower():
                return True
    # Source-level: any module imports `mcp.server` or `fastmcp`
    src_dir = root / "src"
    candidates = [src_dir] if src_dir.is_dir() else [root]
    for base in candidates:
        try:
            for py in base.rglob("*.py"):
                if py.stat().st_size > 200_000:
                    continue
                try:
                    text = py.read_text()
                except OSError:
                    continue
                if "from mcp.server" in text or "import fastmcp" in text or "from fastmcp" in text:
                    return True
        except (OSError, PermissionError):
            continue
    return False


def _has_scripts_section(pyproject_text: str) -> bool:
    return "[project.scripts]" in pyproject_text


def detect(project_path: str) -> dict:
    """Return {"type": <one_of_PROJECT_TYPES>, "evidence": [str, ...]}."""
    root = Path(project_path)
    pyproject_text = _read_pyproject(root)
    evidence: list[str] = []

    if not pyproject_text:
        # Non-Python projects fall through to unknown for now.
        if (root / "package.json").is_file():
            evidence.append("package.json present")
        return {"type": "unknown", "evidence": evidence}

    has_mcp = _has_mcp_signal(pyproject_text, root)
    has_scripts = _has_scripts_section(pyproject_text)

    if has_mcp:
        evidence.append("mcp/fastmcp signal detected")
        return {"type": "mcp_server", "evidence": evidence}

    if has_scripts:
        evidence.append("[project.scripts] declared")
        return {"type": "cli", "evidence": evidence}

    if "[project]" in pyproject_text:
        evidence.append("[project] declared, no scripts")
        return {"type": "library", "evidence": evidence}

    return {"type": "unknown", "evidence": evidence}


def type_specific_recommendations(project_type: str, root: Path) -> list[dict]:
    """Return type-targeted recommendation dicts (matching recommend._rec shape)."""
    recs: list[dict] = []
    if project_type == "mcp_server":
        # CLAUDE.md presence — MCP servers benefit from agent context
        if not (root / "CLAUDE.md").is_file() and not (root / ".claude" / "CLAUDE.md").is_file():
            recs.append(
                {
                    "category": "setup",
                    "priority": "medium",
                    "title": "Add CLAUDE.md for MCP server",
                    "reason": (
                        "MCP servers are usually consumed by Claude — "
                        "agent context belongs in the repo."
                    ),
                    "action": "Create CLAUDE.md with usage notes and tool inventory",
                    "confidence": 80,
                }
            )
        # .mcp.json for local registration
        if not (root / ".mcp.json").is_file():
            recs.append(
                {
                    "category": "setup",
                    "priority": "low",
                    "title": "Add .mcp.json for local development",
                    "reason": (
                        "Local MCP registration enables in-repo testing "
                        "without global config edits."
                    ),
                    "action": "Create .mcp.json with server entrypoint",
                    "confidence": 65,
                }
            )
    elif project_type == "cli":
        # CLIs benefit from a README usage section more than libraries
        readme = root / "README.md"
        if not readme.is_file():
            recs.append(
                {
                    "category": "setup",
                    "priority": "medium",
                    "title": "Add README with usage examples",
                    "reason": (
                        "CLI tools are discovered through their README — "
                        "examples drive adoption."
                    ),
                    "action": "Create README.md with installation and usage examples",
                    "confidence": 75,
                }
            )
    return recs
