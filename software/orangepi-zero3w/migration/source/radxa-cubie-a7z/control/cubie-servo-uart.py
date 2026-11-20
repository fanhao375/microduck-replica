#!/usr/bin/env python3
"""A7Z UART0 setup for the HAT. Host registers only; no UART packets are sent."""

import argparse
from contextlib import ExitStack
import json
import mmap
import os
from pathlib import Path
import struct
import subprocess

PORT = Path("/dev/ttyAS0")
ALIAS = Path("/dev/ttyS2")
CLOCK = 0x0400000E  # pll-peri0-480m / 15 = 32 MHz, for the driver's divisor 2
MUX_MASK = 0xFF0
MUX = 0x220  # PB9, PB10 function VALUE 2, not the pin chart's column number


def console_conflicts(cmdline):
    return any(token.split(",", 1)[0] in ("console=ttyAS0", "console=ttyS2")
               or token.startswith("earlycon=") for token in cmdline.split())


def verify_board():
    compatible = Path("/sys/firmware/devicetree/base/compatible").read_bytes().split(b"\0")
    if b"radxa,cubie-a7z" not in compatible:
        raise ValueError("refusing MMIO setup on a board other than Cubie A7Z")
    if console_conflicts(Path("/proc/cmdline").read_text()):
        raise ValueError("UART console/earlycon is enabled; remove it before connecting the HAT")
    for name in ("ttyAS0", "ttyS2"):
        result = subprocess.run(["systemctl", "is-active", "serial-getty@" + name + ".service"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        if result.stdout.strip() in ("active", "activating", "reloading"):
            raise ValueError("serial getty owns " + name)
    if not PORT.is_char_device():
        raise ValueError("/dev/ttyAS0 is not a character device")
    if os.path.lexists(ALIAS) and (not ALIAS.is_symlink() or ALIAS.resolve() != PORT):
        raise ValueError("/dev/ttyS2 already refers to another device; refusing to replace it")


def configure(check=False):
    verify_board()
    if not check:
        state = subprocess.run(["systemctl", "is-active", "robotd.service"],
                               stdout=subprocess.PIPE, text=True, timeout=5).stdout.strip()
        if state == "active":
            raise ValueError("robotd is running; use --check instead of reconfiguring its UART")
    flags = os.O_RDONLY if check else os.O_RDWR | os.O_SYNC
    fd = os.open("/dev/mem", flags)
    try:
        with ExitStack() as stack:
            prot = mmap.PROT_READ | (0 if check else mmap.PROT_WRITE)
            pio = stack.enter_context(mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, prot, offset=0x02000000))
            ccu = stack.enter_context(mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, prot, offset=0x02002000))
            if not check:
                current = struct.unpack_from("<I", pio, 0x104)[0]
                struct.pack_into("<I", pio, 0x104, (current & ~MUX_MASK) | MUX)
                struct.pack_into("<I", ccu, 0x538, CLOCK)
                if not os.path.lexists(ALIAS):
                    ALIAS.symlink_to(PORT)
            mux = struct.unpack_from("<I", pio, 0x104)[0]
            clock = struct.unpack_from("<I", ccu, 0x538)[0]
    finally:
        os.close(fd)
    if mux & MUX_MASK != MUX or clock != CLOCK or not ALIAS.is_symlink():
        raise ValueError("UART pinmux, 32 MHz clock or ttyS2 alias verification failed")
    return {"port": str(PORT), "alias": str(ALIAS), "pinmux": hex(mux),
            "apb_uart": hex(clock), "check_only": check}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="read host state without changing registers")
    print(json.dumps(configure(parser.parse_args().check)))
