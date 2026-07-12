# AHK/外部调用专用 CLI 接口实现计划

## 背景与目标

用户希望在 AHK 等自动化脚本中，随时调用本程序的截图与 OCR 能力来提取文字、数字，然后由 AHK 根据结果执行后续动作。

为了满足这个极轻量、高性能的需求，我们将剥离 UI 依赖，打造一个**纯命令行接口 (CLI)**，并实现**纯内存截图与识别**，不产生任何硬盘文件。

---

## 实施策略

### 1. 核心接口内存化改造

**[MODIFY] `base.py` (OCR 和 Capture)**
- `BaseOCREngine.recognize` 增加支持传入 `numpy.ndarray` 内存图像格式，不再强制要求 `Path`。
- `ScreenshotResult` 数据类增加 `image_data: numpy.ndarray | None` 字段。

**[MODIFY] `rapid_ocr.py`**
- 修改 `recognize` 方法。如果传入的是 `numpy.ndarray`，直接丢给 `self._engine()` 进行识别，因为 RapidOCR 原生支持 numpy。

**[MODIFY] `mss_capture.py`**
- 在截图逻辑中，将 `mss` 抓取到的图像转为 numpy array (`np.array(shot)`) 并存入 `ScreenshotResult`。
- 如果是不落盘模式，跳过写文件步骤。

### 2. 命令行入口开发

**[NEW] `src/screenvision_sentinel/cli.py`**
编写一个独立的命令行工具，专供脚本调用：
- 使用 `argparse` 接收参数：`--rect <left,top,width,height>` 和 `--engine <engine_name>`。
- 调用 `MssCaptureService` 进行纯内存截图。
- 调用 `create_ocr_engine` 和 `recognize` 进行内存识别。
- **只向标准输出 (stdout) 打印 JSON 字符串**，例如：`{"success": true, "text": "120", "confidence": 0.98}`。
- 关闭所有不必要的内部 `LOGGER` 打印，防止污染 stdout。

**[MODIFY] `pyproject.toml`**
- 增加终端脚本入口：`svs-cli = "screenvision_sentinel.cli:main"`。
- 安装后，系统环境变量中就有了 `svs-cli.exe`。

### 3. AHK 调用示例

**[NEW] `scripts/ahk_example.ahk`**
提供一个标准的 AutoHotkey v1 或 v2 示例脚本，演示：
- 如何用 `RunWait` 执行 `svs-cli.exe --rect 100,100,200,50`。
- 如何截获命令行的 stdout 输出。
- 如何用 JSON 解析库读取其中的 `text` 字段并弹窗显示。

---

## 验证计划

1. 代码完成后，在终端直接运行 CLI，看能否成功输出 JSON。
2. 不应在 `data/screenshots/` 下产生任何新文件。
3. 运行提供的 AHK 脚本，确保 AHK 能够完美抓取到识别到的文本。

## 用户需确认的问题 (Open Questions)

1. 你平时编写 AHK 脚本是用的 **AHK v1** 还是 **AHK v2**？（这决定了我提供的示例脚本版本）
2. 这个 CLI 设计是否符合你想要的调用体验？
