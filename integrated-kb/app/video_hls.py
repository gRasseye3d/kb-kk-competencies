"""Safe path validation for generated HLS video files."""

import os
import re
import stat
from pathlib import Path


HLS_DIR = Path("/srv/kb/uploads/hls")

STORAGE_NAME_RE = re.compile(
    r"^[0-9a-f]{32}\.mp4$"
)

SEGMENT_NAME_RE = re.compile(
    r"^segment_[0-9]{5}\.ts$"
)


def validate_storage_name(storage_name):
    """Accept only application-generated MP4 storage names."""
    return (
        isinstance(storage_name, str)
        and STORAGE_NAME_RE.fullmatch(storage_name) is not None
    )


def validate_hls_filename(filename):
    """Accept only the playlist or numbered TS segments."""
    return (
        filename == "index.m3u8"
        or (
            isinstance(filename, str)
            and SEGMENT_NAME_RE.fullmatch(filename) is not None
        )
    )


def get_hls_path(storage_name, filename):
    """Build an HLS path without accepting arbitrary path components."""
    if not validate_storage_name(storage_name):
        raise ValueError("Invalid video storage name")

    if not validate_hls_filename(filename):
        raise ValueError("Invalid HLS filename")

    return HLS_DIR / storage_name[:32] / filename


def hls_file_available(storage_name, filename):
    """Check HLS file using directory-relative, no-follow opens."""
    try:
        path = get_hls_path(storage_name, filename)
    except ValueError:
        return False

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW
    file_flags |= getattr(os, "O_NONBLOCK", 0)

    descriptors = []

    try:
        root_fd = os.open(HLS_DIR, directory_flags)
        descriptors.append(root_fd)

        video_fd = os.open(
            path.parent.name,
            directory_flags,
            dir_fd=root_fd,
        )
        descriptors.append(video_fd)

        file_fd = os.open(
            filename,
            file_flags,
            dir_fd=video_fd,
        )
        descriptors.append(file_fd)

        info = os.fstat(file_fd)

        return stat.S_ISREG(info.st_mode) and info.st_size > 0

    except OSError:
        return False

    finally:
        for fd in reversed(descriptors):
            os.close(fd)
