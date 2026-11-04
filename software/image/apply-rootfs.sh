#!/bin/sh
# 在一个已经挂载好的 rootfs 目录上安装 payload、Studio 和 overlay，并做清理。
# 纯文件操作：不碰 loop 设备，不 chroot。
set -eu
LC_ALL=C
export LC_ALL

STEP=初始化
ERROR_PRINTED=0
WORK=
ROOT=
DEL_LOG=
ADD_LOG=
SSH_WARN=
PYTHON=
STUDIO_DEST=
SYSTEMD=
DELETE_LIST=
PAYLOAD_META=
STUDIO_VERSION=${STUDIO_VERSION:-}
BASE_IMG_NAME=${BASE_IMG_NAME:-未知}
BASE_IMG_SHA256=${BASE_IMG_SHA256:-未知}
PAYLOAD_SHA256=${PAYLOAD_SHA256:-}
IMAGE_VERSION=${IMAGE_VERSION:-}
ALLOW_UNCONFIRMED=${ALLOW_UNCONFIRMED:-0}

usage() {
    echo "用法：sh apply-rootfs.sh ROOT PAYLOAD_TGZ STUDIO_DIR OVERLAY_DIR" >&2
}

fail() {
    ERROR_PRINTED=1
    echo "错误：卡在「$STEP」：$1" >&2
    if [ "$#" -ge 2 ]; then
        echo "怎么办：$2" >&2
    fi
    exit 1
}

on_exit() {
    rc=$?
    trap '' 0
    if [ "$rc" -ne 0 ] && [ "$ERROR_PRINTED" -eq 0 ]; then
        echo "错误：脚本在「$STEP」这一步意外停止（退出码 $rc）。" >&2
        echo "怎么办：看上面的命令报错；修好后重跑。ROOT 里的文件可能已部分修改。" >&2
    fi
    if [ -n "$WORK" ] && [ -d "$WORK" ]; then
        rm -rf "$WORK" || true
    fi
    exit "$rc"
}
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
trap on_exit 0

rel_path() {
    p=$1
    case "$p" in
        "$ROOT"/*) p=${p#"$ROOT"/} ;;
    esac
    printf '%s' "$p"
}

add_del() {
    printf '%s\n' "$(rel_path "$1")" >> "$DEL_LOG"
}

add_add() {
    printf '%s\n' "$(rel_path "$1")" >> "$ADD_LOG"
}

assert_parent_in_root() {
    p=$1
    parent=$(dirname "$p")
    [ -d "$parent" ] || fail "目标父目录不存在：$parent" "先确认 ROOT 结构完整，或重新挂载镜像。"
    real=$(cd "$parent" && pwd -P)
    case "$real" in
        "$ROOT"|"$ROOT"/*) ;;
        *) fail "拒绝操作 ROOT 外的路径：$p（实际父目录 $real）" "检查 ROOT 里是否有指向宿主机的符号链接。" ;;
    esac
}

# 创建目录（含缺失的上级），新目录一律 0755；任何一个已存在或将要创建的
# 路径解析到 ROOT 外都直接失败，绝不先写再检查。
safe_dir() {
    dir=$1
    case "$dir" in
        "$ROOT"|"$ROOT"/*) ;;
        *) fail "拒绝在 ROOT 外创建目录：$dir" "这是内部错误；停止并检查参数。" ;;
    esac
    probe=$dir
    while [ ! -e "$probe" ] && [ ! -L "$probe" ]; do
        parent=$(dirname "$probe")
        [ "$parent" != "$probe" ] || break
        probe=$parent
    done
    [ -d "$probe" ] || fail "路径上有非目录：$probe" "检查 ROOT 里的文件结构。"
    real=$(cd "$probe" && pwd -P) || fail "无法解析目录：$probe" "检查权限。"
    case "$real" in
        "$ROOT"|"$ROOT"/*) ;;
        *) fail "拒绝写入 ROOT 外：$dir 的上级 $probe 实际是 $real" "ROOT 里有指向镜像外的符号链接；先清理后再重试。" ;;
    esac
    : > "$WORK/absent-dirs.txt"
    probe=$dir
    while [ ! -d "$probe" ] && [ "$probe" != "$ROOT" ] && [ "$probe" != "/" ]; do
        printf '%s\n' "$probe" >> "$WORK/absent-dirs.txt"
        probe=$(dirname "$probe")
    done
    mkdir -p "$dir" || fail "无法创建目录：$dir" "检查 ROOT 是否可写、磁盘空间是否足够。"
    if [ -s "$WORK/absent-dirs.txt" ]; then
        while IFS= read -r d; do
            [ -n "$d" ] || continue
            chmod 0755 "$d" || fail "无法设置目录权限：$d" "检查 ROOT 是否可写。"
        done < "$WORK/absent-dirs.txt"
    fi
    real=$(cd "$dir" && pwd -P) || fail "无法进入目录：$dir" "检查权限。"
    case "$real" in
        "$ROOT"|"$ROOT"/*) ;;
        *) fail "拒绝写入 ROOT 外：$dir 实际是 $real" "ROOT 里有指向镜像外的符号链接；先清理后再重试。" ;;
    esac
}

# 目标若是符号链接先删掉（只删链接本身），再交给调用者写；父目录走 safe_dir。
safe_file() {
    file=$1
    case "$file" in
        "$ROOT"/*) ;;
        *) fail "拒绝写入 ROOT 外：$file" "这是内部错误；停止并检查参数。" ;;
    esac
    if [ "$file" = "$ROOT" ]; then
        fail "拒绝写入 ROOT 本身" "脚本只写 ROOT 里的具体文件。"
    fi
    safe_dir "$(dirname "$file")"
    if [ -L "$file" ]; then
        echo "  删除符号链接（避免写到链接目标）：$file"
        add_del "$file"
        rm -f "$file" || fail "无法删除符号链接：$file" "检查权限。"
    fi
    if [ -d "$file" ]; then
        fail "目标是目录，不能写成文件：$file" "检查 ROOT 结构。"
    fi
}

remove_path() {
    p=$1
    if [ ! -e "$p" ] && [ ! -L "$p" ]; then
        return 0
    fi
    case "$p" in
        "$ROOT"/*) ;;
        *) fail "拒绝删除 ROOT 外路径：$p" "这是内部错误；停止并检查参数。" ;;
    esac
    if [ "$p" = "$ROOT" ]; then
        fail "拒绝删除 ROOT 本身" "脚本只删除 ROOT 里的具体路径。"
    fi
    assert_parent_in_root "$p"
    echo "  删除 $p"
    add_del "$p"
    rm -rf "$p" || fail "删除失败：$p" "检查权限；ROOT 可能只读。"
}

truncate_regular_file() {
    p=$1
    safe_file "$p"
    : > "$p" || fail "无法清空文件：$p" "检查权限。"
}

print_list() {
    label=$1
    file=$2
    if [ -s "$file" ]; then
        echo "    $label（$(wc -l < "$file" | tr -d '[:space:]') 项）："
        sed 's/^/      - /' "$file"
    fi
}

find_python() {
    for c in python3 python3.13 python3.12 python3.11 python; do
        if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import tomllib' >/dev/null 2>&1; then
            printf '%s' "$c"
            return 0
        fi
    done
    return 1
}

sha256_of() {
    sha256sum "$1" | awk '{print $1}'
}

size_of() {
    wc -c < "$1" | tr -d '[:space:]'
}

# Windows 上的 grep/awk 会吞掉 CR，所以用 Python 按二进制读文件。
# 含 NUL 或常见二进制后缀的文件跳过（png 等本来就可能含 0x0d）。
check_crlf_paths() {
    "$PYTHON" - "$WORK/crlf.txt" "$OVERLAY" "$STUDIO/deploy" "$STUDIO/run.py" <<'PY' || fail "换行检查失败" "检查 overlay 和 Studio 目录是否可读。"
import sys
from pathlib import Path

out = Path(sys.argv[1])
found = []
binary_suffixes = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tif", ".tiff",
    ".pdf", ".zip", ".gz", ".xz", ".tgz", ".bz2", ".7z", ".so", ".bin", ".pyc",
    ".ttf", ".otf", ".woff", ".woff2", ".mp3", ".wav", ".ogg", ".mp4", ".webm",
}
for raw in sys.argv[2:]:
    path = Path(raw)
    files = [path] if path.is_file() else (list(path.rglob("*")) if path.is_dir() else [])
    for item in files:
        if not item.is_file():
            continue
        if item.suffix.lower() in binary_suffixes:
            continue
        try:
            data = item.read_bytes()
        except OSError:
            continue
        if b"\x00" in data:
            continue
        if b"\r" in data:
            found.append(str(item))
out.write_text("".join(line + "\n" for line in sorted(found)), encoding="utf-8")
PY
}

rewrite_radxa_gecos() {
    file=$1
    [ -e "$file" ] || [ -L "$file" ] || fail "缺少账号文件：$file" "确认底图里有 etc/passwd 和 etc/passwd-。"
    safe_file "$file"
    [ -f "$file" ] || fail "账号文件不是普通文件：$file" "检查 ROOT/etc 的结构。"
    "$PYTHON" - "$file" "$WORK/passwd.new" <<'PY' || fail "无法只改 radxa 的全名：$file" "确认文件是标准 passwd 格式（7 个冒号分隔字段）。"
import sys
from pathlib import Path

src, dst = sys.argv[1], sys.argv[2]
raw = Path(src).read_bytes()
lines = raw.split(b'\n')
seen = 0
for index, line in enumerate(lines):
    cr = b''
    if line.endswith(b'\r'):
        line = line[:-1]
        cr = b'\r'
    fields = line.split(b':')
    if fields and fields[0] == b'radxa':
        seen += 1
        if len(fields) != 7:
            raise SystemExit('radxa 行不是 7 个字段')
        fields[4] = b'Microduck'
        lines[index] = b':'.join(fields) + cr
if seen != 1:
    raise SystemExit(f'radxa 行应恰好 1 条，实际 {seen} 条')
Path(dst).write_bytes(b'\n'.join(lines))
PY
    cat "$WORK/passwd.new" > "$file" || fail "无法写回账号文件：$file" "检查权限。"
    echo "  已改 $(rel_path "$file") 的 radxa 全名：Microduck"
    add_add "$file"
}

# 只换 radxa 那一行的第 2 个字段（密码哈希），其它字段、其它行、文件属性都不动。
set_radxa_password_hash() {
    file=$1
    hash=$2
    [ -e "$file" ] || fail "缺少账号文件：$file" "确认底图里有 etc/shadow（有 shadow- 的话也一起检查）。"
    safe_file "$file"
    [ -f "$file" ] || fail "账号文件不是普通文件：$file" "检查 ROOT/etc 的结构。"
    "$PYTHON" - "$file" "$WORK/shadow.new" "$hash" <<'PY' || fail "无法只改 radxa 的密码哈希：$file" "确认文件是标准 shadow 格式（9 个冒号分隔字段），且 radxa 恰好一行。"
import sys
from pathlib import Path

src, dst, new_hash = sys.argv[1], sys.argv[2], sys.argv[3].encode("ascii")
raw = Path(src).read_bytes()
lines = raw.split(b"\n")
seen = 0
for index, line in enumerate(lines):
    cr = b""
    if line.endswith(b"\r"):
        line = line[:-1]
        cr = b"\r"
    fields = line.split(b":")
    if fields and fields[0] == b"radxa":
        seen += 1
        if len(fields) != 9:
            raise SystemExit("radxa 行不是 9 个字段")
        fields[1] = new_hash
        lines[index] = b":".join(fields) + cr
if seen != 1:
    raise SystemExit(f"radxa 行应恰好 1 条，实际 {seen} 条")
Path(dst).write_bytes(b"\n".join(lines))
PY
    cat "$WORK/shadow.new" > "$file" || fail "无法写回账号文件：$file" "检查权限。"
    echo "  已设置 $(rel_path "$file") 里 radxa 的默认密码哈希"
    add_add "$file"
}

set_updater_dev_keys_false() {
    file=$1
    [ -e "$file" ] || [ -L "$file" ] || fail "缺少更新器配置：$file" "确认底图里有 etc/robot/updater.toml。"
    safe_file "$file"
    [ -f "$file" ] || fail "更新器配置不是普通文件：$file" "检查 ROOT/etc/robot 的结构。"
    "$PYTHON" - "$file" "$WORK/updater.toml.new" <<'PY' || fail "无法只改 allow_dev_keys 的值：$file" "确认配置文件里 allow_dev_keys 只有一行。"
import re
import sys
from pathlib import Path

src, dst = sys.argv[1], sys.argv[2]
raw = Path(src).read_bytes()
lines = raw.split(b'\n')
pattern = re.compile(rb'^([ \t]*allow_dev_keys[ \t]*=[ \t]*)(true|false)([ \t]*(?:#.*)?)$')
matches = []
for index, line in enumerate(lines):
    cr = b''
    if line.endswith(b'\r'):
        line = line[:-1]
        cr = b'\r'
    match = pattern.match(line)
    if match:
        matches.append((index, match, cr))
if len(matches) != 1:
    raise SystemExit(f'allow_dev_keys 应恰好 1 行，实际 {len(matches)} 行')
index, match, cr = matches[0]
lines[index] = match.group(1) + b'false' + match.group(3) + cr
Path(dst).write_bytes(b'\n'.join(lines))
PY
    if cmp -s "$file" "$WORK/updater.toml.new"; then
        echo "  $(rel_path "$file") 已经是 allow_dev_keys = false"
    else
        cat "$WORK/updater.toml.new" > "$file" || fail "无法写回更新器配置：$file" "检查权限。"
        echo "  已改 $(rel_path "$file")：allow_dev_keys = false"
        add_add "$file"
    fi
}

prune_dev_keys() {
    dir=$1
    [ -d "$dir" ] || fail "缺少更新器公钥目录：$dir" "确认底图里有 etc/robot/trusted_keys。"
    dev_keys=$WORK/dev-keys.txt
    find "$dir" -mindepth 1 -maxdepth 1 -name '*.dev.pub' -print | LC_ALL=C sort > "$dev_keys" \
        || fail "无法列出开发者签名公钥：$dir" "检查目录权限。"
    print_list "将删除开发者签名公钥" "$dev_keys"
    while IFS= read -r p; do
        [ -n "$p" ] || continue
        remove_path "$p"
    done < "$dev_keys"
    release_keys=$WORK/release-keys.txt
    find "$dir" -mindepth 1 -maxdepth 1 -type f -name 'release-*.pub' -print | LC_ALL=C sort > "$release_keys" \
        || fail "无法列出官方签名公钥：$dir" "检查目录权限。"
    if [ ! -s "$release_keys" ]; then
        fail "信任目录里没有 release-*.pub：$dir" "更新器遇到空的信任目录会起不来；先恢复官方 release 公钥再重试。"
    fi
    echo "    保留官方签名公钥："
    sed 's/^/      - /' "$release_keys"
}

manifest_to_tsv() {
    manifest=$1
    out=$2
    : > "$out"
    if [ -n "$PYTHON" ]; then
        if "$PYTHON" - "$manifest" > "$WORK/manifest.raw" 2>/dev/null <<'PY'
import json
import sys
with open(sys.argv[1], encoding='utf-8') as fh:
    data = json.load(fh)
files = data.get('files')
if not isinstance(files, list):
    raise SystemExit('payload.json 缺少 files 数组')
for item in files:
    path = item['path']
    digest = item['sha256']
    size = int(item['size'])
    if '\t' in path or '\n' in path:
        raise SystemExit('payload 路径包含制表符或换行')
    print(f'{path}\t{digest}\t{size}')
PY
        then
            tr -d '\r' < "$WORK/manifest.raw" > "$out"
        fi
    fi
    if [ ! -s "$out" ]; then
        echo "    提示：python 解析 payload.json 失败，改用 awk 兜底。" >&2
        awk '
            {
                line = $0
                if (match(line, /"path"[ \t]*:[ \t]*"[^"]*"/)) {
                    p = substr(line, RSTART, RLENGTH)
                    sub(/^"path"[ \t]*:[ \t]*"/, "", p)
                    sub(/"$/, "", p)
                    rest = substr(line, RSTART + RLENGTH)
                    h = ""
                    s = ""
                    if (match(rest, /"sha256"[ \t]*:[ \t]*"[0-9a-fA-F]+"/)) {
                        h = substr(rest, RSTART, RLENGTH)
                        sub(/^"sha256"[ \t]*:[ \t]*"/, "", h)
                        sub(/"$/, "", h)
                    }
                    if (match(line, /"size"[ \t]*:[ \t]*[0-9]+/)) {
                        s = substr(line, RSTART, RLENGTH)
                        sub(/^"size"[ \t]*:[ \t]*/, "", s)
                    }
                    if (h != "" && s != "") print p "\t" h "\t" s
                }
            }
        ' "$manifest" > "$out"
    fi
    [ -s "$out" ] || fail "无法解析 payload.json" "用 capture-payload.sh 重新生成，或安装 python3 后重试。"
}

