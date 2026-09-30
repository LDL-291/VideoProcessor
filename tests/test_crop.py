import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_planner import make_info
from video_batch.core import verify
from video_batch.core.planner import (
    Action,
    CropMode,
    CropSpec,
    Preset,
    build_plan,
    compute_crop,
)
from video_batch.settings import AppSettings, settings_from_dict, settings_to_dict


def test_inactive_specs_return_none():
    info = make_info()
    assert compute_crop(info, CropSpec()) is None
    assert compute_crop(info, CropSpec(mode=CropMode.MARGINS)) is None


def test_margins_trim_each_edge():
    spec = CropSpec(mode=CropMode.MARGINS, left=100, top=20, right=60, bottom=40)
    assert compute_crop(make_info(), spec) == (1760, 1020, 100, 20)


def test_margins_round_to_even():
    spec = CropSpec(mode=CropMode.MARGINS, left=11, top=3, right=0, bottom=0)
    w, h, x, y = compute_crop(make_info(), spec)
    assert (x, y) == (10, 2)
    assert w % 2 == 0 and h % 2 == 0


def test_margins_larger_than_source_raise():
    spec = CropSpec(mode=CropMode.MARGINS, left=1000, right=1000)
    with pytest.raises(ValueError):
        compute_crop(make_info(), spec)


def test_aspect_9_16_from_landscape_is_centered():
    spec = CropSpec(mode=CropMode.ASPECT, aspect_w=9, aspect_h=16)
    assert compute_crop(make_info(), spec) == (606, 1080, 657 - 657 % 2, 0)


def test_aspect_wider_than_source_crops_height():
    info = make_info(width=1080, height=1920)
    spec = CropSpec(mode=CropMode.ASPECT, aspect_w=1, aspect_h=1)
    assert compute_crop(info, spec) == (1080, 1080, 0, 420)


def test_aspect_matching_source_is_a_noop():
    spec = CropSpec(mode=CropMode.ASPECT, aspect_w=16, aspect_h=9)
    assert compute_crop(make_info(), spec) is None


def test_plan_adds_crop_filter_and_records_rect():
    spec = CropSpec(mode=CropMode.MARGINS, left=100, right=100)
    plan = build_plan(make_info(), Preset.BALANCED, Path("C:/out"), crop=spec)

    assert plan.action == Action.ENCODE
    assert plan.crop_rect == (1720, 1080, 100, 0)
    assert plan.args[plan.args.index("-vf") + 1] == "crop=1720:1080:100:0"


def test_crop_forces_reencode_of_h264_mp4():
    info = make_info(video_codec="h264", container="mov,mp4,m4a,3gp,3g2,mj2")
    spec = CropSpec(mode=CropMode.MARGINS, top=10)

    assert build_plan(info, Preset.BALANCED, Path("C:/out")).action == Action.COPY
    plan = build_plan(info, Preset.BALANCED, Path("C:/out"), crop=spec)

    assert plan.action == Action.ENCODE
    assert any("generation loss" in w for w in plan.warnings)


def test_unsatisfiable_crop_refuses_plan():
    spec = CropSpec(mode=CropMode.MARGINS, left=5000)
    plan = build_plan(make_info(), Preset.BALANCED, Path("C:/out"), crop=spec)

    assert plan.action == Action.REFUSE
    assert plan.output_path is None


def test_structural_check_uses_expected_size():
    src = make_info()
    out = make_info(width=606, height=1080)

    assert not verify.check_structural(src, out).passed
    assert verify.check_structural(src, out, expected_size=(606, 1080)).passed
    assert not verify.check_structural(src, src, expected_size=(606, 1080)).passed


def test_reference_chain_crops_only_when_requested():
    assert verify._reference_chain(None) == "[1:v]null[ref];"
    assert verify._reference_chain((606, 1080, 656, 0)) == "[1:v]crop=606:1080:656:0[ref];"


def test_settings_roundtrip_and_validation():
    s = AppSettings(crop_mode="aspect", crop_aspect_w=4, crop_aspect_h=5, crop_left=8)
    assert settings_from_dict(settings_to_dict(s)) == s
    assert s.crop_spec().mode == CropMode.ASPECT

    bad = settings_from_dict({"crop_mode": "zoom", "crop_left": -4, "crop_aspect_w": 0})
    assert bad.crop_mode == "none" and bad.crop_left == 0 and bad.crop_aspect_w == 9


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_end_to_end_crop_with_real_ffmpeg(tmp_path):
    from video_batch.core.probe import probe_file

    src = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=2",
         "-pix_fmt", "yuv420p", str(src)],
        check=True,
    )
    info = probe_file(src)
    spec = CropSpec(mode=CropMode.ASPECT, aspect_w=9, aspect_h=16)
    plan = build_plan(info, Preset.BALANCED, tmp_path / "out", crop=spec)
    plan.tmp_output_path.parent.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", *plan.args], check=True)

    out = probe_file(plan.tmp_output_path)
    assert (out.width, out.height) == plan.crop_rect[:2] == (202, 360)
    assert verify.check_structural(info, out, plan.crop_rect[:2]).passed

    score = verify.run_quality_check(src, plan.tmp_output_path, info.duration_s,
                                     use_vmaf=False, crop_rect=plan.crop_rect)
    assert score.ssim is not None and score.ssim > 0.95
