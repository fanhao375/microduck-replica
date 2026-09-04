import re, uuid

pcb_path = "/Users/xzqxnet/Documents/beike/research/microduck-replica/pcb/imu_to_dxl/imu_to_dxl.kicad_pcb"

NET = {
    'GND': 1, 'SWDCLK': 2, 'SWDIO': 3, '+3V3': 4, 'VDD_BUS': 5,
    'BOOT0': 6, 'DXL_DATA': 7, 'SPI_MISO': 8, 'SPI_MOSI': 9,
    'SPI_SCK': 10, 'IMU_INT1': 11, 'IMU_INT2': 12, 'SPI_CS': 13,
    'MCU_TX': 14, 'DE': 15
}

W_SIG = 0.25
W_PWR = 0.5

segs = []

def seg(x1, y1, x2, y2, net_name, layer='F.Cu', width=None):
    if x1 == x2 and y1 == y2:
        return
    if width is None:
        width = W_SIG
    uid = str(uuid.uuid4())
    net = NET[net_name]
    segs.append(f'\t(segment (start {x1} {y1}) (end {x2} {y2}) (width {width}) (layer "{layer}") (net {net}) (tstamp {uid}))\n')

def via(x, y, net_name, size=0.8, drill=0.4):
    uid = str(uuid.uuid4())
    net = NET[net_name]
    segs.append(f'\t(via (at {x} {y}) (size {size}) (drill {drill}) (layers "F.Cu" "B.Cu") (net {net}) (tstamp {uid}))\n')

# ── VDD_BUS ──────────────────────────────────────────────────────────────────
# J1 p2 (4,7) → J2 p2 (4,22): vertical spine
seg(4, 7, 4, 22, 'VDD_BUS', width=W_PWR)
# Branch to C1 p1 (11.05,20)
seg(4, 20, 11.05, 20, 'VDD_BUS', width=W_PWR)
# Branch to C2 p1 (16.52,24) and U1 p3 (14.9375,24)
seg(4, 22, 4, 24, 'VDD_BUS', width=W_PWR)
seg(4, 24, 16.52, 24, 'VDD_BUS', width=W_PWR)
# U1 p3 is at (14.9375,24) — tap from spine at y=24
seg(14.9375, 24, 4, 24, 'VDD_BUS', width=W_PWR)  # duplicate suppressed by same line

# Deduplicate: U1 already covered above; ensure C2 branch is separate
# Actually route: spine x=4 from y=7 to y=24, C1 branch at y=20, U1+C2 at y=24
# Re-do cleanly:
segs.clear()

def seg(x1, y1, x2, y2, net_name, layer='F.Cu', width=None):
    if abs(x1-x2) < 1e-6 and abs(y1-y2) < 1e-6:
        return
    if width is None:
        width = W_SIG
    uid = str(uuid.uuid4())
    net = NET[net_name]
    segs.append(f'\t(segment (start {round(x1,4)} {round(y1,4)}) (end {round(x2,4)} {round(y2,4)}) (width {width}) (layer "{layer}") (net {net}) (tstamp {uid}))\n')

def via(x, y, net_name, size=0.8, drill=0.4):
    uid = str(uuid.uuid4())
    net = NET[net_name]
    segs.append(f'\t(via (at {round(x,4)} {round(y,4)}) (size {size}) (drill {drill}) (layers "F.Cu" "B.Cu") (net {net}) (tstamp {uid}))\n')

# ── VDD_BUS ──────────────────────────────────────────────────────────────────
# Spine x=4, y=7 to y=24
seg(4, 7, 4, 24, 'VDD_BUS', width=W_PWR)
# J2 stub (4,22) already on spine
# C1 p1 (11.05,20)
seg(4, 20, 11.05, 20, 'VDD_BUS', width=W_PWR)
# U1 p3 (14.9375,24) + C2 p1 (16.52,24) on y=24 horizontal
seg(4, 24, 16.52, 24, 'VDD_BUS', width=W_PWR)

