"""Remote backend implementations for hermes-sync."""

from .base import RemoteBackend, RemoteObject, RemoteObjectMetadata
from .local import LocalFolderBackend
from .oss import OssBackend, OssCredentials
from .s3 import S3CompatibleBackend, S3Credentials
from .webdav import WebDavBackend, WebDavCredentials

__all__ = [
    "LocalFolderBackend",
    "OssBackend",
    "OssCredentials",
    "RemoteBackend",
    "RemoteObject",
    "RemoteObjectMetadata",
    "S3CompatibleBackend",
    "S3Credentials",
    "WebDavBackend",
    "WebDavCredentials",
]
