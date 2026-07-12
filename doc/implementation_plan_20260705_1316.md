# 接入 RapidOCR（主力）+ Windows OCR（备用）

## 背景

当前"测试 OCR"按钮已串联 MockOCR，UI 流程已验证通过。下一步是接入真实 OCR 引擎，使程序能够实际识别截图中的文字。

用户约束：低配机器、CPU-only、中文+数字必须准确、本地运行。

## 实施策略

分两个阶段推进，**先让 RapidOCR 跑通并验证准确率，再接 Windows OCR 作为备用**。不一次性做完，避免两个引擎同时调试。

---

## 第一阶段：RapidOCR 引擎接入

### 依赖安装

需要手动执行：
```powershell
pip install rapidocr_onnxruntime
```

> [!NOTE]
> `rapidocr_onnxruntime` 会自动下载 PP-OCRv4 模型（约 10MB），首次运行时需要网络。后续运行不需要网络。

---

### OCR 模块

#### [NEW] [rapid_ocr.py](file:///c:/Users/Administrator/Desktop/屏幕视觉哨兵/src/screenvision_sentinel/ocr/rapid_ocr.py)

新建 `RapidOCREngine`，实现 `BaseOCREngine` 协议：

- 构造时创建 `RapidOCR()` 实例（模型只加载一次）
- `recognize(image_path)` 调用引擎识别，将结果转换为统一 `OCRResult`
- 多行文字用换行符拼接，置信度取各行平均值
- 四点坐标框转换为 `OCRBoundingBox`（取外接矩形）
- 引擎导入失败时返回带明确错误信息的 `OCRResult`

#### [NEW] [engine_factory.py](file:///c:/Users/Administrator/Desktop/屏幕视觉哨兵/src/screenvision_sentinel/ocr/engine_factory.py)

引擎工厂函数 `create_ocr_engine(name: str) -> BaseOCREngine`：

- `"rapidocr"` → `RapidOCREngine`
- `"mock"` → `MockOCREngine`
- 后续增加 `"winocr"` → `WindowsOCREngine`
- 默认值为 `"rapidocr"`
- 导入失败时打印警告并回退到 MockOCR

---

### UI 更新

#### [MODIFY] [main_window.py](file:///c:/Users/Administrator/Desktop/屏幕视觉哨兵/src/screenvision_sentinel/ui/main_window.py)

- 将 `self._ocr_engine = MockOCREngine()` 替换为通过 `create_ocr_engine()` 工厂创建
- 在按钮区域上方添加一个引擎选择下拉框（QComboBox），选项为 `rapidocr` / `mock`
- 切换下拉框时重新创建引擎实例
- 其余 OCR 调用逻辑不变（已经通过 `BaseOCREngine` 接口解耦）

---

### 配置与依赖

#### [MODIFY] [config.py](file:///c:/Users/Administrator/Desktop/屏幕视觉哨兵/src/screenvision_sentinel/app/config.py)

- `AppConfig` 增加 `ocr_engine: str = "rapidocr"` 字段
- 从 `local.toml` 的 `[ocr]` 节读取 `engine` 字段
- 保存配置时写入 `[ocr]` 节

#### [MODIFY] [pyproject.toml](file:///c:/Users/Administrator/Desktop/屏幕视觉哨兵/pyproject.toml)

- `dependencies` 中增加 `rapidocr_onnxruntime>=1.4,<2`

#### [MODIFY] [requirements.txt](file:///c:/Users/Administrator/Desktop/屏幕视觉哨兵/requirements.txt)

- 增加 `rapidocr_onnxruntime>=1.4,<2`

---

### 第一阶段验证

1. 安装依赖：`pip install rapidocr_onnxruntime`
2. 重装本地包：`pip install . --no-build-isolation`
3. 启动程序，选择监控区域，点击"测试截图"
4. 下拉框选择 `rapidocr`，点击"测试 OCR"
5. 确认识别结果是否与截图内容一致
6. 多次截取不同区域对比准确率和耗时

> [!IMPORTANT]
> 第一阶段完成后，请人工反馈 RapidOCR 在你的低配机器上的准确率和耗时表现。如果满意，再推进第二阶段。

---

## 第二阶段：Windows OCR 备用引擎（第一阶段验证后再实施）

#### [NEW] [windows_ocr.py](file:///c:/Users/Administrator/Desktop/屏幕视觉哨兵/src/screenvision_sentinel/ocr/windows_ocr.py)

- 使用 Windows Runtime Python 投影包（`winrt-runtime` 等）调用 `Windows.Media.Ocr` API
- 需要系统安装中文语言包（`zh-Hans`）
- 零模型下载，极低资源占用
- 如果 WinRT 包不可用，备选方案是通过 PowerShell 子进程调用系统 OCR

> [!NOTE]
> Windows OCR 的 Python 调用方式在不同 Windows 版本上可能有差异，需要实际验证。第二阶段开始前再确认具体实现方案。

---

## Open Questions

1. RapidOCR 首次加载模型可能需要 2-5 秒，是否接受这个冷启动时间？还是希望在程序启动时预加载模型？
2. 引擎选择是否需要持久化到 `local.toml`，还是每次启动都默认 RapidOCR？

## 不做什么

- 不实现持续截图循环（仍然是手动点击测试）
- 不实现自动动作
- 不修改安全控制逻辑
- 颜色识别暂不纳入
