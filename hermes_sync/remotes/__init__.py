"""Remote backend implementations for hermes-sync."""

from .base import RemoteBackend, RemoteObject, RemoteObjectMetadata
from .local import LocalFolderBackend
from .oss import OssBackend, OssCredentials

__all__ = [
    "LocalFolderBackend",
    "OssBackend",
    "OssCredentials",
    "RemoteBackend",
    "RemoteObject",
    "RemoteObjectMetadata",
]
