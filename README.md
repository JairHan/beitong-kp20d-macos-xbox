# 北通 KP20D：macOS 有线原生 Xbox 补丁

针对 **BTP-KP20D（鲲鹏 20）及指定 1.1.1 固件**的实验性固件补丁和 Mac 本机刷写工具。修复后由 macOS 自带 Xbox 驱动接管，无需 OpenJoy、虚拟机或后台输入转换工具。

> 仅在一只 KP20D、macOS 27.3 上完成有线实测。USB 产品名和版本号不足以证明其他硬件批次兼容；切勿用于其他型号。刷写中断可能导致手柄无法启动，本项目没有验证断电救砖。已经正常工作的设备无需重复刷写。

## 已验证与未验证

| 项目 | 结果 |
| --- | --- |
| Mac 本机刷写 | 两个固件段均收到设备 CRC 成功应答；没有完整 Flash 回读 |
| 原生驱动 | `com.apple.gamecontroller.driver.XboxGamepad` / `Xbox360Gamepad` |
| 游戏输入 | GameController 识别一个手柄，`extendedGamepad = true` |
| 冷拔插 | OpenJoy 关闭、刷写工具退出后仍能识别 |
| 操作 | ABXY、LB/RB、LS/RS、开始/返回、十字键、双摇杆、模拟 LT/RT |
| 维护接口 | 修改 USB 身份后，两次间隔 3.5 秒的版本查询均成功 |
| Windows | 尚未实测；预计 XInput 可用，但官方配置/升级软件可能因 ID 改变而无法识别 |
| 接收器 | 没有修改、没有验证 |
| 蓝牙 | 原来可用，本次未在刷写后重新测试 |
| 震动、长时间游戏、延迟 | 尚未验证或测量 |

GameController 将手柄分类为 Xbox One，而底层驱动名为 Xbox360Gamepad；这代表 API 的分类结果，不代表硬件变成相应型号。[实测证据](evidence/)保存了验收摘要。历史离线证据针对当时的开发工具；发布版本另有可重复运行的离线测试。

## 修补内容

1. 仅将有线 mode 0 的 USB VID/PID 从 `20bc:5158` 改为 `045e:028e`，使 Apple Xbox 驱动匹配。
2. 修正 mode 0 初始化结束逻辑，设置初始化完成标记，避免早期补丁只跳过超时判断却未更新状态。
3. 保留复合设备 class 0、辅助厂商 HID 维护接口、输入报告和端点，以及固件第二段。

[技术说明](docs/technical.md)列出偏移、ARM 指令和传输流程。版本字符串仍为 **1.1.1**，不能用版本号单独判断是否装了补丁。

## 准备

- macOS、Python 3.9 或更新版本、Xcode Command Line Tools（`xcode-select --install`）。
- USB 数据线直连 Mac，保持供电稳定。关闭 OpenJoy 及其他手柄管理/升级工具。
- 从 [v2.0.0 Release](https://github.com/JairHan/beitong-kp20d-macos-xbox/releases/tag/v2.0.0) 下载独立附件 `KP20D-official-1.1.1.bin`，或自行从厂商渠道取得对应镜像。该附件是本次使用的未修改官方原件；源码仓库和工具压缩包不包含完整固件。
- 官方镜像必须为 340302 字节，SHA256：

```text
6574054de479bb44becf1f7b338d8a05d1190bdae28f8702f463afb1324d11b9
```

## 构建和生成固件（离线，不访问手柄）

在仓库根目录执行。文件名中的路径均为本地相对路径。

```sh
make
mkdir -p firmware logs
# 将下载或自行取得的、哈希相符的官方固件放在：
# firmware/KP20D-official-1.1.1.bin
python3 tools/build_firmware.py firmware/KP20D-official-1.1.1.bin --output firmware/KP20D-nativeXboxUSB-v2.bin
```

预期补丁镜像 SHA256：

```text
b8f9a7e864b0750eda4ebb86af19b19d9a0c09f588ae5178cabc877b3f38857b
```

构建器只接受上述官方镜像，检查旧指令和描述符，重新计算容器与分段 CRC。输出文件已存在时会拒绝覆盖。

## 检查与刷写（会访问手柄）

先执行只读检查。若手柄已进入 NS 模式，工具会等待重新插入 USB 后的初始 XInput 阶段；连接目标选择 Mac。

```sh
python3 tools/native_updater.py --inspect --journal logs/inspect-01.jsonl
```

只读检查成功不代表固件一定适合其他硬件批次。确认型号与镜像正确后刷写：

```sh
python3 tools/native_updater.py --flash firmware/KP20D-nativeXboxUSB-v2.bin --journal logs/flash-01.jsonl
```

每次使用新的日志文件名。工具先重复查询版本、等待连接稳定，再依次传输 channel 2 和 channel 1。只有每块应答与校验匹配才会发出最终 CRC 请求；失败不会自动从头重刷。刷写期间不要拔线、休眠或退出工具。

成功结束后，退出工具并重新插入 USB。确认 OpenJoy 未运行，再在 macOS 游戏控制器设置及实际游戏中验证全部输入。正常使用无需运行本项目工具。

工具只选择产品名前缀 `BTP-KP20D`、指定 VID/PID 和厂商 HID usage。**一次只连接一只目标手柄**；这些筛选无法识别所有硬件修订。

## 恢复官方固件

官方镜像应在刷写前保留好。若辅助维护接口仍可访问，可用相同工具传回原件：

```sh
python3 tools/native_updater.py --flash firmware/KP20D-official-1.1.1.bin --journal logs/restore-01.jsonl
```

恢复会撤销本项目的 macOS 原生有线兼容改动。新 USB 身份下的维护接口已只读验证；**用发布工具从 v2 完整恢复官方固件尚未实测**，也不能用于保证设备变砖后能恢复。构建器的 `--restore` 只还原磁盘镜像，不写入设备。

## 离线测试

```sh
make test
```

不需要 USB、虚拟机、OpenJoy 或官方镜像。使用合成数据检查帧应答、拒绝错误校验、重试和超时后停止提交等行为。离线测试不能替代真实硬件验收。

## Release 与许可

Release 提供Apple Silicon（arm64）原生库和源码工具压缩包；库的构建最低部署目标为 macOS 12.0，但仅在 macOS 27.3 实测；其他架构建议在本机运行 `make`。这些工具无需安装常驻服务。原生库未经过 Apple 公证。

本项目自行编写的工具与文档采用 MIT 许可；Release 中的厂商固件原件仅作为本型号的构建输入与恢复备份，版权归原权利人，不受本项目 MIT 许可授权；商标和第三方逆向样本亦不在该许可范围内。本项目与北通、Microsoft、Apple 没有关联。
