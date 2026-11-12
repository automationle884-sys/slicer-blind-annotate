# slicer-blind-annotate

在 [3D Slicer](https://www.slicer.org/) 里做多模态配准研究的盲法成对标注。[English](README.md)

评价配准方法需要的参考标注，应当**在原图上**、由**没看过配准结果**的人来标。这个工具把这套规程变成一条命令：

- 每个任务左右两个视图，各显示一幅图：三维体积（CT、MR 等），或由帧序列合成的二维投影（DSA、透视、X 线）。只加载配置里列出的图像。
- 每个视图带一种标注：**命名点**、**分割**或**曲线**。
- 两侧都是点时按**对应点对**标：点完一侧的某个名称，自动切到另一侧的同名点；点到另一侧视图的点会被撤销。
- 结果存为 JSON / NIfTI：体积上的点用患者坐标（LPS，mm），投影上的点用像素坐标（列、行，可带帧号）。再次打开同一病例会接着上次的标。
- `status` 命令按标注者统计进度，并给出第二人重标的固定子集。

## 要求

3D Slicer 5.6 及以上（在 5.12 上测试）。不需要扩展或额外的包，工具只有一个 Python 文件。

## 快速开始

```bash
/Applications/Slicer.app/Contents/bin/PythonSlicer examples/make_demo_data.py
/Applications/Slicer.app/Contents/MacOS/Slicer --python-script blind_annotate.py examples/demo_config.json case01 landmarks --annotator A
python3 blind_annotate.py status examples/demo_config.json --annotator A
```

保存用 **Ctrl+S**（Mac 上也是 Ctrl，不是 Cmd）或面板上的 *Save* 按钮。

## 配置与输出

配置格式、投影参数、输出文件说明见英文 [README.md](README.md)，两份示例：

- [examples/demo_config.json](examples/demo_config.json)：配合合成数据的演示。
- [examples/liver_tace_mr_ct_dsa.json](examples/liver_tace_mr_ct_dsa.json)：肝癌 TACE 的 MR/CT/DSA 配准研究里实际用的配置，包括 CT↔MR 标志点、MR↔DSA 动脉分叉点、MR 肿瘤分割与 DSA 染色和肝缘轮廓、MR 肝动脉分割。

## 测试

```bash
Slicer --no-splash --python-script tests/test_in_slicer.py
```

结果写在 `tests/result.txt`。

## 关于盲法

工具只加载配置里列出的路径。请把配准结果、模型预测等派生数据放在这些路径之外，每位标注者用自己的 `--annotator` 代号，输出不会混在一起。

## 许可证

MIT
