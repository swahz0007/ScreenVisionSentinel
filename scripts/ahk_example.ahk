; ==============================================================================
; ScreenVision Sentinel - 值班只读 OCR 工作台 (AutoHotkey v1)
; ==============================================================================
; 用途：
;   1. 拾取目标窗口内参数坐标。
;   2. 调用本地 OCR API 读取参数。
;   3. 用弹窗、ToolTip、剪贴板和 JSON 输出结果。
;
; 安全边界：
;   只读，不点击，不输入，不提交，不审核，不发布。
; ==============================================================================

#NoEnv
#SingleInstance Force
SendMode Input
SetWorkingDir %A_ScriptDir%

global LastJson := ""
global LastSummary := ""
global TargetWindowID := ""
global OCR_FAST_MODE := false
global MONITOR_FAST_MODE := true
global MONITOR_INTERVAL_SECONDS := 5
global MONITOR_STATUS_POLL_MS := 2000
global LIVE_DATA_POLL_MS := 2000
global LIVE_DATA_SCREEN_OPEN := false
global COORDINATE_MODE := "screen"
global COORDINATE_PROFILE_NAME := "本机调试坐标（请按实际窗口确认）"
global PrefixPickerDone := false
global PrefixPickerValue := ""

; ==============================================================================
; 参数坐标：窗口内相对坐标或屏幕绝对坐标 X/Y/W/H
; COORDINATE_MODE = "window" 时按目标窗口左上角相对坐标读取。
; COORDINATE_MODE = "screen" 时不依赖窗口句柄，直接按屏幕绝对坐标读取。
; ==============================================================================

; 心室率（HR）
HR_X := 1377
HR_Y := 228
HR_W := 76
HR_H := 39

; PR 间期
PR_X := 1680
PR_Y := 223
PR_W := 81
PR_H := 37

; QTC 间期
QTC_X := 1520
QTC_Y := 271
QTC_W := 90
QTC_H := 40

; 心电轴
AXIS_X := 1522
AXIS_Y := 324
AXIS_W := 77
AXIS_H := 38

; RV5+SV1
RV5SV1_X := 1369
RV5SV1_Y := 373
RV5SV1_W := 75
RV5SV1_H := 33

; RV5
RV5_X := 1525
RV5_Y := 375
RV5_W := 77
RV5_H := 34

; 患者姓名
NAME_X := 351
NAME_Y := 223
NAME_W := 96
NAME_H := 34

; 性别
GENDER_X := 674
GENDER_Y := 223
GENDER_W := 78
GENDER_H := 39

; 出生日期
DOB_X := 999
DOB_Y := 225
DOB_W := 118
DOB_H := 33

; 年龄
AGE_X := 1118
AGE_Y := 221
AGE_W := 88
AGE_H := 52

; 患者编号
PAT_ID_X := 344
PAT_ID_Y := 268
PAT_ID_W := 197
PAT_ID_H := 47

; 患者科室
PAT_DEPT_X := 673
PAT_DEPT_Y := 270
PAT_DEPT_W := 204
PAT_DEPT_H := 44

; 床号
BED_NO_X := 997
BED_NO_Y := 269
BED_NO_W := 212
BED_NO_H := 45

; 检查科室
EXAM_DEPT_X := 470
EXAM_DEPT_Y := 357
EXAM_DEPT_W := 175
EXAM_DEPT_H := 32

; 检查时间
EXAM_TIME_X := 667
EXAM_TIME_Y := 317
EXAM_TIME_W := 218
EXAM_TIME_H := 52

; 编辑时间
EDIT_TIME_X := 996
EDIT_TIME_Y := 313
EDIT_TIME_W := 211
EDIT_TIME_H := 53

; 申请单号
REQ_NO_X := 468
REQ_NO_Y := 404
REQ_NO_W := 179
REQ_NO_H := 32

; 仅供开发和离线包验收：解析完整脚本后立即退出，不显示工作台、不读取屏幕。
if (A_Args.Length() > 0 && A_Args[1] = "--syntax-check")
    ExitApp
if (A_Args.Length() > 0 && A_Args[1] = "--live-screen-selftest")
{
    BuildLiveDataScreenGui(false)
    ResizeLiveDataScreen(680, 680)
    ExitApp
}

BuildControlPanel()
return

; ==============================================================================
; 鼠标按钮
; ==============================================================================

BtnSelectTarget:
    SelectTargetWindow()
return

BtnCheckServer:
    CheckServerButton()
return

BtnStartServer:
    StartServerButton()
return

BtnReadHR:
    ReadSingleParam("HR")
return

BtnReadECG:
    ReadBatchParams("ECG")
return

BtnReadPatient:
    ReadBatchParams("PATIENT")
return

BtnReadAll:
    ReadBatchParams("ALL")
return

BtnStartMonitor:
    StartBackgroundMonitor("ALL")
return

BtnStopMonitor:
    StopBackgroundMonitor()
return

BtnMonitorStatus:
    ShowMonitorStatus()
return

BtnLiveData:
    ShowLiveDataScreen()
return

RefreshMonitorStatusTimer:
    RefreshMonitorLiveStatus()
return

RefreshLiveDataTimer:
    RefreshLiveDataScreen()
return

BtnPickRegion:
    PickAndUpdateRegion()
return

BtnToggleCoordMode:
    ToggleCoordinateMode()
return

ToggleOcrFastMode:
    Gui, 1:Submit, NoHide
    OCR_FAST_MODE := OcrFastModeChecked ? true : false
    UpdateOcrModeStatus()
return

BtnCopyLast:
    CopyLastOutput()
return

BtnShowJson:
    ShowLastJson()
return

BtnHelp:
    ShowWorkbenchHelp()
return

GuiClose:
BtnExit:
    ExitApp
return

3GuiClose:
3GuiEscape:
    CloseLiveDataScreen()
return

3GuiSize:
    if (A_EventInfo != 1)
        ResizeLiveDataScreen(A_GuiWidth, A_GuiHeight)
return

PrefixPickerOK:
    Gui, PrefixPicker:Submit, NoHide
    PrefixPickerDone := true
return

PrefixPickerCancel:
PrefixPickerGuiClose:
    PrefixPickerValue := ""
    PrefixPickerDone := true
return

; ==============================================================================
; 工作台函数
; ==============================================================================

BuildControlPanel() {
    global TargetStatus, ModeStatus, OcrModeStatus, OcrRuntimeStatus, MonitorLiveStatus
    global OcrFastModeChecked, OCR_FAST_MODE, MONITOR_INTERVAL_SECONDS
    OcrFastModeChecked := OCR_FAST_MODE ? 1 : 0
    Gui, 1:+AlwaysOnTop +Resize +MinSize540x350
    Gui, 1:Margin, 12, 10
    Gui, 1:Font, s10, Microsoft YaHei UI
    Gui, 1:Add, Text, xm ym w520, 屏幕视觉哨兵 - 只读 OCR 工作台
    Gui, 1:Add, Text, xm y+4 w520 vTargetStatus, 目标窗口：未选择
    Gui, 1:Add, Text, xm y+4 w520 vModeStatus, % CoordinateModeStatusText()
    Gui, 1:Add, Text, xm y+4 w520 vOcrModeStatus, % OcrModeStatusText()
    Gui, 1:Add, Text, xm y+4 w520 vOcrRuntimeStatus, OCR 设备：等待“检查服务”
    Gui, 1:Add, Text, xm y+6 w520 h42 +Border vMonitorLiveStatus, % "后台监测：未启动｜低负载：极速、" . MONITOR_INTERVAL_SECONDS . " 秒/轮"
    Gui, 1:Add, Checkbox, xm y+6 vOcrFastModeChecked gToggleOcrFastMode Checked%OcrFastModeChecked%, 极速模式（速度优先，可能降低准确率）
    Gui, 1:Add, Button, xm y+10 w168 h30 gBtnSelectTarget, 选择目标窗口
    Gui, 1:Add, Button, x+8 w168 h30 gBtnCheckServer, 检查服务
    Gui, 1:Add, Button, x+8 w168 h30 gBtnStartServer, 启动服务
    Gui, 1:Add, Button, xm y+8 w168 h30 gBtnReadHR, 读取 HR
    Gui, 1:Add, Button, x+8 w168 h30 gBtnReadECG, 批量心电
    Gui, 1:Add, Button, x+8 w168 h30 gBtnReadPatient, 患者信息
    Gui, 1:Add, Button, xm y+8 w168 h30 gBtnReadAll, 读取全部
    Gui, 1:Add, Button, x+8 w168 h30 gBtnPickRegion, 拾取坐标
    Gui, 1:Add, Button, x+8 w168 h30 gBtnShowJson, 查看 JSON
    Gui, 1:Add, Button, xm y+8 w168 h30 gBtnStartMonitor, 启动后台监测
    Gui, 1:Add, Button, x+8 w168 h30 gBtnStopMonitor, 停止监测
    Gui, 1:Add, Button, x+8 w168 h30 gBtnMonitorStatus, 监测状态
    Gui, 1:Add, Button, xm y+8 w168 h30 gBtnCopyLast, 复制结果
    Gui, 1:Add, Button, x+8 w168 h30 gBtnToggleCoordMode, 坐标模式
    Gui, 1:Add, Button, x+8 w168 h30 gBtnHelp, 帮助
    Gui, 1:Add, Button, xm y+8 w168 h30 gBtnExit, 退出
    Gui, 1:Add, Button, x+8 w168 h30 gBtnLiveData, 实时参数屏
    Gui, 1:Add, Text, xm y+10 w520, 只读：不点击、不输入、不提交。窗口句柄不稳定时可切到屏幕坐标。
    Gui, 1:Show, AutoSize, 屏幕视觉哨兵
}

