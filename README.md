# MT6639 / Xiaomi 15S Pro (Dijun) & Xiaomi Pad 7S Pro / 7 Ultra Wi-Fi 与蓝牙能力研究

> **English abstract.** An open research archive of the MediaTek MT6639 Wi-Fi/Bluetooth stack on
> Xiaomi 15S Pro (*dijun*), Xiaomi Pad 7S Pro (*violin*), and Xiaomi Pad 7 Ultra (*jinghu*) across
> HyperOS 3 (Linux 6.6 fallback) and HyperOS 4 (Linux 6.18): what the vendor firmware actually gates,
> which configurations can be reproduced on real hardware, and where the limits are. It ships read-only
> inspection and evidence tooling, host-side offline tests, native fixtures, and the source of
> KernelSU/LSPosed helper modules. It is **not** a one-click unlock: the project records both successful
> experiments and the checks that still fail. See [DISCLAIMER.md](DISCLAIMER.md) for the compliance boundary.

这是一个**实验研究档案**，不是"全功能解锁完成"的日用发行版。它记录 MT6639 在 Xiaomi 15S Pro（`dijun`）及小米平板 7S Pro（`violin`）/ 7 Ultra（`jinghu`）上的能力边界：哪些 6GHz / Wi-Fi 7 / EHT / 热点 / 蓝牙特性在特定固件版本和特定 AP 条件下真的跑通过，哪些只是属性声明，哪些被门禁或生命周期问题挡住。

历史上已在特定版本、独立 6GHz 网络中取得 6GHz / 320MHz EHT 双端关联和 ICMP 证据，并在 `jinghu`（HyperOS 3 / Enforcing）实测确认 6GHz 60 信道注册与界面角标显示；重复回包、吞吐、冷启动恢复机制与长期稳定性尚未全部验收。属性或 PHY 速率不代表真实业务通过。

## 项目边界

- `dijun-oneclick` 之类的外部 root / KernelSU 工具不是本项目的一部分；本项目只消费已经可用的 root 权限，不改提权链，不改 BL。
- 不打包 root 利用链、原厂驱动/固件、NVRAM、校准数据、AP/手机凭据或第三方未经许可的 APK 与脚本。
- 默认不注入固定虚假地区或无条件修改功率与校准。6GHz 模块默认采用 `auto` 自适应策略：若系统当前已在 6GHz 放行地区（如用户自行使用第三方国家码模块设为 `US`/`JP`/`TW` 等）则保留现状不覆盖，仅在 `CN`/`00` 基线时临时切换并在关闭时按原基线精确还原；亦可通过 `/data/adb/mt6639-6g/country.conf` 显式设为 `keep` 或指定两位国家码。
- 二进制构件（模块 ZIP、APK、以及其中第三方构建的 `hostapd`）只在 GitHub Release 发布，不进仓库树；仓库只保留可复现源码与构建脚本。

## 仓库内容

| 目录 | 内容 |
| --- | --- |
| `tools/` | 只读巡检、DUP/OFDMA/打孔取证、F01-F10 矩阵运行器、自测日志收集器构建、发布审计；依赖 Python 3 标准库 |
| `tests/` | 主机侧离线测试（481 项），含 C 夹具、Java LSPosed 夹具与 shell 契约测试 |
| `native/` | 原生夹具源码：恢复事务、netdev、mlo-gate、hostapd、TWT 桥 |
| `modules/` | KernelSU / LSPosed 模块源码与可复现构建脚本 |
| `docs/` | 精选研究记录 44 篇：能力台账、安装与测试指南、门禁分析、逐项验证与部署/构建说明 |
| `release/` | 候选构件清单与校验值；构件本体在 [Releases](../../releases) |

## 快速开始

```sh
# 主机侧离线测试（不需要真机）
python3 -m unittest discover -s tests -v

# 包解析器自测
python3 tools/summarize_icmp_pcap.py --self-test

# 只读巡检：--out 必须是尚不存在的新目录，ADB 路径必须是绝对路径
python3 tools/inspect_mt6639.py --adb /absolute/path/to/adb \
  --serial YOUR_SERIAL --out /tmp/NEW_INSPECTION
```

测试会按环境自动 `skip`：缺少 `aapt2` / `javac` / Android SDK `android.jar`，或缺少本地已构建的模块构件时，对应用例会带原因跳过，不会伪装成通过。CI（`.github/workflows/ci.yml`）会尽量补齐 Android 工具链后再跑一轮。

巡检工具不扫描、不切网、不改国家码、不挂载或加载驱动；每次调用有超时，失败/空输出保留具体状态。原始输出可能包含网络身份，摘要只保留派生信息。工具退出 0 表示完成采集/解析，不表示所有探针成功、6GHz 可用或恢复通过。

## 研究结论入口

