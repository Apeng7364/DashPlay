# DashPlay「盒子设置」图标来源

- 创建日期：2026-10-11。
- 来源：为 DashPlay 新写的几何绘图脚本 `scripts/generate-box-settings-icon.py`；由 Codex 按本项目需求编写。使用矩形、圆角和两根滑杆表达盒子与设置，没有外部图片、字体、商标或第三方图标输入。
- 许可：与本项目一起按 GPL-3.0 分发；脚本是可编辑源文件。
- 产物：`assets/box-settings.png`，180 × 180、RGB PNG。青绿色底与浅色盒子轮廓；不复用 Apple 设置图标。
- 复现：Python 3 + Pillow（生成时为 12.3.0），运行 `python3 scripts/generate-box-settings-icon.py`。PNG 编码字节可能随 Pillow/zlib 版本变化。
- 集成：`patches/playport-car-viewer.patch` 中的二进制新增资源已替换为此图，目标路径仍是 `server/src/main/resources/f50-settings.png`，避免改变接收端资源加载约定。公开补丁不再包含原来来源未记录的图片。
- 验证：在临时目录顺序应用公开补丁，核对资源与 PNG 的 SHA-256 一致，并检查 180 × 180 图像；尚未部署到盒子或验证真实车机的图标显示。
