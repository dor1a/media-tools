"""mkvtoolnix 호출과 파일 규칙. UI 없음 — cli.py 가 이 위에 얹힌다."""

import json
import os
import re
import subprocess

SUB_EXTS = ['.srt', '.ass']
LANGS = ['kor', 'jpn', 'eng', 'und']
TOOLS = ('mkvmerge', 'mkvpropedit')

# mkvmerge --gui-mode 는 진행률을 줄 단위로 뱉는다 (기본 모드는 \r 로 덮어써서
# 파싱이 번거롭다)
PROGRESS_RE = re.compile(r'#GUI#progress\s+(\d+)%')


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def missing_tools():
    """없는 mkvtoolnix 명령의 목록. 비어 있으면 다 있다."""
    return [t for t in TOOLS if not run(['which', t]).stdout.strip()]


# --- 조회 ------------------------------------------------------------------

def probe(mkv):
    r = run(['mkvmerge', '-J', mkv])
    if r.returncode not in (0, 1):
        return None
    return json.loads(r.stdout)


def tracks_of(info, kind):
    return [t for t in info['tracks'] if t['type'] == kind]


def summary(mkv):
    """{'video': str, 'audio': [(a번호, id번호, 언어, 코덱)], 'subs': [언어]}.

    오디오에 번호가 둘인 것은 도구가 서로 다른 체계를 쓰기 때문이다 —
    mkvpropedit 은 오디오 중 순번(`a1`), mkvmerge 는 전역 트랙 id(`id0`).
    섞으면 엉뚱한 트랙이 지워진다.
    """
    info = probe(mkv)
    if not info:
        return None
    v = tracks_of(info, 'video')
    return {
        'video': f"{v[0]['codec']} {v[0]['properties'].get('pixel_dimensions', '?')}" if v else None,
        'audio': [
            (i + 1, t['id'], t['properties'].get('language', 'und'), t['codec'])
            for i, t in enumerate(tracks_of(info, 'audio'))
        ],
        'subs': [t['properties'].get('language', 'und') for t in tracks_of(info, 'subtitles')],
    }


def audio_language(mkv):
    """첫 오디오 트랙의 언어. 트랙이 없으면 None, 읽기 실패면 False."""
    s = summary(mkv)
    if s is None:
        return False
    return s['audio'][0][2] if s['audio'] else None


# --- 파일 탐색 --------------------------------------------------------------

def mkvs_in(path):
    return sorted(
        os.path.join(path, f) for f in os.listdir(path) if f.lower().endswith('.mkv')
    )


def find_candidates(root='.'):
    """현재 위치와 바로 아래 디렉토리 중 mkv 가 들어있는 곳."""
    root = os.path.abspath(root)
    out = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return out
    if any(f.lower().endswith('.mkv') for f in entries):
        out.append(root)
    for name in entries:
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            continue
        try:
            if any(f.lower().endswith('.mkv') for f in os.listdir(d)):
                out.append(d)
        except OSError:
            pass
    return out


def sub_for(mkv):
    """옆에 있는 `<이름>.kor.srt` / `.kor.ass`. 없으면 None."""
    base = mkv[:-4]
    for ext in SUB_EXTS:
        cand = f"{base}.kor{ext}"
        if os.path.isfile(cand):
            return cand
    return None


# --- 변경 ------------------------------------------------------------------

def set_audio_language(mkv, index, lang):
    """리먹싱 없이 트랙 헤더만 고친다. index 는 `a번호`."""
    r = run(['mkvpropedit', mkv, '--edit', f'track:a{index}', '--set', f'language={lang}'])
    return r.returncode == 0


def remux(mkv, pre_args, sub=None, on_progress=None):
    """파일을 다시 쓰고 원본은 `<이름>.backup` 으로 남긴다. (ok, 에러문자열).

    remux 는 파일 전체를 다시 쓴다. 네트워크 마운트면 GB 단위 왕복이라
    파일당 수 분이 걸리므로, 진행률을 흘려보내지 않으면 멈춘 것처럼 보인다.
    """
    tmp = os.path.join(os.path.dirname(mkv), f"temp_{os.path.basename(mkv)}")
    cmd = ['mkvmerge', '--gui-mode', '-o', tmp] + pre_args + [mkv]
    if sub:
        cmd += ['--language', '0:kor', sub]

    tail = []
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1)
        for line in p.stdout:
            m = PROGRESS_RE.search(line)
            if m:
                if on_progress:
                    on_progress(int(m.group(1)))
            elif line.strip():
                tail.append(line.strip())
                del tail[:-4]
        rc = p.wait()
    except FileNotFoundError:
        return False, 'mkvmerge 를 찾을 수 없습니다'

    if rc not in (0, 1):
        if os.path.exists(tmp):
            os.remove(tmp)
        return False, tail[-1] if tail else 'mkvmerge 실패'
    os.replace(mkv, f"{mkv}.backup")
    os.replace(tmp, mkv)
    return True, None


# --- 백업 ------------------------------------------------------------------

def backups_for(mkvs):
    return [f"{m}.backup" for m in mkvs if os.path.isfile(f"{m}.backup")]


def backup_size_gb(paths):
    return sum(os.path.getsize(p) for p in paths) / 1024 ** 3


def delete_backups(paths):
    for p in paths:
        os.remove(p)
