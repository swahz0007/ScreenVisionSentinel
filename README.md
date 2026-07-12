# ScreenVision Sentinel

中文名称：屏幕视觉哨兵

**当前版本仅用于本地开发和脱敏测试。当前版本不得用于自动执行临床相关操作。**

ScreenVision Sentinel 是运行在 Windows 本地的通用屏幕视觉感知工具。项目当前目标是让 GUI、CLI、本地 HTTP Server、AHK 或其他本地脚本指定屏幕区域，通过本地截图和 OCR 读取文字、数字与界面状态，并返回统一结构化结果。

项目核心原则是：指哪里，看哪里，读取哪里。当前开发重心是 Vision Core，不进入自动操作阶段。

## 当前阶段

- 阶段：阶段 1：统一视觉核心与多入口安全收口
- 版本：`0.1.0-dev`
- 模式：只读观察模式
- 自动操作：默认关闭，当前未实现自动点击或键盘输入
- OCR：默认 RapidOCR；初始化失败或引擎名无效时进入不可输出模拟值的 Mock 占位状态，只有显式选择 `mock` 才会返回测试文本

## 实际入口

- GUI：`python -m screenvision_sentinel.main`
- CLI：`svs-cli` 或 `python -m screenvision_sentinel.cli`
- Server：双击 `start_server.bat`，或先设置 `PYTHONPATH=src` 后运行 `python -m screenvision_sentinel.server`
- AHK：`scripts/ahk_example.ahk` 作为自包含按钮工作台调用本地 Server

GUI 仍保留既有手动截图和 OCR 链路，尚未迁移到 `VisionPipeline`。CLI 和 Server 已使用统一 `VisionPipeline`。

## 当前功能状态

已实现：

- PySide6 本地窗口、拖拽框选区域、手动输入坐标。
- Qt 逻辑坐标到 mss 物理像素坐标换算。
- 用户手动触发单次截图，保存到 `data/screenshots/`。
- `VisionPipeline`：统一区域校验、截图、OCR、调试图保存和脱敏结构化日志。
- `CapturePolicy`：限制宽度、高度、总像素数、坐标范围和严格整数输入。
- CLI 单区域和多区域 OCR，输出 JSON。
- 本地 HTTP Server：`HTTPServer` 串行处理，只绑定 `127.0.0.1`。
- `GET /health` 健康检查和 `POST /ocr` OCR 请求。
- `POST /ocr/batch` 高速批量 OCR 请求，供 AHK 或按键精灵一次读取多个参数。
- `POST /monitor/tick` 调用方主动触发的一次监测观察；`GET /monitor/status` 返回脱敏运行状态，`GET /monitor/latest` 返回后台最新一轮内存参数，供实时参数屏和本地脚本读取。
- `POST /monitor/start` 与 `POST /monitor/stop`：仅在本地调用方显式请求后运行或停止 Python 后台只读监测；默认使用低负载极速模式、5 秒一轮，不自动启动、不保存周期调试图、不执行任何操作。
- 字段级结构校验：可声明 `text`、`number`、`date`、`datetime` 或 `gender`；空值、低置信度和格式不符会返回“需复核”标记，不会篡改 OCR 原文或做临床判断。
- Server 空读时最多再尝试一次相反识别模式，每个字段总计最多两轮；JSON 返回 `fallback_used` 与 `fallback_strategy`。
- AHK 工作台默认关闭调试截图，且不再传递自定义截图路径。
- AHK 工作台已内置心电参数、患者信息字段和坐标拾取；患者信息包含姓名、性别、出生日期、年龄、患者编号、患者科室、床号、检查科室、检查时间、编辑时间和申请单号。
- RapidOCR 适配，以及不会把模拟文本伪装成真实结果的 Mock 故障占位。
- RapidOCR 使用受限 CPU 推理线程；文字检测只处理一份规范化输入，避免单次空读把昂贵检测成倍放大。
- 安全控制与动作执行接口，自动动作默认阻止。
- pytest 和 ruff 基础质量检查。

未实现或未验证：

- GUI 尚未迁移到 `VisionPipeline`。
- 事件持久化、告警和长时间运行验收尚未完成；后台轮询已实现，但必须由本地调用方显式 `/monitor/start`，服务启动本身不会自动截图。
- OCR 强制超时/取消未实现。
- 未验证多显示器、远程桌面和复杂 DPI。
- 未验证 Server 长时间运行和 AHK 真实调用稳定性。
- 未实现任何自动临床操作。

