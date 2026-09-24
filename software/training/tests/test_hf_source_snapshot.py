"""HF source archives must remain runnable when training lives in a monorepo."""

import subprocess
import tarfile
from pathlib import Path

import pytest

from mjlab_microduck.hf_jobs import _build_tarball, _repo_root


@pytest.mark.parametrize("layout", [".", "software/training"])
@pytest.mark.parametrize("entry", [".", "scripts"])
def test_source_archive_contains_only_current_training_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, layout: str, entry: str
) -> None:
    repo = tmp_path / "checkout"
    project = repo / layout
    project.mkdir(parents=True)
    files = {
        "pyproject.toml": '[project]\nname = "mjlab-microduck"\n',
        "uv.lock": "version = 1\n",
        "src/mjlab_microduck/__init__.py": "",
        "scripts/hf/uploader.py": "# checkpoint uploader\n",
        ".gitignore": "logs/\n.venv/\n",
    }
    for rel, content in files.items():
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    if layout != ".":
        (repo / "outside-training.txt").write_text("unrelated", encoding="utf-8")
        (repo / "pyproject.toml").write_text("# another project", encoding="utf-8")

    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()

    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=Snapshot Test", "-c", "user.email=test@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "initial source")
    # The snapshot includes current edits and new source files, excludes outputs.
    (project / "scripts/hf/uploader.py").write_text("# edited uploader\n", encoding="utf-8")
    (project / "new_source.txt").write_text("source notes", encoding="utf-8")
    (project / "logs").mkdir()
    (project / "logs/model.pt").write_bytes(b"ignored checkpoint")
    (project / ".venv").mkdir()
    (project / ".venv/local.txt").write_text("ignored", encoding="utf-8")
    monkeypatch.chdir(project / entry)

    archive = tmp_path / "source.tar.gz"
    sha = _build_tarball(_repo_root(), archive)
    assert sha == git("rev-parse", "--short", "HEAD")
    with tarfile.open(archive) as tar:
        assert set(tar.getnames()) == {*files, "new_source.txt"}
        assert tar.extractfile("scripts/hf/uploader.py").read() == b"# edited uploader\n"
