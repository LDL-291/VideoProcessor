from pathlib import Path

from video_batch.core.probe import parse_ffprobe_json


def _ffprobe_json(**video_overrides):
    video_stream = {
        "index": 0,
        "codec_type": "video",
        "codec_name": "h264",
        "profile": "High",
        "pix_fmt": "yuv420p",
        "width": 1920,
        "height": 1080,
        "avg_frame_rate": "30/1",
        "r_frame_rate": "30/1",
        "nb_frames": "3615",
        "color_space": "bt709",
        "color_primaries": "bt709",
        "color_transfer": "bt709",
        "color_range": "tv",
    }
    video_stream.update(video_overrides)
    return {
        "format": {"duration": "120.5", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
        "streams": [
            video_stream,
            {"index": 1, "codec_type": "audio", "codec_name": "aac"},
            {"index": 2, "codec_type": "subtitle", "codec_name": "mov_text"},
        ],
    }


def test_parse_basic_fields():
    info = parse_ffprobe_json(_ffprobe_json(), Path("clip.mp4"))

    assert info.duration_s == 120.5
    assert info.video_codec == "h264"
    assert info.width == 1920
    assert info.height == 1080
    assert info.fps == 30.0
    assert info.audio_codecs == ["aac"]
    assert info.subtitle_count == 1
    assert info.nb_frames == 3615


def test_missing_nb_frames_is_none():
    info = parse_ffprobe_json(_ffprobe_json(nb_frames=None), Path("clip.mp4"))

    assert info.nb_frames is None


def test_hdr_detection_via_transfer_characteristic():
    info = parse_ffprobe_json(
        _ffprobe_json(color_transfer="smpte2084", color_primaries="bt2020"),
        Path("hdr.mp4"),
    )

    assert info.is_hdr is True


def test_sdr_is_not_hdr():
    info = parse_ffprobe_json(_ffprobe_json(), Path("sdr.mp4"))

    assert info.is_hdr is False


def test_odd_dimensions_detected():
    info = parse_ffprobe_json(_ffprobe_json(width=1921, height=1080), Path("odd.mp4"))

    assert info.is_odd_dimensions is True


def test_even_dimensions_not_flagged():
    info = parse_ffprobe_json(_ffprobe_json(), Path("even.mp4"))

    assert info.is_odd_dimensions is False


def test_vfr_detected_when_avg_and_r_frame_rate_diverge():
    info = parse_ffprobe_json(
        _ffprobe_json(avg_frame_rate="29/1", r_frame_rate="60/1"), Path("vfr.mp4")
    )

    assert info.is_vfr is True


def test_cfr_not_flagged_as_vfr():
    info = parse_ffprobe_json(_ffprobe_json(), Path("cfr.mp4"))

    assert info.is_vfr is False


def test_missing_video_stream_does_not_crash():
    data = {
        "format": {"duration": "10", "format_name": "wav"},
        "streams": [{"index": 0, "codec_type": "audio", "codec_name": "pcm_s16le"}],
    }

    info = parse_ffprobe_json(data, Path("audio_only.wav"))

    assert info.video_codec is None
    assert info.width is None
    assert info.is_odd_dimensions is False