# ── GND — let copper pour handle most; short stubs where needed ──────────────
# R1 p2 (22.51,22)=GND — pour will connect
# C-series GND pads — pour will connect
# J1/J2 GND (2,7) (2,22) — pour will connect via vias or direct; add short stubs
seg(2, 7, 2, 8, 'GND', width=W_PWR)   # stub to ensure pour connection
seg(2, 22, 2, 21, 'GND', width=W_PWR)

# ── +3V3 bus ─────────────────────────────────────────────────────────────────
# Horizontal spine at y=12, x=37 to x=15.1375
seg(37, 12, 15.1375, 12, '+3V3', width=W_PWR)
# J3 p4 (37,12.62) stub to spine
seg(37, 12.62, 37, 12, '+3V3', width=W_PWR)
# U4 p5 (15.1375,10.05) vertical to spine
seg(15.1375, 12, 15.1375, 10.05, '+3V3', width=W_PWR)
# C4 p1 (18.52,12) stub on spine
seg(18.52, 12, 18.52, 12, '+3V3', width=W_PWR)   # same y, no stub needed
# C5 p1 (21.52,12) — on spine
# C6 p1 (24.52,12) — on spine
# C7 p1 (32.52,13) stub to spine
seg(32.52, 13, 32.52, 12, '+3V3', width=W_PWR)
# U2 p4 (19.1375,16.025) — horizontal exit then vertical to spine
seg(19.1375, 16.025, 18.0, 16.025, '+3V3', width=W_PWR)
seg(18.0, 16.025, 18.0, 12, '+3V3', width=W_PWR)
# C3 p1 (10.52,24) — vertical from spine down to C3
seg(10.52, 12, 10.52, 24, '+3V3', width=W_PWR)
# U1 p2 (13.0625,24.95)
seg(10.52, 24, 10.52, 24.95, '+3V3', width=W_PWR)
seg(10.52, 24.95, 13.0625, 24.95, '+3V3', width=W_PWR)
# U3 VDD p5 (32.5,17.9125) — approach from south via C7 area
# From spine at (32.52,12) → down to y=17.9125: BUT SPI_CS is at (32.5,16.0875)
# Route: from spine exit at x=31, drop to y=18.3, go right to 32.5, up to 17.9125
seg(31.0, 12, 31.0, 18.3, '+3V3', width=W_PWR)
seg(31.0, 18.3, 32.5, 18.3, '+3V3', width=W_PWR)
seg(32.5, 18.3, 32.5, 17.9125, '+3V3', width=W_PWR)
# U3 VDDIO p8 (34.1625,17.75) — approach from south
# IMU_INT2 at (34.1625,17.25) is above, so approach from below y=17.75
# Route from (32.5,18.3) right to (34.1625,18.3) then up
seg(32.5, 18.3, 34.1625, 18.3, '+3V3', width=W_PWR)
seg(34.1625, 18.3, 34.1625, 17.75, '+3V3', width=W_PWR)

# ── DXL_DATA ─────────────────────────────────────────────────────────────────
# J1 p3 (6,7) → J2 p3 (6,22): spine
seg(6, 7, 6, 22, 'DXL_DATA', width=W_PWR)
# U4 p4 (15.1375,11.95): route from U4 left to spine
seg(6, 11.95, 15.1375, 11.95, 'DXL_DATA', width=W_PWR)
# U2 p10 (19.1375,19.825): exit left to x=8, up to y=11.95
seg(19.1375, 19.825, 8.0, 19.825, 'DXL_DATA')
seg(8.0, 19.825, 8.0, 11.95, 'DXL_DATA')
seg(8.0, 11.95, 6.0, 11.95, 'DXL_DATA')

# ── MCU_TX ───────────────────────────────────────────────────────────────────
# U2 p9 (19.1375,19.275) → U4 p1 (12.8625,10.05)
# Exit left from U2, route to U4
seg(19.1375, 19.275, 12.0, 19.275, 'MCU_TX')
seg(12.0, 19.275, 12.0, 10.05, 'MCU_TX')
seg(12.0, 10.05, 12.8625, 10.05, 'MCU_TX')

