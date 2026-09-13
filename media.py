#!/usr/bin/env python3
"""Entry point for the tools that manage /Volumes/Media.

    python3 media.py [folder]

Choose the medium first. Video requires mkvtoolnix and collects only MKV
files, while audio has different dependencies and inputs. Dispatch to the
appropriate menu here.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pathinput

MEDIA = [
    ("영상  (mkv 트랙, 자막, TVDB/TMDB 이름 정리)", 'video'),
    ("음원  (라이브러리 편입, 포맷 변환)", 'music'),
]


def pick():
    print("\n무엇을 다룰까요? (0=종료)")
    for i, (label, _) in enumerate(MEDIA, 1):
        print(f"  {i}. {label}")
    while True:
        raw = input("선택: ").strip()
        if raw == '0':
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(MEDIA):
            return MEDIA[int(raw) - 1][1]
        print("  잘못된 입력입니다.")


def main():
    pathinput.enable()
    path_arg = sys.argv[1] if len(sys.argv) > 1 else None

    kind = pick()
    if kind is None:
        return 0

    if kind == 'video':
        from mkv import cli as mkv_cli
        return mkv_cli.main(path_arg) or 0

    # Import audio tools only after selection; they need requests and mutagen.
    try:
        from music import cli as music_cli
    except ImportError as e:
        print(f"\n❌ 음원 도구를 불러오지 못했습니다: {e}")
        print("   uv pip install -r requirements.txt")
        return 1
    return music_cli.main(path_arg) or 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\n중단했습니다.")
        sys.exit(130)
