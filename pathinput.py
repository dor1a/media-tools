"""Add shell-style path editing: arrow keys, delete and tab completion.

Use only the standard library. Importing readline enables line editing in
input(); registering a completer adds tab completion.
"""

import os


def _dir_completer(text, state):
    """Complete directories only; these tools always ask for a working folder."""
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
        return  # Fall back to ordinary input() without changing tool behavior.
    # Remove spaces from delimiters. Media folders often contain spaces;
    # otherwise completion would only see the last word in "Re Zero".
    readline.set_completer_delims('\t\n')
    # macOS Python often links libedit rather than GNU readline, and their
    # key binding syntax differs.
    if 'libedit' in (readline.__doc__ or ''):
        readline.parse_and_bind('bind ^I rl_complete')
    else:
        readline.parse_and_bind('tab: complete')
    readline.set_completer(_dir_completer)