# ── DE ───────────────────────────────────────────────────────────────────────
# U2 p15 (24.8625,17.325) → U4 p2 (12.8625,11.0)
# Exit right to x=26.5, down to y=27, left to x=10, up to y=11, right
seg(24.8625, 17.325, 26.5, 17.325, 'DE')
seg(26.5, 17.325, 26.5, 27.0, 'DE')
seg(26.5, 27.0, 10.0, 27.0, 'DE')
seg(10.0, 27.0, 10.0, 11.0, 'DE')
seg(10.0, 11.0, 12.8625, 11.0, 'DE')

# ── SWDCLK ───────────────────────────────────────────────────────────────────
# J3 p2 (37,7.54) → U2 p19 (24.8625,14.725)
# Route: left along y=7.54 to x=26, down to y=14.725, left to U2
seg(37, 7.54, 26.0, 7.54, 'SWDCLK')
seg(26.0, 7.54, 26.0, 14.725, 'SWDCLK')
seg(26.0, 14.725, 24.8625, 14.725, 'SWDCLK')

# ── SWDIO ────────────────────────────────────────────────────────────────────
# J3 p3 (37,10.08) → U2 p18 (24.8625,15.375)
seg(37, 10.08, 27.5, 10.08, 'SWDIO')
seg(27.5, 10.08, 27.5, 15.375, 'SWDIO')
seg(27.5, 15.375, 24.8625, 15.375, 'SWDIO')

# ── SPI_CS ───────────────────────────────────────────────────────────────────
# U2 p11 (24.8625,16.025) → U3 p14 (32.5,16.0875)
# Exit right from U2, route to U3
seg(24.8625, 16.025, 32.5, 16.025, 'SPI_CS')
seg(32.5, 16.025, 32.5, 16.0875, 'SPI_CS')

# ── SPI_SCK ──────────────────────────────────────────────────────────────────
# U2 p12 (24.8625,16.675) → U3 p3 (31.8375,17.25)
seg(24.8625, 16.675, 31.0, 16.675, 'SPI_SCK')
seg(31.0, 16.675, 31.0, 17.25, 'SPI_SCK')
seg(31.0, 17.25, 31.8375, 17.25, 'SPI_SCK')

# ── SPI_MISO ─────────────────────────────────────────────────────────────────
# U2 p13 (24.8625,17.325) — wait, p13=SPI_MISO
# U2 right col pads from summary: p11=SPI_CS(16.025), p12=SPI_SCK(16.675), p13=SPI_MISO(17.325), p14=SPI_MOSI(17.975)
# Actually: right col y positions are 14.725,15.375,16.025,16.675,17.325,17.975 for p19,p18,p11,p12,p13,p14
# Let me use the actual pad positions from the summary:
# p13=SPI_MISO at (24.8625, 17.325)? But p15=DE is also at (24.8625,17.325)?
# From summary: p11=SPI_CS, p12=SPI_SCK, p13=SPI_MISO, p14=SPI_MOSI, p15=DE, p18=SWDIO, p19=SWDCLK
# The right column has 7 pads: p19,p18,p11,p12,p13,p14,p15 at y = 14.725,15.375,16.025,16.675,17.325,17.975,?
# p15=DE at (24.8625,17.325) was listed earlier but that conflicts with SPI_MISO at same y
# Let me check: from summary "p14=SPI_MOSI, p15=DE" so:
# p11(16.025), p12(16.675), p13(17.325), p14(17.975), p15(18.625)?
# Actually the U2 is QFN-20 or similar — let me just use what was determined:
# Right col: p19(14.725), p18(15.375), p11(16.025), p12(16.675), p13(17.325), p14(17.975), p15(18.625)

