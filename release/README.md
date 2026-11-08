# Release 构件

二进制构件不进仓库树，发布在 GitHub Releases。本目录只保留清单与校验值。

`v0.1.0-pre`（预发布）附五个当前候选：

| 构件 | 版本 | 说明 |
| --- | --- | --- |
| `mt6639-6g-v0.2.16.zip` | 6G 模块 v0.2.16 | KernelSU 模块；Enforcing 生命周期、冷启动与长稳仍未完成完整验收 |
| `mt6639-hotspot-v0.2.22.zip` | 热点模块 v0.2.22 | 原生 EHT 热点，默认关闭；内含第三方构建的 `hostapd` |
| `mt6639-bt-v0.1.2.zip` | 蓝牙模块 v0.1.2 | 控制器特性与 LE Audio 相关的受控入口 |
| `mt6639-ui-lsposed-0.2.7.apk` | UI 徽标 v0.2.7 | LSPosed 模块，仅影响 Wi‑Fi 列表行的 6G 徽标显示 |
| `mt6639-aware-service-0.1.3.apk` | Aware 服务 v0.1.3 | 有界实验用；未完成冷启动与 fail-open 验证前不要扩大 scope |

## 校验

把构件下载到本目录后：

```sh
cd release
sha256sum -c SHA256SUMS
```

五个文件的期望值同时写在 `SHA256SUMS` 与仓库 README 中。

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
