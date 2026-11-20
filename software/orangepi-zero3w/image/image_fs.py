"""Small, checked ext4 operations for offline regular image files."""
import hashlib
import os
import shutil
import stat
import struct
import subprocess
from pathlib import Path

OFFSET = 65536 * 512
OFFICIAL_SHA256 = "e0a9f05e9e4af2aac7560b8ca3e752754d23581142d953dcc0c02b623552fa56"


def tool(name):
    for path in (Path('/opt/homebrew/opt/e2fsprogs/sbin') / name,
                 Path('/usr/local/opt/e2fsprogs/sbin') / name):
        if path.is_file():
            return str(path)
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f'Missing {name}; install e2fsprogs')
    return found


def run(args, *, data=None):
    result = subprocess.run(list(map(str, args)), input=data, capture_output=True)
    if result.returncode:
        # Do not echo subprocess output: some operations handle credentials.
        raise RuntimeError(f'{Path(str(args[0])).name} failed (exit {result.returncode})')
    return result.stdout


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def quote(path):
    value = str(path)
    if any(c in value for c in '\n\r\0'):
        raise ValueError('Invalid image path')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def debug(fs, command, *, write=False):
    args = [tool('debugfs')]
    if write:
        args.append('-w')
    result = subprocess.run(args + ['-R', command, str(fs)], capture_output=True)
    errors = result.stderr.decode(errors='replace').splitlines()
    # debugfs often returns zero on errors; accept only its version banner.
    if result.returncode or any(line and not line.startswith('debugfs ') for line in errors):
        raise RuntimeError('debugfs request failed: ' + command.split(' ', 1)[0])
    return result.stdout


def exists(fs, path):
    result = subprocess.run([tool('debugfs'), '-R', 'stat ' + quote(path), str(fs)],
                            capture_output=True)
    return result.returncode == 0 and b'Inode:' in result.stdout


def read(fs, path):
    return debug(fs, 'cat ' + quote(path))


def children(fs, path):
    if not exists(fs, path):
        return []
    result = []
    seen = set()
    valid_types = {stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK, stat.S_IFIFO,
                   stat.S_IFCHR, stat.S_IFBLK, stat.S_IFSOCK}
    for line in debug(fs, 'ls -lp ' + quote(path)).decode().splitlines():
        if not line:
            continue
        fields = line.split('/')
        if (len(fields) != 8 or fields[0] or fields[-1]
                or not fields[1].isascii() or not fields[1].isdigit()):
            raise ValueError('Malformed debugfs directory listing')
        # ext4 keeps inode-zero holes after deleting entries. They have no child
        # path; treating the empty name as a directory would recurse into itself.
        if int(fields[1]) == 0:
            continue
        name = fields[5]
        if name in ('.', '..'):
            continue
        if not name or any(ord(char) < 32 or ord(char) == 127 for char in name):
            raise ValueError('Unsafe or empty image directory entry name')
        if (len(fields[2]) != 6 or any(char not in '01234567' for char in fields[2])
                or any(not value.isascii() or not value.isdigit() for value in fields[3:5])
                or (fields[6] and (not fields[6].isascii() or not fields[6].isdigit()))):
            raise ValueError('Malformed image directory entry metadata')
        mode = int(fields[2], 8)
        if stat.S_IFMT(mode) not in valid_types or mode & ~0o177777:
            raise ValueError('Unsupported image directory entry mode')
        if name in seen:
            raise ValueError('Duplicate image directory entry name')
        seen.add(name)
        result.append((path.rstrip('/') + '/' + name, mode))
    return result


def remove(fs, path):
    if not exists(fs, path):
        return
    stat = debug(fs, 'stat ' + quote(path))
    if b'Type: directory' in stat:
        for child, _ in children(fs, path):
            remove(fs, child)
        debug(fs, 'rmdir ' + quote(path), write=True)
    else:
        debug(fs, 'rm ' + quote(path), write=True)


def mkdir(fs, path):
    parent = str(Path(path).parent)
    if parent != '/' and not exists(fs, parent):
        mkdir(fs, parent)
    if not exists(fs, path):
        debug(fs, 'mkdir ' + quote(path), write=True)
        for field, value in [('mode', '040755'), ('uid', '0'), ('gid', '0')]:
            debug(fs, f'set_inode_field {quote(path)} {field} {value}', write=True)


def write(fs, path, content, work, mode=0o644, gid=0):
    mkdir(fs, str(Path(path).parent))
    remove(fs, path)
    temporary = Path(work) / 'inode-input'
    # A local shadow staging file is always private, including on shared hosts.
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
        debug(fs, 'write ' + quote(temporary) + ' ' + quote(path), write=True)
        for field, value in [('mode', f'{0o100000 | mode:07o}'), ('uid', '0'), ('gid', str(gid))]:
            debug(fs, f'set_inode_field {quote(path)} {field} {value}', write=True)
        if read(fs, path) != content:
            raise RuntimeError('Image content readback failed')
    finally:
        temporary.unlink(missing_ok=True)


def check(fs, logfile):
    output = run([tool('e2fsck'), '-fn', fs])
    Path(logfile).write_bytes(output)


def geometry(header, disk_bytes):
    """Validate the one-partition Orange Pi MBR and return its sector length."""
    if (len(header) != 512 or header[510:] != b'\x55\xaa'
            or header[450] != 0x83 or any(header[462:510])):
        raise ValueError('Expected one Orange Pi MBR Linux partition')
    start, sectors = struct.unpack('<II', header[454:462])
    if (start * 512 != OFFSET or sectors == 0
            or (start + sectors) * 512 != disk_bytes):
        raise ValueError('Unexpected partition layout or image boundary')
    return sectors


def extract(image, output):
    image = Path(image)
    if not image.is_file() or image.is_symlink():
        raise ValueError('Input must be a regular image file')
    with image.open('rb') as source:
        geometry(source.read(512), image.stat().st_size)
        source.seek(OFFSET)
        with Path(output).open('xb') as target:
            shutil.copyfileobj(source, target, 8 * 1024 * 1024)


def assemble(boot_image, fs, output):
    """Preserve the board boot area and update MBR length for a resized rootfs."""
    boot_image, fs = Path(boot_image), Path(fs)
    if any(not path.is_file() or path.is_symlink() for path in (boot_image, fs)):
        raise ValueError('Boot image and rootfs must be regular image files')
    filesystem_bytes = fs.stat().st_size
    if filesystem_bytes == 0 or filesystem_bytes % 512:
        raise ValueError('Rootfs size must be a positive whole number of sectors')
    sectors = filesystem_bytes // 512
    if sectors > 0xffffffff or OFFSET // 512 + sectors > 0x100000000:
        raise ValueError('Rootfs exceeds the MBR sector-address boundary')
    with boot_image.open('rb') as boot:
        boot_area = bytearray(boot.read(OFFSET))
    if len(boot_area) != OFFSET:
        raise ValueError('Boot image is shorter than its first partition offset')
    geometry(boot_area[:512], boot_image.stat().st_size)
    struct.pack_into('<I', boot_area, 458, sectors)
    with Path(output).open('xb') as target:
        target.write(boot_area)
        with fs.open('rb') as source:
            shutil.copyfileobj(source, target, 8 * 1024 * 1024)
