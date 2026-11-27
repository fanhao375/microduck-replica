"""Initialize a fresh Studio model library under the Studio service account."""

from pathlib import Path
import shutil
import tempfile


def initialize(seed, library):
    # The existence of the library records first use, including subsequent
    # renames or deletions made by the user in Studio.
    if library.is_dir():
        return
    with tempfile.TemporaryDirectory(dir=library.parent, prefix=".factory-models-") as temp:
        ready = Path(temp) / "models"
        shutil.copytree(seed, ready)
        ready.rename(library)


if __name__ == "__main__":
    initialize(Path("/usr/share/cubie-duck/factory-models"),
               Path("/var/lib/microduck-studio/models"))
