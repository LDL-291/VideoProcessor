from pathlib import Path

import pytest

from video_batch.core.planner import Action, OverwritePolicy, Preset, build_plan
from video_batch.core.probe import MediaInfo


def make_info(**overrides) -> MediaInfo:
    defaults = dict(
        path=Path("C:/videos/clip.mov"),
        duration_s=60.0,
        video_codec="prores",
        profile=None,
        pix_fmt="yuv422p10le",
        width=1920,
        height=1080,
        fps=30.0,
        nb_frames=1800,
        color_space="bt709",
        color_primaries="bt709",
        color_transfer="bt709",
        color_range="tv",
        is_vfr=False,
        audio_codecs=["pcm_s16le"],
        subtitle_count=0,
        container="mov",
    )
    defaults.update(overrides)
    return MediaInfo(**defaults)


def test_refuses_when_output_dir_equals_source_dir_and_no_suffix():
    info = make_info(path=Path("C:/videos/clip.mov"))

    plan = build_plan(info, Preset.VISUALLY_LOSSLESS, output_dir=Path("C:/videos"))

    assert plan.action == Action.REFUSE


def test_allows_same_dir_when_suffix_set():
    info = make_info(path=Path("C:/videos/clip.mov"))

    plan = build_plan(
        info, Preset.VISUALLY_LOSSLESS, output_dir=Path("C:/videos"), suffix="_h264"
    )

    assert plan.action == Action.ENCODE
    assert plan.output_path.name == "clip_h264.mp4"


def test_default_preset_uses_crf_17_slow():
    info = make_info()

    plan = build_plan(info, Preset.VISUALLY_LOSSLESS, output_dir=Path("C:/out"))

    assert "-crf" in plan.args
    assert plan.args[plan.args.index("-crf") + 1] == "17"
    assert plan.args[plan.args.index("-preset") + 1] == "slow"


def test_keeps_source_pix_fmt_by_default():
    info = make_info(pix_fmt="yuv422p10le")

    plan = build_plan(info, Preset.VISUALLY_LOSSLESS, output_dir=Path("C:/out"))

    assert "yuv422p10le" in plan.args


def test_web_compatible_forces_pix_fmt_and_warns():
    info = make_info(pix_fmt="yuv422p10le")

    plan = build_plan(info, Preset.WEB_COMPATIBLE, output_dir=Path("C:/out"))

    idx = plan.args.index("-pix_fmt")
    assert plan.args[idx + 1] == "yuv420p"
    assert any("Pixel format forced" in w for w in plan.warnings)


def test_hdr_warns_by_default():
    info = make_info(color_transfer="smpte2084", color_primaries="bt2020")

    plan = build_plan(info, Preset.VISUALLY_LOSSLESS, output_dir=Path("C:/out"))

    assert any("HDR" in w for w in plan.warnings)


def test_color_tags_passed_through():
    info = make_info()

    plan = build_plan(info, Preset.VISUALLY_LOSSLESS, output_dir=Path("C:/out"))

    assert "-colorspace" in plan.args
    assert "-color_primaries" in plan.args
    assert "-color_trc" in plan.args
    assert "-color_range" in plan.args


def test_audio_copy_when_mp4_compatible():
    info = make_info(audio_codecs=["aac"])

    plan = build_plan(info, Preset.VISUALLY_LOSSLESS, output_dir=Path("C:/out"))

    idx = plan.args.index("-c:a")
    assert plan.args[idx + 1] == "copy"


def test_audio_reencoded_when_incompatible():
    info = make_info(audio_codecs=["pcm_s16le"])

    plan = build_plan(info, Preset.VISUALLY_LOSSLESS, output_dir=Path("C:/out"))

    idx = plan.args.index("-c:a")
    assert plan.args[idx + 1] == "aac"
    assert "256k" in plan.args


def test_explicit_stream_mapping():
    info = make_info(subtitle_count=1)

    plan = build_plan(info, Preset.VISUALLY_LOSSLESS, output_dir=Path("C:/out"))

    assert plan.args.count("-map") == 3
    assert "-map_chapters" in plan.args
    assert "-c:s" in plan.args


def test_vfr_uses_passthrough_fps_mode():
    info = make_info(is_vfr=True)

    plan = build_plan(info, Preset.VISUALLY_LOSSLESS, output_dir=Path("C:/out"))

    idx = plan.args.index("-fps_mode")
    assert plan.args[idx + 1] == "passthrough"


def test_odd_dimensions_padded_and_warned():
    info = make_info(width=1921, height=1080)

    plan = build_plan(info, Preset.VISUALLY_LOSSLESS, output_dir=Path("C:/out"))

    assert any("pad=" in a for a in plan.args)
    assert any("odd dimensions" in w for w in plan.warnings)


def test_no_profile_forced_by_default():
    info = make_info()

    plan = build_plan(info, Preset.VISUALLY_LOSSLESS, output_dir=Path("C:/out"))

    assert "-profile:v" not in plan.args


