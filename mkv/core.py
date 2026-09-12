"""mkvtoolnix calls and file rules; cli.py provides the user interface."""

import json
import os
import re
import subprocess

SUB_EXTS = ['.srt', '.ass']
LANGS = ['kor', 'jpn', 'eng', 'und']
TOOLS = ('mkvmerge', 'mkvpropedit')

# mkvmerge --gui-mode emits progress on separate lines. The default mode
# overwrites the current line with \r, which is harder to parse.
PROGRESS_RE = re.compile(r'#GUI#progress\s+(\d+)%')


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def missing_tools():
    """List missing mkvtoolnix commands; an empty list means all are available."""
    return [t for t in TOOLS if not run(['which', t]).stdout.strip()]


# --- Inspection ------------------------------------------------------------

def probe(mkv):
    r = run(['mkvmerge', '-J', mkv])
    if r.returncode not in (0, 1):
        return None
    return json.loads(r.stdout)


def tracks_of(info, kind):
    return [t for t in info['tracks'] if t['type'] == kind]


def summary(mkv):
    """{'video': str, 'audio': [(a_number, track_id, language, codec)], 'subs': [language]}.

    Audio has two identifiers: mkvpropedit uses the audio ordinal (`a1`),
    while mkvmerge uses the global track ID (`id0`). Confusing them can
    remove the wrong track.
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
    """Return the first audio language, None for no track, or False on read failure."""
    s = summary(mkv)
    if s is None:
        return False
    return s['audio'][0][2] if s['audio'] else None


# --- File discovery --------------------------------------------------------

def mkvs_in(path):
    return sorted(
        os.path.join(path, f) for f in os.listdir(path) if f.lower().endswith('.mkv')
    )


def find_candidates(root='.'):
    """Find folders containing MKV files here and one directory level below."""
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
    """Find an adjacent `<name>.kor.srt` or `.kor.ass`, or return None."""
    base = mkv[:-4]
    for ext in SUB_EXTS:
        cand = f"{base}.kor{ext}"
        if os.path.isfile(cand):
            return cand
    return None


# --- Changes ---------------------------------------------------------------

def set_audio_language(mkv, index, lang):
    """Edit track headers without remuxing. index is an audio ordinal (`aN`)."""
    r = run(['mkvpropedit', mkv, '--edit', f'track:a{index}', '--set', f'language={lang}'])
    return r.returncode == 0


def remux(mkv, pre_args, sub=None, on_progress=None):
    """Rewrite a file, keeping `<name>.backup`. Return (ok, error_message).

    Remuxing rewrites the entire file. On a network mount, transferring
    gigabytes can take minutes per file; progress keeps it from looking stuck.
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


# --- Backups ---------------------------------------------------------------

def backups_for(mkvs):
    return [f"{m}.backup" for m in mkvs if os.path.isfile(f"{m}.backup")]


def backup_size_gb(paths):
    return sum(os.path.getsize(p) for p in paths) / 1024 ** 3


def delete_backups(paths):
    for p in paths:
        os.remove(p)