verify_manifest() {
    base=$1
    label=$2
    while IFS=$(printf '\t') read -r rel expected size; do
        case "$rel" in
            ""|/*|*".."*) fail "payload 清单里有不安全路径：$rel" "重新生成 payload。" ;;
        esac
        target=$base/$rel
        if [ ! -e "$target" ] && [ ! -L "$target" ]; then
            fail "$label 缺少文件：$rel" "重新运行 capture-payload.sh，或检查 tgz 是否完整。"
        fi
        if [ -L "$target" ]; then
            fail "$label 里有符号链接：$rel" "payload 不允许符号链接；重新生成 payload。"
        fi
        actual=$(sha256_of "$target")
        if [ "$actual" != "$expected" ]; then
            fail "$label 的 sha256 不符：$rel（期望 $expected，实际 $actual）" "不要继续：重新拉 payload，确认传输没有损坏。"
        fi
        actual_size=$(size_of "$target")
        if [ "$actual_size" != "$size" ]; then
            fail "$label 的大小不符：$rel（期望 $size，实际 $actual_size）" "重新拉 payload。"
        fi
    done < "$WORK/files.tsv"
}

put_file() {
    src=$1
    dst=$2
    mode=$3
    [ -f "$src" ] || fail "缺少源文件：$src" "检查 STUDIO_DIR/deploy 是否完整。"
    safe_file "$dst"
    if [ "$(id -u)" -eq 0 ]; then
        install -D -m "$mode" -o 0 -g 0 "$src" "$dst" || fail "安装文件失败：$dst" "检查 ROOT 是否可写、磁盘空间是否足够。"
    else
        install -D -m "$mode" "$src" "$dst" || fail "安装文件失败：$dst" "检查 ROOT 是否可写、磁盘空间是否足够。"
    fi
    add_add "$dst"
}

# 出厂让主程序跑标定模式。小助手在上手清单第 3 步成功后删掉这个文件。
# 文件内容和注释是与小助手的约定，不能改字。
write_commissioning_dropin() {
    src=$STAGE/etc/systemd/system/robotd.service.d/zzzz-r17-0151.conf
    dst=$SYSTEMD/robotd.service.d/zzzz-zz-microduck-commissioning.conf
    [ -f "$src" ] || fail "payload 里缺少 robotd 的 drop-in：etc/systemd/system/robotd.service.d/zzzz-r17-0151.conf" "重新运行 capture-payload.sh；payload 不完整。"
    "$PYTHON" - "$src" "$WORK/commissioning.conf" <<'PY' || fail "无法从 payload 的 zzzz-r17-0151.conf 生成出厂标定模式 drop-in" "检查 payload 里的 drop-in：必须恰好一行非空 ExecStart=，且不能已经带 --commissioning。"
import re
import sys
from pathlib import Path

src, dst = sys.argv[1], sys.argv[2]
exec_lines = []
for raw in Path(src).read_text(encoding="utf-8", errors="strict").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or line.startswith(";"):
        continue
    if line.startswith("ExecStart="):
        value = line[len("ExecStart="):].strip()
        if value:
            exec_lines.append(value)
if len(exec_lines) != 1:
    raise SystemExit("zzzz-r17-0151.conf 里的非空 ExecStart= 应恰好 1 行，实际 %d 行" % len(exec_lines))
command = exec_lines[0]
if re.search(r"(^|\s)--commissioning(\s|$)", command):
    raise SystemExit("zzzz-r17-0151.conf 已经带 --commissioning，payload 不对")
text = (
    "# Microduck 复刻镜像：出厂让主程序跑标定模式（--commissioning）。\n"
    "# 上手清单第 3 步「写进主程序」成功后，root 小助手删掉这个文件，主程序回到运动模式。\n"
    "[Service]\n"
    "ExecStart=\n"
    "ExecStart=" + command + " --commissioning\n"
)
Path(dst).write_text(text, encoding="utf-8", newline="\n")
PY
    safe_file "$dst"
    cp -f "$WORK/commissioning.conf" "$dst" || fail "无法写入出厂标定模式 drop-in：$dst" "检查 ROOT 是否可写、磁盘空间是否足够。"
    chmod 0644 "$dst" || fail "无法设置权限：$dst" "检查 ROOT 文件系统权限。"
    if [ "$(id -u)" -eq 0 ]; then
        chown 0:0 "$dst" || fail "无法设置属主：$dst（root:root）" "检查 ROOT 文件系统是否支持 chown。"
    fi
    add_add "$dst"
    echo "    已写出厂标定模式 drop-in：$(rel_path "$dst")"
}

make_enable_link() {
    target=$1
    link=$2
    safe_file "$link"
    if ! ln -s "$target" "$link" 2>/dev/null; then
        source_file=$ROOT$target
        [ -f "$source_file" ] || fail "单元文件不存在，无法建 enable 链接：$target" "检查 OVERLAY_DIR 或 systemd 单元路径。"
        cp -f "$source_file" "$link" || fail "无法创建链接：$link" "检查权限。"
        echo "  警告：本机不能创建符号链接，测试环境改为复制单元文件：$link"
    fi
    echo "  创建链接 $link -> $target"
    add_add "$link"
}

queue_delete() {
    p=$1
    if [ -e "$p" ] || [ -L "$p" ]; then
        printf '%s\n' "$p" >> "$DELETE_LIST"
    fi
}

queue_unit_links() {
    name=$1
    find "$ROOT" \( -type d -name '*.wants' -o -type d -name '*.requires' \) -print > "$WORK/unit-dirs.txt" 2>/dev/null || true
    while IFS= read -r d; do
        [ -n "$d" ] || continue
        queue_delete "$d/$name"
    done < "$WORK/unit-dirs.txt"
}

purge_unit() {
    name=$1
    queue_delete "$SYSTEMD/$name"
    queue_delete "$SYSTEMD/$name.d"
    queue_unit_links "$name"
}

ssh_file_records() {
    file=$1
    label=$2
    awk -v label="$label" '
        {
            raw=$0
            line=raw
            sub(/^[ \t]+/, "", line)
            if (line == "" || line ~ /^#/) next
            n=split(line, a, /[ \t=]+/)
            key=tolower(a[1])
            if (key == "include") next
            val=tolower(a[2])
            if (key=="passwordauthentication" || key=="permitrootlogin" || key=="kbdinteractiveauthentication" || key=="challengeresponseauthentication")
                print key "\t" val "\t" label ":" FNR ": " raw
        }
    ' "$file" >> "$WORK/ssh-records.txt"
}

ssh_audit() {
    main=$ROOT/etc/ssh/sshd_config
    : > "$WORK/ssh-records.txt"
    : > "$SSH_WARN"
    if [ ! -f "$main" ]; then
        echo "  警告：ROOT 里没有 sshd 主配置：$main（请人工确认 SSH 策略）"
        return 0
    fi
    include_line=$(awk '
        {
            line=$0
            sub(/^[ \t]+/, "", line)
            if (line ~ /^#/) next
            if (tolower(line) ~ /^include[ \t]+/ && index(line, "/etc/ssh/sshd_config.d/") > 0) {
                print NR
                exit
            }
        }
    ' "$main")
    if [ -n "$include_line" ]; then
        awk -v upto="$include_line" 'NR < upto' "$main" > "$WORK/ssh-main-before"
        awk -v from="$include_line" 'NR > from' "$main" > "$WORK/ssh-main-after"
    else
        cp "$main" "$WORK/ssh-main-before"
        : > "$WORK/ssh-main-after"
    fi
    ssh_file_records "$WORK/ssh-main-before" "sshd_config(Include 之前)"
    if [ -d "$ROOT/etc/ssh/sshd_config.d" ]; then
        find "$ROOT/etc/ssh/sshd_config.d" -maxdepth 1 -type f -name '*.conf' -print | sort > "$WORK/ssh-dropins.txt" || true
        while IFS= read -r f; do
            [ -n "$f" ] || continue
            ssh_file_records "$f" "sshd_config.d/$(basename "$f")"
        done < "$WORK/ssh-dropins.txt"
    fi
    ssh_file_records "$WORK/ssh-main-after" "sshd_config(Include 之后)"
    # sshd 取每个选项第一次出现的值；10-microduck.conf 之前的 drop-in 才可能翻盘。
    awk -F'\t' '!seen[$1]++ && $2 != "no" { print $3 }' "$WORK/ssh-records.txt" > "$SSH_WARN"
}

clean_log_dir() {
    dir=$1
    [ -d "$dir" ] || return 0
    if [ -d "$dir/journal" ]; then
        find "$dir/journal" -mindepth 1 -maxdepth 1 -print > "$WORK/journal.txt" || true
        if [ -s "$WORK/journal.txt" ]; then
            echo "    将删除 $dir/journal 的内容："
            sed 's/^/      - /' "$WORK/journal.txt"
            while IFS= read -r p; do
                remove_path "$p"
            done < "$WORK/journal.txt"
        fi
    fi
    find "$dir" -type f ! -path "$dir/journal/*" -print > "$WORK/log-files.txt" || true
    if [ -s "$WORK/log-files.txt" ]; then
        echo "    清空 $(wc -l < "$WORK/log-files.txt" | tr -d '[:space:]') 个日志文件（保留目录结构）：$dir"
        while IFS= read -r p; do
            truncate_regular_file "$p"
        done < "$WORK/log-files.txt"
    fi
}

[ "$#" -eq 4 ] || { usage; exit 2; }
ROOT_ARG=$1
PAYLOAD_ARG=$2
STUDIO_ARG=$3
OVERLAY_ARG=$4

[ -n "$ROOT_ARG" ] || fail "ROOT 不能为空" "传入已经挂载好的 rootfs 目录。"
[ "$ROOT_ARG" != "/" ] || fail "ROOT 不能是 /" "绝不能对运行脚本的这台机器自己的根目录操作。"
[ -d "$ROOT_ARG" ] || fail "ROOT 不是目录：$ROOT_ARG" "先挂载镜像的 p1。"
ROOT=$(cd "$ROOT_ARG" && pwd -P)
case "$ROOT" in
    /|//|///) fail "ROOT 解析后是根目录：$ROOT" "换成一个独立的挂载目录。" ;;
esac

[ -f "$PAYLOAD_ARG" ] || fail "payload 文件不存在：$PAYLOAD_ARG" "先运行 capture-payload.sh，并确认路径正确。"
PAYLOAD=$(cd "$(dirname "$PAYLOAD_ARG")" && pwd -P)/$(basename "$PAYLOAD_ARG")
[ -d "$STUDIO_ARG" ] || fail "STUDIO_DIR 不存在：$STUDIO_ARG" "指向 software/studio 目录。"
STUDIO=$(cd "$STUDIO_ARG" && pwd -P)
[ -d "$OVERLAY_ARG" ] || fail "OVERLAY_DIR 不存在：$OVERLAY_ARG" "指向本目录下的 overlay。"
OVERLAY=$(cd "$OVERLAY_ARG" && pwd -P)
HERE=$(cd "$(dirname "$0")" && pwd -P)
SOFTWARE_COMPONENTS_REQUESTED=0
if [ -n "${SOFTWARE_UPDATE_PACKAGE:-}${SOFTWARE_UPDATE_PUBLIC_KEY:-}${SOFTWARE_UPDATE_ENGINE:-}${VOICE_RESOURCES_DIR:-}" ]; then
    SOFTWARE_COMPONENTS_REQUESTED=1
    [ -f "${SOFTWARE_UPDATE_PACKAGE:-}" ] && [ -f "${SOFTWARE_UPDATE_PUBLIC_KEY:-}" ] && \
        [ -f "${SOFTWARE_UPDATE_ENGINE:-}" ] && [ -f "${VOICE_RESOURCES_DIR:-}/resources-manifest.json" ] || \
        fail "软件升级组件输入不完整" "同时提供 SOFTWARE_UPDATE_PACKAGE、SOFTWARE_UPDATE_PUBLIC_KEY、SOFTWARE_UPDATE_ENGINE 和 VOICE_RESOURCES_DIR，且文件必须存在。"
    [ -f "$HERE/software_components.py" ] && [ -f "$HERE/../updater/bootstrap.py" ] || \
        fail "软件升级组件输入缺少共享安装器" "使用包含 software/updater 与 software/image 的完整冻结源码。"
fi
CAL_SRC=$HERE/calib/hd1910-calibration.option-a.toml

if [ -z "$IMAGE_VERSION" ]; then
    if command -v git >/dev/null 2>&1 && IMAGE_VERSION=$(git -C "$HERE" rev-parse --short HEAD 2>/dev/null); then
        if [ -n "$(git -C "$HERE" status --porcelain -- . 2>/dev/null)" ]; then
            IMAGE_VERSION="$IMAGE_VERSION-dirty"
        fi
    else
        IMAGE_VERSION=未知
    fi
fi

STEP="检查参数和 ROOT 边界"
echo "==> [0/9] 检查参数和 ROOT 边界"
MISSING_TOOLS=
for tool in tar sha256sum awk sed find grep cmp cp mkdir chmod chown install ln dirname basename readlink date mktemp rm mv sort tr wc stat openssl; do
    command -v "$tool" >/dev/null 2>&1 || MISSING_TOOLS="$MISSING_TOOLS $tool"
done
    [ -z "$MISSING_TOOLS" ] || fail "缺少命令：$MISSING_TOOLS" "在 WSL / Debian 里安装 coreutils、tar、grep、openssl 后重试。"
PYTHON=$(find_python) || PYTHON=
[ -n "$PYTHON" ] || fail "找不到带 tomllib 的 python3（需要 3.11+）" "安装 python3（>=3.11）后重试。"

[ -e "$ROOT/etc" ] || fail "ROOT 不像 rootfs：缺少 etc/" "确认挂载的是 R1 的 p1。"
[ -e "$ROOT/etc/systemd/system/basic.target.wants/armbian-resize-filesystem.service" ] || [ -L "$ROOT/etc/systemd/system/basic.target.wants/armbian-resize-filesystem.service" ] || fail "缺少 basic.target.wants/armbian-resize-filesystem.service" "第一次开机就不会自动扩容到整张卡；确认底图是 Armbian R1。"
[ ! -e "$ROOT/root/.no_rootfs_resize" ] || fail "存在 root/.no_rootfs_resize" "这会禁止第一次开机自动扩容；删掉该标记后重试。"

# 顶层目录若指向 ROOT 外，后面的复制会把宿主机的 /etc 之类写坏。
for d in etc opt var home usr root tmp boot; do
    if [ -d "$ROOT/$d" ]; then
        real_d=$(cd "$ROOT/$d" && pwd -P)
        case "$real_d" in
            "$ROOT"|"$ROOT"/*) ;;
            *) fail "ROOT/$d 指向 ROOT 外：$real_d" "检查镜像里的符号链接，不要继续。" ;;
        esac
    fi
done

if [ -n "${STUDIO_VERSION:-}" ]; then
    :
else
    STUDIO_VERSION=
    VERSION_FILE=$STUDIO/studio/__init__.py
    if [ -f "$VERSION_FILE" ]; then
        parsed=$(awk -F'"' '/^[ \t]*__version__[ \t]*=/ {k=$1; sub(/[ \t]*=.*$/,"",k); gsub(/[ \t]/,"",k); if (k=="__version__") {print $2; exit}}' "$VERSION_FILE")
        if [ -n "$parsed" ]; then
            STUDIO_VERSION=$parsed
        fi
    fi
    if [ -z "$STUDIO_VERSION" ]; then
        STUDIO_VERSION="未知"
    fi
fi

WORK=$(mktemp -d "${TMPDIR:-/tmp}/microduck-apply.XXXXXX") || fail "无法创建临时目录" "检查 TMPDIR 和磁盘空间。"
DEL_LOG=$WORK/deleted.txt
ADD_LOG=$WORK/added.txt
SSH_WARN=$WORK/ssh-warnings.txt
DELETE_LIST=$WORK/delete.list
: > "$DEL_LOG"
: > "$ADD_LOG"
: > "$SSH_WARN"
: > "$DELETE_LIST"
SYSTEMD=$ROOT/etc/systemd/system
STAGE=$WORK/payload
mkdir -p "$STAGE"

STEP="检查 overlay、Studio 的换行符"
echo "    检查 overlay、Studio/deploy 和 run.py 是否含 CR（Windows 换行）"
: > "$WORK/crlf.txt"
check_crlf_paths
if [ -s "$WORK/crlf.txt" ]; then
    echo "    这些文件含 CR（Windows 换行），装进镜像后脚本会跑不起来："
    sed 's/^/      - /' "$WORK/crlf.txt"
    fail "overlay 或 Studio 里有 CRLF 换行的文本文件" "用 git config core.autocrlf false 重新 clone，或者对这些文件运行 dos2unix。"
fi

if [ -z "$PAYLOAD_SHA256" ]; then
    PAYLOAD_SHA256=$(sha256_of "$PAYLOAD") || fail "无法计算 payload 的 sha256：$PAYLOAD" "检查文件权限和磁盘。"
fi

STEP="解包 payload 并逐个核对 sha256"
echo "==> [1/9] 解包 payload 并逐个核对 sha256"
PAYLOAD_META=$WORK/payload-meta.tsv
: > "$PAYLOAD_META"
for rel in etc/robot/feetech-ft5/robotd.toml etc/robot/feetech-ft5/robotd.saved.toml; do
    if [ -f "$ROOT/$rel" ]; then
        meta=$(stat -c '%u %g %a' "$ROOT/$rel") || fail "读取底图文件属性失败：$ROOT/$rel" "检查 ROOT 是否可读。"
    else
        meta='0 0 644'
    fi
    printf '%s\t%s\n' "$rel" "$meta" >> "$PAYLOAD_META"
done

tar -xzf "$PAYLOAD" -C "$STAGE" --numeric-owner || fail "payload 解包失败" "检查 tgz 是否完整、磁盘空间是否足够。"
[ -f "$STAGE/payload.json" ] || fail "payload 里没有 payload.json" "用 capture-payload.sh 重新生成。"
manifest_to_tsv "$STAGE/payload.json" "$WORK/files.tsv"
verify_manifest "$STAGE" "临时解包"

if find "$STAGE" -type l -print | grep -q .; then
    echo "    payload 里的符号链接："
    find "$STAGE" -type l -print | sed 's/^/      - /'
    fail "payload 里有符号链接" "重新生成 payload；镜像不接受符号链接。"
fi
if find "$STAGE" ! -type d ! -type f ! -path "$STAGE" -print | grep -q .; then
    echo "    payload 里的非常规文件："
    find "$STAGE" ! -type d ! -type f ! -path "$STAGE" -print | sed 's/^/      - /'
    fail "payload 里有非常规文件" "重新生成 payload。"
fi
(
    cd "$STAGE"
    find . -type f ! -name payload.json | sed 's|^\./||' | sort
) > "$WORK/stage-files.txt"
awk -F'\t' '{print $1}' "$WORK/files.tsv" | sort -u > "$WORK/manifest-files.txt"
comm -13 "$WORK/manifest-files.txt" "$WORK/stage-files.txt" > "$WORK/extra-files.txt" || true
if [ -s "$WORK/extra-files.txt" ]; then
    echo "    payload 里清单外的文件："
    sed 's/^/      - /' "$WORK/extra-files.txt"
    fail "payload 里有清单外的文件" "不要手工往 tgz 里加东西；用 capture-payload.sh 重新生成。"
fi

while IFS=$(printf '\t') read -r rel _expected _size; do
    [ -n "$rel" ] || continue
    safe_dir "$ROOT/$(dirname "$rel")"
done < "$WORK/files.tsv"

tar -cf "$WORK/payload.tar" -C "$STAGE" --numeric-owner --exclude='./payload.json' . || fail "重新打包 payload 失败" "检查临时目录空间。"
( cd "$ROOT" && find . -type d ) | sort > "$WORK/dirs-before.txt"
tar -C "$ROOT" --no-overwrite-dir --numeric-owner -xpf "$WORK/payload.tar" || fail "把 payload 解到 ROOT 失败" "检查 ROOT 是否可写、磁盘空间是否足够。"
( cd "$ROOT" && find . -type d ) | sort > "$WORK/dirs-after.txt"
comm -13 "$WORK/dirs-before.txt" "$WORK/dirs-after.txt" > "$WORK/dirs-new.txt" || true
while IFS= read -r d; do
    [ -n "$d" ] || continue
    chmod 0755 "$ROOT/$d" || fail "无法设置新目录权限：$ROOT/$d" "检查 ROOT 是否可写。"
done < "$WORK/dirs-new.txt"
while IFS=$(printf '\t') read -r rel meta; do
    [ -n "$rel" ] || continue
    set -- $meta
    [ "$#" -eq 3 ] || fail "底图文件属性格式不对：$rel（$meta）" "检查 stat 输出。"
    uid=$1 gid=$2 mode=$3
    dst=$ROOT/$rel
    [ -f "$dst" ] || fail "payload 安装后缺少文件：$dst" "检查 payload 是否完整。"
    chown "$uid:$gid" "$dst" || fail "无法设置属主：$dst（$uid:$gid）" "检查 ROOT 文件系统是否支持 chown。"
    chmod "$mode" "$dst" || fail "无法设置权限：$dst（$mode）" "检查 ROOT 文件系统权限。"
done < "$PAYLOAD_META"
verify_manifest "$ROOT" "ROOT"
STEP="生成出厂标定模式 drop-in"
write_commissioning_dropin
STEP="解包 payload 并逐个核对 sha256"
cp -f "$STAGE/payload.json" "$WORK/payload.json"
ROBOTD_SHA=$(awk -F'"' '/"robotd_sha256"/ {for (i=1;i<=NF;i++) if ($i=="robotd_sha256") {print $(i+2); exit}}' "$WORK/payload.json")
if [ -z "$ROBOTD_SHA" ] && [ -f "$ROOT/opt/robot/feetech-ft5-r5/robotd" ]; then
    ROBOTD_SHA=$(sha256_of "$ROOT/opt/robot/feetech-ft5-r5/robotd")
fi
echo "    payload 文件已放进 ROOT，sha256 全部一致。"

STEP="删除另一个作者的服务和名字"
echo "==> [2/9] 删除另一个作者的服务和名字"
purge_unit microduck-setup.service
queue_delete "$SYSTEMD/microduck-setup.service.wants"
queue_delete "$ROOT/usr/local/libexec/microduck-setup-portal.py"
queue_delete "$ROOT/usr/local/libexec/__pycache__"
queue_delete "$ROOT/usr/local/sbin/microduck-public-image"
queue_delete "$ROOT/etc/NetworkManager/system-connections/Microduck-Setup.nmconnection"

purge_unit gamesir-xboxd.service
queue_delete "$ROOT/usr/local/sbin/gamesir-xboxd"
queue_delete "$ROOT/usr/local/sbin/gamesir-xbox-status"
queue_delete "$ROOT/etc/udev/rules.d/70-gamesir-xbox-bridge.rules"
queue_delete "$ROOT/etc/modules-load.d/gamesir-xbox.conf"

queue_delete "$ROOT/etc/systemd/system/alsa-restore.service.d/microduck-audio.conf"
queue_delete "$ROOT/etc/systemd/system/alsa-state.service.d/microduck-audio.conf"
queue_delete "$ROOT/etc/sudoers.d/microduck-alsa-volume"
ASOUND_STATE=$ROOT/var/lib/alsa/asound.state
if [ -e "$ASOUND_STATE" ] || [ -L "$ASOUND_STATE" ]; then
    echo "    底图鸭子留下的 ALSA 混音状态（开机时会被 alsa-restore 用来盖掉 aic3104-init 的音量）：$(rel_path "$ASOUND_STATE")"
    cat "$ASOUND_STATE" 2>/dev/null | sed 's/^/      /' || true
    remove_path "$ASOUND_STATE"
fi

queue_delete "$ROOT/etc/microduck-v112-image.txt"
queue_delete "$ROOT/home/radxa/microduck-public-image.sh"
queue_delete "$ROOT/home/radxa/microduck-public-image-R5.sh"

STEP="收紧更新器信任"
echo "    收紧更新器信任（只信任官方 release 签名）"
set_updater_dev_keys_false "$ROOT/etc/robot/updater.toml"
prune_dev_keys "$ROOT/etc/robot/trusted_keys"
STEP="删除另一个作者的服务和名字"

if [ -d "$SYSTEMD/padd.service.d" ]; then
    find "$SYSTEMD/padd.service.d" -mindepth 1 -maxdepth 1 -print > "$WORK/padd-dropins.txt" || true
    while IFS= read -r p; do
        [ -n "$p" ] || continue
        base=$(basename "$p")
        case "$base" in
            zzzz-replica-padd.conf|zzzz-replica-netpad.conf) ;;
            *) printf '%s\n' "$p" >> "$DELETE_LIST" ;;
        esac
    done < "$WORK/padd-dropins.txt"
fi

# 另一个作者留在 opt 目录里的东西（白名单之外）。
OPT=$ROOT/opt/robot/feetech-ft5-r5
if [ -d "$OPT" ]; then
    for entry in "$OPT"/* "$OPT"/.[!.]* "$OPT"/..?*; do
        if [ ! -e "$entry" ] && [ ! -L "$entry" ]; then
            continue
        fi
        base=$(basename "$entry")
        case "$base" in
            robotd|padd|mediad|onnxruntime|models|roller|padd.service|padd-sysusers.conf|LICENSE)
                ;;
            *) printf '%s\n' "$entry" >> "$DELETE_LIST" ;;
        esac
    done
    # models/、roller/、onnxruntime/ 以 payload 为准：底图里多出来的（训练归档、没用上的旧模型）都删。
    LC_ALL=C sort -u "$WORK/manifest-files.txt" > "$WORK/manifest-files.C.txt"
    for sub in models roller onnxruntime; do
        [ -d "$OPT/$sub" ] || continue
        ( cd "$ROOT" && find "opt/robot/feetech-ft5-r5/$sub" ! -type d -print ) | LC_ALL=C sort -u > "$WORK/opt-$sub.txt"
        LC_ALL=C comm -23 "$WORK/opt-$sub.txt" "$WORK/manifest-files.C.txt" | sed "s|^|$ROOT/|" >> "$DELETE_LIST"
    done
    # 模型作者导出工具生成的 使用说明.md 和图里的实际情况不符；payload 里有也删。
    for sub in models roller; do
        [ -d "$OPT/$sub" ] || continue
        find "$OPT/$sub" -type f -name '使用说明.md' -print >> "$DELETE_LIST" || true
    done
fi

sort -u "$DELETE_LIST" -o "$DELETE_LIST"
if [ -s "$DELETE_LIST" ]; then
    echo "    将删除以下 $(wc -l < "$DELETE_LIST" | tr -d '[:space:]') 项："
    sed 's/^/      - /' "$DELETE_LIST"
    while IFS= read -r p; do
        remove_path "$p"
    done < "$DELETE_LIST"
else
    echo "    没有找到需要删除的旧服务和文件。"
fi
for sub in models roller onnxruntime; do
    [ -d "$OPT/$sub" ] || continue
    # 一层层往上删，直到没有空目录。
    while :; do
        find "$OPT/$sub" -mindepth 1 -type d -empty -print > "$WORK/opt-empty.txt" || true
        [ -s "$WORK/opt-empty.txt" ] || break
        while IFS= read -r d; do
            [ -n "$d" ] || continue
            echo "  删除空目录 $d"
            add_del "$d"
            rmdir "$d" || fail "删除空目录失败：$d" "检查权限；ROOT 可能只读。"
        done < "$WORK/opt-empty.txt"
    done
done
for d in "$SYSTEMD/alsa-restore.service.d" "$SYSTEMD/alsa-state.service.d"; do
    if [ -d "$d" ] && [ -z "$(ls -A "$d" 2>/dev/null)" ]; then
        echo "  删除空目录 $d"
        add_del "$d"
        rmdir "$d" || true
    fi
done

EXTLINUX=$ROOT/boot/extlinux/extlinux.conf
if [ -f "$EXTLINUX" ]; then
    if grep -q '^menu title' "$EXTLINUX"; then
        safe_file "$EXTLINUX"
        awk 'BEGIN { done=0 }
             /^menu title/ && !done { print "menu title Microduck (Radxa U-Boot)"; done=1; next }
             { print }' "$EXTLINUX" > "$WORK/extlinux.new"
        cat "$WORK/extlinux.new" > "$EXTLINUX" || fail "无法改写 extlinux.conf" "检查权限。"
        echo "  已改 extlinux.conf 的第一行菜单标题：Microduck (Radxa U-Boot)"
        add_add "$EXTLINUX"
    else
        echo "  提示：extlinux.conf 里没有以 menu title 开头的行，菜单标题未改。"
    fi
fi

STEP="安装 Studio 和 root 小助手"
echo "==> [3/9] 安装 Studio 和 root 小助手"
RADXA_LINE=$(awk -F: '$1=="radxa" {print $3 ":" $4; exit}' "$ROOT/etc/passwd")
[ -n "$RADXA_LINE" ] || fail "ROOT/etc/passwd 里没有 radxa 用户" "确认底图是 R1；Studio 需要 radxa 的 uid/gid。"
rewrite_radxa_gecos "$ROOT/etc/passwd"
rewrite_radxa_gecos "$ROOT/etc/passwd-"

STEP="设置 radxa 默认密码"
SALT=$(openssl rand -hex 8) || fail "无法生成 radxa 密码盐" "检查 openssl 是否可用。"
[ "${#SALT}" -eq 16 ] || fail "生成的密码盐长度不对：$SALT" "检查 openssl rand 的输出。"
RADXA_HASH=$(openssl passwd -6 -salt "$SALT" microduck) || fail "无法生成 radxa 密码哈希" "检查 openssl 版本是否支持 passwd -6。"
case "$RADXA_HASH" in
    '$6$'*) ;;
    *) fail "radxa 密码哈希不是 SHA-512 crypt：$RADXA_HASH" "检查 openssl passwd -6 的输出。" ;;
esac
set_radxa_password_hash "$ROOT/etc/shadow" "$RADXA_HASH"
if [ -e "$ROOT/etc/shadow-" ] || [ -L "$ROOT/etc/shadow-" ]; then
    set_radxa_password_hash "$ROOT/etc/shadow-" "$RADXA_HASH"
else
    echo "    提示：底图没有 etc/shadow-，只改了 etc/shadow。"
fi
STEP="安装 Studio 和 root 小助手"

STUDIO_DEST=$ROOT/home/radxa/studio-dev
if [ -e "$STUDIO_DEST" ] || [ -L "$STUDIO_DEST" ]; then
    echo "    清空已有的 $STUDIO_DEST"
    remove_path "$STUDIO_DEST"
fi
safe_dir "$STUDIO_DEST"
STUDIO_TAR=$WORK/studio.tar
STUDIO_MEMBERS=
for rel in run.py README.md LICENSE studio web deps deploy; do
    if [ -e "$STUDIO/$rel" ] || [ -L "$STUDIO/$rel" ]; then
        STUDIO_MEMBERS="$STUDIO_MEMBERS $rel"
    fi
done
[ -f "$STUDIO/run.py" ] || fail "STUDIO_DIR 里缺少 run.py：$STUDIO" "确认指向 software/studio。"
[ -d "$STUDIO/deploy" ] || fail "STUDIO_DIR 里缺少 deploy/：$STUDIO" "确认 Studio 的运行文件完整。"
(
    cd "$STUDIO"
    # 编译缓存不进镜像：旧的 .pyc 可能是改名之前编出来的，扫描会抓到。
    tar -cf "$STUDIO_TAR" --numeric-owner --anchored \
        --exclude='*/__pycache__' --exclude='*.pyc' $STUDIO_MEMBERS
) || fail "打包 STUDIO_DIR 失败" "检查 STUDIO_DIR 是否可读。"
tar -xpf "$STUDIO_TAR" -C "$STUDIO_DEST" --numeric-owner || fail "解包 Studio 失败" "检查 ROOT 磁盘空间。"
find "$STUDIO_DEST" -type d -exec chmod 0755 {} \; || fail "无法设置 Studio 目录权限" "检查 ROOT 是否可写。"
find "$STUDIO_DEST" -type f -exec chmod 0644 {} \; || fail "无法设置 Studio 文件权限" "检查 ROOT 是否可写。"
if ! chown -R "$RADXA_LINE" "$STUDIO_DEST" 2>/dev/null; then
    if [ "$(id -u)" -eq 0 ]; then
        fail "无法把 Studio 的属主改成 radxa($RADXA_LINE)" "确认挂载的 rootfs 支持 chown。"
    fi
    echo "    警告：当前不是 root，未设置 Studio 属主（测试环境可忽略；正式构建会设置成 radxa）。"
