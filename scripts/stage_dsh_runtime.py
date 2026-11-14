"""Stage an official DeepSeek Harness Windows SDK runtime for GXWorks Agent."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


EXE = "deepseek-harness-sdk-runtime-win-x64.exe"
RIPGREP = "deepseek-harness-sdk-runtime-win-x64-rg.exe"
TARGET = "win-x64"
LICENSES = ("LICENSE", "THIRD_PARTY_NOTICES.md")
NODE_LICENSE = "Node-LICENSE.txt"
RIPGREP_LICENSE = "ripgrep-LICENSE.txt"
OFFICIAL_SOURCE = "https://github.com/deepseek-ai/deepseek-harness"


def _copy_checkout(source: Path, destination: Path) -> None:
    artifacts = source / "dist-exe" if (source / "dist-exe").is_dir() else source
    for filename in (EXE, RIPGREP):
        shutil.copy2(artifacts / filename, destination)
    if (artifacts / TARGET / "downloads.json").is_file():
        shutil.copytree(artifacts / TARGET, destination / TARGET)
    for filename in LICENSES:
        shutil.copy2(source / filename, destination)
    if (artifacts / RIPGREP_LICENSE).is_file():
        shutil.copy2(artifacts / RIPGREP_LICENSE, destination)


def _copy_wheel(source: Path, destination: Path) -> None:
    runtime = ("deepseek_harness_runtime", "runtime")
    with zipfile.ZipFile(source) as wheel:
        for member in wheel.infolist():
            parts = PurePosixPath(member.filename).parts
            if member.is_dir() or not parts or ".." in parts or any(
                "\\" in part or ":" in part for part in parts
            ):
                continue
            if parts[:2] == runtime and len(parts) >= 3 and parts[2] in {EXE, RIPGREP, TARGET}:
                target = destination.joinpath(*parts[2:])
            elif len(parts) == 3 and parts[0].endswith(".dist-info") and parts[1] == "licenses" and parts[2] in LICENSES:
                target = destination / parts[2]
            else:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with wheel.open(member) as binary, target.open("wb") as output:
                shutil.copyfileobj(binary, output)


def stage_dsh_runtime(destination: Path, *, source: Path | None = None) -> Path:
    """Copy the SDK runtime, sidecars, resource metadata, and licenses."""
    if source is None:
        configured = os.environ.get("GXWORKS_DSH_SOURCE")
        if configured:
            source = Path(configured)
        else:
            source = Path(__file__).resolve().parents[1] / ".runtime/dsh-runtime"
            if not (source / EXE).is_file():
                raise FileNotFoundError(
                    "Set GXWORKS_DSH_SOURCE to a checkout with the official Windows SDK runtime already built "
                    "or to the official Windows SDK runtime wheel."
                )
    source = source.resolve()
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f"Use a fresh DeepSeek Harness staging destination: {destination}")

    node_license_setting = os.environ.get("GXWORKS_DSH_NODE_LICENSE")
    if node_license_setting:
        node_license = Path(node_license_setting)
    else:
        artifact_root = source.parent if source.is_file() else source
        node_license = artifact_root / NODE_LICENSE
        if not node_license.is_file():
            node_license = artifact_root / "dist-exe" / NODE_LICENSE
    if not node_license.is_file():
        raise FileNotFoundError(
            f"Provide the embedded Node.js version's LICENSE as {NODE_LICENSE} beside the runtime artifacts "
            "or set GXWORKS_DSH_NODE_LICENSE to that file."
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    # Publish a complete sibling directory so a failed copy can be retried.
    with tempfile.TemporaryDirectory(prefix=".dsh-runtime-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "runtime"
        staged.mkdir()
        if source.suffix.lower() == ".whl":
            _copy_wheel(source, staged)
            shutil.copy2(source.parent / RIPGREP_LICENSE, staged / RIPGREP_LICENSE)
            distribution = source.name
        else:
            _copy_checkout(source, staged)
            distribution = "Official Python SDK runtime build for node24-win-x64"
        for filename in (EXE, RIPGREP, RIPGREP_LICENSE, *LICENSES):
            if not (staged / filename).is_file():
                raise FileNotFoundError(f"The DeepSeek Harness runtime input does not contain {filename}")
        # An SDK build can produce the runnable EXE before its optional Office
        # download manifest. GXWorks uses the core profile and can ship it alone.
        metadata = next((path for path in (
            staged / TARGET / "downloads.json",
            source / "runtime-source.json",
            source / "package.json",
        ) if path.is_file()), None)
        if metadata is None:
            raise FileNotFoundError("Cannot determine the DeepSeek Harness runtime version")
        manifest = json.loads(metadata.read_text(encoding="utf-8"))
        shutil.copy2(node_license, staged / NODE_LICENSE)
        (staged / "runtime-source.json").write_text(json.dumps({
            "source": OFFICIAL_SOURCE,
            "distribution": distribution,
            "version": manifest["version"],
            "target": TARGET,
        }, indent=2) + "\n", encoding="utf-8")
        staged.rename(destination)
    return destination / EXE


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--source", type=Path, help="Built SDK checkout or official Windows runtime wheel")
    arguments = parser.parse_args()
    print(stage_dsh_runtime(arguments.destination, source=arguments.source))
