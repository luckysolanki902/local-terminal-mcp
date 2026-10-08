"""Tests for binary writes, image reads, and local image generation."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from local_terminal_mcp import executor
from local_terminal_mcp.imagegen import GeneratorConfigError, build_generator_argv
from local_terminal_mcp.policy import Policy, PolicyError

# A minimal valid 1x1 PNG.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


# -- base64 binary write ---------------------------------------------------


def test_write_base64_requires_write_mode(tmp_path: Path) -> None:
    policy = Policy(root=tmp_path, allow_write=False)
    with pytest.raises(PolicyError, match="write operations are disabled"):
        executor.write_file_base64(policy, "a.png", "aGk=")


def test_write_base64_writes_bytes(tmp_path: Path) -> None:
    policy = Policy(root=tmp_path, allow_write=True)
    b64 = base64.b64encode(PNG_1X1).decode()
    executor.write_file_base64(policy, "sprites/a.png", b64)
    assert (tmp_path / "sprites" / "a.png").read_bytes() == PNG_1X1


def test_write_base64_accepts_data_url(tmp_path: Path) -> None:
    policy = Policy(root=tmp_path, allow_write=True)
    b64 = base64.b64encode(PNG_1X1).decode()
    executor.write_file_base64(policy, "a.png", f"data:image/png;base64,{b64}")
    assert (tmp_path / "a.png").read_bytes() == PNG_1X1


def test_write_base64_rejects_bad_base64(tmp_path: Path) -> None:
    policy = Policy(root=tmp_path, allow_write=True)
    with pytest.raises(PolicyError, match="not valid base64"):
        executor.write_file_base64(policy, "a.png", "not base64!!!")


def test_write_base64_contained(tmp_path: Path) -> None:
    policy = Policy(root=tmp_path, allow_write=True)
    with pytest.raises(PolicyError):
        executor.write_file_base64(policy, "../escape.png", "aGk=")


# -- image read ------------------------------------------------------------


def test_resolve_image_ok(tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(PNG_1X1)
    policy = Policy(root=tmp_path)
    assert executor.resolve_image(policy, "a.png").name == "a.png"


def test_resolve_image_rejects_non_image(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hi")
    policy = Policy(root=tmp_path)
    with pytest.raises(PolicyError, match="not a supported image"):
        executor.resolve_image(policy, "a.txt")


def test_resolve_image_rejects_too_large(tmp_path: Path) -> None:
    big = tmp_path / "big.png"
    big.write_bytes(b"\x00" * (executor.IMAGE_READ_MAX_BYTES + 1))
    policy = Policy(root=tmp_path)
    with pytest.raises(PolicyError, match="larger than"):
        executor.resolve_image(policy, "big.png")


def test_read_image_payload_small_is_exact(tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(PNG_1X1)
    policy = Policy(root=tmp_path)
    data, fmt = executor.read_image_payload(policy, "a.png", max_bytes=100_000)
    assert data == PNG_1X1 and fmt == "png"


def test_read_image_payload_downscales_large(tmp_path: Path) -> None:
    import os

    from PIL import Image as PILImage

    # A big, high-entropy image that won't compress under the cap at full size.
    big = PILImage.frombytes("RGB", (2000, 2000), os.urandom(2000 * 2000 * 3))
    big.save(tmp_path / "big.png")
    assert (tmp_path / "big.png").stat().st_size > 1_000_000

    policy = Policy(root=tmp_path)
    cap = 500_000
    data, fmt = executor.read_image_payload(policy, "big.png", max_bytes=cap)
    assert fmt == "jpeg"
    assert len(data) <= cap
    # original on disk is untouched
    assert (tmp_path / "big.png").stat().st_size > 1_000_000


# -- describe_image (text companion for the dual return) -------------------


def test_describe_image_small(tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(PNG_1X1)
    policy = Policy(root=tmp_path)
    data, fmt = executor.read_image_payload(policy, "a.png", max_bytes=100_000)
    text = executor.describe_image(policy, "a.png", data, fmt)
    assert "image: a.png" in text
    assert f"bytes on disk: {len(PNG_1X1)}" in text
    assert "downscaled" not in text
    assert "1x1" in text


def test_describe_image_downscaled_notes_it(tmp_path: Path) -> None:
    import os

    from PIL import Image as PILImage

    big = PILImage.frombytes("RGB", (2000, 2000), os.urandom(2000 * 2000 * 3))
    big.save(tmp_path / "big.png")
    policy = Policy(root=tmp_path)
    data, fmt = executor.read_image_payload(policy, "big.png", max_bytes=500_000)
    text = executor.describe_image(policy, "big.png", data, fmt)
    assert "downscaled" in text
    assert "original 2000x2000" in text


# -- generator argv building -----------------------------------------------


def test_build_generator_argv_keeps_prompt_as_one_token() -> None:
    argv = build_generator_argv(
        "sd -p {prompt} -o {output}", "a cute cat", "out.png"
    )
    assert argv == ["sd", "-p", "a cute cat", "-o", "out.png"]


def test_build_generator_argv_requires_output() -> None:
    with pytest.raises(GeneratorConfigError, match="output"):
        build_generator_argv("sd -p {prompt}", "x", "out.png")


# -- generate_image end to end (with a stub generator) ---------------------


def test_generate_image_with_stub(tmp_path: Path) -> None:
    # A stub "generator": a python script that writes PNG bytes to {output}.
    stub = tmp_path / "stub_gen.py"
    stub.write_text(
        "import sys, base64\n"
        "data = base64.b64decode(sys.argv[1])\n"
        "open(sys.argv[2], 'wb').write(data)\n"
    )
    b64 = base64.b64encode(PNG_1X1).decode()
    # The template ignores the real prompt and writes fixed PNG bytes; {prompt}
    # is still passed to prove it flows through.
    import sys

    template = f"{sys.executable} {stub} {b64} {{output}}"
    policy = Policy(root=tmp_path, allow_write=True)
    msg = executor.generate_image(
        policy, "ignored prompt", "sprites/hero.png", template, timeout_seconds=30
    )
    assert "generated image" in msg
    assert (tmp_path / "sprites" / "hero.png").read_bytes() == PNG_1X1


def test_generate_image_requires_write(tmp_path: Path) -> None:
    policy = Policy(root=tmp_path, allow_write=False)
    with pytest.raises(PolicyError, match="write operations are disabled"):
        executor.generate_image(policy, "x", "o.png", "sd -o {output}", 10)


def test_write_bytes_ok(tmp_path: Path) -> None:
    policy = Policy(root=tmp_path, allow_write=True)
    target = executor.write_bytes(policy, "assets/a.png", PNG_1X1, max_bytes=1000)
    assert target.read_bytes() == PNG_1X1


def test_write_bytes_requires_write(tmp_path: Path) -> None:
    policy = Policy(root=tmp_path, allow_write=False)
    with pytest.raises(PolicyError, match="write operations are disabled"):
        executor.write_bytes(policy, "a.png", PNG_1X1, max_bytes=1000)


def test_write_bytes_size_cap(tmp_path: Path) -> None:
    policy = Policy(root=tmp_path, allow_write=True)
    with pytest.raises(PolicyError, match="exceeds limit"):
        executor.write_bytes(policy, "a.png", b"x" * 2000, max_bytes=1000)


def test_write_bytes_contained(tmp_path: Path) -> None:
    policy = Policy(root=tmp_path, allow_write=True)
    with pytest.raises(PolicyError):
        executor.write_bytes(policy, "../escape.png", PNG_1X1, max_bytes=1000)


def test_generate_image_missing_generator(tmp_path: Path) -> None:
    policy = Policy(root=tmp_path, allow_write=True)
    with pytest.raises(PolicyError, match="not found on PATH"):
        executor.generate_image(
            policy, "x", "o.png", "definitely-not-a-real-binary-xyz {output}", 10
        )
