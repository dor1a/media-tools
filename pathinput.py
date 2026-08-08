"""경로 입력에 셸 같은 편집을 붙인다 — 방향키·delete·tab 보완.

표준 라이브러리만 쓴다. `readline` 을 import 하는 것만으로 `input()` 이
줄 편집을 받게 되고, completer 를 달면 tab 보완까지 된다.
"""

import os


def _dir_completer(text, state):
    """폴더만 보완한다. 이 도구의 입력은 항상 작업 폴더다."""
    stub = os.path.expanduser(text)
    parent = os.path.dirname(stub) or '.'
    prefix = os.path.basename(stub)
    try:
        names = sorted(os.listdir(parent))
    except OSError:
        return None

    hits = []
    for n in names:
        if not n.startswith(prefix):
            continue
        p = os.path.join(parent, n)
        if os.path.isdir(p):
            hits.append(p.rstrip(os.sep) + os.sep)

    if text.startswith('~'):
        home = os.path.expanduser('~')
        hits = [('~' + h[len(home):]) if h.startswith(home) else h for h in hits]

    return hits[state] if state < len(hits) else None


def enable():
    try:
        import readline
    except ImportError:
        return  # 없으면 그냥 기본 input() — 기능은 다 동작한다
    # 공백을 구분자에서 뺀다. 미디어 폴더 이름에 공백이 흔해서, 공백이
    # 구분자면 "Re Zero" 의 뒷단어만 보완 대상이 되어 버린다.
    readline.set_completer_delims('\t\n')
    # macOS 파이썬은 GNU readline 대신 libedit 를 링크하는 경우가 많고
    # 바인딩 구문이 다르다.
    if 'libedit' in (readline.__doc__ or ''):
        readline.parse_and_bind('bind ^I rl_complete')
    else:
        readline.parse_and_bind('tab: complete')
    readline.set_completer(_dir_completer)