SelectTargetWindow() {
    return CaptureTargetWindowFromMouse(true, true)
}

CaptureTargetWindowFromMouse(ShowPanelAfter := true, ShowSuccess := false) {
    global TargetWindowID, TargetStatus
    Gui, 1:Hide
    ToolTip, 请用鼠标点击 Gink/目标窗口任意位置...
    KeyWait, LButton, D
    MouseGetPos,,, WindowID
    KeyWait, LButton, U
    ToolTip
    WindowID := NormalizeTargetWindowID(WindowID)
    if (!WindowID)
    {
        Gui, 1:Show
        MsgBox, 48, 提示, 未能获取目标窗口。
        return false
    }
    TargetWindowID := WindowID
    TargetWinTitle := "ahk_id " . TargetWindowID
    WinGetTitle, Title, %TargetWinTitle%
    if (Title = "")
        Title := "ahk_id " . TargetWindowID
    GuiControl, 1:, TargetStatus, % "目标窗口：" . Title
    if (ShowPanelAfter)
        Gui, 1:Show
    if (ShowSuccess)
        MsgBox, 64, 目标窗口, % "已选择：" . Title
    return TargetWindowID
}

NormalizeTargetWindowID(WindowID) {
    if (!WindowID)
        return ""
    RootID := DllCall("GetAncestor", "Ptr", WindowID, "UInt", 2, "Ptr")
    if (RootID)
        return RootID
    return WindowID
}

GetTargetWindowID() {
    global TargetWindowID, TargetStatus
    if (IsTargetWindowAvailable(TargetWindowID))
        return TargetWindowID
    TargetWindowID := ""
    GuiControl, 1:, TargetStatus, 目标窗口：未选择（当前为只读模式）
    MsgBox, 48, 提示, 请点击“选择目标窗口”，或直接点击“拾取坐标”让脚本自动选择。
    return ""
}

GetOrSelectTargetWindow(ShowPanelAfter := true) {
    global TargetWindowID
    if (IsTargetWindowAvailable(TargetWindowID))
        return TargetWindowID
    TargetWindowID := ""
    return CaptureTargetWindowFromMouse(ShowPanelAfter, false)
}

IsTargetWindowAvailable(WindowID) {
    return (WindowID != "" && WinExist("ahk_id " . WindowID))
}

IsScreenCoordinateMode() {
    global COORDINATE_MODE
    return (COORDINATE_MODE = "screen")
}

CoordinateModeStatusText() {
    global COORDINATE_PROFILE_NAME
    if (IsScreenCoordinateMode())
        return "坐标配置：" . COORDINATE_PROFILE_NAME . "｜屏幕绝对坐标（不依赖窗口句柄）"
    return "坐标配置：" . COORDINATE_PROFILE_NAME . "｜窗口相对坐标（需要目标窗口）"
}

OcrModeStatusText() {
    global OCR_FAST_MODE
    if (OCR_FAST_MODE)
        return "识别模式：极速（速度优先）"
    return "识别模式：稳定（默认，已启用文字检测）"
}

UpdateCoordinateModeStatus() {
    GuiControl, 1:, ModeStatus, % CoordinateModeStatusText()
}

UpdateOcrModeStatus() {
    GuiControl, 1:, OcrModeStatus, % OcrModeStatusText()
}

UpdateOcrRuntimeStatus(HealthJson) {
    if (HealthJson = "")
    {
        GuiControl, 1:, OcrRuntimeStatus, OCR 设备：服务未连接
        return
    }
    DeviceLabel := SvsParseJsonString(HealthJson, "device_label")
    DeviceDetail := SvsParseJsonString(HealthJson, "device_detail")
    if (DeviceLabel = "")
        DeviceLabel := "未知"
    StatusText := "OCR 设备：" . DeviceLabel
    if (DeviceDetail != "")
        StatusText .= "（" . DeviceDetail . "）"
    GuiControl, 1:, OcrRuntimeStatus, %StatusText%
}

ToggleCoordinateMode() {
    global COORDINATE_MODE
    PreviousMode := COORDINATE_MODE
    if (IsScreenCoordinateMode())
        COORDINATE_MODE := "window"
    else
        COORDINATE_MODE := "screen"
    if (!PersistCoordinateMode(COORDINATE_MODE))
    {
        COORDINATE_MODE := PreviousMode
        UpdateCoordinateModeStatus()
        MsgBox, 16, 坐标模式, 坐标模式写回失败，已保持原设置。
        return
    }
    UpdateCoordinateModeStatus()
    if (IsScreenCoordinateMode())
        MsgBox, 64, 坐标模式, 已切换到屏幕绝对坐标。拾取和读取将不再依赖 Ginko 窗口句柄。
    else
        MsgBox, 64, 坐标模式, 已切换到窗口相对坐标。读取前需要选择目标窗口。
}

PersistCoordinateMode(Mode) {
    TargetScript := A_ScriptFullPath
    FileRead, ScriptContent, %TargetScript%
    if (ErrorLevel)
        return false
    ModePattern := "im)^([ \t]*global[ \t]+COORDINATE_MODE[ \t]*:=[ \t]*"")[^""]+(""[ \t]*)$"
    ScriptContent := RegExReplace(ScriptContent, ModePattern, "$1" . Mode . "$2", ReplacementCount, 1)
    if (ReplacementCount != 1)
        return false
    return SvsWriteScriptAtomically(TargetScript, ScriptContent)
}

SvsWriteScriptAtomically(TargetScript, ScriptContent) {
    TempScript := TargetScript . ".svs.tmp"
    FileDelete, %TempScript%
    FileAppend, %ScriptContent%, *%TempScript%, UTF-8
    if (ErrorLevel)
    {
        FileDelete, %TempScript%
        return false
    }
    FileMove, %TempScript%, %TargetScript%, 1
    if (ErrorLevel)
    {
        FileDelete, %TempScript%
        return false
    }
    return true
}

BuildScreenRect(ActiveID, X, Y, W, H, ByRef ScreenLeft, ByRef ScreenTop) {
    if (IsScreenCoordinateMode())
    {
        ScreenLeft := X
        ScreenTop := Y
        return true
    }
    WinGetPos, WinX, WinY,,, ahk_id %ActiveID%
    if (ErrorLevel)
    {
        MsgBox, 48, 提示, 目标窗口不可用，请重新点击“选择目标窗口”，或切换到“屏幕坐标”模式。
        return false
    }
    ScreenLeft := WinX + X
    ScreenTop := WinY + Y
    return true
}

CheckServerButton() {
    HealthJson := SvsGetHealthJson()
    if (!SvsHealthSuccess(HealthJson))
    {
        UpdateOcrRuntimeStatus("")
        MsgBox, 48, OCR 服务, 后台服务不可用。请点击“启动服务”，等待窗口显示 running 后重试。
        return
    }
    EngineName := SvsParseJsonString(HealthJson, "engine_name")
    RequestedEngine := SvsParseJsonString(HealthJson, "requested_engine")
    FallbackReason := SvsParseJsonString(HealthJson, "fallback_reason")
    if (EngineName = "")
        EngineName := "unknown"
    if (RequestedEngine = "")
        RequestedEngine := "unknown"
    UpdateOcrRuntimeStatus(HealthJson)
    DeviceLabel := SvsParseJsonString(HealthJson, "device_label")
    DeviceDetail := SvsParseJsonString(HealthJson, "device_detail")
    if (DeviceLabel = "")
        DeviceLabel := "未知"
    if (!SvsHealthOcrReady(HealthJson))
    {
        MsgBox, 48, OCR 服务, % "后台服务可访问，但 OCR 引擎未就绪。`n当前引擎：" . EngineName . "`n期望引擎：" . RequestedEngine . "`n原因：" . FallbackReason . "`n`n这种状态会返回模拟 OCR 结果，请替换新版离线包或检查服务窗口报错。"
        return
    }
    MsgBox, 64, OCR 服务, % "后台服务正常：127.0.0.1:8181`nOCR 引擎：" . EngineName . "`n推理设备：" . DeviceLabel . "`n" . DeviceDetail
}

