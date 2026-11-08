# 独立hostapd候选

本目录用于补齐原厂AP EHT原生路径，当前是独立进程工具。已接Android系统IP共享，尚未实现Android IHostapd Binder及系统热点设置集成，不替换原厂服务。

- `standalone.config`：明确编译nl80211、HE/EHT、SAE/SAE-PK、OWE、ACS、控制接口及OpenSSL。当前诊断构建不含RADIUS/accounting/VLAN，不将其当作完整产品构建。
- `config_check.c`：经链接器`--wrap=main`新增`--mt6639-check-config FILE`。只调用配置解析/释放，不初始化driver、接口或AP；其它参数仍进入原生main。
- `../../tools/build_standalone_hostapd.sh HOSTAPD_SOURCE LIBNL_SOURCE STAGING TOOLCHAIN NEW_OUTPUT`：复制源树到新的自有输出后构建静态aarch64程序，不修改共享OpenWrt源码、sysroot或服务。实际编译命令和输入SHA随产物保存；使用`EXTRA_CFLAGS`，避免命令行CFLAGS覆盖Makefile中的EHT宏。
- `../../tools/softap_native_config.sh TASK render|check|status 5g|6g WIDTH`：仅处理文件的规范配置工具；5GHz支持20/40/80MHz配置，固定主信道36；6GHz支持20/40/80/160/320MHz配置，固定主信道37。读取私有`TASK/ap.key`（单行8–63字节可打印ASCII），`render`输出配置，调用者重定向至`TASK/ap.conf`。`check`逐字核对规范配置，拒绝重复键、include、ACS和country覆盖；`status`核对运行器保存的native状态。配置解析通过不代表驱动或对端接受该带宽。
- `../../tools/softap_native_once.sh TASK run|run-tether [5g|6g WIDTH]`及`TASK guardian|recover`：固定版本、候选哈希的单次BSS入口，默认档为6g/20。任务须同时放入新配置工具和`ap.key`，运行前调用`check`，BSS启动后调用`status`核对实际native频率、HE/EHT带宽和中心索引；失败不进入共享。仅接受规范任务路径、root私有文件，配置helper也固定SHA256。当前runner固定6GHz STA上游；实测5GHz AP会迁移到该上游信道，因此最终runner在无线改动前拒绝所有5GHz档位，待独立热点/适用上游的生命周期实现后再开放。保持SAE/PMF和HE/EHT，不设置country_code，不改变现有regulatory配置。只使用新接口和自有进程，备份后写ApEHT，关闭/恢复由独立guardian执行。旧任务应继续使用任务目录内的旧runner恢复，不用新格式覆盖既有任务。
- `../../native/framework/NativeTether.java`、`../../tools/build_native_tether.sh NEW_OUTPUT`：编译并调用本机`ITetheringConnector`的新请求接口；通过`TETHERING_VIRTUAL + setInterfaceName(mtap0)`让系统管理独立BSS的地址、DHCP、DNS及转发。停止时使用`stopTetheringRequest`按type/interface匹配；本机服务端内部仍按type清理，不能据此宣称隔离了所有并发同type请求。`compile-stubs/`仅用于编译Binder子类，不能打入DEX，脚本会检查。使用真实系统Stub/Proxy发送请求和Binder结果回调，不猜connector事务号、不发送虚构的Wi-Fi状态广播。
- `../../native/netdev/delete_ifindex.c`、`../../tools/build_delete_ifindex.sh NEW_OUTPUT`：交叉构建静态 AArch64 helper，通过 generic-netlink 解析 nl80211 family，再以保存的正整数 ifindex 发送 `NL80211_CMD_DEL_INTERFACE` 并等内核 ACK；使用 Wi-Fi 正式删除路径，避免校验后重新按名称解析。编译器通过`ZIG=/path/to/zig`指定；静态构建成功不代替 Android 真机 SELinux/netlink 验收。
- `run-tether`只在无现有共享状态时执行，固定本机framework-tethering.jar和DEX哈希。运行窗口内不可由其它任务启动虚拟网络共享；`TETHERING_VIRTUAL`是系统单例，恢复按request API与接口名匹配，但服务端内部仍会清理整个 type。该入口由runner版本/boot/接口归属校验和独立看护约束，不能作为任意接口通用工具直接使用。先撤销系统共享并确认全局 upstream 请求退出，再停止独立BSS、删除本任务接口及恢复驱动/原STA。

第一次真实结果见独立候选与6GHz BSS初证，后续Android共享接线已有一次真实TetheredState、系统DHCP启动、wlan0上游及NAT/转发规则和正常退出恢复。无客户端关联、DHCP租约或转发流量，不等于端到端联网通过。虚拟下游类型不等于Framework Wi-Fi热点；IPv6、offload、系统热点UI、异常矩阵、宽带宽和长期质量仍未验收，不默认常驻启动。

频段/带宽配置与实测状态见配置入口与5GHz短窗口。320MHz采用6GHz operating class137，EHT中心31、主信道37所在HE160中心47；不把EHT宽度枚举直接用作HE宽度。5GHz/160MHz覆盖DFS子信道，尚无完整DFS/CAC路径，所以最终生成入口拒绝该档；实际支持程度按各档实测记录。