## Vision Core 与 Automation Layer

Vision Core 只负责接收区域、校验区域、截图、OCR、标准化结果、可选安全调试图保存和不含敏感正文的日志。

Automation Layer 未来才可能负责规则、提醒、人工确认、鼠标键盘动作、紧急停止和动作审计。当前默认关闭，不得把 OCR 结果直接绑定审核、发布、删除、提交、患者身份修改或临床诊断判断。

## 调试截图策略

- 默认不保存调试截图。
- CLI 使用纯开关：`--save-debug`。
- Server 只接受布尔值：`"save_debug": true` 或 `false`。
- 调用方不得传入文件名、相对路径或绝对路径。
- 调试图固定保存到 `data/debug/`。
- 文件名由程序生成，格式为时间戳加随机短 ID，后缀固定 `.png`。
- 保存失败不影响正常 OCR 结果。
- 普通日志不记录调试截图路径或 OCR 全文。

## CLI 示例

从源码目录运行时，先在同一个 `cmd.exe` 窗口把本项目的 `src` 放到导入路径最前面，避免误用 `.venv` 中残留的旧安装副本：

```text
set "PYTHONPATH=%CD%\src"
```

单区域：

```text
.venv\Scripts\python.exe -m screenvision_sentinel.cli --rect 100,100,300,120 --engine mock
```

多区域：

```text
.venv\Scripts\python.exe -m screenvision_sentinel.cli --rects "100,100,300,120;500,100,200,80"
```

保存调试截图：

```text
.venv\Scripts\python.exe -m screenvision_sentinel.cli --rect 100,100,300,120 --save-debug
```

`--save-debug C:\temp\a.png` 会被拒绝。

## Server 示例

启动：

```text
start_server.bat
```

`start_server.bat` 会把当前项目的 `src/` 放到 `PYTHONPATH` 最前面，确保启动的是本地源码版本，而不是 `.venv` 中旧的已安装包。

健康检查：

```text
curl http://127.0.0.1:8181/health
```

OCR 请求：

```json
{
  "rect": [100, 100, 300, 120],
  "fast_mode": false,
  "save_debug": false
}
```

批量 OCR 请求：

```json
{
  "fast_mode": false,
  "save_debug": false,
  "items": [
    {"name": "HR", "field_type": "number", "rect": [100, 100, 80, 40]},
    {"name": "PR", "field_type": "number", "rect": [200, 100, 80, 40]}
  ]
}
```

批量响应中的 `texts` 是给 AHK 或按键精灵消费的核心字段，例如 `texts.HR`、`texts.PR`、`texts.EXAM_TIME`。`summary` 是给人看的中文汇总。`elapsed_ms` 是端到端耗时，`shared_capture: true` 表示本次批量读取复用了一次屏幕截图；`ocr_mode`、`fallback_count`、`empty_count`、`review_count` 与 `batch_*_ms` 用于不含识别原文的性能诊断。如果共享截图全空，服务端会自动回退到逐项稳定读取，并返回 `shared_capture_fallback: true`。批量请求最多接受 64 个名称唯一的字段；单字段批量请求直接走单区域链路，跨度过大的组合会安全退回逐项截图。

`field_type` 是可选的结构提示，缺省为 `text`。每项返回 `validation_status`、`is_valid` 与 `requires_review`：它们只判断是否读到内容、OCR 置信度是否达到当前阈值、以及字符结构是否符合声明类型；不校正数值、不检查医学参考范围，也不替代人工判断。OCR 调用的 `success` 与字段是否“可直接使用”是两个不同概念。

监测基础接口使用与批量 OCR 相同的请求体：

```text
POST http://127.0.0.1:8181/monitor/tick
POST http://127.0.0.1:8181/monitor/start
POST http://127.0.0.1:8181/monitor/stop
GET  http://127.0.0.1:8181/monitor/status
GET  http://127.0.0.1:8181/monitor/latest
```

`/monitor/tick` 返回本次低负载批量 OCR 结果，并在 `monitor.fields` 中返回连续确认次数、是否稳定和是否发生变化。监测链路每个字段只做一轮识别，共享截图全空时等待下一轮，不在同一轮回退为逐项重复截图。

