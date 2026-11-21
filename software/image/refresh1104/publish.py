"""Maintain the fixed public 1104 image download using the authenticated GitHub CLI."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

REPO = "fanhao375/microduck-replica"
API = "repos/" + REPO + "/releases"
IMAGE_TAG = "microduck-replica-image-20261104"
PREFIX = "microduck-software-v"
IMAGE = "microduck-replica-20261104.img.xz"
SIDECAR = IMAGE + ".sha256"
BUILD = "1104-build.json"
SUMS = "SHA256SUMS.txt"
NAMES = (IMAGE, SIDECAR, BUILD, SUMS)
BASE = "factory-1104-source.xz"
BASE_HASH = "f9970ec551bcddbeeae9748b0ce38f36450fa1497618c07920723849f975aa78"
PREP_HASH = "41c5988ea4f711b106743b9538ff6fea221841aae06105ade00853c194279f07"
VERSION = r"(?:0|[1-9][0-9]{0,8})(?:\.(?:0|[1-9][0-9]{0,8})){2}"
MARKER = re.compile(r"<!-- refresh1104 version=(" + VERSION + r") package=([a-f0-9]{64}) sums=([a-f0-9]{64}) -->")
OBSOLETE = {"README.txt", "update-summary.txt", "runtime-provenance.json", "validation-report.json",
            "microduck-replica-20261104-manifest.txt", "microduck-replica-20261104-verification.json",
            "microduck-replica-20261104-software-components.json"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def gh(*arguments, data=None):
    return subprocess.run(["gh", *map(str, arguments)], input=data, stdout=subprocess.PIPE, check=True).stdout


def api(path, method="GET", fields=None):
    args = ["api", "--hostname", "github.com", "--method", method, path]
    if fields is None:
        raw = gh(*args)
    else:
        raw = gh(*args, "--input", "-", data=json.dumps(fields).encode())
    return json.loads(raw) if raw.strip() else None


def pages(path):
    return [row for page in json.loads(gh("api", "--hostname", "github.com", "--paginate", "--slurp", path))
            for row in page]


def version_key(version):
    require(re.fullmatch(VERSION, version), "Expected a stable three-part software version")
    return tuple(map(int, version.split(".")))


def newest(releases):
    choices = (r for r in releases if not r["draft"] and not r["prerelease"] and r["published_at"]
               and re.fullmatch(PREFIX + VERSION, r["tag_name"]))
    return max(choices, key=lambda r: version_key(r["tag_name"][len(PREFIX):]), default=None)


def asset_hash(asset):
    digest = asset.get("digest") or ""
    require(asset["state"] == "uploaded" and asset["size"] > 0 and re.fullmatch(r"sha256:[0-9a-f]{64}", digest),
            f"Missing GitHub SHA-256 or incomplete upload: {asset['name']}")
    return digest[7:]


def stable(expected=None):
    release = newest(pages(API + "?per_page=100"))
    require(release, "No published stable software release")
    version = release["tag_name"][len(PREFIX):]
    require(expected is None or expected == version, f"Newest public stable software is {version}, requested {expected}")
    name = version + ".software-update.tar"
    assets = [a for a in release["assets"] if a["name"] == name]
    require(len(assets) == 1, f"The stable release must contain {name}")
    return version, asset_hash(assets[0])


def target():
    release = api(API + "/tags/" + IMAGE_TAG)
    require(not release["draft"] and not release["prerelease"] and release["published_at"]
            and not release.get("immutable"), "1104 must be a published, editable stable release")
    assets = {a["name"]: a for a in pages(f"{API}/{release['id']}/assets?per_page=100")}
    require(BASE in assets and asset_hash(assets[BASE]) == BASE_HASH, "The pinned factory source is missing or changed")
    require(not (assets.keys() & OBSOLETE), "Archive the obsolete original factory records before the first promotion")
    return release, assets


def owned_temporary(name):
    pattern = r"refresh1104-(?:stage-[0-9a-f]{64}|prev-[0-9]+)-(" + "|".join(map(re.escape, NAMES)) + r")\.tmp"
    return re.fullmatch(pattern, name) is not None


def others(assets):
    fixed = {}
    for name, asset in assets.items():
        if name in NAMES or owned_temporary(name):
            continue
        require(not re.search(r"[\\\x00-\x1f\x7f/]", name), f"Unsafe checksum filename: {name!r}")
        fixed[name] = asset_hash(asset)
    return fixed


def checksum_file(digests):
    return "".join(f"{digests[name]}  {name}\n" for name in sorted(digests)).encode("utf-8")


def file_hash(path):
    with path.open("rb") as source:
        digest = hashlib.sha256()
        for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(out, version, package):
    for name in (IMAGE, SIDECAR, BUILD):
        path = out / name
        require(not path.is_symlink() and path.is_file(), f"Missing regular output file: {name}")
    require((out / BUILD).stat().st_size <= 4_000_000 and (out / SIDECAR).stat().st_size <= 4096,
            "Unexpected output metadata size")
    require((out / IMAGE).stat().st_size > 0, "Empty factory image")
    digests = {name: file_hash(out / name) for name in (IMAGE, SIDECAR, BUILD)}
    record = json.loads((out / BUILD).read_bytes())
    fields = {"schema": 1, "base_version": "1104", "software_version": version, "base_sha256": BASE_HASH,
              "package_sha256": package, "prepare_sha256": PREP_HASH, "image_sha256": digests[IMAGE],
              "image_bytes": (out / IMAGE).stat().st_size, "hardware_boot_verified": False,
              "compressed_image_readback": True, "ext4_checked": True, "arm_studio_http": True,
              "stale_browser_revalidation": True}
    for name, value in fields.items():
        require(record.get(name) == value and type(record[name]) is type(value), f"Build record mismatch: {name}")
    require(re.fullmatch(r"[0-9a-f]{40}", record.get("source_revision", "")), "Invalid software source revision")
    count = record.get("verified_runtime_files")
    require(type(count) is int and count > 0, "Invalid verified runtime file count")
    require((out / SIDECAR).read_text("ascii").split() == [digests[IMAGE], IMAGE], "Image SHA-256 sidecar mismatch")
    return digests


def marker_and_floor(release, assets, version, package):
    marker = MARKER.search(release.get("body") or "")
    installed = [(marker[1], marker[2])] if marker else []
    for name, asset in assets.items():
        if name == BUILD or (owned_temporary(name) and name.startswith("refresh1104-prev-") and name.endswith("-" + BUILD + ".tmp")):
            require(0 < asset["size"] <= 4_000_000, "Unexpected remote build record size")
            data = gh("api", "--hostname", "github.com", "-H", "Accept: application/octet-stream", f"{API}/assets/{asset['id']}")
            require(hashlib.sha256(data).hexdigest() == asset_hash(asset), "Remote build record digest mismatch")
            record = json.loads(data)
            installed.append((record["software_version"], record["package_sha256"]))
    for old, old_package in installed:
        require(version_key(version) >= version_key(old), f"Refusing to roll factory software back from {old} to {version}")
        require(old != version or old_package == package, "Stable software package changed under an existing version")
    return marker


def complete(release, assets, version, package):
    marker = marker_and_floor(release, assets, version, package)
    if not marker or marker[1] != version or not all(name in assets for name in NAMES):
        return False
    content = others(assets) | {name: asset_hash(assets[name]) for name in (IMAGE, SIDECAR, BUILD)}
    return marker[3] == asset_hash(assets[SUMS]) == hashlib.sha256(checksum_file(content)).hexdigest()


def cleanup(assets):
    for name, item in assets.items():
        if owned_temporary(name):
            api(f"{API}/assets/{item['id']}", "DELETE")


def release_notes(version, package, image_hash, sums_hash):
    root = f"https://github.com/{REPO}/releases"
    download = f"{root}/download/{IMAGE_TAG}"
    return f"""**新卡烧录**　Radxa ZERO 3W + 飞特 HD-1910 的 1104 镜像已预装正式软件 **{version}**。下载 [整卡镜像]({download}/{IMAGE}) 和 [同名校验文件]({download}/{SIDECAR})，核对 SHA-256 后烧录到新卡，完成配网、调零和标定。

