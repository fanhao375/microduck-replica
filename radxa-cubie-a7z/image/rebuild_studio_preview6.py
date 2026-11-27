"""Rebuild the A7Z Studio preview6 from its published base and overlay assets."""

import argparse
import json
from pathlib import Path
import tarfile
import tempfile

import build_camera_update as update
import build_image as image

BASE = "b48086a2727bb96ed274af15a6a35ed900eafe98f66efef47048e34126a7f903"
OVERLAY = "87f2125a52031e679ce0810ecc0d1d9058c7c7bba9518992293c605e0be7dcaa"
MANIFEST = "d363a828e9836d572aea0085d33709ce664395e98165f2b8e52c19a41bfc5a1e"


def rebuild(base, payload, manifest, output):
    if image.digest(payload) != OVERLAY or image.digest(manifest) != MANIFEST:
        raise ValueError("download the matching preview6 overlay and manifest")
    release = json.loads(manifest.read_text())
    with tempfile.TemporaryDirectory(prefix="preview6-overlay-") as stage:
        with tarfile.open(payload, "r:gz") as archive:
            # This exact archive is pinned above. Preserve its reviewed systemd links.
            archive.extractall(stage, filter="tar")
        return update.build(base, Path(stage), output, base_sha256=BASE,
                            release=release["release"], removals=release["removed_files"],
                            protected=tuple(release["protected_unchanged"]),
                            absent=release["verification"]["absent_paths"],
                            expected_files=release["verification"]["runtime_files_sha256"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base", "payload", "manifest", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(rebuild(args.base, args.payload, args.manifest, args.output), indent=2))