fi
add_add "$STUDIO_DEST"

put_file "$STUDIO/deploy/microduck-helper" "$ROOT/usr/local/sbin/microduck-helper" 0755
put_file "$STUDIO/deploy/zzzz-studio-bus.conf" "$SYSTEMD/robotd.service.d/zzzz-studio-bus.conf" 0644
put_file "$STUDIO/deploy/sudoers-microduck-studio" "$ROOT/etc/sudoers.d/microduck-studio" 0440
put_file "$STUDIO/deploy/microduck-studio.service" "$SYSTEMD/microduck-studio.service" 0644

if command -v visudo >/dev/null 2>&1; then
    if visudo -cf "$ROOT/etc/sudoers.d/microduck-studio" >/dev/null 2>&1; then
        echo "    sudoers 已通过 visudo -cf 校验。"
    else
        remove_path "$ROOT/etc/sudoers.d/microduck-studio"
        fail "sudoers 校验失败：$ROOT/etc/sudoers.d/microduck-studio" "检查 deploy/sudoers-microduck-studio 的内容和权限。"
    fi
else
    echo "    提示：本机没有 visudo，未校验 sudoers；正式构建建议在 WSL/Debian 里装 sudo 后重跑。"
fi

make_enable_link /etc/systemd/system/microduck-studio.service \
    "$SYSTEMD/multi-user.target.wants/microduck-studio.service"

