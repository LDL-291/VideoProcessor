# Batch H.264 Encoder (GUI): Final Plan

Goal: batch-convert videos to H.264 MP4 with a GUI, with **no visible quality loss**, and prove it.

Honest baseline: any lossy-to-lossy re-encode loses *some* information. The realistic target is **visually transparent** output, meaning a VMAF score of 95 or higher against the source, enforced by an automatic check rather than assumed.

---

## 1. Code review of the earlier snippets

Issues found in the ffmpeg and PyAV examples I gave, ordered by impact on quality.

| # | Issue | Impact | Fix |
|---|---|---|---|
| 1 | Default `-crf 23 -preset medium` | Visible softening and blocking on detailed or dark scenes | Default to CRF 17 with `-preset slow` ("Visually lossless"). CRF 18 is the ceiling. |
| 2 | Forced `-pix_fmt yuv420p` on everything | 10-bit or 4:2:2/4:4:4 sources lose bit depth and chroma. Banding appears in gradients. | Keep the source pixel format when libx264 supports it. Only convert on request, and warn first. |
| 3 | HDR / wide-gamut sources converted to 8-bit with no tonemapping | Washed-out or wrong colors | Detect HDR (bt2020, PQ/HLG) via ffprobe. Warn and skip by default, or offer an explicit tonemap option. |
| 4 | Color tags not carried over | Colors shift between players | Pass through `-colorspace`, `-color_primaries`, `-color_trc`, `-color_range` from ffprobe. |
| 5 | Audio always re-encoded to AAC 128k | Needless audio quality loss | Use `-c:a copy` when the source codec is MP4-compatible (AAC, MP3, AC3). Otherwise AAC at 256k. |
| 6 | Only the default video and audio streams kept | Extra audio tracks, subtitles and chapters silently dropped | Map explicitly: `-map 0:v:0 -map 0:a? -map 0:s? -map_metadata 0 -map_chapters 0`, with subtitles as `mov_text`. |
| 7 | `-profile:v main` forced | Caps quality tools and blocks 10-bit | Do not force a profile. Offer an optional "Max compatibility" preset that sets main profile and 8-bit. |
| 8 | Variable frame rate not handled | Audio/video drift or duplicated frames | Add `-fps_mode passthrough` to keep the original timestamps. |
| 9 | Odd width or height with yuv420p | libx264 errors out | Detect it in the probe. Pad or crop by 1px, and show a warning. |
| 10 | PyAV example | Dropped audio, VFR and color metadata, and gave less control | **Remove it.** Use the ffmpeg subprocess only. |
| 11 | `out_time_ms` used for progress | It is actually microseconds, so progress would be wrong | Use `out_time_us` (or `out_time`) divided by the probed duration. |
| 12 | NVENC/QSV/AMF offered as equal options | Hardware encoders are lower quality per bit than x264 | Default to libx264. Hardware encoding is a labeled "Fast (lower quality)" option, using `-cq` with `-preset p7 -tune hq` for NVENC. |
| 13 | Nothing verified the result | A bad encode could go unnoticed | Add the verification pipeline in section 4. |
| 14 | Output could land beside or over the source | Risk of destroying originals | Never write into the source file's path. Refuse if the output dir equals the source dir unless a suffix is set. |

---

## 2. Quality presets

| Preset | Settings | Use |
|---|---|---|
| **Visually Lossless (default)** | libx264, CRF 17, preset slow, source pix_fmt | General use |
| **Archival** | libx264, CRF 14, preset slower | Masters you will edit again |
| **Lossless** | libx264, CRF 0 | Huge files. For intermediates only. |
| **Balanced** | CRF 20, preset medium | Smaller files, still very good |
| **Web/Unity compatible** | CRF 18, main profile, yuv420p, faststart | Only when compatibility matters more than fidelity |
| **Fast (GPU)** | NVENC `-cq 19 -preset p7 -tune hq` | Speed. Flagged as lower efficiency. |

Rules:
- Show the quality tradeoff in plain language next to the preset.
- Never let the "size" sliders silently override the default without a visible warning.
- If the output ends up **larger** than the source, warn and offer to keep the source instead. Highly compressed sources can grow when encoded at CRF 17.

---

## 3. Skip and copy logic

