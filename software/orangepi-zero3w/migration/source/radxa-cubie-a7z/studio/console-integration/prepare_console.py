#!/usr/bin/env python3
"""Rebuild the installed A7Z baseline, then apply the reviewed frontend overlay."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

HERE = Path(__file__).resolve().parent
COMMIT = 'f682b7bd30d1ef4e6f931db16406b340d4d54fec'
VERIFIED = {
    'studio/pad.py': '27bbe0ae137b6e827f5b1d1b8a40a62ae57e670b19d9c36cc498ca884eaf2b5f',
    'studio/robotd.py': '8f3f4412901756fdd29fb6305b89421cbf1a9fa45e33df73e413faaef69c2750',
    'studio/state.py': 'fba77fae1068faed81267bd86687e81bf6851a62e93d1ce860d4ede61fc38c77',
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_repo', type=Path)
    parser.add_argument('--output', type=Path, default=HERE / 'workspace/studio')
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('output already exists; choose a fresh directory')
    archive = subprocess.check_output(['git', '-C', str(args.source_repo), 'archive', COMMIT, 'software/studio'])
    args.output.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        members = []
        for member in source.getmembers():
            path = Path(member.name)
            if path.parts[:2] != ('software', 'studio') or len(path.parts) <= 2:
                continue
            member.name = str(Path(*path.parts[2:]))
            members.append(member)
        source.extractall(args.output, members=members, filter='data')
    for name, strip in [('a7z-readonly-studio.patch', '3'), ('a7z-disabled-configd-pad.patch', '1')]:
        subprocess.run(['patch', '--batch', '--forward', '-p' + strip, '-i', str(HERE.parent / name)], cwd=args.output, check=True)
    for relative, expected in VERIFIED.items():
        if digest(args.output / relative) != expected:
            raise SystemExit('unexpected A7Z baseline: ' + relative)
    hashes = {str(p.relative_to(args.output)): digest(p) for p in (args.output / 'web').rglob('*') if p.is_file()}
    (args.output.parent / 'baseline-web.json').write_text(json.dumps(hashes, indent=2) + '\n')
    if (HERE / 'web').exists():
        shutil.copytree(HERE / 'web', args.output / 'web', dirs_exist_ok=True)
    print(args.output)


if __name__ == '__main__':
    main()
