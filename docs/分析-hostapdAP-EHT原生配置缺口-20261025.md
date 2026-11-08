# 原厂hostapd的AP EHT配置缺口：静态检查点

2026-10-25。承接[两次热点短检查](验证-SoftAP配置控制与5GHz最小检查-20261025.md)：SAE请求的活动SoftApManager保留EHT=true，但新生成的厂商配置只有HE，实际仍报11ax。当前仅定位原生层，不新增热点窗口、不替换生产二进制。

对象为手机原厂`/vendor/bin/hw/hostapd`，SHA256 `818bf3b67b503c3bf1d7f12ead2860a1ac7719bfb0a71210fb3351e2652e448d`。原件仅在私有证据目录；本报告不分发厂商二进制。

## 新证据

对ELF `.rodata`的精确NUL结尾配置键检查：

| 配置键 | 本机结果 |
| --- | --- |
| ieee80211ax | 存在（节内偏移42582） |
| ieee80211be | 未找到 |
| eht_oper_chwidth | 未找到 |
| eht_oper_centr_freq_seg0_idx | 未找到 |
| eht_su_beamformer | 未找到 |
| mld_ap | 未找到 |

`ieee80211be=%d`确实存在，但属于包含channel/HE/EHT/beacon/dtim的运行状态格式串，起始虚拟地址0x32907；引用点0xab94c/0xab950进入格式化输出。它并不是独立的配置解析键，不能据此证明AP EHT配置解析或生成已实现。相应反汇编和键审计保存在`outputs/softap-20261025-1/`。

作为源码对照，[LineageOS hostapd/config_file.c](https://github.com/LineageOS/android_external_wpa_supplicant_8/blob/lineage-24.0/hostapd/config_file.c)把上述BE/EHT解析项置于`CONFIG_IEEE80211BE`内。当前实际下载来源、blob SHA及文件校验以同目录`lineage-hostapd-config-source.json`和本地源码为准；这只是开源实现对照，不能替代小米厂商编译源码。

## 当前判断

这些证据把“原厂AP EHT配置处理未完整编入/未接线”提升为优先假设。AIDL遗漏或厂商私有替代路径仍需区分；本轮没有直接读取Binder载荷，也没有动态证明所有解析入口不存在，因此不把静态键缺失升级为硬件不支持或确定的编译结论。

后续适配应面向vendor AIDL到hostapd的原生实现：确认BE字段是否被读取、是否有厂商专用生成路径；若当前构建缺实现，再准备匹配AIDL v2与厂商回调的可恢复原生构建/移植。单纯增加Framework开关不再作为重复实验。5GHz请求回报6135MHz保留为独立频段/并发缺口，尚不等于6GHz热点外部业务已通。

按实现优先策略，该缺口不阻塞其它独立功能。本轮另完成P2P GO与6GHz STA角色闭环，热点EHT保持受阻，不包装为已解锁。

本次对照源码blob SHA：`769459b5173f07723a0b2b5307d972ec0c420b6d`。

后续进展：独立hostapd与6GHz EHT BSS补齐AIDL实际字段布局和原厂生成函数检查，并完成独立静态候选的一次6GHz/20MHz BSS建立/停止。EHT beacon缓冲已提交，未外部观测；原厂服务未替换，Android共享及客户端业务仍未完成。这里的原厂缺口不再等于所有原生路线都无法建立BSS。
