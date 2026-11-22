# Release 构件

二进制构件不进仓库树，发布在 GitHub Releases。本目录只保留清单与校验值。

`v0.3.0-pre`（预发布，多设备适配实验候选）附以下构件与一站式测试集合包：

| 构件 | 版本 | 说明 |
| --- | --- | --- |
| `mt6639-6g-v0.4.1.zip` | 6G 模块 v0.4.1 (`24`) | 支持 `dijun` (OS4)、`violin` / `jinghu` (OS3 Linux 6.6 + OS4 Linux 6.18)；支持任意 2 位国家码基线、`/data/adb/mt6639-6g/country.conf`（`auto` / `keep` / 自定义两位码）、按原基线精确恢复、断网快速恢复及 `SHA256SUMS` 修复 |
| `mt6639-hotspot-v0.3.1.zip` | 热点模块 v0.3.1 (`32`) | 原生 EHT 热点（默认关闭），支持平板无卡纯本地直连 `LocalHotspotState`、15 秒空闲快照刷新与跨重启自动核验归档；内含第三方构建的 `hostapd` |
| `mt6639-bt-v0.2.0.zip` | 蓝牙模块 v0.2.0 (`4`) | 控制器特性与 LE Audio 6 项 Host Profile 受控入口（多机型兼容表） |
| `mt6639-ui-lsposed-0.3.0.apk` | UI 增强 0.3.0 (`14`) | LSPosed 模块，支持系统 Wi-Fi 列表 2.4G/5G/6G 角标与热点控制面板（OS3/OS4 通用） |
| `mt6639-debug-collector-v0.1.1.zip` | 日志收集器 v0.1.1 (`2`) | 纯只读脱敏排障日志导出器（兼容 Toybox、采集 `apply.log` 与 `country.conf`） |
| `mt6639-aware-service-0.1.3.apk` | Aware 服务 0.1.3 (`4`) | 有界实验用；未完成冷启动与 fail-open 验证前不要扩大 scope |
| `mt6639-group-test-pack-20261122-v0.4.1.zip` | 完整整合包 | 打包以上自测所需 5 个安装文件、`安装与测试指南.md` 与 `SHA256SUMS`；**请先解压此整合包，再在 KernelSU 中刷入内部模块 ZIP** |

## 国家码设置说明（`mt6639-6g` v0.4.1）

默认 `auto` 会保留已经放行 6GHz 的地区国家码（如第三方工具设置的 `US` / `JP` / `TW` 等），仅在 `CN` / `00` 基线时临时切换为 `US`；禁用或回滚时严格恢复至启用前基线。如需固定配置，可通过 root 执行：

```sh
sh /data/adb/modules/mt6639-6g/bin/control.sh /data/adb/modules/mt6639-6g get-country
sh /data/adb/modules/mt6639-6g/bin/control.sh /data/adb/modules/mt6639-6g set-country auto
sh /data/adb/modules/mt6639-6g/bin/control.sh /data/adb/modules/mt6639-6g set-country keep
sh /data/adb/modules/mt6639-6g/bin/control.sh /data/adb/modules/mt6639-6g set-country JP
```

## 校验

把构件下载到本目录后：

```sh
cd release
sha256sum -c SHA256SUMS
```

七个文件的期望值同时写在 `SHA256SUMS` 与仓库 README 中。

## 安装边界

这些是**候选**，不是"全功能可用"发行版：

- 6G 模块未在 Enforcing 生命周期、冷启动、三次启动与 24 小时长稳上完成验收；不要把单次关联成功当作可用。
- 热点模块默认关闭，未完成 Enforcing 下最终启动 / 客户端 / 异常验收。
- Aware 服务与并发门禁只用于有界实验；不要在实验范围之外安装或扩大 LSPosed scope。
- 冷启动、Enforcing 生命周期、6G/5G 热点起停、双 STA、5GHz 客户端业务、蓝牙外设角色、三次启动与 24 小时验收仍是未完成项。

## 回滚

- 6G：先停止 / 恢复活动事务，再替换模块；保留上一版 ZIP 作为回退参照。
- 热点：请求 `stop`，执行模块自带的 checked `control.sh recover`，确认 `mtap0` 消失、Tethering 回到 idle、STA 恢复，再禁用或卸下模块。
- UI：禁用 LSPosed 模块，或恢复上一版已签名 APK。
- 并发门禁：先解除 lease，清掉 request/mode，恢复选择，再关闭 LSPosed scope。

## 许可

构件中的第三方组件（例如 BSD 许可的 `hostapd`）保持各自许可，不因本仓库的 GPL-3.0 而改变。详见仓库根目录 [DISCLAIMER.md](../DISCLAIMER.md)。
