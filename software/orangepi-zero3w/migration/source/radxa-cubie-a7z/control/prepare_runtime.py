#!/usr/bin/env python3
"""Apply the A7Z patch to a clean pinned runtime snapshot without editing its source repo."""

import argparse
from pathlib import Path
import shutil
import subprocess

REVISION = "d1a2c62efdfb8c7a446ac99f562849e5a3a9587f"
FIRMWARE = Path("microduck-hd1910/firmware")


def prepare(source, output):
    subprocess.run(["git", "-C", str(source), "cat-file", "-e", REVISION + "^{commit}"], check=True)
    output.mkdir(parents=True, exist_ok=False)
    # git archive uses the pinned commit, never another chat's uncommitted changes.
    with subprocess.Popen(["git", "-C", str(source), "archive", REVISION, str(FIRMWARE)],
                          stdout=subprocess.PIPE) as archive:
        subprocess.run(["tar", "-xf", "-", "-C", str(output)], stdin=archive.stdout, check=True)
        if archive.wait() != 0:
            raise RuntimeError("git archive failed")
    patch = Path(__file__).with_name("runtime-a7z-sync3.patch")
    subprocess.run(["git", "apply", "--check", str(patch)], cwd=output, check=True)
    subprocess.run(["git", "apply", str(patch)], cwd=output, check=True)
    # The upstream git excludes this test fixture; reuse the existing local resource only.
    fixture = FIRMWARE / "packaging/models/1cbf8730eadd75a5704332c9f3c10f60/policy.onnx"
    if (source / fixture).is_file():
        shutil.copy2(source / fixture, output / fixture)
    print(output / FIRMWARE)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="existing Microduck-kissqy git repo containing the pinned revision")
    parser.add_argument("output", type=Path, help="new empty output path")
    args = parser.parse_args()
    prepare(args.source.resolve(), args.output.resolve())