STEP="安装 OVERLAY 并处理 enable.list"
echo "==> [4/9] 安装 OVERLAY 并处理 enable.list"
if find "$OVERLAY" -type l -print | grep -q .; then
    find "$OVERLAY" -type l -print | sed 's/^/      - /'
    fail "OVERLAY_DIR 里有符号链接" "overlay 只放普通文件；删掉链接后重试。"
fi
: > "$WORK/overlay-modes.tsv"
if [ -f "$OVERLAY/modes.list" ]; then
    awk 'NF >= 2 && $1 !~ /^#/ { p=$1; sub(/^\.\//, "", p); print p "\t" $2 }' "$OVERLAY/modes.list" > "$WORK/overlay-modes.tsv"
    while IFS=$(printf '\t') read -r p m; do
        [ -n "$p" ] || continue
        case "$m" in
            [0-7][0-7][0-7]|[0-7][0-7][0-7][0-7]) ;;
            *) fail "modes.list 里的模式不合法：$p $m" "写成三位或四位八进制，例如 0755。" ;;
        esac
    done < "$WORK/overlay-modes.tsv"
fi
(
    cd "$OVERLAY"
    find . -type f ! -path './enable.list' ! -path './modes.list' | sed 's|^\./||' | sort
) > "$WORK/overlay-files.txt"
while IFS= read -r rel; do
    [ -n "$rel" ] || continue
    mode=$(awk -F'\t' -v r="$rel" '$1==r {print $2; exit}' "$WORK/overlay-modes.tsv")
    [ -n "$mode" ] || mode=0644
    dst=$ROOT/$rel
    safe_file "$dst"
    if [ "$(id -u)" -eq 0 ]; then
        install -D -m "$mode" -o 0 -g 0 "$OVERLAY/$rel" "$dst" || fail "安装 overlay 文件失败：$dst" "检查权限和空间。"
    else
        install -D -m "$mode" "$OVERLAY/$rel" "$dst" || fail "安装 overlay 文件失败：$dst" "检查权限和空间。"
    fi
    add_add "$dst"
