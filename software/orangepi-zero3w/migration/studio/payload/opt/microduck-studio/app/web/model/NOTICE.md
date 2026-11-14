# 模型来源

`model.json` + `meshes.bin` 由 `../build_model.py` 从 [apirrone/microduck_rl](https://github.com/apirrone/microduck_rl)
的 `src/mjlab_microduck/robot/microduck/robot_allcollisions.xml` 及其 `assets/*.stl` 转换而来（2026-09-17 快照），
该仓库为 Apache License 2.0。网格是 Pollen Robotics 官方 Microduck CAD 导出的。只保留了可见外形，去掉了电路板、轴承等内部件，
顶点做了 int16 量化。
