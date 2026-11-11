"""Checks that the plugin's manifests agree with each other and with the files they point to, using only the standard library.

``--root DIR`` is the plugin tree: the working copy of the mirror in CI, the repository checkout in development.
``--mirror {private,public}`` is the visibility of the mirror the tree is released to; it defaults to ``public``.

* The three plugin manifests (Claude Code, Codex, Copilot) parse, name the same plugin and carry the same version: X.Y.Z for a
  release, X.Y.Z-dev.N for a build between releases. The newest section of CHANGELOG.md is that X.Y.Z, or the one below an
  upcoming release's section that is marked unreleased.
* A public release (plain X.Y.Z from 1.0.0, the first public version, on) ships a finished CHANGELOG section. Its README carries no
  link still marked "link added before release" when the mirror is public; a private mirror may still carry one, because nobody
  outside the organization reads it. The placeholders must be gone before the mirror becomes public.
* The marketplace lists the plugin with the repository itself (`./`) as its source.
* Every file a manifest, a hooks file or an MCP entry names exists: `hooks` and `mcpServers` paths, `${CLAUDE_PLUGIN_ROOT}/...`
  commands, and script arguments of an MCP command.
* Every skill has frontmatter with a `name` equal to its folder and a `description`.

Problems are printed to stderr, one per line, and the exit status is 1.
"""

import argparse
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path

PLUGIN_MANIFESTS = [".claude-plugin/plugin.json", ".codex-plugin/plugin.json", ".github/plugin/plugin.json"]
MARKETPLACE = ".claude-plugin/marketplace.json"
DEFAULT_HOOKS = "hooks/hooks.json"
VERSION = re.compile(r"^(\d+\.\d+\.\d+)(-dev\.\d+)?$")
#: The first public version. A plain X.Y.Z at or above it is a public release.
FIRST_PUBLIC_RELEASE = (1, 0, 0)
#: What the README says in place of a link that does not exist yet; a release may not carry it.
PENDING_LINK = "link added before release"
#: The visibilities of the mirror a tree is released to. The check assumes the stricter one unless it is told otherwise.
MIRROR_VISIBILITIES = ("private", "public")
PLUGIN_ROOT_PATH = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\"\s\\]+)")
CHANGELOG_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\](.*)$", re.MULTILINE)


def _strings(node: object) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, list):
        for item in node:
            yield from _strings(item)
    elif isinstance(node, dict):
        for item in node.values():
            yield from _strings(item)


def is_public_release(version: str | None) -> bool:
    """A plain X.Y.Z from 1.0.0 on. A dev build, and any version below 1.0.0, is not a public release."""
    match = VERSION.match(version or "")
    return bool(match) and match[2] is None and tuple(int(part) for part in match[1].split(".")) >= FIRST_PUBLIC_RELEASE