1. Probe every file with ffprobe (codec, profile, pix_fmt, resolution, fps, duration, HDR flags, audio codecs, C2PA presence).
2. If the video is already H.264 and the container is MP4, offer **stream copy** (no quality loss at all) or copy the file unchanged.
3. Re-encoding an already-H.264 file is allowed but shows a **generation loss** warning.

---

## 4. Verification pipeline (the "quality must not drop" guarantee)

After each encode, before the temp file is renamed to its final name:

1. **Structural checks:** duration within 0.1 s of the source, same resolution, same frame count (or within 1), all expected streams present.
2. **Quality score:** run ffmpeg `libvmaf` (or SSIM/PSNR if libvmaf is missing) on the whole file for short clips, or on 5 evenly spaced 5-second samples for long ones.
3. **Threshold:** VMAF of at least 95 and SSIM of at least 0.98 pass.
4. **Auto-retry:** on failure, re-encode once with CRF lowered by 2. If it still fails, mark the file "Quality check failed" and keep the temp file for inspection.
5. Log every score to a per-run report (CSV). Optionally skip verification for speed.

---

## 5. C2PA handling

Not implemented. C2PA credentials are not retained or re-signed; re-encoding invalidates them and that is accepted. The original source file is always kept untouched regardless.

---

## 6. GUI (PySide6)

- Add files, add folder (recursive optional), drag and drop.
- Output directory picker, with an option to mirror the input folder structure.
- Queue table columns: name, source codec, resolution, status, progress, quality score, warnings.
- Overall progress bar, log panel, "open output folder" button.
- Settings: preset, CRF override (advanced), audio mode, overwrite policy (skip / rename / overwrite), parallel jobs, verification on or off.
- Job states: Queued, Probing, Encoding, Verifying, Done, Skipped, Failed, Cancelled, Quality check failed.

---

## 7. Architecture

```
video_batch/
├── app.py
├── ui/
│   ├── main_window.py
│   ├── settings_panel.py
│   └── widgets.py
├── core/
│   ├── scanner.py        # expand folders to file list
│   ├── probe.py          # ffprobe wrapper, returns a MediaInfo dataclass
│   ├── planner.py        # MediaInfo + preset -> ffmpeg args + warnings
│   ├── encoder.py        # runs ffmpeg, parses progress
│   ├── verify.py         # structural checks + VMAF/SSIM
│   └── job_queue.py      # job model and state machine
├── settings.py           # JSON config
├── tests/
└── requirements.txt
```

`planner.py` is the key module. It is pure logic (no I/O), which makes every decision in section 1 unit-testable.

---

## 8. Technical details

- ffmpeg via `subprocess` with `-progress pipe:1 -nostats -hide_banner`.
- Encode in worker threads (`QThreadPool` or `QProcess`). The UI only receives signals.
- Write to `name.tmp.mp4` with an explicit `-f mp4`, rename on success, delete on cancel or failure.
- Cancel: send `q` to ffmpeg's stdin first, then terminate the process tree if it does not exit.
- Windows: use `CREATE_NO_WINDOW`, handle Unicode and long paths, and locate `ffmpeg.exe` on PATH first and then next to the app.
- Parallel jobs default to 1 (x264 already uses all cores). Allow 2 to 3 for small files.
- Always pass `-y` only after the overwrite policy has been resolved by the app, never blindly.

---

## 9. Milestones

1. **Core, no GUI:** scanner, probe, planner (with unit tests), encoder with correct progress.
2. **Verification:** structural checks, VMAF/SSIM, auto-retry, CSV report.
3. **Basic window:** pickers, queue table, start and cancel.
4. **Settings and presets:** persisted config, skip and copy logic, warnings display.
5. **Polish:** drag and drop, parallel jobs, C2PA detection, hardware encoder option.
6. **Packaging:** PyInstaller, bundle an ffmpeg build that includes libx264 and libvmaf, test on a clean Windows machine.

---

## 10. Test plan

Test on a small set of clips covering: 8-bit SDR, 10-bit, HDR, VFR phone video, rotated phone video, multi-audio-track, odd dimensions, already-H.264, Unicode path, and very long file. For each, confirm the planner's decision, the VMAF score, and that the original is byte-identical afterward.

---

## 11. Decisions

1. **Windows only.** No cross-platform support planned.
2. **NVIDIA GPU available.** NVENC is offered as the "Fast (lower quality)" preset.
3. **No C2PA support.** Credentials are not retained or re-signed.
4. **Deliverable is a packaged `.exe`** (PyInstaller), not a bare script.
