"""Offline regressions for RustFS image copy-up and secret preservation."""

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_SOURCE = Path(__file__).parents[1] / "scripts/stack_s3_runtime.py"
_SPEC = importlib.util.spec_from_file_location("bootstrap_under_test", _SOURCE)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("Cannot load S3 bootstrap for tests")
bootstrap = importlib.util.module_from_spec(_SPEC)
with patch.dict(sys.modules, {"scripts": SimpleNamespace(stack_runtime=SimpleNamespace())}):
    _SPEC.loader.exec_module(bootstrap)


class LocalS3BootstrapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / "data"
        self.secrets = self.root / "secrets"
        self.spool = self.root / "spool"
        self.data.mkdir()
        self.secrets.mkdir()
        owner = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
        for name, value in (
            ("S3_DATA", self.data),
            ("S3_SECRETS", self.secrets),
            ("SPOOL", self.spool),
        ):
            self.enterContext(patch.object(bootstrap, name, value))
        self.enterContext(patch.object(bootstrap.pwd, "getpwnam", return_value=owner))
        self.enterContext(patch.object(bootstrap.os, "chown"))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def skeleton(self):
        (self.data / ".rustfs/events").mkdir(parents=True)
        (self.data / ".rustfs/audit").mkdir()

    def test_empty_volume_seeds(self):
        bootstrap.seed_s3()
        pair = json.loads((self.secrets / "credentials.json").read_text())
        self.assertEqual(len(pair["access_key"]), 20)
        self.assertEqual(len(pair["secret_key"]), 64)

    def test_image_directories_seed_without_deletion(self):
        self.skeleton()
        bootstrap.seed_s3()
        self.assertTrue((self.data / ".rustfs/events").is_dir())
        self.assertTrue((self.data / ".rustfs/audit").is_dir())
        self.assertTrue((self.secrets / "credentials.json").is_file())

    def test_existing_data_without_credentials_is_rejected(self):
        self.skeleton()
        marker = self.data / ".rustfs/format.json"
        marker.write_text("existing format")
        with self.assertRaises(RuntimeError):
            bootstrap.seed_s3()
        self.assertEqual(marker.read_text(), "existing format")
        self.assertFalse((self.secrets / "credentials.json").exists())

    def test_file_in_known_directory_is_rejected(self):
        self.skeleton()
        (self.data / ".rustfs/events/object").write_bytes(b"archive")
        with self.assertRaises(RuntimeError):
            bootstrap.seed_s3()

    def test_unknown_empty_directory_is_rejected(self):
        (self.data / "unknown").mkdir()
        with self.assertRaises(RuntimeError):
            bootstrap.seed_s3()

    def test_symlink_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.data / ".rustfs").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            bootstrap.seed_s3()

    def test_partial_secret_is_retained_and_rejected(self):
        partial = self.secrets / "access_key"
        partial.write_text("existing credential")
        with self.assertRaises(RuntimeError):
            bootstrap.seed_s3()
        self.assertEqual(partial.read_text(), "existing credential")

    def test_second_seed_preserves_credentials_with_real_data(self):
        bootstrap.seed_s3()
        first = (self.secrets / "credentials.json").read_bytes()
        marker = self.data / "object"
        marker.write_bytes(b"archive")
        bootstrap.seed_s3()
        self.assertEqual((self.secrets / "credentials.json").read_bytes(), first)
        self.assertEqual(marker.read_bytes(), b"archive")


if __name__ == "__main__":
    unittest.main()
