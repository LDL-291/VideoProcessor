# PyInstaller spec for the Batch H.264 Encoder GUI (Windows).
#
# Build with:  pyinstaller video_batch.spec
# Output:      dist/BatchH264Encoder/BatchH264Encoder.exe (one-folder build)
#
# ffmpeg.exe / ffprobe.exe are NOT bundled by this spec (see build.ps1) --
# licensing and binary size mean the build script copies a separately
# obtained ffmpeg build (with libx264 + libvmaf enabled) into
# dist/BatchH264Encoder/ffmpeg/ afterward. video_batch.core.ffmpeg_locate
# looks there (and on PATH) at runtime.

block_cipher = None

a = Analysis(
    ["video_batch/app.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BatchH264Encoder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # GUI app: no console window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="BatchH264Encoder",
)