StartServerButton() {
    PackageRoot := A_ScriptDir . "\.."
    ServerBat := PackageRoot . "\start_server.bat"
    if (!FileExist(ServerBat))
    {
        MsgBox, 16, 启动失败, 找不到：`n%ServerBat%
        return
    }
    EnvGet, CmdExe, ComSpec
    if (CmdExe = "")
        CmdExe := A_WinDir . "\System32\cmd.exe"
    CmdLine := Chr(34) . CmdExe . Chr(34) . " /k call " . Chr(34) . ServerBat . Chr(34)
    Run, %CmdLine%, %PackageRoot%, UseErrorLevel
    if (ErrorLevel)
        MsgBox, 16, 启动失败, 无法启动服务脚本：`n%ServerBat%
}

ShowWorkbenchHelp() {
    HelpText := "ScreenVision Sentinel 只读 OCR 工作台`n`n"
    HelpText .= "选择目标窗口：点按钮后，再点一下 Gink/目标窗口。`n"
    HelpText .= "检查服务：确认 127.0.0.1:8181 可用。`n"
    HelpText .= "启动服务：打开后端 OCR 服务窗口。`n"
    HelpText .= "读取 HR：读取 HR 单项。`n"
    HelpText .= "批量心电：读取 HR/PR/QTC/AXIS/RV5SV1/RV5。`n"
    HelpText .= "患者信息：读取已配置的患者信息字段。`n"
    HelpText .= "读取全部：读取所有已配置字段。`n"
    HelpText .= "启动后台监测：低负载模式按当前坐标每 5 秒极速观察一次全部字段；面板会持续显示心跳。`n"
    HelpText .= "停止监测：请求 Python 后台在当前轮 OCR 完成后停止；不执行任何自动操作。`n"
    HelpText .= "实时参数屏：通过 /monitor/latest 显示后台最新一轮参数，验证 AHK/按键精灵可读取接口数据。`n"
    HelpText .= "识别模式：默认“稳定”；勾选“极速模式”才优先速度。拾取坐标后的测试会使用当前模式。`n"
    HelpText .= "字段校验：空值、低置信度或格式不符会标记为“需复核”，不会自动改写结果。`n"
    HelpText .= "监测 API：/monitor/tick 为调用方主动触发的一次观察；不会自行启动后台轮询。`n"
    HelpText .= "OCR 设备：点击“检查服务”可查看当前离线包实际使用的推理设备。`n"
    HelpText .= "拾取坐标：如果未选窗口，会先让你点目标窗口，再点目标区域左上角和右下角。`n"
    HelpText .= "坐标模式：Ginko 句柄不稳定时切到屏幕绝对坐标，读取不再依赖窗口句柄。`n"
    HelpText .= "查看 JSON：查看并复制最后一次原始 API JSON。`n"
    HelpText .= "当前脚本只读：不点击、不输入、不提交。"
    MsgBox, 64, 使用说明, %HelpText%
}

ReadSingleParam(Prefix) {
    global LastJson, LastSummary, OCR_FAST_MODE
    if (!EnsureServerReady())
        return

    ActiveID := ""
    if (!IsScreenCoordinateMode())
    {
        ActiveID := GetOrSelectTargetWindow()
        if (!ActiveID)
            return
    }

    if (!GetRegionByPrefix(Prefix, X, Y, W, H, Label))
        return
    if (W <= 0 || H <= 0)
    {
        MsgBox, 48, 提示, % Label . " 尚未配置有效坐标。"
        return
    }

    if (!BuildScreenRect(ActiveID, X, Y, W, H, ScreenLeft, ScreenTop))
        return
    FieldType := GetFieldType(Prefix)
    ToolTip, 正在读取 %Label%...
    LastJson := SvsOCRJson(ScreenLeft, ScreenTop, W, H, OCR_FAST_MODE, false, FieldType)
    ToolTip
    ErrorMessage := SvsParseJsonString(LastJson, "error")
    if (ErrorMessage != "")
    {
        LastSummary := Label . ": [读取失败：" . ErrorMessage . "]"
        Clipboard := LastSummary
        MsgBox, 48, 单项读取结果, % LastSummary
        return
    }
    RawText := SvsParseJsonText(LastJson)
    Value := CleanValue(Prefix, RawText)
    if (Value = "")
        Value := "[未识别]"
    ValidationStatus := SvsParseJsonString(LastJson, "validation_status")
    ValidationNote := ValidationStatusText(ValidationStatus)
    LastSummary := Label . ": " . Value . "`n原始 OCR: " . RawText
    if (ValidationNote != "")
        LastSummary .= "`n识别状态：" . ValidationNote
    Clipboard := LastSummary
    MsgBox, 64, 单项读取结果, % LastSummary . "`n`n结果已复制到剪贴板。"
}

ReadBatchParams(Mode) {
    global LastJson, LastSummary, OCR_FAST_MODE
    if (!EnsureServerReady())
        return

    ActiveID := ""
    if (!IsScreenCoordinateMode())
    {
        ActiveID := GetOrSelectTargetWindow()
        if (!ActiveID)
            return
    }

    Prefixes := GetPrefixList(Mode)
    ResultsJson := ""
    UnconfiguredSummary := ""
    ConfiguredCount := 0
    ItemsJson := ""
    Loop, Parse, Prefixes, |
    {
        Prefix := A_LoopField
        if (!GetRegionByPrefix(Prefix, X, Y, W, H, Label))
            continue
        if (W <= 0 || H <= 0)
        {
            UnconfiguredSummary .= Label . ": [未配置坐标]`n"
            ResultJson := "{""name"":""" . SvsJsonEscape(Prefix) . """,""label"":""" . SvsJsonEscape(Label) . """,""configured"":false,""text"":"""",""raw_text"":""""}"
            ResultsJson := SvsAppendJsonItem(ResultsJson, ResultJson)
            continue
        }

        ConfiguredCount += 1
        if (!BuildScreenRect(ActiveID, X, Y, W, H, ScreenLeft, ScreenTop))
            return
        FieldType := GetFieldType(Prefix)
        ItemJson := "{""name"":""" . SvsJsonEscape(Prefix) . """,""label"":""" . SvsJsonEscape(Label) . """,""field_type"":""" . SvsJsonEscape(FieldType) . """,""rect"":[" . ScreenLeft . "," . ScreenTop . "," . W . "," . H . "]}"
        ItemsJson := SvsAppendJsonItem(ItemsJson, ItemJson)
        ResultJson := "{""name"":""" . SvsJsonEscape(Prefix) . """,""label"":""" . SvsJsonEscape(Label) . """,""configured"":true,""rect"":[" . X . "," . Y . "," . W . "," . H . "],""screen_rect"":[" . ScreenLeft . "," . ScreenTop . "," . W . "," . H . "]}"
        ResultsJson := SvsAppendJsonItem(ResultsJson, ResultJson)
    }

    if (ConfiguredCount = 0)
    {
        LastJson := "{""success"":false,""mode"":""" . SvsJsonEscape(Mode) . """,""error"":""no configured fields"",""configured_results"":[" . ResultsJson . "]}"
        LastSummary := "当前模式没有任何有效坐标。请点击“拾取坐标”逐项配置。"
        Clipboard := LastSummary
        MsgBox, 48, 批量读取结果, % LastSummary
        return
    }

    ToolTip, 正在批量读取...
    ApiJson := SvsOCRBatchJson(ItemsJson, OCR_FAST_MODE)
    ToolTip
    LastJson := ApiJson
    LastSummary := SvsParseJsonSummary(ApiJson)
    ErrorMessage := SvsParseJsonString(ApiJson, "error")
    if (LastSummary = "" && ErrorMessage != "")
    {
        LastSummary := "批量读取失败：" . ErrorMessage
        if (UnconfiguredSummary != "")
            LastSummary := RTrim(LastSummary . "`n" . RTrim(UnconfiguredSummary, "`n"), "`n")
        Clipboard := LastSummary
        MsgBox, 48, 批量读取结果, % LastSummary
        return
    }
    if (LastSummary = "")
        LastSummary := BuildSummaryFromJson(ApiJson, Prefixes)
    if (UnconfiguredSummary != "")
        LastSummary := RTrim(LastSummary . "`n" . RTrim(UnconfiguredSummary, "`n"), "`n")
    Diagnostics := BuildBatchDiagnostics(ApiJson)
    if (Diagnostics != "")
        LastSummary := LastSummary . "`n" . Diagnostics
    Clipboard := LastSummary
    if (!SvsParseJsonBoolean(ApiJson, "success"))
        MsgBox, 48, 批量读取结果, % LastSummary . "`n`n部分或全部字段读取失败；“查看 JSON”可看原始返回。"
    else
        MsgBox, 64, 批量读取结果, % LastSummary . "`n`n汇总已复制到剪贴板；“查看 JSON”可看原始返回。"
}

StartBackgroundMonitor(Mode) {
    global LastJson, LastSummary, MONITOR_FAST_MODE, MONITOR_INTERVAL_SECONDS, MONITOR_STATUS_POLL_MS
    if (!EnsureServerReady())
        return
    HealthJson := SvsGetHealthJson()
    if (!SvsHealthBackgroundMonitorAvailable(HealthJson))
    {
        MsgBox, 48, 后台监测, 当前 OCR 服务是旧版本，未加载后台监测接口。`n`n请关闭所有 OCR 服务窗口，再从本项目根目录重新启动 start_server.bat。
        return
    }

    if (!IsScreenCoordinateMode())
    {
        MsgBox, 48, 后台监测, 后台监测仅支持屏幕绝对坐标，避免目标窗口移动后继续读取旧位置。请切换坐标模式并重新拾取坐标。
        return
    }
    ActiveID := ""

    ItemsJson := ""
    ConfiguredCount := 0
    Prefixes := GetPrefixList(Mode)
    Loop, Parse, Prefixes, |
    {
        Prefix := A_LoopField
        if (!GetRegionByPrefix(Prefix, X, Y, W, H, Label))
            continue
        if (W <= 0 || H <= 0)
            continue
        if (!BuildScreenRect(ActiveID, X, Y, W, H, ScreenLeft, ScreenTop))
            return
        FieldType := GetFieldType(Prefix)
        ItemJson := "{""name"":""" . SvsJsonEscape(Prefix) . """,""label"":""" . SvsJsonEscape(Label) . """,""field_type"":""" . SvsJsonEscape(FieldType) . """,""rect"":[" . ScreenLeft . "," . ScreenTop . "," . W . "," . H . "]}"
        ItemsJson := SvsAppendJsonItem(ItemsJson, ItemJson)
        ConfiguredCount += 1
    }

    if (ConfiguredCount = 0)
    {
        MsgBox, 48, 后台监测, 当前没有任何有效坐标。请先拾取需要观察的字段坐标。
        return
    }

    ToolTip, 正在启动只读后台监测...
    ApiJson := SvsStartMonitorJson(ItemsJson, MONITOR_FAST_MODE, MONITOR_INTERVAL_SECONDS)
    ToolTip
    LastJson := ApiJson
    ErrorMessage := SvsParseJsonString(ApiJson, "error")
    if (ErrorMessage != "")
    {
        LastSummary := "后台监测启动失败：" . ErrorMessage
        Clipboard := LastSummary
        MsgBox, 48, 后台监测, %LastSummary%
        return
    }
    Interval := SvsParseJsonNumber(ApiJson, "interval_seconds")
    if (Interval = "")
        Interval := MONITOR_INTERVAL_SECONDS
    SetTimer, RefreshMonitorStatusTimer, %MONITOR_STATUS_POLL_MS%
    RefreshMonitorLiveStatus()
    LastSummary := "后台只读监测已启动：" . ConfiguredCount . " 项`n低负载模式：极速、单轮识别`n间隔：" . Interval . " 秒`n面板将持续显示运行心跳。"
    Clipboard := LastSummary
    MsgBox, 64, 后台监测, %LastSummary%
}

StopBackgroundMonitor() {
    global LastJson, LastSummary
    HealthJson := SvsGetHealthJson()
    if (!SvsHealthSuccess(HealthJson))
    {
        MsgBox, 48, 后台监测, OCR 服务不可用，无法发送停止请求。
        return
    }
    if (!SvsHealthBackgroundMonitorAvailable(HealthJson))
    {
        MsgBox, 48, 后台监测, 当前 OCR 服务是旧版本，未加载后台监测接口。请关闭旧服务后重新启动当前项目的 start_server.bat。
        return
    }
    ApiJson := SvsStopMonitorJson()
    LastJson := ApiJson
    ErrorMessage := SvsParseJsonString(ApiJson, "error")
    if (ErrorMessage != "")
    {
        LastSummary := "停止请求失败：" . ErrorMessage
        Clipboard := LastSummary
        MsgBox, 48, 后台监测, %LastSummary%
        return
    }
    LastSummary := "已请求停止后台监测。若当前正在 OCR，本轮完成后会停止。"
    Clipboard := LastSummary
    MsgBox, 64, 后台监测, %LastSummary%
}

ShowMonitorStatus() {
    global LastJson, LastSummary
    HealthJson := SvsGetHealthJson()
    if (!SvsHealthSuccess(HealthJson))
    {
        MsgBox, 48, 后台监测, OCR 服务不可用，请先启动服务。
        return
    }
    if (!SvsHealthBackgroundMonitorAvailable(HealthJson))
    {
        MsgBox, 48, 后台监测, 当前 OCR 服务是旧版本，未加载后台监测接口。请关闭旧服务后重新启动当前项目的 start_server.bat。
        return
    }
    StatusJson := SvsGetMonitorStatusJson()
    LastJson := StatusJson
    if (!SvsHealthSuccess(StatusJson))
    {
        MsgBox, 48, 后台监测, 监测状态不可用，请先启动 OCR 服务。
        return
    }
    Running := SvsParseJsonBoolean(StatusJson, "running")
    Interval := SvsParseJsonNumber(StatusJson, "interval_seconds")
    TickCount := SvsParseJsonNumber(StatusJson, "tick_count")
    LastTickMs := SvsParseJsonNumber(StatusJson, "last_tick_elapsed_ms")
    ErrorType := SvsParseJsonString(StatusJson, "last_error_type")
    LastSummary := "后台监测状态：" . (Running ? "运行中" : "未运行")
    if (Interval != "")
        LastSummary .= "`n间隔：" . Interval . " 秒"
    if (TickCount != "")
        LastSummary .= "`n已观察：" . TickCount . " 轮"
    if (LastTickMs != "")
        LastSummary .= "`n最近 OCR：" . Round(LastTickMs) . " ms"
    if (ErrorType != "")
        LastSummary .= "`n最近错误类型：" . ErrorType
    LastSummary .= "`n状态接口不显示 OCR 原文。"
    Clipboard := LastSummary
    MsgBox, 64, 后台监测, %LastSummary%
}

RefreshMonitorLiveStatus() {
    global MONITOR_INTERVAL_SECONDS
    StatusJson := SvsGetMonitorStatusJson()
    if (!SvsHealthSuccess(StatusJson))
    {
        GuiControl, 1:, MonitorLiveStatus, 后台监测：服务未连接（心跳已停止）
        SetTimer, RefreshMonitorStatusTimer, Off
        return false
    }

    Running := SvsParseJsonBoolean(StatusJson, "running")
    StopRequested := SvsParseJsonBoolean(StatusJson, "stop_requested")
    Interval := SvsParseJsonNumber(StatusJson, "interval_seconds")
    TickCount := SvsParseJsonNumber(StatusJson, "tick_count")
    FieldCount := SvsParseJsonNumber(StatusJson, "field_count")
    LastTickMs := SvsParseJsonNumber(StatusJson, "last_tick_elapsed_ms")
    EmptyCount := SvsParseJsonNumber(StatusJson, "last_empty_count")
    ReviewCount := SvsParseJsonNumber(StatusJson, "last_review_count")
    ErrorType := SvsParseJsonString(StatusJson, "last_error_type")
    if (Interval = "")
        Interval := MONITOR_INTERVAL_SECONDS
    if (TickCount = "")
        TickCount := 0

    if (Running && StopRequested)
        StateText := "正在停止"
    else if (Running)
        StateText := "运行中"
    else
        StateText := "已停止"
    StatusText := "后台监测：" . StateText . "｜心跳 " . TickCount . " 轮｜低负载 " . Interval . " 秒/轮"
    if (FieldCount != "")
        StatusText .= "`n字段 " . FieldCount
    if (LastTickMs != "")
        StatusText .= "｜最近 " . Round(LastTickMs) . " ms"
    if (EmptyCount != "")
        StatusText .= "｜空值 " . EmptyCount
    if (ReviewCount != "")
        StatusText .= "｜复核 " . ReviewCount
    if (ErrorType != "")
        StatusText .= "｜错误 " . ErrorType
    GuiControl, 1:, MonitorLiveStatus, %StatusText%
    if (!Running)
        SetTimer, RefreshMonitorStatusTimer, Off
    return true
}

ShowLiveDataScreen() {
    global LIVE_DATA_SCREEN_OPEN, LIVE_DATA_POLL_MS
    HealthJson := SvsGetHealthJson()
    if (!SvsHealthSuccess(HealthJson))
    {
        MsgBox, 48, 实时参数屏, OCR 服务不可用，请先启动服务。
        return
    }
    if (!SvsHealthMonitorLatestAvailable(HealthJson))
    {
        MsgBox, 48, 实时参数屏, 当前 OCR 服务不支持最新参数接口。请关闭旧服务并重新启动当前版本。
        return
    }
    if (LIVE_DATA_SCREEN_OPEN)
    {
        Gui, 3:Show
        return
    }

    BuildLiveDataScreenGui(true)
    SetTimer, RefreshLiveDataTimer, %LIVE_DATA_POLL_MS%
    RefreshLiveDataScreen()
}

BuildLiveDataScreenGui(ShowWindow := true) {
    global LIVE_DATA_SCREEN_OPEN, LiveDataTitle, LiveDataHeader, LiveDataValues, LiveDataPrivacy
    Gui, 3:+AlwaysOnTop +Resize +MinSize540x500
    Gui, 3:Margin, 12, 10
    Gui, 3:Font, s10, Microsoft YaHei UI
    Gui, 3:Add, Text, xm ym w510 vLiveDataTitle, 后台实时参数
    Gui, 3:Add, Text, xm y+5 w510 h38 +Border vLiveDataHeader, 接口已连接，等待后台监测数据...
    Gui, 3:Add, Edit, xm y+8 w510 h480 ReadOnly +VScroll vLiveDataValues, 尚未收到参数。请先在主工作台启动后台监测。
    Gui, 3:Add, Text, xm y+8 w510 vLiveDataPrivacy, 仅显示服务内存中的最新一轮；可能含敏感信息，请勿截图或外传。
    if (ShowWindow)
    {
        Gui, 3:Show, w680 h680, 后台实时参数
        LIVE_DATA_SCREEN_OPEN := true
        ResizeLiveDataScreen(680, 680)
    }
}

ResizeLiveDataScreen(GuiWidth, GuiHeight) {
    global LiveDataTitle, LiveDataHeader, LiveDataValues, LiveDataPrivacy
    Margin := 12
    ContentWidth := Max(320, GuiWidth - Margin * 2)
    HeaderY := 39
    EditY := 85
    FooterY := Max(130, GuiHeight - 34)
    EditHeight := Max(120, FooterY - EditY - 8)
    GuiControl, 3:Move, LiveDataTitle, % "x" . Margin . " y10 w" . ContentWidth
    GuiControl, 3:Move, LiveDataHeader, % "x" . Margin . " y" . HeaderY . " w" . ContentWidth . " h38"
    GuiControl, 3:Move, LiveDataValues, % "x" . Margin . " y" . EditY . " w" . ContentWidth . " h" . EditHeight
    GuiControl, 3:Move, LiveDataPrivacy, % "x" . Margin . " y" . FooterY . " w" . ContentWidth
}

CloseLiveDataScreen() {
    global LIVE_DATA_SCREEN_OPEN
    SetTimer, RefreshLiveDataTimer, Off
    LIVE_DATA_SCREEN_OPEN := false
    Gui, 3:Destroy
}

RefreshLiveDataScreen() {
    global LIVE_DATA_SCREEN_OPEN
    if (!LIVE_DATA_SCREEN_OPEN)
        return
    LiveJson := SvsGetMonitorLatestJson()
    if (!SvsHealthSuccess(LiveJson))
    {
        GuiControl, 3:, LiveDataHeader, 参数接口未连接；正在等待服务恢复...
        GuiControl, 3:, LiveDataValues, 无法读取 /monitor/latest。
        return
    }

    Running := SvsParseJsonBoolean(LiveJson, "running")
    SnapshotAvailable := SvsParseJsonBoolean(LiveJson, "snapshot_available")
    TickCount := SvsParseJsonNumber(LiveJson, "tick_count")
    SnapshotTick := SvsParseJsonNumber(LiveJson, "snapshot_tick_count")
    FieldCount := SvsParseJsonNumber(LiveJson, "field_count")
    EmptyCount := SvsParseJsonNumber(LiveJson, "empty_count")
    ReviewCount := SvsParseJsonNumber(LiveJson, "review_count")
    ErrorType := SvsParseJsonString(LiveJson, "last_error_type")
    StateText := Running ? "监测运行中" : "监测已停止"
    Header := StateText
    if (TickCount != "")
        Header .= "｜心跳 " . TickCount . " 轮"
    if (SnapshotTick != "")
        Header .= "｜显示第 " . SnapshotTick . " 轮"
    if (FieldCount != "")
        Header .= "`n字段 " . FieldCount
    if (EmptyCount != "")
        Header .= "｜空值 " . EmptyCount
    if (ReviewCount != "")
        Header .= "｜复核 " . ReviewCount
    if (ErrorType != "")
        Header .= "｜错误 " . ErrorType
    GuiControl, 3:, LiveDataHeader, %Header%

    if (!SnapshotAvailable)
    {
        GuiControl, 3:, LiveDataValues, 尚未收到参数。请先在主工作台启动后台监测。
        return
    }
    Summary := BuildLiveDataSummary(LiveJson)
    if (Summary = "")
        Summary := "最新一轮没有可显示字段。"
    GuiControl, 3:, LiveDataValues, %Summary%
}

BuildLiveDataSummary(Json) {
    EcgSummary := BuildLiveDataGroup(Json, GetPrefixList("ECG"))
    PatientSummary := BuildLiveDataGroup(Json, GetPrefixList("PATIENT"))
    Summary := ""
    if (EcgSummary != "")
        Summary .= "【心电参数】`r`n" . EcgSummary
    if (PatientSummary != "")
    {
        if (Summary != "")
            Summary .= "`r`n`r`n"
        Summary .= "【患者信息】`r`n" . PatientSummary
    }
    return Summary
}

BuildLiveDataGroup(Json, Prefixes) {
    Summary := ""
    Loop, Parse, Prefixes, |
    {
        Prefix := A_LoopField
        if (!SvsJsonHasNamedText(Json, Prefix))
            continue
        if (!GetRegionByPrefix(Prefix, X, Y, W, H, Label))
            Label := Prefix
        RawText := SvsParseJsonNamedText(Json, Prefix)
        DisplayValue := CleanValue(Prefix, RawText)
        if (DisplayValue = "" && RawText != "")
            DisplayValue := "[原始：" . RawText . "]"
        if (DisplayValue = "")
            DisplayValue := "[空]"
        Summary .= Label . "：" . DisplayValue . "`r`n"
    }
    return RTrim(Summary, "`r`n")
}

BuildSummaryFromJson(Json, Prefixes) {
    Summary := ""
    Loop, Parse, Prefixes, |
    {
        Prefix := A_LoopField
        if (!GetRegionByPrefix(Prefix, X, Y, W, H, Label))
            continue
        if (W <= 0 || H <= 0)
        {
            Summary .= Label . ": [未配置坐标]`n"
            continue
        }
        RawText := SvsParseJsonNamedText(Json, Prefix)
        Value := CleanValue(Prefix, RawText)
        DisplayValue := Value
        if (DisplayValue = "" && RawText != "")
            DisplayValue := "[原始：" . RawText . "]"
        if (DisplayValue = "")
            DisplayValue := "[空]"
        Summary .= Label . ": " . DisplayValue . "`n"
    }
    return RTrim(Summary, "`n")
}

BuildBatchDiagnostics(Json) {
    ModeLabel := SvsParseJsonString(Json, "ocr_mode_label")
    TotalMs := SvsParseJsonNumber(Json, "batch_elapsed_ms")
    OcrMs := SvsParseJsonNumber(Json, "batch_ocr_elapsed_ms")
    FallbackCount := SvsParseJsonNumber(Json, "fallback_count")
    EmptyCount := SvsParseJsonNumber(Json, "empty_count")
    AttemptLimit := SvsParseJsonNumber(Json, "attempt_limit")
    ReviewCount := SvsParseJsonNumber(Json, "review_count")
    if (ModeLabel = "" && TotalMs = "")
        return ""
    Summary := "诊断："
    if (ModeLabel != "")
        Summary .= ModeLabel . "模式"
    if (TotalMs != "")
        Summary .= "｜总 " . Round(TotalMs) . " ms"
    if (OcrMs != "")
        Summary .= "｜OCR " . Round(OcrMs) . " ms"
    if (FallbackCount != "")
        Summary .= "｜回退 " . FallbackCount . " 项"
    if (EmptyCount != "")
        Summary .= "｜未识别 " . EmptyCount . " 项"
    if (ReviewCount != "")
        Summary .= "｜需复核 " . ReviewCount . " 项"
    if (AttemptLimit != "")
        Summary .= "｜每项最多 " . AttemptLimit . " 轮"
    return Summary
}

PickAndUpdateRegion() {
    global OCR_FAST_MODE
    TargetScript := A_ScriptFullPath
    ActiveID := ""
    if (!IsScreenCoordinateMode())
    {
        ActiveID := GetOrSelectTargetWindow(false)
        if (!ActiveID)
            return
    }

    Gui, 1:Hide
    if (!IsScreenCoordinateMode())
    {
        WinActivate, ahk_id %ActiveID%
        Sleep, 200
        CoordMode, Mouse, Window
        CoordMode, ToolTip, Window
    }
    else
    {
        CoordMode, Mouse, Screen
        CoordMode, ToolTip, Screen
    }

    SetTimer, UpdatePickToolTip1, 50
    KeyWait, LButton, D
    SetTimer, UpdatePickToolTip1, Off
    MouseGetPos, X1, Y1
    KeyWait, LButton, U

    SetTimer, UpdatePickToolTip2, 50
    KeyWait, LButton, D
    SetTimer, UpdatePickToolTip2, Off
    MouseGetPos, X2, Y2
    KeyWait, LButton, U
    ToolTip

    X := X1 < X2 ? X1 : X2
    Y := Y1 < Y2 ? Y1 : Y2
    W := Abs(X2 - X1)
    H := Abs(Y2 - Y1)
    if (W < 5 || H < 5)
    {
        Gui, 1:Show
        MsgBox, 48, 提示, % "区域太小：W=" . W . ", H=" . H
        return
    }

    Prefix := SelectPrefixFromList(X, Y, W, H)
    if (Prefix = "")
    {
        Gui, 1:Show
        return
    }

    Prefix := Trim(Prefix, " `t")
    StringUpper, Prefix, Prefix
    if (!GetRegionByPrefix(Prefix, OldX, OldY, OldW, OldH, Label))
    {
        Gui, 1:Show
        MsgBox, 48, 提示, 不支持的参数名：%Prefix%
        return
    }

    FileRead, ScriptContent, %TargetScript%
    if (ErrorLevel)
    {
        Gui, 1:Show
        MsgBox, 16, 错误, 读取脚本失败。
        return
    }

    RegexX := "i)(\b" . Prefix . "_X\s*:=\s*)-?\d+"
    RegexY := "i)(\b" . Prefix . "_Y\s*:=\s*)-?\d+"
    RegexW := "i)(\b" . Prefix . "_W\s*:=\s*)\d+"
    RegexH := "i)(\b" . Prefix . "_H\s*:=\s*)\d+"
    ScriptContent := RegExReplace(ScriptContent, RegexX, "$1" . X, CountX, 1)
    ScriptContent := RegExReplace(ScriptContent, RegexY, "$1" . Y, CountY, 1)
    ScriptContent := RegExReplace(ScriptContent, RegexW, "$1" . W, CountW, 1)
    ScriptContent := RegExReplace(ScriptContent, RegexH, "$1" . H, CountH, 1)
    if (CountX != 1 || CountY != 1 || CountW != 1 || CountH != 1)
    {
        Gui, 1:Show
        MsgBox, 16, 错误, 坐标字段写回校验失败；原脚本未被覆盖。
        return
    }

    if (!SvsWriteScriptAtomically(TargetScript, ScriptContent))
    {
        Gui, 1:Show
        MsgBox, 16, 错误, 写回脚本失败；原脚本未被覆盖。
        return
    }
    SetRegionByPrefix(Prefix, X, Y, W, H)

    TestText := ""
    HealthJson := SvsGetHealthJson()
    if (SvsHealthSuccess(HealthJson) && SvsHealthOcrReady(HealthJson))
    {
        if (BuildScreenRect(ActiveID, X, Y, W, H, ScreenLeft, ScreenTop))
            TestJson := SvsOCRJson(ScreenLeft, ScreenTop, W, H, OCR_FAST_MODE, false, GetFieldType(Prefix))
        else
            TestJson := ""
        TestText := SvsParseJsonText(TestJson)
    }
    else if (SvsHealthSuccess(HealthJson))
        TestText := "[OCR引擎未就绪]"
    TestValue := CleanValue(Prefix, TestText)
    if (TestValue = "" && TestText != "")
        TestValue := TestText
    if (TestValue = "")
        TestValue := "[空]"
    ValidationNote := ValidationStatusText(SvsParseJsonString(TestJson, "validation_status"))
    if (ValidationNote != "")
        ValidationNote := "`n识别状态：" . ValidationNote
    Gui, 1:Show
    MsgBox, 64, 坐标已更新, % Label . " = X:" . X . " Y:" . Y . " W:" . W . " H:" . H . "`n" . CoordinateModeStatusText() . "`n立即 OCR：" . TestValue . ValidationNote . "`n已写回本脚本；当前会话已同步，无需重载。"
    return
}

SelectPrefixFromList(X, Y, W, H) {
    global PrefixPickerDone, PrefixPickerValue
    PrefixPickerDone := false
    PrefixPickerValue := ""
    Items := GetPrefixChoiceItems()
    Gui, PrefixPicker:New, +AlwaysOnTop +ToolWindow
    Gui, PrefixPicker:Margin, 12, 10
    Gui, PrefixPicker:Font, s10, Microsoft YaHei UI
    Gui, PrefixPicker:Add, Text, xm ym w440, % "坐标：X=" . X . ", Y=" . Y . ", W=" . W . ", H=" . H
    Gui, PrefixPicker:Add, Text, xm y+8 w440, 请选择要更新的参数：
    Gui, PrefixPicker:Add, ListBox, xm y+6 w440 h320 vPrefixPickerValue, %Items%
    Gui, PrefixPicker:Add, Button, xm y+10 w90 h30 Default gPrefixPickerOK, 确定
    Gui, PrefixPicker:Add, Button, x+8 w90 h30 gPrefixPickerCancel, 取消
    Gui, PrefixPicker:Show,, 更新坐标
    while (!PrefixPickerDone)
        Sleep, 50
    Gui, PrefixPicker:Destroy
    Gui, 1:Default
    return ExtractPrefixFromChoice(PrefixPickerValue)
}

EnsureServerReady() {
    HealthJson := SvsGetHealthJson()
    if (!SvsHealthSuccess(HealthJson))
    {
        UpdateOcrRuntimeStatus("")
        MsgBox, 48, OCR 服务, 后台服务不可用。请点击“启动服务”，等待窗口显示 running 后重试。
        return false
    }
    UpdateOcrRuntimeStatus(HealthJson)
    if (!SvsHealthOcrReady(HealthJson))
    {
        EngineName := SvsParseJsonString(HealthJson, "engine_name")
        RequestedEngine := SvsParseJsonString(HealthJson, "requested_engine")
        FallbackReason := SvsParseJsonString(HealthJson, "fallback_reason")
        MsgBox, 48, OCR 引擎未就绪, % "当前服务正在使用模拟 OCR，读取结果不可信。`n当前引擎：" . EngineName . "`n期望引擎：" . RequestedEngine . "`n原因：" . FallbackReason . "`n`n请关闭服务窗口，替换新版离线包后重新启动。"
        return false
    }
    return true
}

CopyLastOutput() {
    global LastJson, LastSummary
    if (LastSummary != "")
    {
        Clipboard := LastSummary
        MsgBox, 64, 已复制, 已复制最后一次汇总结果。
        return
    }
    if (LastJson != "")
    {
        Clipboard := LastJson
        MsgBox, 64, 已复制, 已复制最后一次 JSON。
        return
    }
    MsgBox, 48, 提示, 还没有读取结果。
}

ShowLastJson() {
    global LastJson
    if (LastJson = "")
    {
        MsgBox, 48, 提示, 还没有 API JSON。
        return
    }
    Clipboard := LastJson
    MsgBox, 64, 原始 JSON, % LastJson . "`n`nJSON 已复制到剪贴板。"
}

GetPrefixList(Mode) {
    if (Mode = "ECG")
        return "HR|PR|QTC|AXIS|RV5SV1|RV5"
    if (Mode = "PATIENT")
        return "NAME|GENDER|DOB|AGE|PAT_ID|PAT_DEPT|BED_NO|EXAM_DEPT|EXAM_TIME|EDIT_TIME|REQ_NO"
    return "HR|PR|QTC|AXIS|RV5SV1|RV5|NAME|GENDER|DOB|AGE|PAT_ID|PAT_DEPT|BED_NO|EXAM_DEPT|EXAM_TIME|EDIT_TIME|REQ_NO"
}

GetPrefixHelpText() {
    return StrReplace(GetPrefixChoiceItems(), "|", "`n")
}

GetPrefixChoiceItems() {
    return "HR 心室率|PR PR间期|QTC QTC间期|AXIS 心电轴|RV5SV1 RV5+SV1|RV5 RV5|NAME 姓名|GENDER 性别|DOB 出生日期|AGE 年龄|PAT_ID 患者编号|PAT_DEPT 患者科室|BED_NO 床号|EXAM_DEPT 检查科室|EXAM_TIME 检查时间|EDIT_TIME 编辑时间|REQ_NO 申请单号"
}

ExtractPrefixFromChoice(Choice) {
    Choice := Trim(Choice, " `t`r`n")
    if (Choice = "")
        return ""
    if (RegExMatch(Choice, "^[A-Z0-9_]+", M))
        return M
    return ""
}

SetRegionByPrefix(Prefix, X, Y, W, H) {
    global HR_X, HR_Y, HR_W, HR_H
    global PR_X, PR_Y, PR_W, PR_H
    global QTC_X, QTC_Y, QTC_W, QTC_H
    global AXIS_X, AXIS_Y, AXIS_W, AXIS_H
    global RV5SV1_X, RV5SV1_Y, RV5SV1_W, RV5SV1_H
    global RV5_X, RV5_Y, RV5_W, RV5_H
    global NAME_X, NAME_Y, NAME_W, NAME_H
    global GENDER_X, GENDER_Y, GENDER_W, GENDER_H
    global DOB_X, DOB_Y, DOB_W, DOB_H
    global AGE_X, AGE_Y, AGE_W, AGE_H
    global PAT_ID_X, PAT_ID_Y, PAT_ID_W, PAT_ID_H
    global PAT_DEPT_X, PAT_DEPT_Y, PAT_DEPT_W, PAT_DEPT_H
    global BED_NO_X, BED_NO_Y, BED_NO_W, BED_NO_H
    global EXAM_DEPT_X, EXAM_DEPT_Y, EXAM_DEPT_W, EXAM_DEPT_H
    global EXAM_TIME_X, EXAM_TIME_Y, EXAM_TIME_W, EXAM_TIME_H
    global EDIT_TIME_X, EDIT_TIME_Y, EDIT_TIME_W, EDIT_TIME_H
    global REQ_NO_X, REQ_NO_Y, REQ_NO_W, REQ_NO_H

    if (Prefix = "HR") {
        HR_X := X, HR_Y := Y, HR_W := W, HR_H := H
    } else if (Prefix = "PR") {
        PR_X := X, PR_Y := Y, PR_W := W, PR_H := H
    } else if (Prefix = "QTC") {
        QTC_X := X, QTC_Y := Y, QTC_W := W, QTC_H := H
    } else if (Prefix = "AXIS") {
        AXIS_X := X, AXIS_Y := Y, AXIS_W := W, AXIS_H := H
    } else if (Prefix = "RV5SV1") {
        RV5SV1_X := X, RV5SV1_Y := Y, RV5SV1_W := W, RV5SV1_H := H
    } else if (Prefix = "RV5") {
        RV5_X := X, RV5_Y := Y, RV5_W := W, RV5_H := H
    } else if (Prefix = "NAME") {
        NAME_X := X, NAME_Y := Y, NAME_W := W, NAME_H := H
    } else if (Prefix = "GENDER") {
        GENDER_X := X, GENDER_Y := Y, GENDER_W := W, GENDER_H := H
    } else if (Prefix = "DOB") {
        DOB_X := X, DOB_Y := Y, DOB_W := W, DOB_H := H
    } else if (Prefix = "AGE") {
        AGE_X := X, AGE_Y := Y, AGE_W := W, AGE_H := H
    } else if (Prefix = "PAT_ID") {
        PAT_ID_X := X, PAT_ID_Y := Y, PAT_ID_W := W, PAT_ID_H := H
    } else if (Prefix = "PAT_DEPT") {
        PAT_DEPT_X := X, PAT_DEPT_Y := Y, PAT_DEPT_W := W, PAT_DEPT_H := H
    } else if (Prefix = "BED_NO") {
        BED_NO_X := X, BED_NO_Y := Y, BED_NO_W := W, BED_NO_H := H
    } else if (Prefix = "EXAM_DEPT") {
        EXAM_DEPT_X := X, EXAM_DEPT_Y := Y, EXAM_DEPT_W := W, EXAM_DEPT_H := H
    } else if (Prefix = "EXAM_TIME") {
        EXAM_TIME_X := X, EXAM_TIME_Y := Y, EXAM_TIME_W := W, EXAM_TIME_H := H
    } else if (Prefix = "EDIT_TIME") {
        EDIT_TIME_X := X, EDIT_TIME_Y := Y, EDIT_TIME_W := W, EDIT_TIME_H := H
    } else if (Prefix = "REQ_NO") {
        REQ_NO_X := X, REQ_NO_Y := Y, REQ_NO_W := W, REQ_NO_H := H
    } else {
        return false
    }
    return true
}

CleanValue(Prefix, RawText) {
    if (GetFieldType(Prefix) = "number")
        return ExtractNumber(RawText)
    return ExtractText(RawText)
}

GetFieldType(Prefix) {
    if (Prefix = "HR" || Prefix = "PR" || Prefix = "QTC" || Prefix = "AXIS" || Prefix = "RV5SV1" || Prefix = "RV5" || Prefix = "AGE")
        return "number"
    if (Prefix = "DOB")
        return "date"
    if (Prefix = "EXAM_TIME" || Prefix = "EDIT_TIME")
        return "datetime"
    if (Prefix = "GENDER")
        return "gender"
    return "text"
}

ValidationStatusText(Status) {
    if (Status = "" || Status = "valid")
        return ""
    if (Status = "unreadable")
        return "未识别，建议人工复核"
    if (Status = "low_confidence")
        return "置信度偏低，建议人工复核"
    if (Status = "format_mismatch")
        return "格式不符合字段类型，建议人工复核"
    if (Status = "ocr_failed")
        return "OCR 失败，建议人工复核"
    return Status . "，建议人工复核"
}

ExtractNumber(Text) {
    Text := RegExReplace(Text, "\s+", "")
    if (RegExMatch(Text, "-?\d+\.?\d*", Match))
        return Match
    return ""
}

ExtractText(Text) {
    Text := RegExReplace(Text, "^\s+|\s+$", "")
    Text := RegExReplace(Text, "\n|\r", " ")
    return Text
}

GetRegionByPrefix(Prefix, ByRef X, ByRef Y, ByRef W, ByRef H, ByRef Label) {
    global HR_X, HR_Y, HR_W, HR_H
    global PR_X, PR_Y, PR_W, PR_H
    global QTC_X, QTC_Y, QTC_W, QTC_H
    global AXIS_X, AXIS_Y, AXIS_W, AXIS_H
    global RV5SV1_X, RV5SV1_Y, RV5SV1_W, RV5SV1_H
    global RV5_X, RV5_Y, RV5_W, RV5_H
    global NAME_X, NAME_Y, NAME_W, NAME_H
    global GENDER_X, GENDER_Y, GENDER_W, GENDER_H
    global DOB_X, DOB_Y, DOB_W, DOB_H
    global AGE_X, AGE_Y, AGE_W, AGE_H
    global PAT_ID_X, PAT_ID_Y, PAT_ID_W, PAT_ID_H
    global PAT_DEPT_X, PAT_DEPT_Y, PAT_DEPT_W, PAT_DEPT_H
    global BED_NO_X, BED_NO_Y, BED_NO_W, BED_NO_H
    global EXAM_DEPT_X, EXAM_DEPT_Y, EXAM_DEPT_W, EXAM_DEPT_H
    global EXAM_TIME_X, EXAM_TIME_Y, EXAM_TIME_W, EXAM_TIME_H
    global EDIT_TIME_X, EDIT_TIME_Y, EDIT_TIME_W, EDIT_TIME_H
    global REQ_NO_X, REQ_NO_Y, REQ_NO_W, REQ_NO_H

    if (Prefix = "HR") {
        X := HR_X, Y := HR_Y, W := HR_W, H := HR_H, Label := "心室率"
    } else if (Prefix = "PR") {
        X := PR_X, Y := PR_Y, W := PR_W, H := PR_H, Label := "PR 间期"
    } else if (Prefix = "QTC") {
        X := QTC_X, Y := QTC_Y, W := QTC_W, H := QTC_H, Label := "QTC"
    } else if (Prefix = "AXIS") {
        X := AXIS_X, Y := AXIS_Y, W := AXIS_W, H := AXIS_H, Label := "心电轴"
    } else if (Prefix = "RV5SV1") {
        X := RV5SV1_X, Y := RV5SV1_Y, W := RV5SV1_W, H := RV5SV1_H, Label := "RV5+SV1"
    } else if (Prefix = "RV5") {
        X := RV5_X, Y := RV5_Y, W := RV5_W, H := RV5_H, Label := "RV5"
    } else if (Prefix = "NAME") {
        X := NAME_X, Y := NAME_Y, W := NAME_W, H := NAME_H, Label := "姓名"
    } else if (Prefix = "GENDER") {
        X := GENDER_X, Y := GENDER_Y, W := GENDER_W, H := GENDER_H, Label := "性别"
    } else if (Prefix = "DOB") {
        X := DOB_X, Y := DOB_Y, W := DOB_W, H := DOB_H, Label := "出生日期"
    } else if (Prefix = "AGE") {
        X := AGE_X, Y := AGE_Y, W := AGE_W, H := AGE_H, Label := "年龄"
    } else if (Prefix = "PAT_ID") {
        X := PAT_ID_X, Y := PAT_ID_Y, W := PAT_ID_W, H := PAT_ID_H, Label := "患者编号"
    } else if (Prefix = "PAT_DEPT") {
        X := PAT_DEPT_X, Y := PAT_DEPT_Y, W := PAT_DEPT_W, H := PAT_DEPT_H, Label := "患者科室"
    } else if (Prefix = "BED_NO") {
        X := BED_NO_X, Y := BED_NO_Y, W := BED_NO_W, H := BED_NO_H, Label := "床号"
    } else if (Prefix = "EXAM_DEPT") {
        X := EXAM_DEPT_X, Y := EXAM_DEPT_Y, W := EXAM_DEPT_W, H := EXAM_DEPT_H, Label := "检查科室"
    } else if (Prefix = "EXAM_TIME") {
        X := EXAM_TIME_X, Y := EXAM_TIME_Y, W := EXAM_TIME_W, H := EXAM_TIME_H, Label := "检查时间"
    } else if (Prefix = "EDIT_TIME") {
        X := EDIT_TIME_X, Y := EDIT_TIME_Y, W := EDIT_TIME_W, H := EDIT_TIME_H, Label := "编辑时间"
    } else if (Prefix = "REQ_NO") {
        X := REQ_NO_X, Y := REQ_NO_Y, W := REQ_NO_W, H := REQ_NO_H, Label := "申请单号"
    } else {
        return false
    }
    return true
}

UpdatePickToolTip1:
    MouseGetPos, mX, mY
    ToolTip, 第一步：点击目标区域左上角..., mX + 30, mY + 30
return

UpdatePickToolTip2:
    MouseGetPos, mX, mY
    ToolTip, 第二步：点击目标区域右下角..., mX + 30, mY + 30
return

RemoveToolTip:
    ToolTip
return

; ==============================================================================
; OCR API 调用函数（已整合进本文件，主流程不再依赖外部 Include）
; ==============================================================================

SvsServerBaseUrl() {
    return "http://127.0.0.1:8181"
}

SvsCheckServer() {
    HealthJson := SvsGetHealthJson()
    return SvsHealthSuccess(HealthJson)
}

SvsGetHealthJson() {
    try
    {
        return SvsHttpRequest("GET", SvsServerBaseUrl() . "/health", "")
    }
    catch e
    {
        return ""
    }
}

SvsHealthSuccess(Json) {
    return SvsParseJsonBoolean(Json, "success")
}

SvsHealthOcrReady(Json) {
    if (!SvsHealthSuccess(Json))
        return false
    return SvsParseJsonBoolean(Json, "ocr_ready")
}

SvsHealthBackgroundMonitorAvailable(Json) {
    if (!SvsHealthSuccess(Json))
        return false
    return SvsParseJsonBoolean(Json, "background_monitor_available")
}

SvsHealthMonitorLatestAvailable(Json) {
    if (!SvsHealthSuccess(Json))
        return false
    return SvsParseJsonBoolean(Json, "monitor_latest_available")
}

SvsOCR(Left, Top, Width, Height, FastMode = false) {
    JsonOutput := SvsOCRJson(Left, Top, Width, Height, FastMode)
    return SvsParseJsonText(JsonOutput)
}

SvsOCRJson(Left, Top, Width, Height, FastMode = false, SaveDebug = false, FieldType = "text") {
    JsonPayload := "{""rect"": [" . Left . "," . Top . "," . Width . "," . Height . "]"
    JsonPayload .= ", ""fast_mode"": " . SvsBoolJson(FastMode)
    JsonPayload .= ", ""save_debug"": " . SvsBoolJson(SaveDebug)
    JsonPayload .= ", ""field_type"": """ . SvsJsonEscape(FieldType) . """"
    JsonPayload .= "}"
    return SvsHttpRequest("POST", SvsServerBaseUrl() . "/ocr", JsonPayload)
}

SvsOCRJsonInWindow(WindowID, X, Y, W, H, FastMode = false, SaveDebug = false, FieldType = "text") {
    WinGetPos, WinX, WinY,,, ahk_id %WindowID%
    if (ErrorLevel)
        return "{""success"":false,""error"":""window not available"",""text"":""""}"
    ScreenLeft := WinX + X
    ScreenTop := WinY + Y
    return SvsOCRJson(ScreenLeft, ScreenTop, W, H, FastMode, SaveDebug, FieldType)
}

SvsOCRInWindow(WindowID, X, Y, W, H, FastMode = false) {
    WinGetPos, WinX, WinY,,, ahk_id %WindowID%
    if (ErrorLevel)
        return ""
    ScreenLeft := WinX + X
    ScreenTop := WinY + Y
    return SvsOCR(ScreenLeft, ScreenTop, W, H, FastMode)
}

SvsOCRBatchJson(ItemsJson, FastMode = false, SaveDebug = false) {
    JsonPayload := "{""items"": [" . ItemsJson . "]"
    JsonPayload .= ", ""fast_mode"": " . SvsBoolJson(FastMode)
    JsonPayload .= ", ""save_debug"": " . SvsBoolJson(SaveDebug)
    JsonPayload .= "}"
    return SvsHttpRequest("POST", SvsServerBaseUrl() . "/ocr/batch", JsonPayload)
}

SvsOCRMonitorTickJson(ItemsJson, FastMode = false, SaveDebug = false) {
    JsonPayload := "{""items"": [" . ItemsJson . "]"
    JsonPayload .= ", ""fast_mode"": " . SvsBoolJson(FastMode)
    JsonPayload .= ", ""save_debug"": " . SvsBoolJson(SaveDebug)
    JsonPayload .= "}"
    return SvsHttpRequest("POST", SvsServerBaseUrl() . "/monitor/tick", JsonPayload)
}

SvsStartMonitorJson(ItemsJson, FastMode = true, IntervalSeconds = 5) {
    JsonPayload := "{""items"": [" . ItemsJson . "]"
    JsonPayload .= ", ""fast_mode"": " . SvsBoolJson(FastMode)
    JsonPayload .= ", ""save_debug"": false"
    JsonPayload .= ", ""interval_seconds"": " . IntervalSeconds
    JsonPayload .= "}"
    return SvsHttpRequest("POST", SvsServerBaseUrl() . "/monitor/start", JsonPayload)
}

SvsStopMonitorJson() {
    return SvsHttpRequest("POST", SvsServerBaseUrl() . "/monitor/stop", "{}")
}

SvsGetMonitorStatusJson() {
    return SvsHttpRequest("GET", SvsServerBaseUrl() . "/monitor/status", "")
}

SvsGetMonitorLatestJson() {
    return SvsHttpRequest("GET", SvsServerBaseUrl() . "/monitor/latest", "")
}

SvsHttpRequest(Method, Url, Body) {
    try
    {
        Http := ComObjCreate("WinHttp.WinHttpRequest.5.1")
        Http.SetTimeouts(1000, 1000, 3000, 30000)
        Http.Open(Method, Url, false)
        if (Method = "POST")
            Http.SetRequestHeader("Content-Type", "application/json; charset=utf-8")
        Http.Send(Body)
        ResponseText := Http.ResponseText
        if (Http.Status >= 200 && Http.Status < 300)
            return ResponseText
        if (ResponseText != "")
            return ResponseText
        return SvsBuildClientErrorJson("http_status", "HTTP 状态 " . Http.Status)
    }
    catch e
    {
        return SvsBuildClientErrorJson("http_request_failed", "请求失败或超时：" . e.Message)
    }
}

SvsBuildClientErrorJson(ErrorCode, ErrorMessage) {
    return "{""success"":false,""error_code"":""" . SvsJsonEscape(ErrorCode) . """,""error"":""" . SvsJsonEscape(ErrorMessage) . """}"
}

SvsBuildBatchItem(WindowID, Name, X, Y, W, H) {
    if (W <= 0 || H <= 0)
        return ""
    WinGetPos, WinX, WinY,,, ahk_id %WindowID%
    if (ErrorLevel)
        return ""
    ScreenLeft := WinX + X
    ScreenTop := WinY + Y
    return "{""name"":""" . SvsJsonEscape(Name) . """,""rect"":[" . ScreenLeft . "," . ScreenTop . "," . W . "," . H . "]}"
}

SvsAppendBatchItem(ItemsJson, ItemJson) {
    if (ItemJson = "")
        return ItemsJson
    if (ItemsJson = "")
        return ItemJson
    return ItemsJson . "," . ItemJson
}

SvsAppendJsonItem(ItemsJson, ItemJson) {
    if (ItemJson = "")
        return ItemsJson
    if (ItemsJson = "")
        return ItemJson
    return ItemsJson . "," . ItemJson
}

SvsAppendJsonPair(PairsJson, Name, Value) {
    PairJson := """" . SvsJsonEscape(Name) . """:""" . SvsJsonEscape(Value) . """"
    if (PairsJson = "")
        return PairJson
    return PairsJson . "," . PairJson
}

SvsParseJsonText(Json) {
    if (RegExMatch(Json, """text"":\s*""((?:\\.|[^""\\])*)""", M))
        return SvsJsonUnescape(M1)
    return ""
}

SvsParseJsonNamedText(Json, Name) {
    Pattern := """texts"":\s*\{[\s\S]*?""" . SvsRegexEscape(Name) . """:\s*""((?:\\.|[^""\\])*)"""
    if (RegExMatch(Json, Pattern, M))
        return SvsJsonUnescape(M1)
    return ""
}

SvsJsonHasNamedText(Json, Name) {
    Pattern := """texts"":\s*\{[\s\S]*?""" . SvsRegexEscape(Name) . """:\s*"""
    return RegExMatch(Json, Pattern)
}

SvsParseJsonSummary(Json) {
    if (RegExMatch(Json, """summary"":\s*""((?:\\.|[^""\\])*)""", M))
        return SvsJsonUnescape(M1)
    return ""
}

SvsParseJsonString(Json, Key) {
    Pattern := """" . Key . """:\s*""((?:\\.|[^""\\])*)"""
    if (RegExMatch(Json, Pattern, M))
        return SvsJsonUnescape(M1)
    return ""
}

SvsParseJsonNumber(Json, Key) {
    Pattern := """" . Key . """:\s*(-?\d+(?:\.\d+)?)"
    if (RegExMatch(Json, Pattern, M))
        return M1
    return ""
}

SvsParseJsonBoolean(Json, Key) {
    Pattern := """" . Key . """:\s*(true|false)"
    if (RegExMatch(Json, Pattern, M))
        return (M1 = "true")
    return false
}

SvsJsonEscape(Text) {
    StringReplace, Text, Text, \, \\, All
    Quote := Chr(34)
    BackslashQuote := "\" . Quote
    StringReplace, Text, Text, %Quote%, %BackslashQuote%, All
    StringReplace, Text, Text, `r, \r, All
    StringReplace, Text, Text, `n, \n, All
    return Text
}

SvsJsonUnescape(Text) {
    StringReplace, Text, Text, \n, `n, All
    StringReplace, Text, Text, \r, `r, All
    StringReplace, Text, Text, \", ", All
    StringReplace, Text, Text, \\, \, All
    return Text
}

SvsRegexEscape(Text) {
    Text := RegExReplace(Text, "([\\\.\*\?\+\[\{\|\(\)\^\$])", "\$1")
    return Text
}

SvsBoolJson(Value) {
    return Value ? "true" : "false"
}
