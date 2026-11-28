"""Rebuild the A7Z Studio preview6 from its published base and overlay assets."""

import argparse
import json
from pathlib import Path
import tarfile
import tempfile

import build_camera_update as update
import build_image as image

BASE = "b48086a2727bb96ed274af15a6a35ed900eafe98f66efef47048e34126a7f903"
OVERLAY = "39f7edf2a595cf08a54bddbb00344faee5733ab92ea6ecb18d57d3932c6ae342"
MANIFEST = "01fc87237e078e7339b51b88cc0e8fcd1a83cc624f444adec64f44c877591bb7"


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