镜像 SHA-256：`{image_hash}`

**已有 1104 卡**　已经准备过的卡可打开 **Studio → 设置 → 软件更新** 查看说明并确认安装。原版 1104 首次在线升级，请先按 [原卡准备说明]({root}/tag/{PREFIX}2026.11.20) 验签并运行工具，重启一次。[{version} 正式软件说明和离线更新包]({root}/tag/{PREFIX}{version})。这条发布流程只更新下载文件，已经烧录的卡由使用者决定何时安装。

本次构建以 1104 原厂镜像为底，保留其主板和舵机适配。镜像与签名软件的构建核验结果见 [1104-build.json]({download}/{BUILD})。新卡启动、热点切换和实际运动仍需实机验收。

`factory-1104-source.xz` 是维护者重建镜像时使用的原始底图，历史验收记录针对该底图。当前烧卡文件使用上方的整卡镜像链接。完整附件校验见 [SHA256SUMS.txt]({download}/{SUMS})。模型仅供个人非商业复刻，各组件许可见附件。

<!-- refresh1104 version={version} package={package} sums={sums_hash} -->"""


def promote(out, version):
    version, package = stable(version)
    local = verify(out, version, package)
    release, assets = target()
    if complete(release, assets, version, package):
        cleanup(assets)
        print(f"1104 already carries {version}")
        return
    keep = others(assets)
    sums_data = checksum_file(keep | local)
    local[SUMS] = hashlib.sha256(sums_data).hexdigest()
    with tempfile.TemporaryDirectory(dir=out.parent, prefix="refresh1104-") as temporary:
        temp = Path(temporary)
        staged = {}
        for name in NAMES:
            current = assets.get(name)
            if current and asset_hash(current) == local[name]:
                staged[name] = current
                continue
            stage = f"refresh1104-stage-{local[name]}-{name}.tmp"
            item = assets.get(stage)
            if item and item["state"] == "starter":
                api(f"{API}/assets/{item['id']}", "DELETE")
                item = None
            if not item:
                path = temp / stage
                if name == SUMS:
                    path.write_bytes(sums_data)
                else:
                    try:
                        os.link(out / name, path)
                    except OSError:
                        shutil.copyfile(out / name, path)
                gh("release", "upload", IMAGE_TAG, path, "-R", "github.com/" + REPO)
                _, refreshed = target()
                item = refreshed.get(stage)
            require(item and asset_hash(item) == local[name], f"Staged digest mismatch: {name}")
            staged[name] = item

    # All candidate bytes are present on GitHub before changing the first public filename.
    require(stable(version)[1] == package, "Software release changed during staging")
    release, assets = target()
    marker_and_floor(release, assets, version, package)
    require(others(assets) == keep, "Release attachments changed during staging")
    for name, item in staged.items():
        remote = assets.get(item["name"])
        require(remote and remote["id"] == item["id"] and asset_hash(remote) == local[name],
                f"Verified attachment changed before promotion: {name}")
    for name in NAMES:
        item = staged[name]
        current = assets.get(name)
        if current and current["id"] == item["id"]:
            continue
        if current:
            api(f"{API}/assets/{current['id']}", "PATCH", {"name": f"refresh1104-prev-{current['id']}-{name}.tmp"})
        try:
            renamed = api(f"{API}/assets/{item['id']}", "PATCH", {"name": name})
            require(renamed["name"] == name and asset_hash(renamed) == local[name], f"Promotion mismatch: {name}")
        except Exception:
            # A network failure can hide a successful PATCH. Restore only if the canonical name is free.
            _, fresh = target()
            if current and name not in fresh:
                api(f"{API}/assets/{current['id']}", "PATCH", {"name": name})
            raise

    release, assets = target()
    require(others(assets) == keep and all(asset_hash(assets[n]) == local[n] for n in NAMES),
            "Remote assets changed during promotion")
    title = f"1104 正式镜像 · Radxa ZERO 3W · 预装软件 {version}"
    notes = release_notes(version, package, local[IMAGE], local[SUMS])
    api(f"{API}/{release['id']}", "PATCH", {"name": title, "body": notes, "make_latest": "true"})
    cleanup(assets)
    release, assets = target()
    require(release["name"] == title and complete(release, assets, version, package), "Final release verification failed")
    print(f"https://github.com/{REPO}/releases/tag/{IMAGE_TAG}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True, dest="command")
    commands.add_parser("candidate", help="Print the version needing a build; empty output means already current")
    for name in ("check", "promote"):
        command = commands.add_parser(name)
        command.add_argument("--version", required=True)
        command.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    require(os.environ.get("GITHUB_REPOSITORY", REPO) == REPO, "Unexpected repository")
    if args.command == "candidate":
        version, package = stable()
        release, assets = target()
        if not complete(release, assets, version, package) or any(owned_temporary(n) for n in assets):
            print(version)
    elif args.command == "check":
        version, package = stable(args.version)
        verify(args.out, version, package)
        print(f"Verified local factory outputs for {version}")
    else:
        promote(args.out.resolve(), args.version)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f"1104 refresh: {error}")
