"""Argument and filesystem boundaries for testing-environment provisioning."""
from pathlib import Path
import tempfile
import unittest

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
            self.assertTrue(Path(result["baseFolder"]).is_relative_to(root / "runtime-setup" / "vms"))
            self.assertNotIn("secret fixture", str(result))
            self.assertEqual(result["guestUser"], "opencore")

    def test_version_parser_rejects_pre_java17(self):
        self.assertEqual(provision.java_major('openjdk version "21.0.8" 2025-07-15'), 21)
        self.assertEqual(provision.java_major('java version "1.8.0_431"'), 8)
        self.assertIsNone(provision.java_major("not a Java runtime"))


if __name__ == "__main__":
    unittest.main()
