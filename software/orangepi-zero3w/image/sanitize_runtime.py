#!/usr/bin/env python3
"""Remove the operator's build paths from prepared runtime ELF diagnostics.

Work only on an unpublished payload copy. Exact path prefixes inside .rodata are
replaced with equal-length, non-NUL generic prefixes; ELF layout stays intact.
"""
import argparse
import getpass
import hashlib
import json
import re
import stat
import struct
from pathlib import Path, PurePosixPath


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sections(data):
    if data[:6] != b'\x7fELF\x02\x01' or len(data) < 64:
        raise ValueError('Expected a little-endian ELF64 runtime binary')
    header = struct.unpack_from('<16sHHIQQQIHHHHHH', data)
    offset, size, count, names_index = header[6], header[11], header[12], header[13]
    if size != 64 or count == 0 or names_index >= count or offset + size * count > len(data):
        raise ValueError('Unsupported ELF section table')
    table = [struct.unpack_from('<IIQQQQIIQQ', data, offset + i * size) for i in range(count)]
    names = table[names_index]
    if names[4] + names[5] > len(data):
        raise ValueError('Invalid ELF section-name table')
    strings = data[names[4]:names[4] + names[5]]
    result = []
    for entry in table:
        if entry[0] >= len(strings):
            raise ValueError('Invalid ELF section name')
        name = strings[entry[0]:].split(b'\0', 1)[0].decode('ascii')
        if entry[1] != 8 and entry[4] + entry[5] > len(data):
            raise ValueError('Invalid ELF section bounds')
        result.append((name, entry[4], entry[5]))
    return result


def scrub_elf(data, private_user):
    identity = re.compile(rb'/(?:Users|home)/' + re.escape(private_user.encode()) + rb'(?=/)')
    paths = re.compile(identity.pattern + rb'/[^\x00\x09\x0a\x0d\x20\x22\x27<>]*?\.rs')
    result = bytearray(data)
    replacements = 0
    roots = set()
    for name, offset, length in sections(data):
        if name != '.rodata':
            continue
        content = data[offset:offset + length]
        local_roots = set()
        for match in paths.finditer(content):
            path = match.group()
            # The local cargo toolchain lives below the private checkout root.
            # Other Rust diagnostics lose their private parent directory too.
            root = path.split(b'/.toolchain/', 1)[0] if b'/.toolchain/' in path else path.rsplit(b'/', 1)[0]
            local_roots.add(root)
        for root in sorted(local_roots, key=len, reverse=True):
            if len(root) < len(b'/build/source'):
                raise ValueError('Private build prefix is too short for safe equal-length replacement')
            generic = b'/build/source' + b'_' * (len(root) - len(b'/build/source'))
            count = content.count(root + b'/')
            content = content.replace(root + b'/', generic + b'/')
            replacements += count
            roots.add(root)
        if identity.search(content):
            raise ValueError('Unrecognized private path remains in ELF .rodata')
        result[offset:offset + length] = content
    after = bytes(result)
    header = struct.unpack_from('<16sHHIQQQIHHHHHH', data)
    program_span = slice(header[5], header[5] + header[9] * header[10])
    section_span = slice(header[6], header[6] + header[11] * header[12])
    if (len(after) != len(data) or after[:64] != data[:64]
            or after[program_span] != data[program_span]
            or after[section_span] != data[section_span]
            or sections(after) != sections(data)):
        raise RuntimeError('ELF structure changed during path sanitization')
    # Reject private strings outside the intentionally supported diagnostic area.
    if identity.search(after):
        raise ValueError('Private paths exist outside supported ELF .rodata diagnostics')
    return after, replacements, len(roots)


def sanitize(base, report_path, private_user):
    if report_path.exists():
        raise ValueError('Choose a new report path to preserve previous sanitization provenance')
    manifest_path = base / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    payload = base / 'payload'
    pending = []
    records = []
    for record in manifest['files']:
        if 'sha256' not in record:
            continue
        relative = PurePosixPath(record['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe payload manifest path')
        path = payload.joinpath(*relative.parts)
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(payload.resolve()):
            raise ValueError('Payload file escapes its prepared directory')
        data = path.read_bytes()
        if digest(data) != record['sha256']:
            raise ValueError('Payload differs from its input manifest')
        if not data.startswith(b'\x7fELF'):
            continue
        after, count, roots = scrub_elf(data, private_user)
        if not count:
            continue
        pending.append((path, after, record))
        records.append({'path': record['path'], 'path_strings_rewritten': count,
                        'private_build_roots_replaced': roots, 'generic_prefix': '/build/source',
                        'before_sha256': digest(data), 'after_sha256': digest(after),
                        'file_size_preserved': True, 'elf_header_and_section_layout_preserved': True})
    # Validate everything before mutating any payload file or its manifest.
    for path, after, record in pending:
        mode = stat.S_IMODE(path.stat().st_mode)
        path.chmod(mode | stat.S_IWUSR)
        try:
            path.write_bytes(after)
        finally:
            path.chmod(mode)
        if path.read_bytes() != after:
            raise RuntimeError('Sanitized ELF readback failed')
        record['sha256'] = digest(after)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    report = {'operation': 'Equal-length private build-path replacement inside ELF .rodata',
              'modified_files': records, 'models_unchanged': True,
              'execution_verification': 'required on Linux ARM64: help, deployment-info, policy validation, read-only RPC'}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'modified_files': len(records),
                      'path_strings_rewritten': sum(x['path_strings_rewritten'] for x in records)}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base', type=Path, help='Unpublished orangepi-quanzhi directory with payload and manifest')
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--private-user', default=getpass.getuser(), help='Build operator username; never included in the report')
    args = parser.parse_args()
    sanitize(args.base.resolve(), args.report.resolve(), args.private_user)
