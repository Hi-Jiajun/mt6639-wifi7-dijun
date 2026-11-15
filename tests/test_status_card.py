"""Real status renderer and integrity checker, with only device observations stubbed."""
from pathlib import Path
import fcntl
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest


MODULE = Path(__file__).resolve().parents[1] / "modules/mt6639-6g"


class StatusCardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mt6639-card-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mod = self.root / "module"
        shutil.copytree(MODULE, self.mod)
        shutil.copyfile(self.mod / "module.prop", self.mod / "module.prop.template")
        self.work = self.root / "work"
        self.work.mkdir()
        stub = self.root / "stub"
        stub.mkdir()
        self.cfg, self.ko, self.boot, self.mounts, self.runtime = (
            self.root / n for n in ("cfg", "ko", "boot", "mounts", "runtime"))
        self.cfg.write_text("DisallowBand6G 1\n")
        self.ko.write_bytes(b"stock ko")
        self.boot.write_text("11111111-1111-4111-8111-111111111111\n")
        self.mounts.write_text("")
        self.runtime.write_text("enabled_config=1\nstate=none\nactive_tx=none\n")
        (self.mod / "bin/native.sh").write_text(
            '#!/bin/sh\n[ "$2" != apply ] || exit "${CARD_APPLY_RC:-7}"\n'
            '[ "$2" = status ] || exit 66\ncat "$CARD_RUNTIME"\nexit "${CARD_NATIVE_RC:-0}"\n')
        scripts = {
            "uname": '#!/bin/sh\necho "${CARD_KERNEL:-fixture-kernel}"\n',
            "iw": '''#!/bin/sh
echo "$*" >> "$CARD_CALLS"
[ "${CARD_IW_FAIL:-0}" = 0 ] || exit 1
case "$*" in
    dev) printf 'Interface wlan0\n type managed\nInterface usb0\n type managed\n' ;;
    'dev wlan0 info') echo 'wiphy 0' ;;
    'phy phy0 info')
        echo ' * 2412 MHz [1]'
        [ "${CARD_DISABLED:-0}" = 1 ] && echo ' * 6135.0 MHz [37] (disabled)' || echo ' * 6135.0 MHz [37]'
        echo ' * 6155 MHz [41] (disabled)' ;;
    'dev wlan0 link')
        if [ "${CARD_DISCONNECTED:-0}" = 1 ]; then echo 'Not connected.'
        else printf 'Connected to 02:00:00:00:00:01\n SSID: private-fixture\n freq: 6135\n'; fi ;;
    *) echo FORBIDDEN >> "$CARD_CALLS"; exit 99 ;;
esac
''',
        }
        for name, body in scripts.items():
            path = stub / name
            path.write_text(body)
            path.chmod(0o755)
        self.calls = self.root / "calls"
        self.env = dict(os.environ, MT6639_TEST_MODE="1", WORK=str(self.work),
                        CFG=str(self.cfg), STOCK_KO=str(self.ko),
                        STOCK_CFG_SHA=hashlib.sha256(self.cfg.read_bytes()).hexdigest(),
                        STOCK_KO_SHA=hashlib.sha256(self.ko.read_bytes()).hexdigest(),
                        KERNEL_GUARD="fixture-kernel", BOOT_ID_FILE=str(self.boot),
                        MOUNTINFO_FILE=str(self.mounts), CARD_RUNTIME=str(self.runtime),
                        CARD_CALLS=str(self.calls), PATH=f"{stub}:{os.environ['PATH']}")
        self.sums()

    def sums(self):
        entries = []
        for p in sorted(self.mod.rglob("*")):
            if p.is_file() and p.name not in ("module.prop", "SHA256SUMS"):
                entries.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(self.mod)}\n")
        (self.mod / "SHA256SUMS").write_text("".join(entries))

    def card(self, *args, **env):
        return subprocess.run(["sh", str(self.mod / "bin/status-card.sh"),
                               str(self.mod), *args], env=dict(self.env, **env),
                              capture_output=True, text=True, timeout=15)

    def verify(self):
        return subprocess.run(["sh", str(self.mod / "bin/verify-module.sh"), str(self.mod)],
                              capture_output=True, text=True, timeout=5).returncode

    def description(self):
        return next(x for x in (self.mod / "module.prop").read_text().splitlines()
                    if x.startswith("description="))

    def test_registered_is_separate_from_transaction_and_business(self):
        result = self.card("update")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.description()
        self.assertIn("[待启用]", text)
        self.assertIn("事务：空闲", text)
        self.assertIn("6GHz：1个已注册信道", text)
        self.assertIn("连接：6135MHz", text)
        self.assertIn("不代表业务通过", text)
        self.assertNotIn("private-fixture", text)
        self.assertNotIn("02:00:", text)
        self.assertNotIn("FORBIDDEN", self.calls.read_text())
        self.assertNotIn("usb0", self.calls.read_text())

    def test_new_kernel_rejection_is_visible(self):
        result = self.card("update", "enable", "3", CARD_KERNEL="new-kernel")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[内核不匹配]", self.description())
        self.assertIn("内核：不匹配", self.description())

    def test_driver_mismatch(self):
        self.ko.write_bytes(b"new driver")
        self.assertEqual(self.card("update").returncode, 0)
        self.assertIn("[驱动不匹配]", self.description())
        self.assertIn("驱动：哈希不匹配", self.description())

    def test_unreadable_driver_is_distinguished_from_hash_mismatch(self):
        self.ko.unlink()
        self.assertEqual(self.card("update").returncode, 0)
        self.assertIn("[驱动不可读]", self.description())
        self.assertIn("驱动：不可读", self.description())

    def test_failed_iw_is_unknown_not_zero_or_success(self):
        self.assertEqual(self.card("update", CARD_IW_FAIL="1").returncode, 0)
        self.assertIn("6GHz：未知", self.description())
        self.assertIn("连接：未知", self.description())

    def test_disabled_channels_and_disconnected(self):
        self.card("update", CARD_DISABLED="1", CARD_DISCONNECTED="1")
        self.assertIn("6GHz：0个已注册信道", self.description())
        self.assertIn("连接：未连接", self.description())

    def test_failed_operation_persists_but_not_across_boot(self):
        self.card("update", "disable", "90")
        self.card("update")
        self.assertIn("[恢复失败]", self.description())
        self.boot.write_text("22222222-2222-4222-8222-222222222222\n")
        self.card("update")
        self.assertNotIn("恢复失败", self.description())

    def test_success_replaces_previous_failure(self):
        self.card("update", "enable", "3")
        self.runtime.write_text("state=restored\nactive_tx=none\n")
        self.card("update", "disable", "0")
        self.assertIn("[已恢复]", self.description())

    def test_active_transaction_and_exact_candidate(self):
        tx = self.work / "transactions/t-fixture"
        tx.mkdir(parents=True)
        shutil.copyfile(self.boot, tx / "wrapper-boot")
        self.cfg.write_text("DisallowBand6G 0\n")
        shutil.copyfile(self.cfg, tx / "candidate")
        self.mounts.write_text(f"9 1 0:1 /candidate {self.cfg} rw - tmpfs tmpfs rw\n")
        self.runtime.write_text(f"state=active\nguardian=alive\nactive_tx={tx}\n")
        self.card("update")
        self.assertIn("[事务运行]", self.description())
        self.assertIn("配置：临时配置", self.description())
        (tx / "candidate").write_text("wrong\n")
        self.card("update")
        self.assertIn("配置：变更或不可读", self.description())

    def test_dead_guardian_never_shows_running(self):
        self.runtime.write_text("state=active\nguardian=not_running\nactive_tx=/somewhere\n")
        self.card("update")
        self.assertIn("事务：待核对", self.description())
        self.assertNotIn("[事务运行]", self.description())

    def test_native_failure_is_visible(self):
        self.card("update", CARD_NATIVE_RC="3")
        self.assertIn("[状态读取失败]", self.description())

    def test_disable_remove_markers(self):
        (self.mod / "disable").touch()
        self.card("update")
        self.assertIn("[模块已禁用]", self.description())
        (self.mod / "remove").touch()
        self.card("update")
        self.assertIn("[模块待移除]", self.description())

    def test_description_updates_do_not_change_integrity_or_metadata(self):
        before = (self.mod / "SHA256SUMS").read_bytes()
        self.assertEqual(self.verify(), 0)
        self.assertEqual(self.card("update").returncode, 0)
        self.assertEqual(self.verify(), 0)
        self.assertEqual(before, (self.mod / "SHA256SUMS").read_bytes())
        text = (self.mod / "module.prop").read_text().replace("versionCode=21", "versionCode=999")
        (self.mod / "module.prop").write_text(text)
        self.assertNotEqual(self.verify(), 0)

    def test_script_and_template_tampering_fail_integrity(self):
        for name in ("bin/native.sh", "module.prop.template"):
            path = self.mod / name
            original = path.read_bytes()
            path.write_bytes(original + b"\n# change\n")
            self.assertNotEqual(self.verify(), 0)
            path.write_bytes(original)

    def test_symlink_property_is_not_overwritten(self):
        outside = self.root / "outside"
        outside.write_text("untouched")
        (self.mod / "module.prop").unlink()
        (self.mod / "module.prop").symlink_to(outside)
        self.assertNotEqual(self.card("update").returncode, 0)
        self.assertEqual(outside.read_text(), "untouched")
        self.assertNotEqual(self.verify(), 0)

    def test_presentation_lock_is_nonblocking(self):
        with (self.work / "status-card.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            before = (self.mod / "module.prop").read_bytes()
            self.assertEqual(self.card("update").returncode, 0)
            self.assertEqual((self.mod / "module.prop").read_bytes(), before)

    def test_busy_renderer_does_not_lose_operation_result(self):
        with (self.work / "status-card.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.card("update", "enable", "3").returncode, 0)
        self.card("update")
        self.assertIn("[启用失败]", self.description())

    def test_real_control_preserves_operation_exit_and_publishes_failure(self):
        result = subprocess.run(["sh", str(self.mod / "bin/control.sh"), str(self.mod), "enable"],
                                env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertIn("[启用失败]", self.description())

    def test_real_control_keeps_error_even_if_renderer_cannot_run(self):
        (self.mod / "module.prop.template").unlink()
        result = subprocess.run(["sh", str(self.mod / "bin/control.sh"), str(self.mod), "enable"],
                                env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
