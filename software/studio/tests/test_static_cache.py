"""Browser revalidation against two releases with identical packaged file times."""
import gzip
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "deps")]

from studio.app import build_app


PACKAGED_DATE = "Thu, 01 Jan 1970 00:00:01 GMT"
ASSETS = ("index.html", "app.js", "m.html", "vendor/lib.js", "model/model.json", "model/meshes.bin")


async def request(app, path, method="GET", **headers):
    headers["host"] = "localhost"
    scope = {"type": "http", "method": method, "scheme": "http", "path": path, "query_string": b"",
             "headers": [(key.encode(), value.encode()) for key, value in headers.items()]}
    messages = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    start = messages[0]
    headers = {key.decode(): value.decode() for key, value in start["headers"]}
    return start["status"], headers, b"".join(msg.get("body", b"") for msg in messages[1:])


class StaticCacheTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def release(self, name, data):
        root = Path(self.tmp.name) / name
        for asset in (*ASSETS, "model/meshes.bin.gz"):
            path = root / asset
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(gzip.compress(data, mtime=0) if asset.endswith(".gz") else data)
            os.utime(path, (1, 1))
        return build_app(web_dir=root, auth=None, robotd=None, log=None, duck={})

    async def test_release_change_with_same_size_and_mtime(self):
        old = self.release("old", b"old")
        new = self.release("new", b"new")
        for path in ("/", *("/" + asset for asset in ASSETS)):
            with self.subTest(path=path):
                _, original, _ = await request(old, path)
                cached = {"if-none-match": original["etag"], "if-modified-since": PACKAGED_DATE}
                status, headers, body = await request(new, path, **cached)
                self.assertEqual((status, body), (200, b"new"))
                self.assertNotEqual(headers["etag"], original["etag"])
                self.assertNotIn("last-modified", headers)
                self.assertEqual(headers["cache-control"], "no-cache")
                status, _, body = await request(new, path, **{"if-modified-since": PACKAGED_DATE})
                self.assertEqual((status, body), (200, b"new"))

    async def test_head_and_conditional_requests(self):
        app = self.release("one", b"contents")
        _, original, _ = await request(app, "/app.js")
        etag = original["etag"]
        status, head, body = await request(app, "/app.js", "HEAD")
        self.assertEqual((status, head["etag"], head["content-length"], body), (200, etag, "8", b""))
        for method in ("GET", "HEAD"):
            for validator in (etag, f'"old", W/{etag}', "*"):
                with self.subTest(method=method, validator=validator):
                    status, headers, body = await request(app, "/app.js", method, **{"if-none-match": validator})
                    self.assertEqual((status, body), (304, b""))
                    self.assertEqual((headers["etag"], headers["cache-control"]), (etag, "no-cache"))

    async def test_encoded_mesh_uses_its_own_validator(self):
        app = self.release("one", b"mesh")
        _, raw, _ = await request(app, "/model/meshes.bin")
        accept = {"accept-encoding": "gzip"}
        status, gz, body = await request(app, "/model/meshes.bin", **accept, **{"if-none-match": raw["etag"]})
        self.assertEqual((status, gzip.decompress(body)), (200, b"mesh"))
        self.assertNotEqual(gz["etag"], raw["etag"])
        self.assertEqual((gz["content-encoding"], gz["vary"]), ("gzip", "Accept-Encoding"))
        status, headers, body = await request(app, "/model/meshes.bin", "HEAD", **accept, **{"if-none-match": gz["etag"]})
        self.assertEqual((status, headers["vary"], body), (304, "Accept-Encoding", b""))

    async def test_if_range_only_matches_current_content(self):
        app = self.release("one", b"mesh")
        _, head, _ = await request(app, "/model/meshes.bin")
        for validator, result in ((head["etag"], (206, b"me")), (PACKAGED_DATE, (200, b"mesh"))):
            with self.subTest(validator=validator):
                status, _, body = await request(app, "/model/meshes.bin", **{"range": "bytes=0-1", "if-range": validator})
                self.assertEqual((status, body), result)
