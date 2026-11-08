# 蓝牙 LE Audio Host 开放回读（Enforcing / KSU）

日期：2026-10-27 21:40–22:01。范围：确认 `mt6639-bt` v0.1.2 在 Enforcing + `u:r:ksu:s0` 条件下把六项 Host profile 属性真正放开，并核对运行中的蓝牙栈是否已注册这些 profile。**不做外设连接、音频、CIS/ASE/LC3 业务测试**——按用户决定，本轮只要求“功能开放”，外设相关验收留到有 LE Audio 设备后。

## 前提与门禁

`modules/mt6639-bt/bin/control.sh` 要求 `uid=0`、`context=u:r:ksu:s0`、`getenforce=Enforcing`、`ro.product.device=dijun`，并对 6 个键做白名单校验、写前后回读和本 boot 快照校验。本轮现场满足全部前置条件：

```text
uid=0(root) gid=0(root) groups=0(root) context=u:r:ksu:s0
Enforcing
```

模块 v0.1.2 / versionCode 3，`/data/adb/modules/mt6639-bt/disable` 不存在（enabled），`config.conf` 的 `ENABLE_LE_AUDIO=1`。

## 属性：false → true

本 boot 快照目录 `/data/adb/mt6639-bt/<UUID>/before.tsv`（2026-10-27 21:40 写入）记录应用前六项全为 `false`；同目录 `state` 为 `active`。当前读回：

| Host profile 属性 | 应用前 | 现在 |
| --- | --- | --- |
| `bluetooth.profile.bap.unicast.client.enabled` | false | true |
| `bluetooth.profile.bap.broadcast.source.enabled` | false | true |
| `bluetooth.profile.bap.broadcast.assist.enabled` | false | true |
| `bluetooth.profile.csip.set_coordinator.enabled` | false | true |
| `bluetooth.profile.vcp.controller.enabled` | false | true |
| `bluetooth.profile.mcp.server.enabled` | false | true |

## 运行栈是否真的加载

只读属性为 true 不等于 Host 已注册 profile，因此另外读了运行态 `dumpsys bluetooth_manager` 的 `Enabled Profile Services` 段，六项全部在列：

```text
Enabled Profile Services:
  GATT
  A2DP
  AVRCP
  LE_AUDIO_BROADCAST_ASSISTANT
  BATTERY
  CSIP_SET_COORDINATOR
  HEADSET
  HEARING_AID
  HID_DEVICE
  HID_HOST
  MAP
  MCP_SERVER
  OPP
  PAN
  PBAP
  VOLUME_CONTROL
  LE_AUDIO
  LE_AUDIO_BROADCAST
```

即 `LE_AUDIO`（BAP unicast client）、`LE_AUDIO_BROADCAST`（broadcast source）、`LE_AUDIO_BROADCAST_ASSISTANT`、`CSIP_SET_COORDINATOR`、`MCP_SERVER` 与 `VOLUME_CONTROL`（VCP controller 所在的 profile）都已由 Host 注册。蓝牙处于 `State: ON`、`settings global bluetooth_on=1`。

## 结论与边界

- **本轮结论**：B03/B04 的“Host 侧开放”这一半在 Enforcing + KSU 域下成立，且不是只改属性——运行中的蓝牙栈确实注册了这些 profile。此前“Permissive boot 被门禁拒绝、六项读回 false”的记录属于更早的 boot，已被本轮覆盖。
- **没有验证**：任何外设连接、BIS/BIG 广播发现与加入、CIS/ASE 建立、LC3 编解码与音频流、CSIP 成组、VCP/MCP 实际控制、A2DP/HFP 回退。用户当前只有 AirPods Pro 3，按决定不做外设侧验收。
- **不外推**：`dumpsys` 里的 profile 注册不等于协议角色可用；也不代表 cold boot 自动成立（本 boot 的 root 来自 oneclick 提权链，模块 service 在其后加载）。开机自动开放仍需在 cold boot 后回读一次。
- 本轮没有改代码、没有重装模块、没有开关蓝牙、没有改 SELinux。
