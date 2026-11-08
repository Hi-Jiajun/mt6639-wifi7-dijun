# 免责与合规边界

> **English summary.** This repository documents and instruments a device the author owns.
> It deliberately does not ship a privilege-escalation chain, vendor firmware, calibration data,
> credentials, or "enable everything" regulatory defaults, and it is not a way to bypass local
> radio regulations. Use it only on hardware you own and only in bands, channels and power levels
> your jurisdiction allows.

## 1. 用途范围

本仓库是**个人对自有设备的研究记录与配套只读/受控工具**，用于理解 MT6639 在 Xiaomi 15S Pro 上的能力边界与失效条件。所有实验都在作者自有、可恢复的设备与自有 AP 上完成。

## 2. 不提供的部分

仓库**不包含、也不提供**：

- root / 提权链、解锁 Bootloader 的方法或工具；
- 原厂驱动、固件、NVRAM、校准数据、监管二进制表；
- AP 或手机的口令、密钥、证书、原始抓包中的设备身份；
- "功能最全国家码"默认值，或绕过本地无线电监管的具体步骤。

国家码、信道、功率与校准是**分离的条件**。研究记录中出现 `iw reg set`、`force-country-code`、功率相关命令，是为了说明门禁存在于哪一层、以及为什么不能把它当成配置项，不是可直接照抄的"解锁配方"。把设备配置成当地不允许的频段、信道或功率属于违法风险，责任由使用者承担。

## 3. 风险自负

在本仓库记录之外使用相关模块、脚本或构件，可能造成设备无线不可用、无法开机、数据丢失或违反设备保修与服务条款。作者不对任何后果作出担保。恢复能力以[能力台账](docs/mt6639-plan/能力台账.md)与[候选安装与回滚](docs/交付-MT6639候选安装与回滚-20261026.md)记录为准，未完成验收的路径不应被当作安全可回滚。

## 4. 第三方权利

- 商标、固件、驱动与系统组件归各自权利人；本仓库不主张任何权利。
- Release 构件中的第三方组件（例如 BSD 许可的 `hostapd`）保持其原有许可，不因本仓库的 GPL-3.0 而改变。
- 本仓库不含未经授权分发的第三方 APK、脚本或私有源码。

## 5. 研究诚信

记录里保留失败、阻塞与撤回的结论。请不要把"属性可读"、"PHY 速率"或"单次通过"当作功能可用或长期稳定的证据；每个结论的适用范围以对应验证记录的日期、固件版本和前提条件为准。

## 6. 报告问题

若你发现仓库中包含不应公开的标识、凭据或第三方内容，请按 [CONTRIBUTING.md](CONTRIBUTING.md) 的方式私下告知，不要先公开传播。
