"""Offline tests for persistent local-stack initialization."""

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import stack_runtime


class StackRuntimeTests(unittest.TestCase):
    def test_seed_preserves_database_password(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            owner = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
            with (
                patch.object(stack_runtime, "SECRETS", root),
                patch.object(stack_runtime.pwd, "getpwnam", return_value=owner),
                patch.object(stack_runtime.os, "chown"),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                stack_runtime.seed()
                first = (root / "postgres-password").read_text()
                stack_runtime.seed()
                self.assertEqual(first, (root / "postgres-password").read_text())
                self.assertEqual(len(first), 64)

    def test_configure_uses_persisted_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "postgres-password").write_text("a" * 64)
            with patch.object(stack_runtime, "SECRETS", root), patch.dict(os.environ):
                stack_runtime.configure()
                self.assertEqual(
                    os.environ["DATABASE_URL"],
                    "postgresql+asyncpg://crawler:" + "a" * 64 + "@postgres:5432/crawler",
                )

    def test_invalid_secret_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "postgres-password"
            secret.write_text("invalid")
            owner = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
            with (
                patch.object(stack_runtime, "SECRETS", root),
                patch.object(stack_runtime.pwd, "getpwnam", return_value=owner),
                patch.object(stack_runtime.os, "chown"),
                self.assertRaises(RuntimeError),
            ):
                stack_runtime.seed()
            self.assertEqual(secret.read_text(), "invalid")


if __name__ == "__main__":
    unittest.main()
