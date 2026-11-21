"""Exercise publication against an in-memory release, including retries after a rename."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish as pub

VERSION = "2026.11.22"
PACKAGE = "a" * 64


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Publication(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out = Path(tmp.name) / "out"
        self.out.mkdir()
        image = b"new factory image"
        record = dict(schema=1, base_version="1104", software_version=VERSION,
                      base_sha256=pub.BASE_HASH, prepare_sha256=pub.PREP_HASH, package_sha256=PACKAGE,
                      source_revision="e" * 40, image_sha256=digest(image), image_bytes=len(image),
                      verified_runtime_files=37, compressed_image_readback=True, ext4_checked=True,
                      arm_studio_http=True, stale_browser_revalidation=True, hardware_boot_verified=False)
        (self.out / pub.IMAGE).write_bytes(image)
        (self.out / pub.SIDECAR).write_text(f"{digest(image)}  {pub.IMAGE}\n")
        (self.out / pub.BUILD).write_bytes(json.dumps(record).encode())
        self.release = {"id": 1, "body": "", "name": "1104"}
        self.files = {}
        self.serial = 20
        self.upload(pub.IMAGE, b"original factory image")
        self.upload("licenses.txt", b"factory licenses")
        self.fail_on = None
        self.built_sha = PACKAGE
        self.release_version = VERSION
        for name, func in {"api": self.api, "gh": self.gh, "target": self.target, "pages": self.catalog}.items():
            patched = patch.object(pub, name, func)
            patched.start()
            self.addCleanup(patched.stop)

    def upload(self, name, content):
        self.serial += 1
        self.files[self.serial] = (name, content)

    def catalog(self, endpoint):
        name = self.release_version + ".software-update.tar"
        return [{"draft": False, "prerelease": False, "published_at": "today",
                 "tag_name": pub.PREFIX + self.release_version,
                 "assets": [{"name": name, "state": "uploaded", "size": 10, "digest": "sha256:" + self.built_sha}]}]

    def target(self):
        return self.release.copy(), {
            name: dict(id=i, name=name, size=len(data), state="uploaded", digest="sha256:" + digest(data))
            for i, (name, data) in self.files.items()}

    def gh(self, *args):
        if args[0] == "api":
            return self.files[int(args[-1].split("/")[-1])][1]
        self.assertEqual(args[:3], ("release", "upload", pub.IMAGE_TAG))
        path = Path(args[3])
        self.upload(path.name, path.read_bytes())
        return b""

    def api(self, path, method, fields=None):
        i = int(path.split("/")[-1])
        if method == "DELETE":
            del self.files[i]
        elif "/assets/" not in path:
            self.release.update(fields)
        else:
            name = fields["name"]
            if self.fail_on == name:
                self.fail_on = None
                raise OSError("interrupted asset rename")
            self.assertNotIn(name, self.target()[1])
            self.files[i] = (name, self.files[i][1])
            return self.target()[1][name]

    def promote(self):
        with redirect_stdout(io.StringIO()):
            pub.promote(self.out, VERSION)

    def test_newest_uses_numeric_versions_and_only_published_stable_tags(self):
        release, = self.catalog("")
        cases = [release | {"tag_name": pub.PREFIX + "2026.11.4"},
                 release | {"tag_name": pub.PREFIX + "2026.11.23-rc.1"},
                 release | {"tag_name": pub.PREFIX + "2026.11.23", "draft": True},
                 release | {"tag_name": pub.PREFIX + "2026.11.24", "prerelease": True},
                 release | {"tag_name": pub.PREFIX + "2026.11.25", "published_at": None},
                 release | {"tag_name": pub.IMAGE_TAG}]
        self.assertEqual(pub.newest([release, *cases]), release)

    def test_interrupted_swap_restores_old_download_and_retry_completes(self):
        self.fail_on = pub.IMAGE
        with self.assertRaisesRegex(OSError, "interrupted"):
            self.promote()
        _, assets = self.target()
        self.assertEqual(pub.asset_hash(assets[pub.IMAGE]), digest(b"original factory image"))
        self.assertEqual(sum(pub.owned_temporary(n) for n in assets), 4)
        self.promote()
        release, assets = self.target()
        self.assertTrue(pub.complete(release, assets, VERSION, PACKAGE))
        self.assertEqual(set(assets), {*pub.NAMES, "licenses.txt"})
        expected = pub.checksum_file({n: pub.asset_hash(a) for n, a in assets.items() if n != pub.SUMS})
        self.assertEqual(self.files[assets[pub.SUMS]["id"]][1], expected)
        snapshot = self.files.copy()
        self.promote()
        self.assertEqual(self.files, snapshot)

    def test_older_software_and_changed_package_never_start_uploads(self):
        original = self.files.copy()
        self.release_version = "2026.11.23"
        with self.assertRaisesRegex(ValueError, "Newest public stable software"):
            self.promote()
        self.release_version = VERSION
        self.built_sha = "b" * 64
        with self.assertRaisesRegex(ValueError, "package_sha256"):
            self.promote()
        self.assertEqual(self.files, original)

    def test_published_version_cannot_be_replaced_by_retagging(self):
        self.promote()
        release, assets = self.target()
        with self.assertRaisesRegex(ValueError, "back from"):
            pub.marker_and_floor(release, assets, "2026.11.20", PACKAGE)
        with self.assertRaisesRegex(ValueError, "package changed"):
            pub.marker_and_floor(release, assets, VERSION, "b" * 64)


if __name__ == "__main__":
    unittest.main()
