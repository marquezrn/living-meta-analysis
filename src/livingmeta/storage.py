"""Private storage with immutable content-addressed object keys."""

from pathlib import Path, PurePosixPath

from .config import Settings


def safe_key(key: str) -> str:
    p = PurePosixPath(key)
    if not key or str(p) == "." or p.is_absolute() or any(part in ("..", ".") for part in p.parts) or "\\" in key:
        raise ValueError("Invalid artifact key")
    return str(p)


class ArtifactStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.private_directory / "artifacts"
        self.client = None
        if settings.storage_backend == "r2":
            import boto3
            self.client = boto3.client("s3", endpoint_url=settings.r2_endpoint_url,
                aws_access_key_id=settings.r2_access_key_id,
                aws_secret_access_key=settings.r2_secret_access_key, region_name="auto")
        else:
            self.root.mkdir(parents=True, exist_ok=True)

    def local_path(self, key):
        path = self.root / safe_key(key)
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("Artifact path escapes private storage")
        if any(parent.is_symlink() for parent in [path, *path.parents] if parent != self.root.parent):
            raise ValueError("Symbolic links are not permitted in artifact paths")
        return path

    def put(self, key: str, content: bytes, content_type: str = "application/octet-stream"):
        key = safe_key(key)
        if self.client:
            self.client.put_object(Bucket=self.settings.r2_bucket, Key=key, Body=content,
                                   ContentType=content_type)
        else:
            path = self.local_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.read_bytes() != content:
                raise ValueError("Immutable artifact key already contains different content")
            temp = path.with_suffix(path.suffix + ".uploading")
            temp.write_bytes(content)
            temp.replace(path)

    def get(self, key: str) -> bytes:
        key = safe_key(key)
        if self.client:
            obj = self.client.get_object(Bucket=self.settings.r2_bucket, Key=key)
            return obj["Body"].read()
        return self.local_path(key).read_bytes()

    def materialize(self, key: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.get(key))
        return destination

    def check(self):
        if self.client:
            self.client.head_bucket(Bucket=self.settings.r2_bucket)
        return True
