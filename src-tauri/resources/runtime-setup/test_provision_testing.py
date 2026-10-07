"""Argument and filesystem boundaries for testing-environment provisioning."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import provision_testing as provision


class ProvisioningBoundaries(unittest.TestCase):
    def test_vm_requires_an_existing_iso_and_a_guest_password_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "ISO"):
                provision.vm_inputs(root, {"isoPath": str(root / "missing.iso")}, {})
            iso = root / "licensed.iso"
            iso.write_bytes(b"fixture")
            with self.assertRaisesRegex(ValueError, "environment"):
                provision.vm_inputs(root, {"isoPath": str(iso), "passwordEnv": "OPENCORE_TEST_GUEST_PASSWORD"}, {})

    def test_vm_paths_are_app_owned_and_password_is_not_returned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            iso = root / "licensed.iso"
            iso.write_bytes(b"fixture")
            result = provision.vm_inputs(root, {"isoPath": str(iso), "passwordEnv": "OPENCORE_TEST_GUEST_PASSWORD"}, {"OPENCORE_TEST_GUEST_PASSWORD": "secret fixture"})
            self.assertTrue(Path(result["baseFolder"]).is_relative_to((root / "runtime-setup" / "vms").resolve()))
            self.assertNotIn("secret fixture", str(result))
            self.assertEqual(result["guestUser"], "opencore")

    def test_version_parser_rejects_pre_java17(self):
        self.assertEqual(provision.java_major('openjdk version "21.0.8" 2025-07-15'), 21)
        self.assertEqual(provision.java_major('java version "1.8.0_431"'), 8)
        self.assertIsNone(provision.java_major("not a Java runtime"))

    def test_busy_android_port_is_rejected_without_starting_a_device(self):
        sockets = [Mock(), Mock()]
        sockets[1].bind.side_effect = OSError("already used")
        with patch.object(provision.socket, "socket", side_effect=sockets):
            with self.assertRaisesRegex(RuntimeError, "5581.*already in use"):
                provision.ensure_android_ports_free(5580)
        self.assertTrue(all(value.close.called for value in sockets))

    def test_android_boot_verifies_the_managed_avd_and_returns_usable_profile_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            sdk = root / "runtime-setup/android/sdk"
            tools = sdk / "cmdline-tools/opencore-15859902"
            (tools / "lib").mkdir(parents=True)
            (tools / "lib/sdkmanager-classpath.jar").touch()
            java = root / "java.exe"; java.touch()
            home = root / "runtime-setup/android/avd"; home.mkdir()
            (home / "OpenCore_API_36.ini").touch()
            calls = []
            avd_identity = ["OpenCore_API_36"]
            def run(args, **kwargs):
                calls.append(([str(value) for value in args], kwargs))
                output = 'openjdk version "21.0.1"' if "-version" in args else avd_identity[0] + "\nOK\n" if args[-3:] == ["emu", "avd", "name"] else "1\n"
                return subprocess.CompletedProcess(args, 0, output, "")
            runner = Mock(); runner.run.side_effect = run
            device = Mock(); device.poll.return_value = None
            with patch.object(provision, "require_windows_x64"), patch.object(provision, "java_paths", return_value=[java]), patch.object(provision, "ensure_android_ports_free"), patch.object(provision.subprocess, "Popen", return_value=device), patch("setup_manager.WindowsJob"):
                result = provision.provision_android(root, {"acceptLicenses": True}, runner)
                avd_identity[0] = "Unrelated_AVD"
                with self.assertRaisesRegex(RuntimeError, "not the managed AVD"):
                    provision.provision_android(root, {"acceptLicenses": True}, runner)
            self.assertEqual(result["androidUserHome"], str(root / "runtime-setup/android/user"))
            self.assertEqual(result["emulatorPort"], 5580)
            self.assertEqual(result["deviceSerial"], "emulator-5580")
            identity = next(args for args, _ in calls if args[-3:] == ["emu", "avd", "name"])
            self.assertEqual(identity[1:3], ["-s", result["deviceSerial"]])
            self.assertTrue(result["bootVerified"])

    def test_vm_secret_is_removed_even_when_poweroff_cleanup_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); iso = root / "licensed.iso"; iso.write_bytes(b"fixture")
            def run(args, **kwargs):
                if "--version" in args: return subprocess.CompletedProcess(args, 0, "7.2.20r123", "")
                if "detect" in args: return subprocess.CompletedProcess(args, 0, 'OSTypeId="Windows11_64"\nIsInstallSupported="true"', "")
                if "modifyvm" in args: raise RuntimeError("fixture configuration failure")
                if "poweroff" in args: raise RuntimeError("fixture cleanup failure")
                return subprocess.CompletedProcess(args, 0, "", "")
            runner = Mock(); runner.run.side_effect = run
            with patch.object(provision, "require_windows_x64"), patch.object(provision, "vbox_path", return_value="VBoxManage"), patch.dict(provision.os.environ, {"OPENCORE_TEST_GUEST_PASSWORD": "private fixture"}):
                with self.assertRaises(RuntimeError): provision.provision_vm(root, {"isoPath": str(iso)}, runner)
            self.assertFalse(list((root / "runtime-setup/vms").glob("*/.guest-password.tmp")))


if __name__ == "__main__":
    unittest.main()