done < "$WORK/overlay-files.txt"

if [ -f "$OVERLAY/enable.list" ]; then
    while IFS= read -r raw || [ -n "$raw" ]; do
        line=$(printf '%s' "$raw" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
        case "$line" in
            ""|\#*) continue ;;
        esac
        unit=$line
        unit=${unit#/}
        unit=${unit#etc/systemd/system/}
        case "$unit" in
            */*)
                echo "    警告：enable.list 里不是单元名，跳过：$line"
                continue
                ;;
        esac
        if [ -f "$SYSTEMD/$unit" ]; then
            target=/etc/systemd/system/$unit
        elif [ -f "$ROOT/usr/lib/systemd/system/$unit" ]; then
            target=/usr/lib/systemd/system/$unit
        else
            fail "enable.list 指定的单元不存在：$unit" "检查 OVERLAY_DIR 里的实际路径。"
        fi
        make_enable_link "$target" "$SYSTEMD/multi-user.target.wants/$unit"
        echo "    已 enable：$unit"
    done < "$OVERLAY/enable.list"
else
    echo "    OVERLAY 没有 enable.list。"
fi

# sshd 别名：systemd 里写成镜像内的绝对路径，不是逃逸。
if [ -f "$ROOT/usr/lib/systemd/system/ssh.service" ]; then
    make_enable_link /usr/lib/systemd/system/ssh.service "$SYSTEMD/sshd.service"
fi

STEP="检查 sshd 配置有没有重新打开安全项"
echo "==> [5/9] 检查 sshd 配置有没有重新打开安全项"
ssh_audit
if [ -s "$SSH_WARN" ]; then
    echo "    警告：以下配置可能重新打开了密码登录或 root 登录；没有自动删除："
    sed 's/^/      /' "$SSH_WARN"
else
    echo "    未发现重新打开 PasswordAuthentication / PermitRootLogin / KbdInteractiveAuthentication 的配置。"
fi

STEP="清理机器身份、日志、历史记录和临时文件"
echo "==> [6/9] 清理机器身份、日志、历史记录和临时文件"
safe_file "$ROOT/etc/machine-id"
: > "$ROOT/etc/machine-id"
chmod 0644 "$ROOT/etc/machine-id"
echo "  清空 $ROOT/etc/machine-id"
add_add "$ROOT/etc/machine-id"
remove_path "$ROOT/var/lib/dbus/machine-id"

if [ -d "$ROOT/etc/ssh" ]; then
    find "$ROOT/etc/ssh" -maxdepth 1 -name 'ssh_host_*' -print > "$WORK/ssh-host-keys.txt" || true
    if [ -s "$WORK/ssh-host-keys.txt" ]; then
        echo "    将删除 SSH 主机密钥："
        sed 's/^/      - /' "$WORK/ssh-host-keys.txt"
        while IFS= read -r p; do
            remove_path "$p"
        done < "$WORK/ssh-host-keys.txt"
    fi
fi

for f in root/.ssh/authorized_keys root/.ssh/known_hosts home/radxa/.ssh/authorized_keys home/radxa/.ssh/known_hosts; do
    remove_path "$ROOT/$f"
done

# 只给自己的验收卡用：DEV_AUTHORIZED_KEY（一行 SSH 公钥）写进 radxa 的 authorized_keys，
# 测出问题能直接远程改，不用重烧卡。正式发布的镜像不要设：那等于给所有用户的鸭子留后门。
DEV_KEY_NOTE=
if [ -n "${DEV_AUTHORIZED_KEY:-}" ]; then
    case "$DEV_AUTHORIZED_KEY" in
        "ssh-ed25519 "*|"ssh-rsa "*|"ecdsa-sha2-"*) ;;
        *) fail "DEV_AUTHORIZED_KEY 不像一行 SSH 公钥" "给一行 ssh-ed25519 开头的公钥（.pub 文件的内容）。" ;;
    esac
    case "$DEV_AUTHORIZED_KEY" in
        *"
"*) fail "DEV_AUTHORIZED_KEY 只能是一行" "只给一把公钥。" ;;
    esac
    RADXA_IDS=$(awk -F: '$1=="radxa" {print $3":"$4; exit}' "$ROOT/etc/passwd")
    [ -n "$RADXA_IDS" ] || fail "ROOT/etc/passwd 里没有 radxa" "确认底图。"
    SSH_DIR=$ROOT/home/radxa/.ssh
    safe_dir "$SSH_DIR"
    safe_file "$SSH_DIR/authorized_keys"
    printf '%s\n' "$DEV_AUTHORIZED_KEY" > "$SSH_DIR/authorized_keys" || fail "写不进 $SSH_DIR/authorized_keys" "检查权限。"
    chmod 0700 "$SSH_DIR" && chmod 0600 "$SSH_DIR/authorized_keys" || fail "设不了 .ssh 的权限" "检查 ROOT 文件系统。"
    if [ "$(id -u)" -eq 0 ]; then
        chown "$RADXA_IDS" "$SSH_DIR" "$SSH_DIR/authorized_keys" || fail "设不了 .ssh 的属主 $RADXA_IDS" "检查 ROOT 文件系统。"
    fi
    add_add "$SSH_DIR/authorized_keys"
    DEV_KEY_NOTE="验收卡：radxa 带了一把开发用 SSH 公钥（${DEV_AUTHORIZED_KEY##* }），正式发布的镜像不带。"
    echo "    $DEV_KEY_NOTE"
fi

: > "$WORK/official-install-residue.txt"
if [ -d "$ROOT/home/radxa" ]; then
    find "$ROOT/home/radxa" -mindepth 1 -maxdepth 1 \
        \( -name 'microduck-official-init' -o -name 'microduck-official-install-*' \) \
        -print | LC_ALL=C sort > "$WORK/official-install-residue.txt" || true
fi
print_list "将删除官方守护进程的安装残留" "$WORK/official-install-residue.txt"
while IFS= read -r p; do
    [ -n "$p" ] || continue
    remove_path "$p"
done < "$WORK/official-install-residue.txt"

clean_log_dir "$ROOT/var/log"
clean_log_dir "$ROOT/var/log.hdd"

remove_path "$ROOT/var/lib/systemd/random-seed"
for d in "$ROOT/var/lib/bluetooth" "$ROOT/var/lib/NetworkManager"; do
    if [ -d "$d" ]; then
        find "$d" -mindepth 1 -maxdepth 1 -print > "$WORK/state-clean.txt" || true
        if [ -s "$WORK/state-clean.txt" ]; then
            echo "    清空 $d："
            sed 's/^/      - /' "$WORK/state-clean.txt"
            while IFS= read -r p; do
                remove_path "$p"
            done < "$WORK/state-clean.txt"
        fi
    fi
done

for home in "$ROOT/root" "$ROOT/home/radxa"; do
    [ -d "$home" ] || continue
    for rel in .python_history .viminfo .lesshst .wget-hsts .cache; do
        remove_path "$home/$rel"
    done
    for p in "$home/.local/share/recently-used"*; do
        if [ -e "$p" ] || [ -L "$p" ]; then
            remove_path "$p"
        fi
    done
done

remove_path "$ROOT/root/.bash_history"
remove_path "$ROOT/home/radxa/.bash_history"
remove_path "$ROOT/var/lib/microduck-helper"
if [ -d "$STUDIO_DEST" ]; then
    for p in "$STUDIO_DEST"/state* "$STUDIO_DEST"/logs*; do
        if [ -e "$p" ] || [ -L "$p" ]; then
            remove_path "$p"
        fi
    done
fi

NM_DIR=$ROOT/etc/NetworkManager/system-connections
if [ -d "$NM_DIR" ]; then
    find "$NM_DIR" -mindepth 1 -maxdepth 1 -print > "$WORK/nm.txt" || true
    while IFS= read -r p; do
        [ -n "$p" ] || continue
        base=$(basename "$p")
        overlay_nm=$OVERLAY/etc/NetworkManager/system-connections/$base
        if [ -e "$overlay_nm" ] || [ -L "$overlay_nm" ]; then
            echo "    保留 OVERLAY 带来的 NM 连接：$base"
        else
            remove_path "$p"
        fi
    done < "$WORK/nm.txt"
fi

if [ -d "$ROOT/tmp" ]; then
    find "$ROOT/tmp" -mindepth 1 -maxdepth 1 -print > "$WORK/tmp.txt" || true
    if [ -s "$WORK/tmp.txt" ]; then
        echo "    清空 $ROOT/tmp"
        while IFS= read -r p; do
            remove_path "$p"
        done < "$WORK/tmp.txt"
    fi
fi
if [ -d "$ROOT/var/cache/apt/archives" ]; then
    find "$ROOT/var/cache/apt/archives" -maxdepth 1 -type f -name '*.deb' -print > "$WORK/debs.txt" || true
    while IFS= read -r p; do
        [ -n "$p" ] || continue
        remove_path "$p"
    done < "$WORK/debs.txt"
fi

HWCLOCK=$ROOT/etc/fake-hwclock.data
HWCLOCK_EPOCH=0
if [ -f "$HWCLOCK" ] && grep -qE '^[0-9]+$' "$HWCLOCK" 2>/dev/null; then
    HWCLOCK_EPOCH=1
fi
if [ "$HWCLOCK_EPOCH" -eq 1 ]; then
    BUILD_TIME=$(date -u '+%s')
else
    BUILD_TIME=$(date -u '+%Y-%m-%d %H:%M:%S')
fi
safe_file "$HWCLOCK"
printf '%s\n' "$BUILD_TIME" > "$HWCLOCK" || fail "无法写入 etc/fake-hwclock.data" "检查权限。"
chmod 0644 "$HWCLOCK"
echo "  etc/fake-hwclock.data 写入构建时间（UTC）：$BUILD_TIME"
add_add "$HWCLOCK"

STEP="安装出厂标定并清理旧的标定文件"
echo "==> [7/9] 安装出厂标定并清理旧的标定文件"
[ -f "$CAL_SRC" ] || fail "缺少标定源文件：$CAL_SRC" "确认 software/image/calib/hd1910-calibration.option-a.toml 存在。"
CAL_DIR=$ROOT/etc/robot/feetech-ft5
CAL_ACTIVE=$CAL_DIR/hd1910-calibration.toml
safe_dir "$CAL_DIR"
find "$CAL_DIR" -maxdepth 1 \( -name 'hd1910-calibration.*.toml' -o -name '*.bak-*' -o -name '*.previous' -o -name '*.tmp.*' \) -print | sort > "$WORK/cal-stale.txt" || true
print_list "将删除旧的标定文件和备份" "$WORK/cal-stale.txt"
while IFS= read -r p; do
    [ -n "$p" ] || continue
    remove_path "$p"
done < "$WORK/cal-stale.txt"

ROBOT_GID=$(awk -F: '$1=="robot" {print $3; exit}' "$ROOT/etc/group")
[ -n "$ROBOT_GID" ] || fail "ROOT/etc/group 里没有 robot 组" "确认底图里 robotd 的 robot 组存在。"

safe_file "$CAL_ACTIVE"
cp -f "$CAL_SRC" "$CAL_ACTIVE" || fail "无法写入标定文件：$CAL_ACTIVE" "检查权限和空间。"
chmod 0644 "$CAL_ACTIVE"
if [ "$(id -u)" -eq 0 ]; then
    chown 0:"$ROBOT_GID" "$CAL_ACTIVE" || fail "无法设置标定文件属主 root:robot($ROBOT_GID)" "检查挂载的 rootfs。"
fi
add_add "$CAL_ACTIVE"
echo "    已安装出厂标定：$CAL_ACTIVE（root:robot，0644）"

CAL_COUNT=$(find "$CAL_DIR" -maxdepth 1 -name 'hd1910-calibration*.toml' -print | wc -l | tr -d '[:space:]')
[ "$CAL_COUNT" = "1" ] || fail "标定目录里还剩 $CAL_COUNT 个 hd1910-calibration*.toml" "清理旧标定文件后重试。"

"$PYTHON" - "$CAL_ACTIVE" <<'PY' || fail "标定文件自检失败：$CAL_ACTIVE" "不要继续；检查 calib/hd1910-calibration.option-a.toml。"
import sys
import tomllib

path = sys.argv[1]
with open(path, 'rb') as fh:
    data = tomllib.load(fh)

expected = [
    ('left_hip_yaw', 20), ('left_hip_roll', 21), ('left_hip_pitch', 22),
    ('left_knee', 23), ('left_ankle', 24),
    ('neck_pitch', 30), ('head_pitch', 31), ('head_yaw', 32),
    ('head_roll', 33), ('mouth', 34),
    ('right_hip_yaw', 10), ('right_hip_roll', 11), ('right_hip_pitch', 12),
    ('right_knee', 13), ('right_ankle', 14),
]
errors = []
top = set(data)
want_top = {'motion_enabled', 'imu_mount_verified', 'imu_mount_quat',
            'units_verified', 'current_ma_per_count', 'joints'}
if top != want_top:
    errors.append(f'顶层键不对：多出 {sorted(top - want_top)}，缺少 {sorted(want_top - top)}')
if data.get('motion_enabled') is not False:
    errors.append('motion_enabled 必须是 false')
if data.get('imu_mount_verified') is not False:
    errors.append('imu_mount_verified 必须是 false')
if data.get('units_verified') is not False:
    errors.append('units_verified 必须是 false')
if data.get('current_ma_per_count') != 0.0:
    errors.append('current_ma_per_count 必须是 0.0')
if data.get('imu_mount_quat') != [0.7071067811865476, 0.0, 0.7071067811865476, 0.0]:
    errors.append('imu_mount_quat 必须是官方 +90 度绕 Y 的四元数')
joints = data.get('joints')
if not isinstance(joints, list) or len(joints) != 15:
    errors.append('joints 必须是 15 个')
else:
    for index, (name, joint_id) in enumerate(expected):
        joint = joints[index]
        if set(joint) != {'name', 'id', 'zero_raw', 'direction', 'calibrated'}:
            errors.append(f'{name}: 键不对 {sorted(joint)}')
            continue
        if joint['name'] != name:
            errors.append(f'第 {index + 1} 个关节名是 {joint["name"]}，应为 {name}')
        if joint['id'] != joint_id:
            errors.append(f'{name}: id 是 {joint["id"]}，应为 {joint_id}')
        if joint['zero_raw'] != 2048:
            errors.append(f'{name}: zero_raw 必须是 2048')
        if joint['direction'] != -1:
            errors.append(f'{name}: direction 必须是 -1')
        if joint['calibrated'] is not False:
            errors.append(f'{name}: calibrated 必须是 false')
if errors:
    print('标定文件校验失败：')
    for item in errors:
        print('  - ' + item)
    raise SystemExit(1)
PY

# 官方配置 [media] 底图默认用摄像头；没接摄像头的鸭子上 mediad 起不来，每 5 秒重启一次。
# 镜像默认改成测试画面，接了摄像头的人改回 "camera" 再重启 mediad。
MEDIA_TOML=$ROOT/etc/robot/robotd.toml
[ -f "$MEDIA_TOML" ] || fail "缺少官方配置：$MEDIA_TOML" "确认底图是 R1-V112。"
safe_file "$MEDIA_TOML"
awk 'BEGIN { sec=""; done=0 }
     /^\[/ { sec=$0 }
     sec=="[media]" && /^#? *source *=/ && !done {
         print "source = \"test\"   # 复刻镜像默认：没接摄像头也能起。接了摄像头改成 \"camera\"，再 sudo systemctl restart mediad"
         done=1; next
     }
     { print }
     END { if (!done) exit 3 }' "$MEDIA_TOML" > "$WORK/robotd-media.new" \
    || fail "官方配置的 [media] 里找不到 source 这一行：$MEDIA_TOML" "底图换过了？先看一下 [media] 段。"
# 覆盖内容不换文件，属主和权限照旧。
cat "$WORK/robotd-media.new" > "$MEDIA_TOML" || fail "无法写入 $MEDIA_TOML" "检查权限和空间。"
echo "    官方配置 [media] source 改成 \"test\"（没接摄像头也能起）"

STEP="写模型说明和镜像清单"
echo "==> [8/9] 写模型说明和镜像清单"
DOC_DIR=$ROOT/usr/share/doc/microduck
safe_dir "$DOC_DIR"
safe_file "$DOC_DIR/payload.json"
cp -f "$WORK/payload.json" "$DOC_DIR/payload.json" || fail "无法写 payload.json 副本" "检查权限。"
chmod 0644 "$DOC_DIR/payload.json"
add_add "$DOC_DIR/payload.json"

MODEL_LIST=$WORK/models.txt
{
    printf '%-58s %s\n' '模型目录（相对 /opt/robot/feetech-ft5-r5）' 'policy.onnx sha256'
    printf '%-58s %s\n' '----------------------------------------------------------' '------------------------------------------------'
    found=0
    : > "$WORK/model-paths.txt"
    for base in models roller; do
        [ -d "$ROOT/opt/robot/feetech-ft5-r5/$base" ] || continue
        find "$ROOT/opt/robot/feetech-ft5-r5/$base" -type f -name policy.onnx -print >> "$WORK/model-paths.txt" 2>/dev/null || true
    done
    if [ -s "$WORK/model-paths.txt" ]; then
        sort "$WORK/model-paths.txt" -o "$WORK/model-paths.txt"
        while IFS= read -r p; do
            [ -n "$p" ] || continue
            found=1
            d=$(dirname "$p")
            rel=${d#"$ROOT"/opt/robot/feetech-ft5-r5/}
            hash=$(sha256_of "$p")
            printf '%-58s %s\n' "$rel" "$hash"
        done < "$WORK/model-paths.txt"
    fi
    if [ "$found" -eq 0 ]; then
        echo "（没有找到 policy.onnx）"
    fi
} > "$MODEL_LIST"

MODEL_DOC=$DOC_DIR/模型说明.txt
safe_file "$MODEL_DOC"
if [ -f "$MODEL_DOC" ] && grep -qx '@@MODEL_LIST@@' "$MODEL_DOC"; then
    awk -v listfile="$MODEL_LIST" '
        $0 == "@@MODEL_LIST@@" {
            while ((getline line < listfile) > 0) print line
            close(listfile)
            next
        }
        { print }
    ' "$MODEL_DOC" > "$WORK/model-doc.new"
    cat "$WORK/model-doc.new" > "$MODEL_DOC" || fail "无法改写模型说明" "检查权限。"
else
    {
        echo ""
        echo "模型文件清单（构建时生成）："
        cat "$MODEL_LIST"
    } >> "$MODEL_DOC"
fi
chmod 0644 "$MODEL_DOC"

MANIFEST=$DOC_DIR/镜像清单.txt
TO_CONFIRM=$WORK/to-confirm.txt
: > "$TO_CONFIRM"
CONFIRM_HAS_FILE=0
UNCONFIRMED_ALLOWED=0
for rel in var/lib/robot/config; do
    p=$ROOT/$rel
    if [ -L "$p" ]; then
        printf '%s  符号链接 -> %s（未跟随，不算普通文件）\n' "$rel" "$(readlink "$p")" >> "$TO_CONFIRM"
    elif [ -d "$p" ]; then
        find "$p" -type f -print | LC_ALL=C sort > "$WORK/confirm-files.txt" || true
        if [ -s "$WORK/confirm-files.txt" ]; then
            while IFS= read -r file; do
                [ -n "$file" ] || continue
                file_rel=${file#"$ROOT"/}
                printf '%s  大小=%s  sha256=%s\n' "$file_rel" "$(size_of "$file")" "$(sha256_of "$file")" >> "$TO_CONFIRM"
            done < "$WORK/confirm-files.txt"
            CONFIRM_HAS_FILE=1
        else
            printf '%s  空目录\n' "$rel" >> "$TO_CONFIRM"
        fi
    elif [ -f "$p" ]; then
        printf '%s  大小=%s  sha256=%s\n' "$rel" "$(size_of "$p")" "$(sha256_of "$p")" >> "$TO_CONFIRM"
        CONFIRM_HAS_FILE=1
    elif [ -e "$p" ]; then
        printf '%s  （不是普通文件，未计算 sha256，不算待确认）\n' "$rel" >> "$TO_CONFIRM"
    fi
done
if [ -s "$TO_CONFIRM" ]; then
    echo "    待人工确认（没有删除）："
    sed 's/^/      - /' "$TO_CONFIRM"
fi
if [ "$CONFIRM_HAS_FILE" -eq 1 ]; then
    if [ "$ALLOW_UNCONFIRMED" = "1" ]; then
        UNCONFIRMED_ALLOWED=1
        echo "    注意：待确认里还有普通文件，已按 ALLOW_UNCONFIRMED=1 放行。"
    else
        fail "待人工确认里还有普通文件（见上）" "先确认这些文件是否含个人数据；确认要保留后，用 ALLOW_UNCONFIRMED=1 重新运行放行。"
    fi
fi

safe_file "$MANIFEST"
{
    echo "Microduck 复刻镜像清单"
    echo "================================"
    echo "构建时间（UTC）：$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "底图文件名：$BASE_IMG_NAME"
    echo "底图 sha256：$BASE_IMG_SHA256"
    echo "payload sha256：$PAYLOAD_SHA256"
    echo "镜像脚本版本：$IMAGE_VERSION"
    echo "payload 的 robotd sha256：$ROBOTD_SHA"
    echo "Studio 版本：$STUDIO_VERSION"
    echo "Studio 只装运行文件（不含 tests/dev/docs）。"
    echo "radxa 默认密码 microduck（只能本机或钥匙登录后 sudo 用；第一次登录请用 passwd 改掉）。"
    [ -z "$DEV_KEY_NOTE" ] || echo "$DEV_KEY_NOTE"
    echo ""
    echo "删除（路径相对镜像根目录）"
    echo "--------------------------------"
    if [ -s "$DEL_LOG" ]; then
        sort -u "$DEL_LOG"
    else
        echo "（无）"
    fi
    echo ""
    echo "添加 / 安装（路径相对镜像根目录）"
    echo "--------------------------------"
    if [ -s "$ADD_LOG" ]; then
        sort -u "$ADD_LOG"
    else
        echo "（无）"
    fi
    echo ""
    echo "待人工确认（没有自动删除，请确认是否含个人数据）"
    echo "--------------------------------"
    if [ -s "$TO_CONFIRM" ]; then
        cat "$TO_CONFIRM"
    else
        echo "（无）"
    fi
    if [ "$UNCONFIRMED_ALLOWED" -eq 1 ]; then
        echo "注意：本次构建用 ALLOW_UNCONFIRMED=1 放行了上面的普通文件。"
    fi
    echo ""
    echo "SSH 配置警告（没有自动删除）"
    echo "--------------------------------"
    if [ -s "$SSH_WARN" ]; then
        while IFS= read -r line; do
            case "$line" in
                "$ROOT"/*) line=${line#"$ROOT"/} ;;
            esac
            echo "  $line"
        done < "$SSH_WARN"
    else
        echo "（无）"
    fi
    echo ""
    echo "许可与模型说明：同目录下的 开源许可.txt、第三方.txt、模型说明.txt。"
} > "$MANIFEST" || fail "无法写镜像清单" "检查权限。"
chmod 0644 "$MANIFEST"
if [ "$(id -u)" -eq 0 ]; then
    chown 0:0 "$MANIFEST" || fail "无法设置镜像清单属主" "检查挂载的 rootfs。"
fi

STEP="残留扫描"
NAME_SCAN='kis''sqy'
echo "==> [9/9] 残留扫描（第三方作者名、悬空链接、删除后的引用）"
SCAN_START=$(date +%s)
: > "$WORK/name-leak.txt"
# 整个 ROOT 的普通文件都扫，包括二进制（-a）；不分大小写，也不用 -w。
find "$ROOT" -type f -exec grep -ailE "$NAME_SCAN" {} + >> "$WORK/name-leak.txt" 2>/dev/null || true
LC_ALL=C sort -u "$WORK/name-leak.txt" -o "$WORK/name-leak.txt"
if [ -s "$WORK/name-leak.txt" ]; then
    # 来源清单和模型署名必须保留；只排除这两个固定文件，其他路径继续检查。
    printf '%s\n' "$ROOT/usr/share/doc/microduck/模型说明.txt" \
        "$ROOT/usr/share/doc/microduck/payload.json" > "$WORK/name-scan-attribution.txt"
    grep -v -x -F -f "$WORK/name-scan-attribution.txt" "$WORK/name-leak.txt" > "$WORK/name-leak-left.txt" || true
    if [ -s "$WORK/name-leak-left.txt" ]; then
        echo "    仍然含第三方作者名的文件："
        sed 's/^/      - /' "$WORK/name-leak-left.txt"
        fail "镜像里还有第三方作者名残留" "把上面的文件清掉，或在 apply-rootfs.sh 里加入删除规则。"
    fi
fi
SCAN_END=$(date +%s)
echo "    第三方作者名扫描用时：$((SCAN_END - SCAN_START)) 秒"

: > "$WORK/dangling.txt"
: > "$WORK/link-dirs.txt"
: > "$WORK/link-list.txt"
for d in "$ROOT/etc/systemd/system" "$ROOT/etc/udev/rules.d"; do
    [ -d "$d" ] && printf '%s\n' "$d" >> "$WORK/link-dirs.txt"
done
for d in "$ROOT"/etc/cron*; do
    { [ -e "$d" ] || [ -L "$d" ]; } && printf '%s\n' "$d" >> "$WORK/link-dirs.txt"
done
while IFS= read -r d; do
    [ -n "$d" ] || continue
    find "$d" -type l -print >> "$WORK/link-list.txt" 2>/dev/null || true
done < "$WORK/link-dirs.txt"
if [ -s "$WORK/link-list.txt" ]; then
    while IFS= read -r l; do
        [ -n "$l" ] || continue
        t=$(readlink "$l") || continue
        case "$t" in
            # systemctl mask 的链接，挂载的 rootfs 里没有 /dev/null，但不是悬空
            /dev/null) continue ;;
            /*) target="$ROOT$t" ;;
            *) target="$(dirname "$l")/$t" ;;
        esac
        if [ ! -e "$target" ]; then
            printf '%s -> %s\n' "$l" "$t" >> "$WORK/dangling.txt"
        fi
    done < "$WORK/link-list.txt"
fi
if [ -s "$WORK/dangling.txt" ]; then
    echo "    悬空的链接（按 ROOT 解析）："
    sed 's/^/      - /' "$WORK/dangling.txt"
    fail "有指向不存在目标的悬空链接" "删掉上面的链接，或在删除单元/规则时一起清理。"
fi

: > "$WORK/deleted-refs.txt"
if [ -s "$DELETE_LIST" ]; then
    "$PYTHON" - "$ROOT" "$DELETE_LIST" "$WORK/deleted-refs.txt" <<'PY' || fail "检查删除后的引用失败" "看上面的 Python 报错；修好后重试。"
import sys
from pathlib import Path

root, delete_list, out_path = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
deleted = {line.strip() for line in delete_list.read_text(encoding="utf-8").splitlines() if line.strip()}
# 按镜像里的完整路径查（/opt/.../robotctl 删了，不能把 /usr/local/bin/robotctl 的提法也算上）；
# 只有删掉的是 systemd 单元时才按单元名查（After=xxx.service 写的是名字不是路径）。
UNIT_SUFFIXES = (".service", ".socket", ".timer", ".path", ".target", ".mount")
needles = set()
for p in deleted:
    path = Path(p)
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        continue
    needles.add("/" + rel)
    if path.name.endswith(UNIT_SUFFIXES):
        needles.add(path.name)
bases = needles
dirs = [root / "etc/systemd", root / "etc/udev", root / "etc/sudoers.d"]
dirs += sorted((root / "etc").glob("cron*")) if (root / "etc").is_dir() else []
rows = set()
for directory in dirs:
    if not directory.exists():
        continue
    items = [directory] if directory.is_file() else list(directory.rglob("*"))
    for item in items:
        if not item.is_file() or item.is_symlink():
            continue
        if str(item) in deleted:
            continue
        try:
            data = item.read_bytes()
        except OSError:
            continue
        for base in bases:
            if base.encode("utf-8") in data:
                rows.add("%s 仍引用 %s" % (item.relative_to(root).as_posix(), base))
out_path.write_text("".join(row + "\n" for row in sorted(rows)), encoding="utf-8")
PY
fi
if [ -s "$WORK/deleted-refs.txt" ]; then
    echo "    以下活文件还引用已删除的文件名："
    sed 's/^/      - /' "$WORK/deleted-refs.txt"
    fail "删除清单里的文件名仍在活文件里被引用" "改掉上面的单元/规则，或者把对应文件一起删除后再重试。"
fi

STEP="确认 opt 目录没有训练产物"
echo "    检查 opt/robot/feetech-ft5-r5 下是否还有训练归档和模型说明"
find "$ROOT/opt/robot/feetech-ft5-r5" \
    \( -name export-data.zip -o -name 'training-*.json' -o -name upstream-params \
       -o -name '*.pt' -o -name '*.ckpt' -o -name '使用说明.md' \) \
    -print | LC_ALL=C sort > "$WORK/training-left.txt" 2>/dev/null || true
if [ -s "$WORK/training-left.txt" ]; then
    echo "    还残留这些训练产物："
    sed 's/^/      - /' "$WORK/training-left.txt"
    fail "opt 目录里还有训练产物或模型作者的使用说明" "从 payload 里去掉这些文件，或在 apply-rootfs.sh 里补删除规则。"
fi

STEP="确认官方声卡初始化完整"
echo "    检查官方声卡初始化文件是否完整"
AIC_UNIT=$ROOT/etc/systemd/system/aic3104-init.service
AIC_WANTS=$ROOT/etc/systemd/system/multi-user.target.wants/aic3104-init.service
AIC_SCRIPT=$ROOT/usr/local/bin/aic3104-init.sh
[ -f "$AIC_UNIT" ] || fail "缺少官方声卡初始化单元：$(rel_path "$AIC_UNIT")" "aic3104-init.service 是官方文件，不能删除。"
[ -e "$AIC_WANTS" ] || [ -L "$AIC_WANTS" ] || fail "缺少官方声卡初始化的 enable 链接：$(rel_path "$AIC_WANTS")" "multi-user.target.wants/aic3104-init.service 必须保留。"
[ -f "$AIC_SCRIPT" ] || fail "缺少官方声卡初始化脚本：$(rel_path "$AIC_SCRIPT")" "aic3104-init.sh 是官方文件，不能删除。"
AIC_MODE=$(stat -c '%a' "$AIC_SCRIPT") || fail "无法读取官方声卡初始化脚本权限：$(rel_path "$AIC_SCRIPT")" "检查 ROOT 是否可读。"
if [ "$AIC_MODE" != "755" ]; then
    if [ -n "${MSYS:-}${CYGWIN:-}" ]; then
        echo "    提示：Git Bash 不保留 Unix 权限位（stat 报 $AIC_MODE），测试环境跳过 0755 精确检查。"
    else
        fail "aic3104-init.sh 权限不是 0755：$AIC_MODE" "chmod 0755 $(rel_path "$AIC_SCRIPT") 后重试；官方文件应为可执行。"
    fi
fi
echo "    官方声卡初始化已保留：aic3104-init.service、multi-user.target.wants 链接、aic3104-init.sh（0755）"

if [ "$SOFTWARE_COMPONENTS_REQUESTED" -eq 1 ]; then
    STEP="安装签名软件包与离线语音资源"
    echo "==> 安装与在线升级相同的签名组件包"
    "$PYTHON" "$HERE/software_components.py" --root "$ROOT" \
        --package "$SOFTWARE_UPDATE_PACKAGE" --public-key "$SOFTWARE_UPDATE_PUBLIC_KEY" \
        --engine "$SOFTWARE_UPDATE_ENGINE" --resources "$VOICE_RESOURCES_DIR" \
        --engine-command-json "${SOFTWARE_UPDATE_ENGINE_COMMAND_JSON:-[]}" || \
        fail "签名软件组件安装失败" "查看共享升级器的签名、资源hash或安装报错；不要绕过验证。"
    printf '\n软件升级与本地语音：已使用共享签名组件安装器；详情见 software-components.json。\n' >> "$MANIFEST"
    printf '%s\n' '本地中文坐站语音：Vosk small-cn-0.22（Apache-2.0）；来源 https://alphacephei.com/vosk/models 。' \
        'ARM64/cp313依赖及许可证随 /opt/microduck/resources/asr-deps 一并保留；资源hash见 resources-manifest.json。' \
        '默认R3单击开关；监听默认关闭，开启后识别明确坐下/站起口令，不是自由AI对话。' > "$DOC_DIR/语音资源说明.txt"
    chmod 0644 "$DOC_DIR/语音资源说明.txt"
fi

STEP="完成"
echo "==> 完成"
echo "    ROOT：$ROOT"
echo "    镜像清单：$MANIFEST"
echo "    删除了 $(wc -l < "$DEL_LOG" | tr -d '[:space:]') 条记录，添加了 $(wc -l < "$ADD_LOG" | tr -d '[:space:]') 条记录。"
if [ -s "$SSH_WARN" ]; then
    echo "    注意：有 $(wc -l < "$SSH_WARN" | tr -d '[:space:]') 条 SSH 配置警告，请人工处理。"
fi
