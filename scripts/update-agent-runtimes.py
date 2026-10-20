#!/usr/bin/env python3
"""Propose stable agent runtime pins in a disposable checkout, never install them."""

import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.request import urlopen

CHROME_FEED = ("https://googlechromelabs.github.io/chrome-for-testing/"
               "last-known-good-versions-with-downloads.json")
CHROME_DOWNLOADS = "https://storage.googleapis.com/chrome-for-testing-public"
CHROME_PIN = r"(?m)^(ARG CHROME_VERSION=)[^\s]+$"
# Dockerfile sha256 argument -> Chrome for Testing download for linux64.
CHROME_ARCHIVES = {
    "CHROME_SHA256": "chrome",
    "CHROME_HEADLESS_SHELL_SHA256": "chrome-headless-shell",
}


def read_url(url):
    with urlopen(url, timeout=30) as response:
        return response.read().decode()


def sha256_of_url(url):
    """Chrome for Testing publishes no checksums; hash the archive the build will fetch."""
    digest = hashlib.sha256()
    with urlopen(url, timeout=120) as response:
        for chunk in iter(lambda: response.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable(version, parts=3):
    """Chrome for Testing versions have four numeric parts; the agent runtimes three."""
    if not re.fullmatch(rf"\d+(?:\.\d+){{{parts - 1}}}", version):
        raise ValueError(f"Expected a stable release, got {version!r}")
    return tuple(map(int, version.split(".")))


def propose_chrome(dockerfile, fetch, digest):
    """Pin the current Stable with fresh archive hashes, or return the Dockerfile unchanged."""
    channel = json.loads(fetch(CHROME_FEED))["channels"]["Stable"]
    latest = channel["version"]
    match = re.search(CHROME_PIN, dockerfile)
    if not match:
        raise ValueError("Missing Chrome for Testing pin")
    current = match[0][len(match[1]):]
    if stable(latest, parts=4) < stable(current, parts=4):
        raise ValueError(f"Refusing Chrome for Testing downgrade: {current} -> {latest}")
    if latest == current:
        return dockerfile, []
    dockerfile = replace_pin(dockerfile, CHROME_PIN, latest)
    for argument, download in CHROME_ARCHIVES.items():
        url = f"{CHROME_DOWNLOADS}/{latest}/linux64/{download}-linux64.zip"
        listed = [entry["url"] for entry in channel["downloads"][download]
                  if entry["platform"] == "linux64"]
        if listed != [url]:
            raise ValueError(f"Chrome for Testing feed does not list {url}")
        dockerfile = replace_pin(
            dockerfile, rf"(?m)^(ARG {argument}=)[0-9a-f]{{64}}$", digest(url))
    return dockerfile, [f"Chrome for Testing: {current} → {latest}"]


def replace_pin(text, pattern, value):
    """Refuse source drift rather than silently publishing a partial update."""
    updated, count = re.subn(pattern, lambda match: match[1] + value, text)
    if count != 1:
        raise ValueError(f"Expected one pin for {pattern!r}, found {count}")
    return updated


def propose(root, fetch=read_url, digest=sha256_of_url):
    dockerfile = (root / "Dockerfile").read_text()
    requirements = (root / "backend/requirements.txt").read_text()
    codex_pin = r"(?m)^(ARG CODEX_VERSION=)[^\s]+$"
    codex_sdk_pin = r"(?m)^(ARG CODEX_SDK_VERSION=)[^\s]+$"
    claude_pin = r"(?m)^(claude-agent-sdk==)[^\s]+$"
    codex = json.loads(fetch("https://registry.npmjs.org/@openai/codex/latest"))["version"]
    sdk_info = json.loads(fetch("https://pypi.org/pypi/openai-codex/json"))["info"]
    codex_sdk = sdk_info["version"]
    sdk_bins = [requirement.removeprefix("openai-codex-cli-bin==")
                for requirement in (sdk_info["requires_dist"] or [])
                if requirement.startswith("openai-codex-cli-bin==")]
    if sdk_bins != [codex_sdk]:
        raise ValueError("Codex SDK release does not declare its matching CLI runtime")
    if codex != codex_sdk:
        raise ValueError("Codex npm CLI and Python SDK releases differ; wait for matching versions")
    claude = json.loads(fetch("https://pypi.org/pypi/claude-agent-sdk/json"))["info"]["version"]
    changes = []
    for name, text, pattern, latest in [
        ("Codex", dockerfile, codex_pin, codex),
        ("Codex Python SDK", dockerfile, codex_sdk_pin, codex_sdk),
        ("Claude SDK + bundled CLI", requirements, claude_pin, claude),
    ]:
        match = re.search(pattern, text)
        if not match:
            raise ValueError(f"Missing {name} pin")
        current = match[0][len(match[1]):]
        if stable(latest) < stable(current):
            raise ValueError(f"Refusing {name} downgrade: {current} -> {latest}")
        if latest != current:
            changes.append(f"{name}: {current} → {latest}")
    dockerfile, chrome_changes = propose_chrome(dockerfile, fetch, digest)
    changes += chrome_changes
    if not changes:
        return {}, []

    dockerfile = replace_pin(dockerfile, codex_pin, codex)
    dockerfile = replace_pin(dockerfile, codex_sdk_pin, codex_sdk)
    requirements = replace_pin(requirements, claude_pin, claude)
    files = {"Dockerfile": dockerfile, "backend/requirements.txt": requirements}
    return {path: content for path, content in files.items()
            if content != (root / path).read_text()}, changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--write", action="store_true", help="Write candidate pins; never install")
    args = parser.parse_args()
    files, changes = propose(args.root)
    print("\n".join(changes) if changes else "Agent runtimes are current.")
    if args.write:
        for path, content in files.items():
            (args.root / path).write_text(content)
        if "backend/requirements.txt" in files:
            print("Regenerate backend/requirements.lock before proposing this change.")


if __name__ == "__main__":
    main()
