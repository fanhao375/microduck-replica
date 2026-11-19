# Microduck 接线图（3D）

可旋转、逐步高亮的接线图：电池 → 香蕉板 → HUB → 腿 / 头 / IMU，摄像头、ToF、扬声器、HAT 各接口。

在线查看：<https://fanhao375.github.io/microduck-replica/wiring3d/>

本地打开（页面要加载模型，不能直接双击 `index.html`）：

```bash
cd docs/wiring3d
python -m http.server 8765
# 浏览器打开 http://127.0.0.1:8765/
```

## 说明

- 鸭子身体是站姿；线的颜色只用来区分；HUB 的前后位置、摄像头和 ToF 小板的外形为示意。
- 舵机接口位置取自 HD-1910 的 STEP 模型；HAT 接口位置取自 HAT 的 PCB 模型；香蕉板、HUB 的板面取自生产文件，外形和接口位置取自 PCB 工程。

## 模型和资料来源

| 内容 | 来源 |
|---|---|
| 鸭子机械模型 | Pollen Robotics Microduck 开源仿真模型（Apache-2.0），转成网页可用的格式 |
| HAT | Pollen Robotics 开源设计（Apache-2.0） |
| 主控 | Radxa ZERO 3W 官方 3D 模型 |
| 舵机 | 飞特 HD-1910-C001 官方 STEP |
| IMU 小板 | 本项目自研 |
| HUB、香蕉板 | 社区开源设计（Apache-2.0） |
| 三维引擎 | three.js（MIT） |