`/monitor/status` 返回运行状态、心跳轮数、最近耗时、空值/复核/兜底计数等非敏感诊断，不返回 OCR 文本、值指纹、坐标或截图。`/monitor/latest` 是独立的数据接口，只保留后台最新一轮的 `texts` 和白名单字段结果；重新启动监测时清空，关闭服务后消失，不写入磁盘或日志，也不返回截图、坐标、识别框、请求 ID 或调试图路径。

最新参数响应示例：

```json
{
  "success": true,
  "running": true,
  "snapshot_available": true,
  "tick_count": 6,
  "snapshot_tick_count": 6,
  "texts": {
    "HR": "70",
    "PR": "150"
  }
}
```

AHK 可以先获取一次 JSON，再从同一份响应中读取多个字段，避免重复 HTTP 请求：

```ahk
LiveJson := SvsGetMonitorLatestJson()
HR := SvsParseJsonNamedText(LiveJson, "HR")
PR := SvsParseJsonNamedText(LiveJson, "PR")
```

按键精灵或其他本地脚本同样只需 `GET http://127.0.0.1:8181/monitor/latest`，再解析 `texts.HR`、`texts.PR` 等键。这条链路能在“实时参数屏”上显示数值，就代表后台 OCR → HTTP JSON → AHK/按键精灵的数据传递已经打通；但 OCR 值仍须经过复核，不能直接作为自动临床操作依据。

`/monitor/start` 使用相同的 `items` 请求体，另可给出 `interval_seconds`（`0.5`–`60`，缺省为配置值 5 秒）。后台线程只会在这个显式请求之后启动；每次重新启动都会清空上一会话的连续确认状态，运行期间会拒绝额外的手动 `/monitor/tick`，避免两种观察混用同一确认计数。`save_debug` 必须为 `false`，`/monitor/stop` 使用 `{}` 请求体并在当前轮 OCR 完成后停止。服务为后台监测和普通 OCR 共用一把执行锁，因此不会并发调用同一 RapidOCR 实例；状态查询只短暂锁定跟踪器，不再等待整轮 OCR。

Server 当前使用 Python 标准库 `HTTPServer`，串行处理请求。这有助于避免 RapidOCR 并发不确定性；服务端会在连接空闲 10 秒后释放不完整请求，但仍不能强制取消已经开始的单次 OCR。客户端也应设置连接和读取超时。

`/health` 会返回 `api_revision`、`background_monitor_available: true` 和 `monitor_latest_available: true`。如果 AHK 提示“旧版本、不支持最新参数接口”或出现 `Not found`，说明 8181 上仍是旧服务：关闭所有旧 OCR 服务后重新运行当前项目的 `start_server.bat`。新版服务使用独占端口绑定，拒绝与旧实例共享 `127.0.0.1:8181`。

当 OCR 成功但文本为空时，Server 最多再尝试一次相反识别模式。返回 JSON 中的 `fallback_used: true` 表示本次结果来自兜底识别，`fallback_strategy` 表示触发的兜底策略。

## AHK 使用说明

1. 先启动 `start_server.bat`，或在 `scripts\ahk_example.ahk` 中点击“启动服务”。
2. 运行 `scripts\ahk_example.ahk`。
3. 可点击“选择目标窗口”预先选择 Gink/目标窗口；也可以直接点“拾取坐标”，脚本会先让你点目标窗口。
4. 点击“拾取坐标”后，依次点参数区域左上角、右下角，并在列表中选择参数名写回脚本。
5. 点击“读取 HR”读取单项。
6. 点击“批量心电”通过 `/ocr/batch` 一次读取心电参数并汇总；未配置字段显示 `[未配置坐标]`，已配置但读不到显示 `[空]`。
7. 点击“患者信息”读取患者字段；点击“读取全部”读取心电和患者字段。
8. 在脱敏测试页面上，可点击“启动后台监测”让 Python 以当前坐标按低负载模式定期只读观察；面板会持续显示运行/停止状态、心跳轮数、最近耗时、空值与复核数。
9. 点击“实时参数屏”查看后台最新一轮参数；数值能够持续刷新即表示 AHK 已成功读取 `/monitor/latest`。关闭小屏幕只停止界面轮询，不会停止后台监测。
10. 点击“查看 JSON”查看并复制最后一次结构化返回。

