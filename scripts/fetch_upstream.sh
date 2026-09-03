#!/usr/bin/env bash
# 拉取上游仓库。本仓库不重复托管上游代码，只放生成结果和脚本。
set -e
mkdir -p upstream && cd upstream

# Microduck 强化学习训练栈 —— MJCF 模型和 47 个 STL 在这里
[ -d microduck_rl ] || git clone --depth 1 https://github.com/pollen-robotics/microduck_rl.git

# Microduck 板载运行时（Rust，绑定 Rockchip RK3566）
[ -d microduck ] || git clone --depth 1 https://github.com/pollen-robotics/microduck.git

# RPI Robot HAT 板 —— 官方开源的 KiCad 工程与生产文件（Gerber / BOM / 贴片坐标）
[ -d elec_RPI_Robot_HAT ] || git clone --depth 1 https://github.com/pollen-robotics/elec_RPI_Robot_HAT.git

cd ..
echo ""
echo "完成。重新生成装配图和 CAD 装配体："
echo "  python scripts/render_assembly.py upstream/microduck_rl assembly-drawings"
echo "  python scripts/export_assembly_stl.py upstream/microduck_rl cad"
