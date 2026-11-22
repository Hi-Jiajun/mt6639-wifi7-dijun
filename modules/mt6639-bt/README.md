# MT6639 LE Audio Host profile 模块

适用于 Xiaomi 15S Pro（`dijun`）、Xiaomi Pad 7S Pro 12.5（`violin`）、Xiaomi Pad 7 Ultra 14（`jinghu`）的 HyperOS 3 / HyperOS 4 固件，由 KernelSU 管理 root 和模块。独立于 6GHz 模块；启用前动态定位 `/apex/com.android.bt/app/*/Bluetooth.apk` 并按 `compatibility.conf` 核对设备代号与 Bluetooth APK SHA256（取代脆弱的整包 build fingerprint 锁定），防止 APEX 独立更新后误用。

默认开放本机已真实加载的六个 Host profile：BAP unicast client、broadcast source/assistant、CSIP coordinator、VCP controller、MCP server。它只开放 Host 入口，不伪造控制器能力、不强制 LC3，也不代表广播 sink、CIS/BIS、耳机互通或音频播放已验证。传统蓝牙、配对数据、offload 配置和 HAP 保持原路径。

## 安装与操作

通过 KernelSU 安装 ZIP。late-load 场景可在安装生效后显式运行本模块 service；模块不会自动重启手机或开关蓝牙。

```sh
su -c 'sh /data/adb/modules/mt6639-bt/service.sh'
su -c 'sh /data/adb/modules/mt6639-bt/bin/control.sh status'
su -c 'sh /data/adb/modules/mt6639-bt/bin/control.sh restore'
su -c 'sh /data/adb/modules/mt6639-bt/bin/control.sh enable'
```

KernelSU 操作按钮切换本 boot 的启用/恢复状态。`config.conf` 的 `ENABLE_LE_AUDIO=1` 为默认启用；设为0则下次 service 恢复原值。这六项是非持久属性。按标准 KernelSU 生命周期，禁用模块在下次 boot 生效；同 boot 立即撤销需调用 `restore`，卸载脚本也会请求恢复。

原值和 SHA256 保存在 `/data/adb/mt6639-bt/<boot-id>/`，不随模块目录移除；不重放其它 boot 的快照。避免与旧 `mt6639-full` 等模块同时修改这六个属性。

如果蓝牙已开启，属性变化与已加载的 profile 不一定同步，要等正常关闭/开启后采用新值；恢复同理，属性恢复不等于运行中的 profile 已卸载。模块不打断现有音频，也不自行重启蓝牙。

## 恢复与证据边界

使用标准属性服务 `setprop`，动作串行执行并逐项回读。失败时尝试恢复，恢复失败保留快照；中断后可以显式恢复。它不是平台原子 CAS，避免外部并发编辑。快照损坏或冲突时拒绝覆盖，恢复态再次启用前核对基线。

本轮最小验证包含一次真实 Host 加载与属性/蓝牙生命周期恢复。外设音频角色及性能未验收；完整故障矩阵和生命周期后置 F01/F08。早期直接内存写属性版本虽能回读 true，但 Host 未加载；改用 setprop 后六个 profile 实际加载。两次结果独立保留，不以属性回显代替功能证据，也不将这一对照扩大为底层缓存机制已完全定位。
