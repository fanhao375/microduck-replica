# 原版零位参考查看器

此目录复用现有 Studio 的 CAD 显示模型、材质、关节变换、法线与编号引线。模型固定为十五关节 `0 rad`，重力为 `null`。它只显示原版零位参考，不订阅回读、不连接设备、不发控制命令。

## 页面接入

先以经典脚本加载根目录 `zero-model-data.js`。在模块脚本之前配置 importmap：

```json
{
  "imports": {
    "@duck-zero/three": "./zero-reference/three.module.js",
    "@duck-zero/controls": "./zero-reference/OrbitControls.js",
    "@duck-zero/environment": "./zero-reference/RoomEnvironment.js",
    "@duck-zero/geometry": "./zero-reference/BufferGeometryUtils.js",
    "@duck-zero/renderer": "./zero-reference/duck3d.js",
    "@duck-zero/labels": "./zero-reference/labels.js"
  }
}
```

再加载根目录 ESM `zero-reference-viewer.js`。单文件构建可将这些模块替换为对应 data URL，并保持 alias 依赖关系不变。

页面提供一个 `[data-zero-stage]` 占位节点及以下可选按钮：

- `data-zero-view="front"`、`"side"`、`"back"` 或 `"reset"`。
- `data-zero-labels`：切换原版编号引线，默认关闭。

根渲染前调用 `const retained = P.zeroReference?.retain()`，替换页面 HTML 后调用 `P.zeroReference?.mount(retained)`。查看器会移动旧 stage 至新占位节点，复用实际 canvas、视角、标注及 WebGL 上下文。新页面无 stage 时销毁模型实例、观察器、标注和渲染循环。

控制器生成 `.zero-reference-canvas`、`.zero-reference-status`、`.zero-reference-error`；编号沿用 `.sv-lines`、`.sv-tag`、`.sv-tag.R/L/H`、`.id/.cn/.en`，样式由主原型提供。加载成功才给 stage 增加 `data-zero-ready="true"`；失败时显示重试按钮。

点击模型只调用 devices 页的 `joint-select`，不执行运动。高亮读取 `P.state.devices.expandedJoint`，模型始终保持零位角度，不跟随演示舵机数值。

## 与原文件的差异

- Three.js 模块完整保留，三个 addons 和标签文件只替换 import alias。
- `duck3d.js` 保留原渲染、材质、光照、关节、选中和生命周期；网络/worker 加载替换为内存 base64 解码。
- 顶点仍按原 int16 量化、bbox 和索引恢复；使用原 fallback 的 `toCreasedNormals(g, Math.PI / 6)`。每个网格算完后让出一次事件循环，使加载提示与输入能够响应，不改变模型或法线精度。
- 增加 `side: [90, 8]`，原 front/back 等视角不变。
- `SOURCE-MANIFEST.json` 保存原文件与适配文件 SHA256。`zero-model-data.js` 的 base64 字节与原 `meshes.bin` 完全一致，JSON 值与原 `model.json` 一致。

## 零位定义与来源

原 `web/servo.js` 的 `POSES.zero` 为 `Array(15).fill(0)`；原说明是腿伸直、头摆正、嘴闭上，零位各颗为 2048。零位不同于站姿。左右按模型自身定义，右腿 10–14 红色、左腿 20–24 蓝色、头颈嘴 30–34 黄色。

保留 `model/NOTICE.md`、上游根 LICENSE/NOTICE、Studio Apache-2.0 和 Three MIT 许可。上游根 LICENSE 明确将 Studio 网格列为 CC BY-NC-SA 4.0，原模型 NOTICE/JSON 则写 Apache-2.0；这些不一致的原声明均保留，适配不主张重新授权或消除该差异。

模型原始说明也明确：嘴部 34 铰链是 Studio 添加的估计位置，仅用于显示。此查看器不把该几何标注描述为精确机械标定数据。
