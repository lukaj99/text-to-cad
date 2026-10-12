"""A skill that runs cadgen must teach the launch command, pinned to this release.

The CAD plugin's server and every skill run cadgen as one command,
`uvx --no-config --managed-python --python 3.13 --from cadgen==<release> <tool>`
(cadgen._internal.launch): uv keeps one installation per requirement, so the same command is
the same installation and the same warm daemon. A skill that runs cadgen any other way -- a
`pip install -r requirements.txt` into the project's interpreter -- makes a second installation
with a daemon of its own, and its docs may describe a cadgen that is not the one running.

Stated as a criterion rather than a list, as before: "uses cadgen" is what the skill's docs
TEACH (`cadgen ...`) or what its own Python imports. Skills that never touch cadgen
(bambu-labs, dfam-check, gcode, sendcutsend, step-parts) carry no launch command, and a
mention on a line that hands off to another skill (`$cad: cadgen stl build ...`) is that
skill's command, not this one's.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILLS = sorted(p for p in (REPO_ROOT / "skills").iterdir() if p.is_dir())

VERSION = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
LAUNCH = "uvx --no-config --managed-python --python 3.13 --from cadgen=={version} {tool}"


_HANDOFF_MARKER = re.compile(r"\$(?P<name>[a-z0-9-]+)")


def _own_lines(skill: Path, text: str) -> str:
    """Drop lines that route the agent to ANOTHER skill (`$cad`, `$dxf`, ...).

    A remediation line like "export an STL with $cad: `cadgen stl build ...`"
    teaches the CAD skill's command, not a dependency of the skill that says it
    — that skill installs nothing and runs nothing; the named skill's own
    requirements cover the command.
    """
    kept = []
    for line in text.splitlines():
        markers = {m.group("name") for m in _HANDOFF_MARKER.finditer(line)}
        if markers and skill.name not in markers:
            continue
        kept.append(line)
    return "\n".join(kept)


def _imports_cadgen(skill: Path) -> bool:
    return any(
        "cadgen" in _own_lines(skill, path.read_text(encoding="utf-8"))
        for path in skill.rglob("*.py")
        if "__pycache__" not in path.parts
    )


_CADGEN_INVOCATION = re.compile(r"(?:^|[`\s])cadgen\s+[a-z]", re.M)


def _docs_text(skill: Path) -> str:
    return "\n".join(
        _own_lines(skill, path.read_text(encoding="utf-8"))
        for path in skill.rglob("*.md")
        if "__pycache__" not in path.parts
    )


def _teaches_cadgen(skill: Path) -> bool:
    """The skill's docs instruct the agent to run the cadgen CLI (or its code imports it)."""
    return _imports_cadgen(skill) or bool(_CADGEN_INVOCATION.search(_docs_text(skill)))


class SkillLaunchCommand(unittest.TestCase):
    def test_skills_were_found(self) -> None:
        self.assertGreaterEqual(len(SKILLS), 8, "the skills/ glob found almost nothing")

    def test_every_skill_that_uses_cadgen_defines_the_launch_command(self) -> None:
        for skill in SKILLS:
            text = (skill / "SKILL.md").read_text(encoding="utf-8")
            with self.subTest(skill=skill.name):
                if not _teaches_cadgen(skill):
                    self.assertNotIn("--from cadgen==", text, "a skill that never runs cadgen downloads nothing")
                    continue
                for tool in ("cadgen", "python"):
                    self.assertIn(f"`{tool}` below means `{LAUNCH.format(version=VERSION, tool=tool)}`", text)

    def test_no_skill_installs_cadgen_any_other_way(self) -> None:
        # A requirements.txt naming cadgen is a second installation, with a daemon of its own.
        for skill in SKILLS:
            manifest = skill / "requirements.txt"
            with self.subTest(skill=skill.name):
                if manifest.is_file():
                    lines = [line.split("#")[0].strip() for line in manifest.read_text(encoding="utf-8").splitlines()]
                    self.assertFalse([line for line in lines if line.startswith("cadgen")], manifest)

    def test_the_launch_command_is_cadgens_own(self) -> None:
        import sys

        sys.path.insert(0, str(REPO_ROOT / "packages" / "cadgen" / "src"))
        from cadgen._internal.launch import launch_command

        self.assertEqual(launch_command("python", VERSION), LAUNCH.format(version=VERSION, tool="python"))


_SCRIPT_RUN = re.compile(r"uv run --script <skill-dir>/(?P<path>scripts/[A-Za-z0-9_./-]+\.py)")
_BARE_PYTHON_SCRIPT = re.compile(r"(?:^|[`\s])python3? scripts/", re.M)
_INLINE_BLOCK = re.compile(r"^# /// script\n(?P<body>(?:^#.*\n)*?)^# ///$", re.M)
_REQ_NAME = re.compile(r"^[A-Za-z0-9_.-]+")


class CadgenFreeSkillScripts(unittest.TestCase):
    """A cadgen-free skill's script runs from its own inline metadata, never the project's Python.

    `pip install -r requirements.txt` into "the active project environment" fails on
    PEP 668 interpreters (Homebrew, Debian, macOS system Python), pollutes the user's
    project, and leaves `python` unresolved where only `python3` exists. uv is already
    a prerequisite of the plugin, so each script carries a PEP 723 block and the skill
    launches it with `uv run --script`: one cached environment per script, nothing
    installed anywhere the user owns.
    """

    def test_no_skill_runs_its_scripts_with_a_bare_interpreter(self) -> None:
        for skill in SKILLS:
            if _teaches_cadgen(skill):
                continue  # cadgen skills define `python` as the pinned launch command
            with self.subTest(skill=skill.name):
                self.assertIsNone(_BARE_PYTHON_SCRIPT.search(_docs_text(skill)))

    def test_every_launched_script_carries_inline_metadata_covering_its_requirements(self) -> None:
        checked = 0
        for skill in SKILLS:
            launched = set(_SCRIPT_RUN.findall((skill / "SKILL.md").read_text(encoding="utf-8")))
            manifest = skill / "requirements.txt"
            required = set()
            if manifest.is_file():
                for line in manifest.read_text(encoding="utf-8").splitlines():
                    match = _REQ_NAME.match(line.split("#")[0].strip())
                    if match:
                        required.add(match.group(0).lower())
            for rel in launched:
                with self.subTest(skill=skill.name, script=rel):
                    script = skill / rel
                    self.assertTrue(script.is_file(), f"SKILL.md launches a missing script: {rel}")
                    block = _INLINE_BLOCK.search(script.read_text(encoding="utf-8"))
                    self.assertIsNotNone(block, f"{rel} has no `# /// script` block")
                    body = block.group("body").lower()
                    self.assertIn("requires-python", body)
                    missing = sorted(name for name in required if f'"{name}' not in body)
                    self.assertEqual(missing, [], f"{rel} inline deps drifted from requirements.txt")
                    checked += 1
        self.assertGreaterEqual(checked, 4, "found almost no launched skill scripts")


if __name__ == "__main__":
    unittest.main()