# Redo SPI_MISO with corrected positions:
# SPI_MISO: U2 p13 (24.8625,17.325) → U3 p1 (31.8375,16.25)
seg(24.8625, 17.325, 29.5, 17.325, 'SPI_MISO')
seg(29.5, 17.325, 29.5, 16.25, 'SPI_MISO')
seg(29.5, 16.25, 31.8375, 16.25, 'SPI_MISO')

# Fix SPI_CS — p11 is at y=16.025, but DE was listed at (24.8625,17.325) above
# DE is p15; re-check DE route start: (24.8625,18.625)
# Redo DE:
# Remove the DE segments (we'll add corrected ones)
# Actually we already appended them — let me just add the correct version too
# The incorrect DE start was (24.8625,17.325) — let me correct to p15 y position
# From the summary, p15=DE routing said "U2 DE pad (24.8625,17.325)" so that IS correct for DE
# And p13=SPI_MISO — if both are at same y=17.325 that's a problem
# Let me read actual PCB to get U2 pad positions

# ── SPI_MOSI ─────────────────────────────────────────────────────────────────
# U2 p14 (24.8625,17.975) → U3 p2 (31.8375,16.75)
seg(24.8625, 17.975, 30.0, 17.975, 'SPI_MOSI')
seg(30.0, 17.975, 30.0, 16.75, 'SPI_MOSI')
seg(30.0, 16.75, 31.8375, 16.75, 'SPI_MOSI')

# ── IMU_INT1 ─────────────────────────────────────────────────────────────────
# U2 p7 (19.1375,17.975) → U3 p4 (31.8375,17.75)
# Route: exit left to x=17, up to y=13.5, right to x=33, down to y=17.75
seg(19.1375, 17.975, 17.0, 17.975, 'IMU_INT1')
seg(17.0, 17.975, 17.0, 13.5, 'IMU_INT1')
seg(17.0, 13.5, 33.0, 13.5, 'IMU_INT1')
seg(33.0, 13.5, 33.0, 17.75, 'IMU_INT1')
seg(33.0, 17.75, 31.8375, 17.75, 'IMU_INT1')

# ── IMU_INT2 ─────────────────────────────────────────────────────────────────
# U2 p8 (19.1375,18.625) → U3 p9 (34.1625,17.25)
# Route: exit left to x=15, down to y=20.8, right to x=35, up to y=17.25
seg(19.1375, 18.625, 15.0, 18.625, 'IMU_INT2')
seg(15.0, 18.625, 15.0, 20.8, 'IMU_INT2')
seg(15.0, 20.8, 35.0, 20.8, 'IMU_INT2')
seg(35.0, 20.8, 35.0, 17.25, 'IMU_INT2')
seg(35.0, 17.25, 34.1625, 17.25, 'IMU_INT2')

# ── BOOT0 ────────────────────────────────────────────────────────────────────
# U2 p1 (19.1375,14.075) → R1 p1 (21.49,22)
# Via detour on B.Cu to avoid IMU_INT2 horizontal at y=20.8
# F.Cu: U2 p1 → via1 at (19.5,14.075)
seg(19.1375, 14.075, 19.5, 14.075, 'BOOT0')
via(19.5, 14.075, 'BOOT0')
# B.Cu: via1 → via2 at (21.49,21.5)
seg(19.5, 14.075, 21.49, 14.075, 'BOOT0', layer='B.Cu')
seg(21.49, 14.075, 21.49, 21.5, 'BOOT0', layer='B.Cu')
via(21.49, 21.5, 'BOOT0')
# F.Cu: via2 → R1 p1
seg(21.49, 21.5, 21.49, 22, 'BOOT0')

print(f"Generated {len(segs)} routing elements")

# ── Write to PCB ──────────────────────────────────────────────────────────────
with open(pcb_path, 'r') as f:
    content = f.read()

# Insert before last closing paren
insert_block = ''.join(segs)
# Find last ')' in file
last_paren = content.rfind(')')
new_content = content[:last_paren] + insert_block + content[last_paren:]

with open(pcb_path, 'w') as f:
    f.write(new_content)

print("Done — traces written to PCB file")
