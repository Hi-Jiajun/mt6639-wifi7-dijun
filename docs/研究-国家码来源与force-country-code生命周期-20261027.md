# 国家码来源与 `force-country-code` 生命周期（离线源码核实，2026-10-27）

目的：回答"开机自动 6GHz"到底该落在哪里。方法：拉 AOSP `packages/modules/Wifi` 源码核对调用链，再读一次设备只读属性；不靠写 `settings` 猜。

证据目录：`outputs/6g-country-source-audit-20261027/`（含 4 份 AOSP 源文件、`device-country-props.txt`）。源文件取自 `android.googlesource.com` 的 `packages/modules/Wifi` `refs/heads/main`，2026-10-27 抓取，非设备镜像。

## 调用链（AOSP）

| 环节 | 源码事实 |
| --- | --- |
| shell 入口 | `WifiShellCommand.java:950` `force-country-code` → 校验两位码后 `mWifiCountryCode.setOverrideCountryCode(countryCode)` |
| 服务层 | `WifiServiceImpl.java:5180` `setOverrideCountryCode()` → 校验 `MANAGE_WIFI_COUNTRY_CODE` 权限后转给 `WifiCountryCode` |
| 存储 | `WifiCountryCode.java:99` `private String mOverrideCountryCode = null;`，`:372` 赋值、`:380` 清空——**纯内存字段，无文件/无设置项落盘** |
| 生效 | `:373` / `:381` 立即调 `updateCountryCode(false)`；`:623` 起按 `pickCountryCode()` 结果下发到 supplicant/HAL |
| 优先级 | `pickCountryCode()`：**override** → telephony（SIM）→ driver（802.11d）→ `config_wifiDefaultCountryCode` |
| 开机默认 | `:58` / `:240` `getOemDefaultCountryCode()` 读系统属性 **`ro.boot.wificountrycode`** |

结论：`force-country-code` 是 system_server 进程内的覆盖，进程活着就一直有效（Wi‑Fi 关开也保留），**重启即失效**。`ro.boot.*` 是只读启动属性，运行期改不了。

## 设备只读读回

| 项 | 值 |
| --- | --- |
| `getprop ro.boot.wificountrycode` | **CN** |
| `settings get global wifi_country_code` | null |
| `gsm.sim.operator.iso-country` | cn |
| `persist.sys.country` | 空 |
| `ro.boot.uwbcountrycode` | US（与本项无关，仅说明该机确实按 boot 属性区分国家码） |

## 由此得到的判断

1. **`settings global wifi_country_code` 不是本机的开关**。AOSP 这四份文件里 `WIFI_COUNTRY_CODE` 的命中全部是权限常量 `MANAGE_WIFI_COUNTRY_CODE`，没有任何对 `Settings.Global.WIFI_COUNTRY_CODE` 的读取。我上一轮提过"试着写这个设置键"，该提议**缺乏依据，撤回**。
2. **重启回 CN 的原因链条是清楚的，但仍是推断**：override 内存态丢失 → 落到 telephony（SIM=cn）或 `ro.boot.wificountrycode=CN`。本次没有重启实测，属于源码推断。
3. **开机自动 6GHz 的候选落点**（供后续选型，均未实施）：
   - 模块在开机脚本里执行 `cmd wifi force-country-code enabled US`（需 root，且要在 system_server / WifiService 就绪之后；等价于每次开机重放本次手动动作）；
   - LSPosed hook `WifiCountryCode.getOemDefaultCountryCode()` 或 `pickCountryCode()` 返回 US（要动 system_server，风险高）；
   - 驱动/HAL 层直接压 country（`wifi.cfg`、conninfra nvram 之类），与框架无关但更底层、更不可预期。

## 未解决

- **OEM 差异**：`miui-wifi-service.jar` 是单个 `classes.dex`（4.5MB），本机没有 JVM/jadx 运行环境，无法反编译核对 HyperOS 是否自行读了某个国家码设置键。只记录了 `strings` 命中，**不据此推断 MIUI 行为**。要坐实只能等有反编译环境或另行真机试验（需授权）。
- 上面第 2 条"重启回 CN"是源码推断，不是实测；真正的跨 boot 结论要等一次受控重启验证。
