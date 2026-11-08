# XR1710G AP Puncturing 门禁分析

日期：2026-10-26。手机协作会话请求AP协作会话只读核实Wi-Fi 7 preamble puncturing。此次未改AP UCI、未reload无线、未启用monitor/trace，也未操作手机。

## 最新交接位置（20:13复核）

精确airoha/an7581对照/候选已完成构建和静态ABI核对，早期“先构建”待办已完成；候选仍未通过实机加载验证。此前阶段A脚本启动后AP失联并重启，旧日志缺失，不能定位失联阶段或宣称候选加载成功；原栈和四BSS已恢复。阶段B未执行、未配置非零bitmap，未取得WM接受/拒绝或逐PPDU证据。热加载重试保持取消。权威报告和候选完整SHA见AP构建交接；以下静态门禁与早期采样保留历史来源，W10/F05仍partial。

## 结论

当前阻塞层是**MT7996 Linux驱动未向cfg80211/nl80211上报`NL80211_EXT_FEATURE_PUNCT`**。hostapd已编入puncturing配置解析和nl80211发送支持；在当前驱动能力缺失时，非零bitmap会先被hostapd本地拒绝，即使绕过hostapd，内核nl80211仍会以`driver doesn't support puncturing`拒绝，不会到达MT7996 MCU回调。

这不能直接归咎为“AP固件不支持”。现有证据只足以确认当前Linux驱动没有功能实现/能力位；MT7996 firmware底层MCU是否支持，仍需后续静态分析或厂商私有路径证据。

2026-10-26 追加只读复核进一步锁定当前 AP 与上游：

- ImmortalWrt `20261021-04cc62e8`，内核 `6.18.55`；
- `mt7996e.ko` SHA256 `cc348fae668567b3141bd99fa31b1e6747c32fa74a132755868423a019e74518`；
- WM firmware SHA256 `ba5b800e560a7940f1c51db0f99ab4dc249389f6e1a9e4e863f62ff28cc5ff50`；
- `iw phy phy0 info` 的 extended feature 列表仍没有 `PUNCT`；
- Linux v6.18 上游 `mt7996/main.c`、`init.c` 中没有 PUNCT feature 设置；
- 上游搜索只找到 mt7925 的 EHT puncturing 实现与后续修复提交，没有 mt7996 puncturing 提交。

但不能据此说 MT7996 没有厂商实现：MediaTek `mtk-openwrt-feeds` 固定提交 `e55f4f30d66b2a99c5a1b75bdc2f22bf6c756243` 含 MT7996 专用 Puncturing 补丁链（`MCU_UNI_CMD_PP`、`PP_DSCB_CTRL`、`pp_mode/pp_bitmap`、hostapd/netifd），详见MTK SDK 补丁审计。当前 ImmortalWrt AP 没有带这套 vendor 路径；其与 Linux 6.18 的 dry-run 只有部分 hunk 可匹配，不能直接 cherry-pick。

2026-10-26 03:19 CST 的只读预检把同一结论固化为机读证据：

`outputs/ap-puncturing-preflight-20261026-031957.json`
SHA256 `90cfd2f328bc625d97c055373a97099645ed961b7d459d60dd1175d4d38fce20`

输出确认 `eht_supported=true`、`six_ghz_320_supported=true`、`puncturing_feature=false`，并锁定内核 `6.18.55`、驱动 `cc348f…e74518` 和 firmware `ba5b80…c5ff50`。该旧版摘要没有 tri-state 字段；修正后的工具只在完整解析 release、Band4 EHT/6GHz 320、module/firmware 哈希和 extended feature 列表后返回 `status=complete`，否则返回 `status=incomplete`、`puncturing_feature=null` 并非零退出。

2026-10-26 03:46 CST 的修正版真实只读复采：

`outputs/ap-puncturing-preflight-v2-20261026-034637.json`
SHA256 `2eebb2dc6204c2dd5dbe2c97e0c74d410cb5714222d58c62ebb8689264426138`

输出仍为 `status=complete`、24 个 extended features、`puncturing_feature=false`；这次结论的 false 有完整列表和 Band4 上下文支撑。工具远端只执行 `/etc/openwrt_release`、`uname`、`iw phy` 和 `sha256sum`，没有 UCI、hostapd、reload、重启或无线写操作；SSH 本地配置和 `known_hosts` 的副作用单独标注，不把整个进程宣称为零写入。

2026-10-26 04:14 CST 又补齐运行模块绑定只读字段：`modinfo` 的 filename/vermagic/depends、`/proc/version` 和 `/proc/modules`。结果仍为 `status=complete`，`module_loaded=true`，vermagic与内核 `6.18.55`一致，驱动哈希和 firmware 哈希未变：

`outputs/ap-puncturing-preflight-v3-20261026-041423.json`
SHA256 `1814430c5a4950c0adc2ce63cc106d56bb130c20b1192a66575e5966d7e6f9a8`

