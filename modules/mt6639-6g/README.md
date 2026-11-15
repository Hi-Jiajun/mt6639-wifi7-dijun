# MT6639 6GHz v0.3.1-dijun.1

本版加入 `6.18.21-android17-5-g284297031c53-abogki553682993-4k` 与对应原厂 WLAN 驱动的 6GHz 适配，保留旧版 `g1d099fcb35e0` 组合；两组使用同一套事务/恢复实现，通过 `compatibility.conf` 选择预期原厂哈希。系统显示版本和 build.prop 不作为门槛；相同内核、驱动、配置组合可复用。未知内核或不同原厂组件仍拒绝，不会随机尝试其它版本。

动态 KernelSU 卡片增加适配组合名，并显示内核/驱动是否匹配、配置状态、事务状态、6GHz 已注册信道数、当前连接频率和采集时间。本版经过离线源码/原厂样本核对与行为回归，实际 6GHz 注册、关联和恢复仍待设备确认；MLO/TWT 内核模块不因该 shell 模块适配而自动兼容。

卡片在启动、操作完成和既有看护的每分钟周期刷新。管理器可能缓存列表，需要重新打开或下拉刷新。没有 `iw` 或观察命令失败时显示“未知”，不会以缺少输出推断功能成功。显示“事务运行”不代表已连接，“已注册信道”不代表业务通过。完整说明及构建入口见 [维护记录](../../docs/研究-OS4新版兼容与模块状态卡-20261115.md)。

只读观察命令（写卡片只更新模块自有展示文件）：

```sh
sh /data/adb/modules/mt6639-6g/bin/status-card.sh /data/adb/modules/mt6639-6g show
sh /data/adb/modules/mt6639-6g/bin/status-card.sh /data/adb/modules/mt6639-6g update
sh /data/adb/modules/mt6639-6g/bin/verify-module.sh /data/adb/modules/mt6639-6g
```

`module.prop.template` 是构建生成、受 SHA256SUMS 校验的静态模板；`module.prop` 允许仅 description 变化。完整校验用 `verify-module.sh`，它同时检查静态文件和除 description 外的模块元数据。不要把单独执行 `sha256sum -c` 当作展示文件元数据也通过了检查，不要运行时重写 SHA256SUMS。

## 6GHz 事务与恢复机制

用户选择默认启用。KernelSU 加载 service 后，在内核、原厂 cfg/ko 哈希和挂载状态检查通过时启动持续 6GHz 事务。`RUN_MODE=persistent` 为默认；需要旧版 15 分钟实验窗口时可显式设置 `RUN_MODE=bounded`。启动和恢复命令仍有截止时间，只有稳定保持阶段不再自动到期，不会周期性重置 Wi-Fi。

service 对暂时不可用的 Wi-Fi 服务最多探测 6 次（每次上限 15 秒，间隔 5 秒）；Wi-Fi 原本关闭则保持关闭。超过等待窗口仍不可用时退出，之后可以用操作按钮启用。

KernelSU 的「操作」按钮：空闲时启用；有事务记录时恢复。恢复后记录仍存在时，第一次点击负责核对并清理该事务，第二次点击才重新启用。按钮只影响本次事务，不改下一次启动的默认值。`enable` 对同启动内活跃事务幂等，不会重复重建 Wi-Fi；已终止事务须成功 checked recovery 后才重开。

Root shell 入口：

```sh
sh /data/adb/modules/mt6639-6g/bin/control.sh /data/adb/modules/mt6639-6g status
sh /data/adb/modules/mt6639-6g/bin/control.sh /data/adb/modules/mt6639-6g enable
sh /data/adb/modules/mt6639-6g/bin/control.sh /data/adb/modules/mt6639-6g refresh
sh /data/adb/modules/mt6639-6g/bin/control.sh /data/adb/modules/mt6639-6g disable
```

`refresh` 用于已经发生 soft reboot、事务和 guardian 仍属同一 kernel boot，但 `iw phy` 已无启用的目标 6135MHz 信道时。它只允许 `u:r:ksu:s0` + Enforcing，要求模块默认值、active state、guardian、事务 boot 和当前 cfg bind 全部一致，然后读取当前 country override，按 `scan-off → country US → Wi-Fi off/on` 重建，再恢复扫描和原 country override（仅失败时）并最多 6 次回读目标信道。解析兼容 `6135.0 MHz` 小数格式、忽略 `(disabled)` 频点，其它 6GHz 频点不能替代 6135。6135 已注册时不触碰无线；启用瞬时失败只重试一次；失败时保留 active 事务并恢复扫描/国家设置。HUP/INT/TERM 中断执行相同恢复。不要把 refresh 当作 cold boot 或模块安装后的自动重注册，也不能在 AP/网络要求禁止无线写操作的窗口运行。

需要停止以后自动启动时，将 `config.conf` 的 `ENABLE_6G` 默认值改为 `0`，或在 KernelSU 中禁用本模块。持续模式看护每 500ms 检查模块的 disable/remove 标记以及目录身份，发现禁用、移除或目录消失/替换时启动恢复；恢复完成仍需有界的 Wi-Fi 重建时间。手动恢复也可用操作按钮或 `control.sh disable`。不能覆盖仍在运行的执行器二进制：升级前先恢复旧事务。

恢复资料保留在 `/data/adb/mt6639-6g`。事务记录损坏、不属于模块或恢复失败时保留记录并报错；不要手工清空后强行启用。跨 boot 不调用旧 WAL 的 recover：必须验证旧/新 boot UUID、版本锁、当前和 init namespace 原厂 cfg 与无目标挂载后，将旧 authority 复制校验至 `retired/` 再清空；原事务目录始终保留。真实跨启动验收尚待重启窗口，夹具通过不能代替真实重启。

TERM/INT 会触发 checked recovery；SIGKILL、内核崩溃或突然断电不可能由已死亡的看护自愈。同 boot 下次 `enable` 会先尝试 checked recovery，失败保留证据；重启后按上述原厂检查整理。模块不会自行恢复 root，仍依赖 dijun-oneclick/KernelSU 正常加载。

所有修改都是临时挂载/运行时设置，原生执行器仍使用原有版本锁、备份和独立看护。卸载脚本尝试恢复并保留资料，但不能把脚本成功运行等同于 KernelSU 跨 boot 卸载流程已验收。