def test_web_compatible_forces_main_profile():
    info = make_info()

    plan = build_plan(info, Preset.WEB_COMPATIBLE, output_dir=Path("C:/out"))

    idx = plan.args.index("-profile:v")
    assert plan.args[idx + 1] == "main"


def test_gpu_preset_uses_nvenc():
    info = make_info()

    plan = build_plan(info, Preset.FAST_GPU, output_dir=Path("C:/out"))

    idx = plan.args.index("-c:v")
    assert plan.args[idx + 1] == "h264_nvenc"
    assert "-cq" in plan.args


def test_already_h264_mp4_offers_stream_copy():
    info = make_info(video_codec="h264", container="mov,mp4,m4a,3gp,3g2,mj2")

    plan = build_plan(info, Preset.VISUALLY_LOSSLESS, output_dir=Path("C:/out"))

    assert plan.action == Action.COPY
    assert "copy" in plan.args


def test_reencoding_h264_warns_generation_loss():
    info = make_info(video_codec="h264", container="mp4")

    plan = build_plan(
        info, Preset.VISUALLY_LOSSLESS, output_dir=Path("C:/out"), force_reencode_h264=True
    )

    assert plan.action == Action.ENCODE
    assert any("generation loss" in w for w in plan.warnings)


def test_skip_policy_skips_existing_output(tmp_path: Path):
    info = make_info(path=tmp_path / "src" / "clip.mov")
    (tmp_path / "src").mkdir()
    info.path.write_bytes(b"x")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "clip.mp4").write_bytes(b"existing")

    plan = build_plan(
        info, Preset.VISUALLY_LOSSLESS, output_dir=out_dir, overwrite_policy=OverwritePolicy.SKIP
    )

    assert plan.action == Action.SKIP


def test_rename_policy_avoids_collision(tmp_path: Path):
    info = make_info(path=tmp_path / "src" / "clip.mov")
    (tmp_path / "src").mkdir()
    info.path.write_bytes(b"x")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "clip.mp4").write_bytes(b"existing")

    plan = build_plan(
        info, Preset.VISUALLY_LOSSLESS, output_dir=out_dir, overwrite_policy=OverwritePolicy.RENAME
    )

    assert plan.action == Action.ENCODE
    assert plan.output_path.name == "clip (1).mp4"


def test_mirrors_folder_structure_when_source_root_given(tmp_path: Path):
    source_root = tmp_path / "input"
    info = make_info(path=source_root / "sub" / "clip.mov")

    plan = build_plan(
        info, Preset.VISUALLY_LOSSLESS, output_dir=tmp_path / "out", source_root=source_root
    )

    assert plan.output_path == tmp_path / "out" / "sub" / "clip.mp4"


def test_encode_and_copy_target_a_tmp_path_not_the_final_path():
    info = make_info()

    plan = build_plan(info, Preset.VISUALLY_LOSSLESS, output_dir=Path("C:/out"))

    assert plan.tmp_output_path == Path("C:/out/clip.tmp.mp4")
    assert plan.args[-1] == str(plan.tmp_output_path)
    assert plan.args[-1] != str(plan.output_path)


def test_plan_reports_crf_used():
    info = make_info()

    plan = build_plan(info, Preset.VISUALLY_LOSSLESS, output_dir=Path("C:/out"))

    assert plan.crf == 17


def test_crf_override_replaces_preset_crf():
    info = make_info()

    plan = build_plan(
        info, Preset.VISUALLY_LOSSLESS, output_dir=Path("C:/out"), crf_override=15
    )

    assert plan.crf == 15
    idx = plan.args.index("-crf")
    assert plan.args[idx + 1] == "15"


def test_crf_override_ignored_for_gpu_preset():
    info = make_info()

    plan = build_plan(
        info, Preset.FAST_GPU, output_dir=Path("C:/out"), crf_override=10
    )

    assert plan.crf is None
    assert "-crf" not in plan.args


def test_copy_plan_has_no_crf():
    info = make_info(video_codec="h264", container="mp4")

    plan = build_plan(info, Preset.VISUALLY_LOSSLESS, output_dir=Path("C:/out"))

    assert plan.crf is None


def test_batch_avoids_output_collision_even_under_skip_policy():
    reserved: set[Path] = set()
    info_a = make_info(path=Path("C:/videos/a/clip.mov"))
    info_b = make_info(path=Path("C:/videos/b/clip.mov"))

    plan_a = build_plan(
        info_a, Preset.VISUALLY_LOSSLESS, output_dir=Path("C:/out"), reserved_paths=reserved
    )
    plan_b = build_plan(
        info_b, Preset.VISUALLY_LOSSLESS, output_dir=Path("C:/out"), reserved_paths=reserved
    )

    assert plan_a.output_path != plan_b.output_path
    assert plan_b.output_path.name == "clip (1).mp4"
