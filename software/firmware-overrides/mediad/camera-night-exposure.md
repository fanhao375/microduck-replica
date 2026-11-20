# mediad 夜间曝光源码补丁

此补丁适用于单传感器 IMX219 的 Linux 软件自动曝光。启用需要自行编译并运行配套的 `mediad`，使用摄像头输入并启用自动曝光。单独更新 Studio 或下载补丁不会改变运行中的摄像头。

已验证的公开基线为 `daemon-v0.15.2`，提交 `8904b65d3628247069f8650d9dada12c67d77fee`，文件 `mediad/src/exposure.rs` 的 Git blob 为 `0a0b7f4597941d1d2670176f11483a5f5d6f2255`。后续的多传感器实现（例如 `daemon-v0.23.0`）按传感器选择曝光限值，此补丁不适用。[固定版本的曝光源码](https://github.com/pollen-robotics/microduck/blob/8904b65d3628247069f8650d9dada12c67d77fee/mediad/src/exposure.rs)

## 应用与测试

以下命令从本复刻仓库根目录开始，在独立目录中检出公开基线。

```sh
patch_file="$(pwd)/software/firmware-overrides/mediad/night-exposure.patch"
git clone --depth 1 --branch daemon-v0.15.2 https://github.com/pollen-robotics/microduck.git mediad-night-build
cd mediad-night-build
git hash-object mediad/src/exposure.rs
git apply --check "$patch_file" && git apply "$patch_file"
git diff -- mediad/src/exposure.rs
```

在 Linux 构建机运行 `cargo test --locked -p mediad --lib exposure::tests`。需要与锁文件兼容的 Rust 工具链、C 构建工具、pkg-config，以及 GStreamer 1.22 或更新版本的开发库（core、app、video、WebRTC）。板端目标为 `aarch64-unknown-linux-gnu`。安装该 Rust 目标、Zig 和 cargo-zigbuild，按上游脚本生成并导出目标 sysroot 的环境变量后，运行 `cargo board --locked -p mediad --bin mediad`。[上游交叉编译说明](https://github.com/pollen-robotics/microduck/blob/8904b65d3628247069f8650d9dada12c67d77fee/scripts/cross-sysroot.sh)

本次在该基线上验证了补丁的正向与反向应用。应用后的文件 blob 为 `46667f8c50eac1a9e5e5732edca21210db620c7c`。使用 Rust 1.99.0 和 GStreamer 1.26.2 sysroot 交叉编译出了 AArch64 release 可执行文件，并在 QEMU 下运行了全部 15 个曝光单元测试，全部通过。

## 参数和真机验收

| 参数 | 原值 | 补丁值 |
| --- | ---: | ---: |
| ISP 输出的目标平均亮度（8 位） | 90 | 115 |
| 硬曝光上限（行） | 1200 | 1700 |
| 数字增益上限 | 16× | 32× |

在此 IMX219 的 1920×1080 模式下，每行约 19.05 µs，1700 行约 32.4 ms。软曝光阈值仍为 600 行，模拟增益上限仍为 11×。本次仅验证了源码、构建和控制算法。暗光可辨识度、噪点、运动拖影、实际帧率和明暗切换仍需目标相机实测。恢复原构建可在此源码树执行 `git apply -R "$patch_file"` 后重新编译。
