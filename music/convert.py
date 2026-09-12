"""Convert incoming files to library formats. No UI.

The library uses 320 kbps MP3 and keeps FLAC unchanged. Converting lossless
FLAC to MP3 would discard audio information irreversibly just for consistency.

Conversion happens in place. The output takes the original name, while the
source moves to `.backup`, following the MKV remux convention.
"""

import json
import shutil
import subprocess
from pathlib import Path

TARGET_KBPS = 320
KEEP_AS_IS = {'.flac'}
BACKUP_SUFFIX = '.backup'


class FFmpegMissing(RuntimeError):
    pass


def have_ffmpeg():
    return bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))


def bitrate_kbps(path):
    """Return the bitrate in kbps, or None if unavailable.

    VBR MP3 streams may omit bit_rate. Fall back to the container bitrate,
    which represents an average in that case.
    """
    cmd = ['ffprobe', '-v', 'error', '-select_streams', 'a:0',
           '-show_entries', 'stream=bit_rate:format=bit_rate',
           '-of', 'json', str(path)]
    try:
        out = json.loads(subprocess.run(cmd, capture_output=True, text=True).stdout or '{}')
    except json.JSONDecodeError:
        return None
    for value in ((out.get('streams') or [{}])[0].get('bit_rate'),
                  (out.get('format') or {}).get('bit_rate')):
        if value and str(value).isdigit():
            return round(int(value) / 1000)
    return None


def state(path):
    """Return (display status, matches library format) without changing the file."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in KEEP_AS_IS:
        return ext[1:], True
    if ext == '.mp3':
        kbps = bitrate_kbps(path)
        if kbps is None:
            return 'mp3 (비트레이트 못 읽음)', False
        return f'mp3 {kbps}k', kbps >= TARGET_KBPS
    return ext[1:], False


def verdict(path):
    """Return ('keep'|'encode', reason) so the UI can preview the action."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in KEEP_AS_IS:
        return 'keep', f'{ext[1:]} 는 상위 포맷'
    if ext == '.mp3':
        kbps = bitrate_kbps(path)
        if kbps is None:
            return 'encode', '비트레이트를 못 읽음'
        if kbps >= TARGET_KBPS:
            return 'keep', f'이미 {kbps}k'
        # Re-encoding cannot recover missing information. Matching the library's
        # 320 kbps convention adds another generation of lossy compression.
        return 'encode', f'{kbps}k → {TARGET_KBPS}k'
    return 'encode', f'{ext[1:]} → mp3 {TARGET_KBPS}k'


def to_mp3(src, keep_original=True):
    """Convert to 320 kbps MP3 in place. Return (ok, error message).

    The output replaces the source, as in MKV remuxing. A retained source is
    named `<name>.<original extension>.backup`. Write to a temporary file first
    because MP3-to-MP3 conversion uses the same input and destination path.

    Copy the cover and tags. They will later be rewritten with iTunes metadata,
    but the original metadata should survive if lookup fails before tagging.
    """
    src = Path(src)
    dst = src.with_suffix('.mp3')
    if dst.exists() and dst != src:
        return False, f'{dst.name} 이 이미 있습니다'
    tmp = src.parent / f"temp_{dst.name}"

    base = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(src)]
    tail = ['-c:a', 'libmp3lame', '-b:a', f'{TARGET_KBPS}k',
            '-map_metadata', '0', '-id3v2_version', '3', str(tmp)]
    r = subprocess.run(base + ['-map', '0', '-c:v', 'copy'] + tail,
                       capture_output=True, text=True)
    if r.returncode != 0:
        # Some files fail with -map 0 due to missing covers or stream layouts.
        r = subprocess.run(base + ['-vn'] + tail, capture_output=True, text=True)
    if r.returncode != 0:
        if tmp.exists():
            tmp.unlink()
        return False, (r.stderr.strip().splitlines() or ['ffmpeg 실패'])[-1]

    if keep_original:
        src.rename(src.with_suffix(src.suffix + '.backup'))
    else:
        src.unlink()
    tmp.replace(dst)
    return True, None


def convert_all(files, on_each=None, keep_original=True):
    """Convert a list of Paths to library formats and return the resulting paths.

    Failed conversions retain their source regardless of keep_original.
    """
    if not have_ffmpeg():
        raise FFmpegMissing('ffmpeg 와 ffprobe 가 필요합니다. brew install ffmpeg')
    result = []
    for f in files:
        action, why = verdict(f)
        if action == 'keep':
            result.append(Path(f))
            if on_each:
                on_each(f, 'keep', why, None)
            continue
        ok, err = to_mp3(f, keep_original)
        result.append(Path(f).with_suffix('.mp3') if ok else Path(f))
        if on_each:
            on_each(f, 'encode' if ok else 'fail', why, err)
    return result
