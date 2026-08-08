#!/usr/bin/env python3
"""TMDB 리네이머 — 터미널.

    python3 -m renamer.cli [폴더]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from renamer import core


def pick(prompt, options, render):
    print()
    for i, o in enumerate(options, 1):
        print(f"  {i}. {render(o)}")
    while True:
        raw = input(f"{prompt} (0=취소): ").strip()
        if raw == '0':
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        print("  잘못된 입력입니다.")


def run(base):
    """폴더 하나를 받아 TMDB 로 이름을 정리한다. mkvtool 이 자기 폴더를 넘겨 부른다."""
    base = os.path.abspath(os.path.expanduser(base))
    if not os.path.isdir(base):
        print(f"❌ 폴더가 없습니다: {base}")
        return 1

    folder = os.path.basename(base)
    print(f"\n🔍 폴더명에서 추정: {folder}")
    title = input(f"제목 (엔터 시 '{folder}'): ").strip() or folder

    try:
        results = core.search_series(title)
    except core.TmdbError as e:
        print(f"❌ {e}")
        return 1
    if not results:
        print("❌ 검색 결과가 없습니다.")
        return 1

    series = pick("시리즈 선택", results, lambda s: f"{s['name']} ({s['year']})")
    if not series:
        return 1
    lang = series['original_language']
    print(f"\n선택: {series['name']} (id={series['id']}, 언어={lang})")
    print(f"파일명에 쓸 제목: {title}")

    mode = pick("시즌을 어떻게 정할까요?",
                ['폴더 구조에서 자동 감지', '시즌을 직접 선택'],
                lambda s: s)
    if not mode:
        return 1

    if mode.startswith('폴더'):
        targets = [(os.path.join(base, d) if d != '.' else base, n)
                   for d, n in core.detect_seasons(base)]
        if not targets:
            print("❌ 시즌을 찾지 못했습니다. '시즌을 직접 선택'으로 해보세요.")
            return 1
    else:
        seasons = core.list_seasons(series['id'])
        s = pick("시즌 선택", seasons,
                 lambda s: (f"{'Specials' if s['season_number'] == 0 else 'Season ' + str(s['season_number'])}"
                            f" — {s['episode_count']}화, 첫 방영 {s['air_date'] or '?'}"))
        if not s:
            return 1
        targets = [(base, s['season_number'])]

    total = []
    for directory, season in targets:
        eps = core.episode_map(series['id'], season, lang)
        if not eps:
            print(f"⚠️  시즌 {season}: TMDB 에 에피소드 정보가 없습니다. 건너뜁니다.")
            continue
        renames = core.plan(directory, title, season, eps)
        if not renames:
            print(f"⚠️  시즌 {season}: 대상 파일이 없습니다. ({os.path.basename(directory)})")
            continue
        print(f"\n📂 시즌 {season} — {os.path.basename(directory) or '.'}")
        for r in renames:
            print(f"  {r.old_name}")
            print(f"    → {r.new_name}")
        total += renames

    if not total:
        print("\n변경할 파일이 없습니다.")
        return 0

    if input(f"\n{len(total)}개를 변경할까요? (y/n): ").strip().lower() != 'y':
        print("취소했습니다.")
        return 0

    print()
    for r, err in core.apply(total):
        print(f"  {'✅' if not err else '❌'} {r.new_name}" + (f"  {err}" if err else ""))
    return 0


def main():
    import pathinput
    pathinput.enable()
    base = sys.argv[1] if len(sys.argv) > 1 else input("\n📁 시리즈 폴더 경로: ").strip().strip('"')
    return run(base)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\n중단했습니다.")
        sys.exit(130)
