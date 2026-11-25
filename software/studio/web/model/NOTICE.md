# 模型来源

`model.json` + `meshes.bin` 由 `../build_model.py` 从 [apirrone/microduck_rl](https://github.com/apirrone/microduck_rl)
的 `src/mjlab_microduck/robot/microduck/robot_allcollisions.xml` 及其 `assets/*.stl` 转换而来（2026-09-17 快照），
该仓库为 Apache License 2.0。网格是 Pollen Robotics 官方 Microduck CAD 导出的。只保留了可见外形，去掉了电路板、轴承等内部件，
顶点做了 int16 量化。

## stills/

`stills/*.webp` 是动作页的缩略图：用上面这只 3D 鸭子，把每个动作模型在仿真里真跑一遍、按实物配色渲染出来的透明底静帧（来自仓库外的设计工程「动作库设计」，跟 `docs/motion-plaza/` 里的预览图同一批）。外形许可同上。