- [群友自测安装与使用指南](docs/群友自测安装与使用指南-dijun-violin-jinghu.md)：适用范围、安装步骤、国家码设置与一键导出脱敏排障日志。
- [能力台账](docs/mt6639-plan/能力台账.md)：逐项登记已实现 / 已最小验证 / 已完整验收，以及未晋级原因。
- [范围-手机终端适用](docs/mt6639-plan/范围-手机终端适用-20261028.md)：适配范围与用例基线。
- [6GHz 门禁与生命周期](docs/分析-MT6639六GHz门禁与生命周期-20261022.md)、[全部监管表](docs/分析-MT6639全部监管表-20261022.md)、[信道禁用路径](docs/分析-MT6639信道禁用路径-20261022.md)、[OS4 新版兼容与动态状态卡](docs/研究-OS4新版兼容与模块状态卡-20261115.md)。
- [国家码来源与 force-country-code 生命周期](docs/研究-国家码来源与force-country-code生命周期-20261027.md)。
- [原生 EHT 热点与共享接线](docs/部署-MT6639-UI-LSPosed-20261023.md)、[热点模块生命周期与可复现构建](docs/验证-热点模块生命周期与构建-20261025.md)。
- [DUP 三点取证](docs/分析-DUP三点取证方案-20261028.md)、[F04 正式测试流程](docs/分析-F04正式测试流程-20261028.md)、[打孔 ABC 三段](docs/验证-打孔ABC三段-20261028.md)。
- [恢复判定器 T02A](docs/mt6639-plan/T02A-恢复判定器-20261023.md)、[T02B 进程与 WAL 骨架](docs/mt6639-plan/T02B-进程与WAL骨架-20261023.md)。

完整目录见 [docs/README.md](docs/README.md)。

## 数据脱敏约定

研究记录中的设备标识已统一替换，复现时请使用你自己的实验环境值：

- 手机序列号 → `<PHONE_SERIAL>`，个人目录 → `<HOME>`；会话 UUID → `<UUID>`。
- MAC / BSSID → `02:00:00:*` 文档地址（保留后两字节以便区分不同节点），IPv4 → `198.51.100.0/24`、`203.0.113.0/24`（RFC 5737 文档用途网段）。
- 实验 AP 的 SSID 统一改为 `LabNet-*` / `LabMLO`。
- Java 包名 `com.hiliang.*` 是模块作者的代码命名空间，与位置信息无关。

## Release 构件

`v0.3.0-pre`（预发布，多设备适配与国家码自定义候选，非稳定版）包含以下独立构件与一站式分发集合包（同时保留来自 `@xiatian410` [PR #1](https://github.com/Hi-Jiajun/mt6639-wifi7-dijun/pull/1) 的 OS4 兼容表、动态状态卡与 root `0700` 工作目录贡献）：

| 构件 | 说明 | SHA-256（同时见 `release/SHA256SUMS`） |
| --- | --- | --- |
| `mt6639-6g-v0.4.1.zip` | 6G 模块 `v0.4.1`（`24`） | `4358a6cfe723966568b8a0c47b803076284cdebd05ca7246e18862bd8f02a23b` |
| `mt6639-hotspot-v0.3.1.zip` | 热点模块 `v0.3.1`（`32`） | `a5dfb8c173535afcbfc87553b9e13e37c752b407b6a04add117f35005c1c8d01` |
| `mt6639-bt-v0.2.0.zip` | 蓝牙模块 `v0.2.0`（`4`） | `e3ed3b48a4caff8f7ab1ebfb5153a150fd1cbe6ebf2b98eafa522292561e339e` |
| `mt6639-ui-lsposed-0.3.0.apk` | UI 增强模块 `0.3.0`（`14`） | `3bd8da0594c07975ed637b4c5a4baca40187a3bf4e69b908f3cbad1aba4ae483` |
| `mt6639-debug-collector-v0.1.1.zip` | 只读日志收集器 `v0.1.1`（`2`） | `94a0881423d0764d15d28ee4a3fe5eb52b3c5ff186e3c1ec6603b2364689609e` |
| `mt6639-aware-service-0.1.3.apk` | Aware 服务 `0.1.3`（`4`） | `42a75c62aa4dc00f310611eac74ccc7addf2e6ece4782287cf3c66fe410e86e7` |
| `mt6639-group-test-pack-20261122-v0.4.1.zip` | 自测整合包（先解压再逐个刷入内部模块与 APK） | `7bed1de252e2bf4a44882191a428447cd938b36a1661b737b44bb5fec75ae245` |

这些是候选，不是"全功能可用"承诺：6G 模块在 Enforcing 生命周期、冷启动、长稳上仍有未完成项；热点模块默认关闭；Aware / 并发门禁只用于有界实验，不要在实验范围外启用。

## 许可证

本仓库自有代码与文档以 **GPL-3.0** 发布，全文见 [LICENSE](LICENSE)。Release 中的构件包含第三方组件（例如 BSD 许可的 `hostapd`），这些组件不因本仓库的 GPL 授权而改变许可；分发或再分发前请自行核对各组件的许可条款。商标、固件与驱动归属各自权利人。

## 贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。提交前请确认没有真实设备标识、凭据、原厂固件或未授权第三方二进制。
