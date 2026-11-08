# 6GHz v0.2.7 升级恢复修复

日期：2026-10-26。范围：v0.2.5 活跃事务跨模块更新后，在新用户空间自动恢复时出现锁争用；记录失败、手工恢复和离线修复。

## 现场时间线

本次使用 `ksud soft-reboot`。内核 `boot_id` 保持：

```text
<UUID>
```

这是用户空间重建，**不是 cold boot，也不能计入 F10 的三个独立 boot**。

soft reboot 后的状态：

| 时间 | 事实 | 证据 |
| --- | --- | --- |
| 05:01:19 | 新 v0.2.6 service 启动，KSU 两个更新已整合 | `update=false`、module.prop v0.2.6 |
| 05:01:19 | 旧 v0.2.5 事务 `t-<BOOT_ID>-<TS>-XXXXXX` 进入 terminal | `module_directory_changed`、`restoration=settings_failed` |
| 同期 | cfg 已回原厂、无 6G 挂载、country CN，手机自动在 LabNet-5G | cfg SHA 原厂、mountinfo none |
| 05:01:36 | 自动 checked recovery 拒绝 | `{"status":"busy_or_invalid_lock"}`，rc=3 |
| 05:01:36 | wrapper 保留恢复 authority | `state=restore_failed`、active_tx 非空 |
| 稍后 | 旧 guardian PID 24974 已不存在，父目录锁和 guardlock 均可取得 | `/proc/24974` gone；flock 双锁 rc0 |
| 05:03:30 | 手工执行 `control.sh ... disable` | `native restore done`，`{"status":"restored"}` |
| 05:03:30 | 旧事务 authority 关闭 | `state=restored`、active_tx=none |
| 05:03:41 | v0.2.6 新事务建立 | `t-<BOOT_ID>-<TS>-XXXXXX`，guardian 27589 |
| 05:04:xx | 手动选择 LabNet-6G | 6135MHz、11be、COMPLETED、5764Mbps |
| 05:05 | active checker | `state=active`、guardian alive、country US；唯一失败为 SELinux Permissive |

结论：**本次手工恢复成功**；自动升级迁移在 v0.2.6 上没有成功，失败路径与锁争用真实可复现，不能用最终 active 状态掩盖。

## v0.2.7 修复

`native.sh` 新增 `native_recover_retry`：

- 只在 `recover` 返回 rc=3 且输出精确为 `{"status":"busy_or_invalid_lock"}` 时重试；
- 只在 guardian 已不存活时重试；
- 最多 15 次、每次间隔 1 秒；
- 其他错误、guardian 仍存活或重试耗尽时继续 fail-closed，保留 active_tx authority；
- 不删除事务目录、不主动清 guardlock、不绕过 native journal。

候选：

`outputs/mt6639-6g-20261026-2/mt6639-6g-v0.2.7.zip`

- SHA256 `3f413f3635fb93f0fcd087818a1a5ed7f8621e46f03bcc4e73e03788f52e1641`
- `version=v0.2.7`、`versionCode=9`

## 验证与边界

- `tests/test_mt6639_control.py` 38/38 通过，新增：
  - busy 两次后恢复成功，第三次成功；
  - busy 持续时 16 次尝试后仍返回 90 并保留 authority。
- `tests/test_6g_module_build.py` 验证新构建可复现、版本和重试函数包含在 ZIP。
- 完整宿主清单 `outputs/host-tests-20261026-v0.2.7/summary.json` 41/41 通过，SHA256 `b5f2f4847418b595396614854656e3f319ca19b8d4ec0863307419c9823c9afa`。
- 当前手机仍运行 v0.2.6 active transaction；v0.2.7 **未安装**。
- v0.2.7 的自动升级迁移、真实旧 guardian 锁释放、cold boot、三 boot 和 24 小时仍未在真机验证；F01 升级迁移项继续保留。

更新后的发布预检准确得到 2 FAIL：

- `selinux=Permissive`，期望 Enforcing；
- `module_mt6639-6g=v0.2.6`，期望候选 v0.2.7。

其余 module/hotspot/BT/LabNet-6G/UI/Zygisk/LSPosed 全部 PASS。

- `outputs/release-device-preflight-20261026-053300/results.tsv` SHA256 `8106aaff087715d0a45616875dd625810f40847adbf6147fa75824a8a9be1eb9`
- `outputs/release-device-preflight-20261026-053300/run.json` SHA256 `de035e6c0d96465f530a632b9ba5d5c6da446592a0e9f898e76fd03901f22904`

## 恢复材料

- 安装 v0.2.6 前模块目录：`/data/local/tmp/mt6639-6g-before-v0.2.6-209f5b48.tar.gz`，SHA256 `4e153b984291c2976dd721a3c6bcfd3ed77a0908ef3e286d37e8d35faacb3f3c`
- 安装 v0.2.6 前运行态：`/data/local/tmp/mt6639-6g-state-before-v0.2.6-209f5b48.tar.gz`，SHA256 `db8f037270fe41880f13615f51d8096bc75862f7802ff9db918f01caaf50db4e`
- v0.2.5 回滚 ZIP：`release/rollback/mt6639-6g-v0.2.5.zip`，SHA256 `be0a0d0b907c99b1a81840d8d7751e501299dc3e4bb3e7080a35f8092bca2d4f`
