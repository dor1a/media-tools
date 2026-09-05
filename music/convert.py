"""들어온 파일을 라이브러리 포맷으로 맞춘다. UI 없음.

라이브러리는 mp3 320 이 기준이고 flac 은 그대로 둔다. flac 은 상위 포맷이라
320 으로 내리는 것은 컨벤션을 위해 음질을 버리는 쪽이고, 되돌릴 수도 없다.

변환은 제자리에서 한다. 결과가 원래 이름을 갖고 원본이 `.backup` 으로
비켜나며, 이건 mkv 쪽 remux 와 같은 방식이다.
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
    """kbps. 못 읽으면 None.

    VBR mp3 는 스트림에 bit_rate 가 비어 있는 경우가 있어 컨테이너 값으로
    떨어진다 — 그 경우 평균값이다.
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
    """(표시할 상태, 라이브러리 포맷인가). 바꾸지 않고 알려만 준다."""
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
    """('keep'|'encode', 사유). 실제로 무엇을 할지 미리 보여주려고 나눠 뒀다."""
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
        # 없던 정보가 생기지는 않는다. 라이브러리 전체가 320 이라 표기를
        # 맞추는 것이고, 그 대가는 손실이 한 번 더 쌓이는 것이다.
        return 'encode', f'{kbps}k → {TARGET_KBPS}k'
    return 'encode', f'{ext[1:]} → mp3 {TARGET_KBPS}k'


def to_mp3(src, keep_original=True):
    """제자리에서 mp3 320 으로 바꾼다. (ok, 에러문자열).

    결과가 원래 자리에 남고 원본이 비켜난다 — mkv 쪽 remux 와 같은 방식으로,
    남긴 원본은 `<이름>.<원래확장자>.backup` 이 된다. 임시 파일로 먼저 뽑는
    것은 mp3 를 mp3 로 바꿀 때 입력과 출력 경로가 같아서다.

    커버와 태그는 그대로 옮긴다. 옮긴 뒤 iTunes 값으로 다시 쓰기는 하지만,
    검색이 실패해 태그 단계까지 못 갔을 때 원본 태그마저 사라지면 손해다.
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
        # 커버가 없거나 스트림 구성이 달라 -map 0 이 실패하는 파일이 있다.
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
    """[Path] 를 라이브러리 포맷으로 맞춘 목록으로 바꾼다.

    실패한 파일의 원본은 어느 쪽을 골라도 그대로 남는다.
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
