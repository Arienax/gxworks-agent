# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Compile the Windows workbench launch window with the inbox .NET compiler."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def compiler_arguments(output):
    compiler = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET/Framework/v4.0.30319/csc.exe"
    return [str(compiler), "/nologo", "/target:winexe", "/optimize+", "/codepage:65001",
            f"/out:{output}", f"/win32icon:{ROOT / 'resources/app.ico'}",
            f"/win32manifest:{ROOT / 'scripts/web_launcher.manifest'}",
            "/reference:System.Windows.Forms.dll", "/reference:System.Drawing.dll",
            "/reference:System.Management.dll", "/reference:System.Web.Extensions.dll",
            str(ROOT / "scripts/web_launcher.cs")]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    output = parser.parse_args().output.resolve()
    subprocess.run(compiler_arguments(output), check=True)
    shutil.copyfile(ROOT / "scripts/web_launcher.config", output.with_name(output.name + ".config"))