这仍不能证明该 `.ko` 与某份源码树逐字节构建绑定，但把 live kernel、模块文件、vermagic、依赖和已加载状态锁定为同一现场；源码树结论继续只作静态旁证。

同一模块的只读 ELF 检查进一步给出运行模块层证据：文件非 stripped、`nm` 共 596 个符号；其中 `punct`/`disabled-subchannel` 匹配为 0，`strings` 匹配数为 0，但 EHT 相关符号如 `mt76_connac_get_eht_phy_cap`、`mt7996_set_stream_he_eht_caps` 存在。原始扫描：

`local/evidence/mt7996-runtime-20261026/symbol-scan-20261026-v2.txt`
SHA256 `e3ac41ad1b9f6480fd8abab01e4e145469b664b901988399f864eadd792775fa`

这是“当前运行 `.ko` 没有可识别的 Puncturing 实现/字符串”的负向证据，不是 firmware/MCU 不支持证明；下一步仍需从 MT7996 firmware 接口或其他厂商实现寻找正向能力证据。

进一步固定 Linux v6.18 commit `7d0a66e4bb9081d75c82ec4957c50034cb0ea449` 的源码快照对照：

- MT7996 `bss_rlm_tlv` 只有 channel/bandwidth/streams/band 等字段，没有 EHT disabled-subchannel bitmap；
- MT7925 `bss_eht_tlv` 明确有 `is_eht_op_present`、`is_eth_dscb_present`、`eht_dis_sub_chan_bitmap`，且 `mt7925/mcu.c` 在 `IEEE80211_CHANCTX_CHANGE_PUNCTURING` 时把 `chandef->punctured` 写入 TLV。

快照与逐文件 SHA 在 `local/evidence/punct-source-v6.18-20261026/README.md`，三个源文件分别为 `d6da75c2…c06fd`、`b2d286f8…9f4e4`、`374bb8a1…75b1`。这证明的是 v6.18 两条驱动路径的适配差异，不是 MT7996 firmware 原生能力差异，也不能据此复制 mt7925 实现并直接发射。

因此当前不是“hostapd 少一个开关”，而是当前 live 驱动没有带 MTK SDK vendor 路径。mt7925 的实现不能直接套用到不同硬件/MCU 路径；MTK SDK 的 MT7996 专用路径说明这更像厂商适配差异，而不是硬件无能。当前 WM firmware 是否接受 SDK MCU 命令、能否回移到 6.18.55，以及真实 punctured PPDU 是否可用仍未确认。

## 分层证据

| 层 | 结果 |
| --- | --- |
| hostapd二进制 | `/usr/sbin/wpad` SHA256 `2c222de3100aec8387bcb91d5c7c92d952d6645531c78d751136f6f3bdfbe91a`，与已有构建记录中strip后的`wpad-mesh-mbedtls`逐字节一致 |
| hostapd功能 | 二进制含`punct_bitmap`、`punct_acs_threshold`、配置校验及nl80211拒绝文案；同来源源码的BE guard下存在解析/ACS/send路径 |
| 当前配置 | radio2 UCI和`/var/run/hostapd-phy0.2.conf`都没有punct配置；status无`punct_bitmap` |
| live PHY | `iw phy phy0 info`有EHT/320MHz，但`Supported extended features`没有PUNCT；`GET_WIPHY` bit62=0 |
| hostapd driver gate | `driver_nl80211_capa.c`只有发现`NL80211_EXT_FEATURE_PUNCT`才设`drv->puncturing`；发送非零bitmap前cap必须为true |
| backports nl80211 | `nl80211.c`在缺少能力位时拒绝`NL80211_ATTR_PUNCT_BITMAP`，extack为`driver doesn't support puncturing` |
| MT7996源码 | 当前共享build tree的`mt7996/`没有punct/PUNCT实现或feature set；同树`mt7925`存在`chandef->punctured`及MCU DSCB bitmap路径，说明缺口集中在MT7996适配 |

静态config文件曾未显示`CONFIG_IEEE80211BE`，但实际二进制字符串和同来源编译逻辑证明BE/punct代码在产物中；不能只凭build_dir里的`.config`判断能力。

## 后续

- 在MT7996驱动上报PUNCT前，不在活动AP做puncturing配置解析/发射试验，也不以hostapd重编尝试绕过内核门禁。
- 离线继续检查 MTK SDK MCU/DSCB 补丁与 6.18.55 的 API 差异、当前运行模块与源码绑定，以及当前 WM firmware 是否支持对应命令；在 dry-run 和构建验证前不改活动 AP。
- 若未来加入驱动支持，再安排一次受控punct bitmap、带宽、MCS/重传和数据面短验证；完整质量归F05。
- 手机侧W10继续保留“驱动/firmware门禁”，不把解析代码存在或配置项可写当作空口通过。
