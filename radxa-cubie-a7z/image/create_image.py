#!/usr/bin/env python3
"""Build the A7Z duck image from the official board image and pinned local inputs."""

import argparse
import json
from pathlib import Path
import subprocess
import tempfile

import build_image
import prepare_image


def create(args):
    args.output = args.output.absolute()
    if args.output.exists():
        raise ValueError('output exists; choose a new release filename')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cubie-release-', dir=args.output.parent) as temporary:
        work = Path(temporary)
        if args.base.suffix == '.xz':
            base = work / 'base.img'
            with base.open('wb') as target:
                subprocess.run([build_image.tool('xz'), '-dc', str(args.base)],
                               stdout=target, check=True)
        else:
            base = args.base.resolve()
        partition = build_image.root_partition(build_image.partitions(base))
        rootfs = work / 'base-rootfs.ext4'
        with base.open('rb') as source, rootfs.open('wb') as target:
            source.seek(partition['offset'])
            build_image.copy_bytes(source, target, partition['length'])
        overlay = work / 'root'
        preparation = argparse.Namespace(
            output=overlay, rootfs=rootfs, studio_archive=prepare_image.STUDIO,
            studio_source=prepare_image.SOURCE,
            debugfs=Path(build_image.tool('debugfs')), release=args.release)
        prepare_image.prepare(preparation)
        removals = (prepare_image.HERE / 'sanitize-files.txt').read_text().splitlines()
        manifest = build_image.build(base, overlay, args.output, removals=removals)
        # The intermediate raw image may have a generic name; retain the supplied
        # compressed base's identity as well for a subsequent release rebuild.
        manifest['source_base'] = {'file': args.base.name,
                                   'sha256': build_image.digest(args.base)}
        Path(str(args.output) + '.manifest.json').write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
    return manifest['output']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--release', default='20261109-a7z-duck-preview1')
    args = parser.parse_args()
    print(json.dumps(create(args), indent=2))


if __name__ == '__main__':
    main()