`ahk_example.ahk` 是主用工作台，已经整合 OCR API 函数，不再依赖 `#Include`。调试截图默认关闭；如临时开启，只会发送布尔 `save_debug: true`，不传自定义保存路径。手动读取默认使用稳定模式，即向 OCR API 发送 `fast_mode: false`；只有勾选“极速模式”才会优先速度。连续后台监测独立使用极速、单轮、5 秒间隔的低负载策略，不受手动读取复选框影响。它会按字段发送结构类型，并将空读、低置信度或格式不符显示为“建议人工复核”。

坐标参数名包括：`HR`、`PR`、`QTC`、`AXIS`、`RV5SV1`、`RV5`、`NAME`、`GENDER`、`DOB`、`AGE`、`PAT_ID`、`PAT_DEPT`、`BED_NO`、`EXAM_DEPT`、`EXAM_TIME`、`EDIT_TIME`、`REQ_NO`。

每次拾取坐标写回后，脚本会立即对该区域做一次 OCR 测试，并显示即时结果；如果是 `[空]`，应放大或微调该字段的坐标框。坐标写回后当前会话会同步更新，不再自动重载脚本，避免目标窗口句柄丢失导致“立即 OCR 正常、批量读取为空”。脚本界面会明确显示当前坐标配置名称；离线工作包标记为“工作电脑（2026-07-08 人工测量）”，本机调试脚本则标记为本机调试坐标，二者不应混用。

AHK v1 中文脚本使用 UTF-8 with BOM 保存，避免中文按钮和提示文字被解析成乱码。

## AHK/按键精灵接口验收

判断外部脚本是否“听见声音”的标准：手动读取可拿到 `/ocr/batch` 的 JSON；后台运行时可拿到 `/monitor/latest` 的 JSON，并读取 `texts` 对象中的字段值。

AHK 工作台中点击“查看 JSON”，复制出来的是手动读取结构；“实时参数屏”消费的是后台最新结构。按键精灵或其他脚本只要能 HTTP GET `http://127.0.0.1:8181/monitor/latest` 并解析 `texts`，就已经听见后台监测数据。

最小响应结构：

```json
{
  "success": true,
  "shared_capture": true,
  "elapsed_ms": 85.2,
  "summary": "心室率: 70\nPR 间期: 150",
  "texts": {
    "HR": "70",
    "PR": "150"
  }
}
```

## 离线包

当前工作区保留一个已解压的离线测试目录：

- 文件夹：`dist\ScreenVisionSentinel_Offline_2026年07月_08日测试`
- 当前工作区未保留对应 zip；如需传输，应先对整个目录重新压缩，并保持目录结构不变

工作电脑使用方式：

1. 将整个 `ScreenVisionSentinel_Offline_2026年07月_08日测试` 目录复制到工作电脑；如自行压缩，先完整解压后再运行。
2. 在该目录根部双击 `一键启动.bat`，不要进入 `scripts` 子目录启动。
3. 点击“检查服务”，确认实际 OCR 引擎为 `rapidocr`。
4. 用 AHK 工作台拾取真实页面坐标并批量读取。

离线包包含便携 Python、RapidOCR/ONNX Runtime/OpenCV/mss 依赖、AHK 运行器、OCR Server 源码和 AHK 工作台。
新版工作台支持窗口相对坐标和屏幕绝对坐标。当前 2026-07-08 离线测试配置默认使用已人工测量的屏幕绝对坐标；切换模式后应重新拾取坐标，不能混用两种坐标值。连续后台监测只允许屏幕绝对坐标，避免目标窗口移动后仍读取旧位置；窗口相对坐标仍可用于每次都会重新换算位置的单次/批量手动读取。
点击“检查服务”会显示当前 OCR 引擎；如果显示 `mock`，说明 RapidOCR 没有加载成功，读取会被阻止，避免误用模拟 OCR。

## 环境初始化

```text
py -3.11 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install . --no-build-isolation
```

中文路径下优先使用普通本地安装，不使用 `pip install -e .`；需要直接运行最新源码时，按上面的 CLI 示例显式设置 `PYTHONPATH=%CD%\src`。

## 测试与检查

```text
python -m pytest -v
python -m ruff check .
python -m ruff format --check .
```

## 隐私和安全原则

- 默认只读。
- 不确定时不执行。
- 不上传截图或 OCR 文本。
- 日志默认不记录患者姓名、住院号、检查号、身份证号或 OCR 全文。
- 未脱敏医院软件截图不得提交。
- 测试数据只使用虚构或脱敏内容。
- 当前项目仍然不做自动临床操作。