class Checker:
    """The checks over one plugin tree.

    `mirror` is the visibility of the mirror the tree is released to. Anything but `private` is checked as public.
    """

    def __init__(self, root: Path, mirror: str = "public") -> None:
        self.root = root
        self.mirror = mirror
        self.problems: list[str] = []

    def load(self, rel: str) -> dict | None:
        path = self.root / rel
        if not path.is_file():
            self.problems.append(f"{rel}: file is missing")
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            self.problems.append(f"{rel}: not valid JSON ({exc})")
            return None
        return data if isinstance(data, dict) else None

    def exists(self, rel: str, where: str) -> bool:
        if not (self.root / rel).exists():
            self.problems.append(f"{where}: {rel} does not exist")
            return False
        return True

    def manifests(self) -> tuple[dict[str, dict], str | None]:
        loaded = {rel: data for rel in PLUGIN_MANIFESTS if (data := self.load(rel)) is not None}
        reference = loaded.get(PLUGIN_MANIFESTS[0])
        name = reference.get("name") if reference else None
        versions = {rel: data.get("version") for rel, data in loaded.items()}
        for rel, data in loaded.items():
            if name and data.get("name") != name:
                self.problems.append(f"{rel}: name {data.get('name')!r} differs from {name!r}")
            if not isinstance(versions[rel], str) or not VERSION.match(versions[rel]):
                self.problems.append(f"{rel}: version {versions[rel]!r} is not X.Y.Z or X.Y.Z-dev.N")
        if len(set(versions.values())) > 1:
            self.problems.append(f"manifests disagree on version: {versions}")
        return loaded, versions.get(PLUGIN_MANIFESTS[0])

    def changelog(self, version: str | None) -> None:
        path = self.root / "CHANGELOG.md"
        if not path.is_file():
            self.problems.append("CHANGELOG.md: file is missing")
            return
        headings = CHANGELOG_HEADING.findall(path.read_text(encoding="utf-8"))
        match = VERSION.match(version or "")
        if not match:
            return
        # While the upcoming release's section is marked unreleased it sits above the section of the version being built.
        newest = 1 if len(headings) > 1 and "unreleased" in headings[0][1].lower() and match[1] != headings[0][0] else 0
        if len(headings) <= newest or headings[newest][0] != match[1]:
            self.problems.append(f"CHANGELOG.md: newest section is {headings[newest][0] if len(headings) > newest else None}, the manifests say {version}")
        elif is_public_release(version) and "unreleased" in headings[newest][1].lower():
            self.problems.append(f"CHANGELOG.md: the {version} section is still marked unreleased in a release")

    def pending_links(self, version: str | None) -> None:
        """A public release into a public mirror may not ship a README that still marks a link as not yet added."""
        readme = self.root / "README.md"
        if self.mirror != "private" and is_public_release(version) and readme.is_file() and PENDING_LINK in readme.read_text(encoding="utf-8"):
            self.problems.append(f"README.md: still says {PENDING_LINK!r}, which a release {version} into a public mirror may not")

    def marketplace(self, name: str | None) -> None:
        data = self.load(MARKETPLACE)
        if data is None:
            return
        entries = [p for p in data.get("plugins", []) if isinstance(p, dict) and p.get("name") == name]
        if not entries:
            self.problems.append(f"{MARKETPLACE}: marketplace lists no plugin named {name!r}")
        elif entries[0].get("source") != "./":
            self.problems.append(f"{MARKETPLACE}: source of {name!r} is {entries[0].get('source')!r}, expected './'")

    def referenced_files(self, manifests: dict[str, dict]) -> None:
        documents: dict[str, dict] = dict(manifests)
        if self.exists(DEFAULT_HOOKS, "Claude Code hooks") and (hooks := self.load(DEFAULT_HOOKS)):
            documents[DEFAULT_HOOKS] = hooks
        for rel, data in manifests.items():
            for key in ("hooks", "mcpServers"):
                value = data.get(key)
                if isinstance(value, str):
                    target = value.removeprefix("./")
                    if self.exists(target, f"{rel} {key}") and (loaded := self.load(target)):
                        documents[target] = loaded
        for rel, data in documents.items():
            for text in _strings(data):
                for match in PLUGIN_ROOT_PATH.finditer(text):
                    self.exists(match.group(1), rel)
        for rel, data in documents.items():
            for server in (data.get("mcpServers") or {}).values() if isinstance(data.get("mcpServers"), dict) else []:
                for arg in server.get("args", []):
                    if isinstance(arg, str) and arg.endswith((".py", ".sh")) and "$" not in arg:
                        self.exists(arg, f"{rel} MCP command")

    def skills(self) -> None:
        for skill in sorted((self.root / "skills").glob("*/SKILL.md")):
            rel = skill.relative_to(self.root).as_posix()
            lines = skill.read_text(encoding="utf-8").split("\n")
            if lines[0] != "---" or "---" not in lines[1:]:
                self.problems.append(f"{rel}: no frontmatter")
                continue
            front = lines[1 : lines.index("---", 1)]
            name = next((line.partition(":")[2].strip() for line in front if line.startswith("name:")), None)
            if name != skill.parent.name:
                self.problems.append(f"{rel}: name {name!r} differs from the folder {skill.parent.name!r}")
            if not any(line.startswith("description:") for line in front):
                self.problems.append(f"{rel}: no description")

    def run(self) -> list[str]:
        manifests, version = self.manifests()
        first = manifests.get(PLUGIN_MANIFESTS[0])
        self.marketplace(first.get("name") if first else None)
        self.changelog(version)
        self.pending_links(version)
        self.referenced_files(manifests)
        self.skills()
        return self.problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path())
    parser.add_argument(
        "--mirror",
        choices=MIRROR_VISIBILITIES,
        default="public",
        help="visibility of the mirror the tree is released to; a private mirror may carry a README link that is still pending",
    )
    args = parser.parse_args()
    problems = Checker(args.root, args.mirror).run()
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
