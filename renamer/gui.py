#!/usr/bin/env python3
"""Tkinter interface for the TMDB renamer.

    python3 -m renamer.gui
"""

import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from renamer import core


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Media Renamer (TMDB)")
        self.geometry("880x620")
        self.minsize(760, 520)

        self.series = None
        self.results = []
        self.seasons = []
        self.renames = []

        self._build()

    # --- UI ---------------------------------------------------------------

    def _build(self):
        pad = dict(padx=8, pady=4)

        top = ttk.Frame(self)
        top.pack(fill='x', **pad)
        ttk.Label(top, text="폴더").pack(side='left')
        self.dir_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.dir_var).pack(side='left', fill='x', expand=True, padx=6)
        ttk.Button(top, text="찾기…", command=self.choose_dir).pack(side='left')

        row = ttk.Frame(self)
        row.pack(fill='x', **pad)
        ttk.Label(row, text="제목").pack(side='left')
        self.title_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.title_var).pack(side='left', fill='x', expand=True, padx=6)
        ttk.Button(row, text="TMDB 검색", command=self.search).pack(side='left')

        ttk.Label(self, text="시리즈").pack(anchor='w', padx=8)
        self.series_box = ttk.Combobox(self, state='readonly')
        self.series_box.pack(fill='x', **pad)
        self.series_box.bind('<<ComboboxSelected>>', self.on_series)

        mode = ttk.Frame(self)
        mode.pack(fill='x', **pad)
        self.mode_var = tk.StringVar(value='auto')
        ttk.Radiobutton(mode, text="폴더 구조에서 자동 감지", value='auto',
                        variable=self.mode_var, command=self.on_mode).pack(side='left')
        ttk.Radiobutton(mode, text="시즌 직접 선택", value='manual',
                        variable=self.mode_var, command=self.on_mode).pack(side='left', padx=12)
        self.season_box = ttk.Combobox(mode, state='disabled', width=34)
        self.season_box.pack(side='left')

        ttk.Button(self, text="미리보기", command=self.preview).pack(**pad)

        self.tree = ttk.Treeview(self, columns=('old', 'new'), show='headings')
        self.tree.heading('old', text='현재 이름')
        self.tree.heading('new', text='바뀔 이름')
        self.tree.column('old', width=340)
        self.tree.column('new', width=480)
        self.tree.pack(fill='both', expand=True, padx=8, pady=4)

        bottom = ttk.Frame(self)
        bottom.pack(fill='x', **pad)
        self.status = ttk.Label(bottom, text="폴더를 고르고 TMDB 에서 시리즈를 검색하세요.")
        self.status.pack(side='left')
        self.apply_btn = ttk.Button(bottom, text="이름 바꾸기", command=self.apply, state='disabled')
        self.apply_btn.pack(side='right')

    # --- helpers ----------------------------------------------------------

    def say(self, text):
        self.status.config(text=text)
        self.update_idletasks()

    def run_bg(self, fn, done):
        """Keep network requests from blocking the window."""
        def worker():
            try:
                result = fn()
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("오류", str(e)))
                self.after(0, lambda: self.say("실패했습니다."))
                return
            self.after(0, lambda: done(result))
        threading.Thread(target=worker, daemon=True).start()

    # --- actions ----------------------------------------------------------

    def choose_dir(self):
        d = filedialog.askdirectory()
        if not d:
            return
        self.dir_var.set(d)
        if not self.title_var.get().strip():
            self.title_var.set(os.path.basename(d))

    def search(self):
        title = self.title_var.get().strip()
        if not title:
            messagebox.showwarning("확인", "제목을 입력하세요.")
            return
        self.say("TMDB 검색 중…")
        self.run_bg(lambda: core.search_series(title), self._got_series)

    def _got_series(self, results):
        self.results = results
        if not results:
            self.say("검색 결과가 없습니다.")
            self.series_box['values'] = []
            return
        self.series_box['values'] = [f"{s['name']} ({s['year']})" for s in results]
        self.series_box.current(0)
        self.on_series()

    def on_series(self, _=None):
        i = self.series_box.current()
        if i < 0:
            return
        self.series = self.results[i]
        self.say(f"선택: {self.series['name']}")
        if self.mode_var.get() == 'manual':
            self.load_seasons()

    def on_mode(self):
        if self.mode_var.get() == 'manual':
            self.season_box.config(state='readonly')
            if self.series:
                self.load_seasons()
        else:
            self.season_box.config(state='disabled')

    def load_seasons(self):
        self.say("시즌 목록 불러오는 중…")

        def done(seasons):
            self.seasons = seasons
            self.season_box['values'] = [
                ('Specials' if s['season_number'] == 0 else f"Season {s['season_number']}")
                + f" — {s['episode_count']}화"
                for s in seasons
            ]
            if seasons:
                self.season_box.current(0)
            self.say("시즌을 고르세요.")

        self.run_bg(lambda: core.list_seasons(self.series['id']), done)

    def preview(self):
        base = self.dir_var.get().strip()
        title = self.title_var.get().strip()
        if not (base and os.path.isdir(base)):
            messagebox.showwarning("확인", "폴더를 고르세요.")
            return
        if not self.series:
            messagebox.showwarning("확인", "시리즈를 먼저 검색·선택하세요.")
            return

        if self.mode_var.get() == 'auto':
            targets = [(os.path.join(base, d) if d != '.' else base, n)
                       for d, n in core.detect_seasons(base)]
            if not targets:
                messagebox.showinfo("결과", "시즌을 찾지 못했습니다. '시즌 직접 선택'을 써보세요.")
                return
        else:
            i = self.season_box.current()
            if i < 0:
                messagebox.showwarning("확인", "시즌을 고르세요.")
                return
            targets = [(base, self.seasons[i]['season_number'])]

        self.say("에피소드 정보 불러오는 중…")

        def work():
            out = []
            for directory, season in targets:
                eps = core.episode_map(self.series['id'], season, self.series['original_language'])
                if eps:
                    out += core.plan(directory, title, season, eps)
            return out

        def done(renames):
            self.renames = renames
            self.tree.delete(*self.tree.get_children())
            for r in renames:
                self.tree.insert('', 'end', values=(r.old_name, r.new_name))
            self.apply_btn.config(state='normal' if renames else 'disabled')
            self.say(f"{len(renames)}개 대상" if renames else "대상 파일이 없습니다.")

        self.run_bg(work, done)

    def apply(self):
        if not self.renames:
            return
        if not messagebox.askyesno("확인", f"{len(self.renames)}개의 이름을 바꿉니다. 진행할까요?"):
            return
        results = core.apply(self.renames)
        failed = [(r, e) for r, e in results if e]
        self.tree.delete(*self.tree.get_children())
        for r, e in results:
            self.tree.insert('', 'end', values=(r.old_name, r.new_name if not e else f"실패: {e}"))
        self.renames = []
        self.apply_btn.config(state='disabled')
        self.say(f"완료 {len(results) - len(failed)}개, 실패 {len(failed)}개")


if __name__ == '__main__':
    App().mainloop()
