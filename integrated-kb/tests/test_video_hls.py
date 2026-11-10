"""Security tests for HLS path validation."""

from pathlib import Path

from app.video_hls import (
    get_hls_path,
    validate_hls_filename,
    validate_storage_name,
)


STORAGE = "a" * 32 + ".mp4"


def test_valid_storage_names():
    assert validate_storage_name(STORAGE)
    assert validate_storage_name("0123456789abcdef" * 2 + ".mp4")


def test_invalid_storage_names():
    invalid = (
        "",
        None,
        123,
        "../video.mp4",
        "/tmp/video.mp4",
        "a" * 32 + ".webm",
        "A" * 32 + ".mp4",
        "a" * 31 + ".mp4",
        "a" * 33 + ".mp4",
        "a" * 32 + ".mp4/../../secret",
    )

    for value in invalid:
        assert not validate_storage_name(value), repr(value)


def test_valid_hls_filenames():
    valid = (
        "index.m3u8",
        "segment_00000.ts",
        "segment_00001.ts",
        "segment_99999.ts",
    )

    for filename in valid:
        assert validate_hls_filename(filename)


def test_invalid_hls_filenames():
    invalid = (
        "",
        None,
        123,
        "../index.m3u8",
        "/etc/passwd",
        "segment_00000.ts/../../secret",
        "segment_00000.ts\x00",
        "segment_00000.ts?x=1",
        "segment_00000.ts/",
        "segment_000000.ts",
        "segment_-0001.ts",
        "segment_abcde.ts",
        "other.ts",
        "INDEX.M3U8",
    )

    for filename in invalid:
        assert not validate_hls_filename(filename), repr(filename)


def test_safe_path():
    result = get_hls_path(STORAGE, "segment_00000.ts")

    assert result == (
        Path("/srv/kb/uploads/hls")
        / ("a" * 32)
        / "segment_00000.ts"
    )


def test_invalid_path_rejected():
    invalid_cases = (
        ("../video.mp4", "index.m3u8"),
        (STORAGE, "../../etc/passwd"),
        (STORAGE, "/etc/passwd"),
        (STORAGE, "segment_00000.ts/../secret"),
    )

    for storage, filename in invalid_cases:
        try:
            get_hls_path(storage, filename)
        except ValueError:
            pass
        else:
            raise AssertionError(
                f"Unsafe path accepted: {storage!r}, {filename!r}"
            )


if __name__ == "__main__":
    test_valid_storage_names()
    test_invalid_storage_names()
    test_valid_hls_filenames()
    test_invalid_hls_filenames()
    test_safe_path()
    test_invalid_path_rejected()

    print("PASS: valid storage names")
    print("PASS: invalid storage names rejected")
    print("PASS: valid HLS filenames")
    print("PASS: invalid HLS filenames rejected")
    print("PASS: safe HLS path generation")
    print("PASS: unsafe paths rejected")
    print("ALL HLS PATH TESTS PASSED")
