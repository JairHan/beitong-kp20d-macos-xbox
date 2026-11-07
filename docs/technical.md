# 技术说明

## 根因与证据边界

同一手柄的蓝牙路径原本可被 macOS 识别为 Xbox；有线与接收器曾在未完成 XInput 握手后回退为 NS。OpenJoy 的虚拟/转换输入会掩盖物理设备的原生驱动状态，因此验收必须关闭它并冷拔插。

有线原始 mode 0 描述符使用 20bc:5158，未匹配本机 Apple Xbox 驱动。早期试验只跳过超时等待，没有写初始化标志，会继续回退。v2 同时修正身份与初始化状态后，本机 Apple 驱动完成持续输入读取；这是此次实测有效的改动组合，并非对所有 macOS 版本兼容性的保证。

## 镜像格式与修改位置

精确官方镜像：340302 字节。34 字节容器头；segment 0 偏移 34、长度 100452；segment 1 偏移 100486、长度 239816。segment 0 映射基址为 ARM 0x08003000。

| ARM 地址 | 变更 |
| --- | --- |
| 0x080196DA | 16 字节 `00f0a2fe40f2dc518842f8d303200be0` → `012084f8540070bd00bf00bf00bf00bf` |
| 0x0801ADB4 + 8 | VID/PID 字节 `bc205851` → `5e048e02` |

新指令为 `movs r0, #1; strb.w r0, [r4, #0x54]; pop {r4,r5,r6,pc}`，之后用 NOP 填满原区域。对应初始化标志 0x20004714。保留 USB bDeviceClass=0 和厂商 HID 接口，不重写输入报告。重新计算两个分段 CRC16-XMODEM 和容器 CRC16-XMODEM；第二段 payload 保持逐字节相同。

构建器提供逆变换，并验证逆变换结果必须等于精确官方 SHA256。版本字符串 1.1.1 未改变。

## Mac 本机传输

原始 20bc:5158 初始连接阶段，工具通过 IOUSBLib 请求 Microsoft OS string 0xEE 及兼容描述符（C0/EE，index 4），并持续异步读取 interface 0 的 IN 0x81，以保持初始化窗口之后连接稳定。只查询辅助 HID 的版本不足以保持这一路径；另一个输入活动检查会触发回退。

新 045e:028e 身份下由 Apple 驱动负责正常输入读取。工具仅访问厂商 HID 维护接口，不抢占 Apple 游戏输入接口。

固件传输使用 64 字节 HID 报告：`02 0f channel` 后接最多 61 字节逻辑帧。逻辑帧：`7e + body长度LE16 + 22 00 operation + payload + CRC16-XMODEM LE16 + e7`。每块 256 字节，末块填零；顺序 channel 2（segment 1），再 channel 1（segment 0）。

工具检查准备应答、擦除进度、每块序号与 sum16，以及最终 padded CRC16。成功应答不能等同于完整 Flash 回读或断电恢复保证。原始开发工具帧与官方主机模块比对了 6656 帧，相关历史证据见 evidence；本次公开工具仅调整目录和移除依赖私有逆向样本的自测入口。

## 实测范围

一只 BTP-KP20D；Mac 本机刷写于 2026-11-08（Asia/Shanghai）结束。随后关闭 OpenJoy、退出刷写工具、冷拔插，Apple Xbox 驱动和 GameController 均识别；操作输入样本与刷写摘要见 evidence。接收器、Windows、震动及延迟没有后续实测。
