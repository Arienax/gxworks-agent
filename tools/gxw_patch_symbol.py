# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Compatibility entry point; all writes use the metadata-aware project pipeline."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gxw_project import main as project_main


def main():
    args = sys.argv[1:]
    if "--report" not in args and not any(a.startswith("--report=") for a in args):
        output = next((args[i + 1] for i, a in enumerate(args[:-1]) if a in {"-o", "--output"}), None)
        if output is None:
            output = next((a.split("=", 1)[1] for a in args if a.startswith("--output=")), None)
        if output:
            args += ["--report", str(Path(output).with_suffix(".write.json"))]
    project_main(["symbol", *args])


if __name__ == "__main__":
    main()
