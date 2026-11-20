"""Compose entrypoint adding persistent local S3 without changing DB bootstrap."""

import json
import os
import pwd
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import stack_runtime as runtime

S3_SECRETS = Path("/run/crawler-s3")
S3_DATA = Path("/var/lib/crawler-s3")
SPOOL = Path("/var/lib/crawler/warc-spool")


def pristine_s3_data(root: Path) -> bool:
    """Allow an empty volume or only empty directories shipped in the S3 image."""
    if root.is_symlink() or not root.is_dir():
        return False
    allowed = {".rustfs", ".rustfs/events", ".rustfs/audit", "logs"}
    for path in root.rglob("*"):
        if (
            path.is_symlink()
            or not path.is_dir()
            or path.relative_to(root).as_posix() not in allowed
        ):
            return False
    return True


def seed_s3() -> None:
    S3_SECRETS.mkdir(parents=True, exist_ok=True)
    source = S3_SECRETS / "credentials.json"
    if not source.exists():
        if not pristine_s3_data(S3_DATA) or any(S3_SECRETS.iterdir()):
            raise RuntimeError("S3 data or partial secrets exist; restore original credentials")
        credentials = {
            "access_key": secrets.token_hex(10).upper(),
            "secret_key": secrets.token_hex(32),
        }
        temporary = source.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        with os.fdopen(fd, "w") as stream:
            json.dump(credentials, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, source)
    credentials = json.loads(source.read_text())
    if len(credentials["access_key"]) != 20 or len(credentials["secret_key"]) != 64:
        raise RuntimeError("Invalid persisted S3 credentials; refusing rotation")
    for field in ("access_key", "secret_key"):
        destination = S3_SECRETS / field
        if destination.exists():
            if destination.read_text() != credentials[field]:
                raise RuntimeError("Conflicting S3 credential files; operator recovery required")
        else:
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
            with os.fdopen(fd, "w") as stream:
                stream.write(credentials[field])
                stream.flush()
                os.fsync(stream.fileno())
    fd = os.open(S3_SECRETS, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    for path, uid, gid in (
        (S3_DATA, 10001, 10001),
        (S3_DATA / "logs", 10001, 10001),
        (SPOOL, pwd.getpwnam("crawler").pw_uid, pwd.getpwnam("crawler").pw_gid),
    ):
        path.mkdir(parents=True, exist_ok=True)
        os.chown(path, uid, gid)
        path.chmod(0o700)
    print("Persistent local S3 credentials and storage permissions ready; secrets omitted")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Expected stack action or application command")
    action = sys.argv[1]
    if action == "secrets":
        runtime.seed()
        seed_s3()
        return
    if action != "key":
        credentials = json.loads((S3_SECRETS / "credentials.json").read_text())
        os.environ["S3_ACCESS_KEY"] = credentials["access_key"]
        os.environ["S3_SECRET_KEY"] = credentials["secret_key"]
    runtime.main()


if __name__ == "__main__":
    main()
