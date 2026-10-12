# -*- coding: utf-8 -*-
# Required Notice: Copyright 2026 Heisenberg-cmd0079
# Licensed under the PolyForm Noncommercial License 1.0.0 (see LICENSE).
# 禁止商业使用；需要商业授权请联系作者。
"""
字幕长图（bili-subshot） —— 图形界面

相比旧版：
    * 网址栏可直接粘贴 B 站客户端复制的整段内容（【标题】+ 链接）：
      自动拆出标题填进「文件名前缀」、自动清掉 vd_source 等分享参数
    * 文件名默认 = 标题 + 编号（标题_01 / 标题_02 …），不会覆盖旧图
    * 可设置「导出文件夹」（自动记忆），一键「打开文件夹」
    * 字幕区域全自动识别（多帧统计 + 时变分析），不再需要手填边距；
      并提供可视化预览窗口，拖滑块即可微调
    * 去重基于字幕文字的白色字形掩码，重复台词不再出现、不同台词不再漏
    * 首帧整图 / 分隔线 / 横向裁切 / 文件名 / Cookies / 完成后打开图片 均可配置
    * 自适应高 DPI 缩放，小屏也能完整显示
"""

import os
import queue
import random
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageDraw

import subtitle_core as sc

FONT_FAMILY = "Microsoft YaHei"
ACCENT = "#007AFF"
ACCENT_HOVER = "#0062CC"
GREY_BTN = "#F4F6FA"
GREY_BTN_HOVER = "#E7ECF3"
# 任务栏/标题栏图标：assets/icon.ico（多尺寸）+ assets/icon.png（兜底），由 make_icon.py 生成
APP_USER_MODEL_ID = "zimupinjie.subtitle.stitcher"


def _claim_taskbar_identity():
    """声明进程身份，让 Windows 任务栏用窗口自己的图标，而不是归到 pythonw 名下。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


class CleanOptionMenu(ctk.CTkOptionMenu):
    """Draw a crisp chevron instead of the platform shape-font glyph."""
    def _draw(self, no_color_updates=False):
        super()._draw(no_color_updates)
        self._canvas.itemconfigure("dropdown_arrow", state="hidden")
        if not self._canvas.find_withtag("ui_chevron"):
            self._canvas.create_line(0, 0, 0, 0, 0, 0, tags="ui_chevron",
                                     capstyle="round", joinstyle="round")
        x = self._apply_widget_scaling(self._current_width - self._current_height / 2)
        y = self._apply_widget_scaling(self._current_height / 2)
        r = self._apply_widget_scaling(4)
        self._canvas.coords("ui_chevron", x-r, y-r/2, x, y+r/2, x+r, y-r/2)
        color = self._text_color_disabled if self._state == tk.DISABLED else self._text_color
        self._canvas.itemconfigure("ui_chevron", fill=self._apply_appearance_mode(color),
                                    width=self._apply_widget_scaling(1.6))


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("Light")

        self.cfg = sc.load_config()
        self.video_path = None          # 已下载片段（供预览复用）
        self.video_key = None
        self.region = None              # 字幕区域识别结果
        self.preview_win = None
        self.busy = False
        # 文件名前缀自动跟随链接标题：auto_title 记住上次自动填的值，
        # title_manual 标记用户是否自己改过（改过就不再覆盖）
        self.auto_title = (self.cfg.get("filename") or "").strip()
        self.title_manual = False
        self.title_url = sc.clean_video_url(self.cfg.get("url") or "")
        self._title_fetch_url = None
        # 工作线程只往队列里塞消息，绝不直接碰 Tk 控件（Tk 非线程安全）
        self.msg_queue = queue.Queue()

        # 图标资源（assets/ 缺失或读取失败都不影响使用）
        self.assets_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
        self.icon_ico = os.path.join(self.assets_dir, "icon.ico")
        self.icon_png = os.path.join(self.assets_dir, "icon.png")
        self._icon_photo = None
        _claim_taskbar_identity()

        self.title("字幕长图")
        self.configure(fg_color="#F5F5F7")
        self._set_window_icon(self)

        # Let CustomTkinter honor the system DPI. Small screens use scrolling
        # and reflow; they must never reduce font sizes to fit all settings.
        self._compact_layout = None
        self._layout_job = None
        self._ui_icons = {}

        self.f_title = ctk.CTkFont(family=FONT_FAMILY, size=26)
        self.f_sec = ctk.CTkFont(family=FONT_FAMILY, size=19, weight="bold")
        self.f_label = ctk.CTkFont(family=FONT_FAMILY, size=16)
        self.f_in = ctk.CTkFont(family=FONT_FAMILY, size=16)
        self.f_small = ctk.CTkFont(family=FONT_FAMILY, size=14)
        self.f_btn = ctk.CTkFont(family=FONT_FAMILY, size=16)
        self.f_log_heading = ctk.CTkFont(family=FONT_FAMILY, size=19)
        self.f_log = ctk.CTkFont(family=FONT_FAMILY, size=14)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.settings_panel = ctk.CTkScrollableFrame(
            self, fg_color="transparent", corner_radius=0,
            scrollbar_button_color="#DAE0EA", scrollbar_button_hover_color="#B9C3D2")
        self.settings_panel.grid(row=1, column=0, sticky="nsew", padx=14)
        self._build_header()
        self._build_video_card()
        self._build_output_card()
        self._build_region_card()
        self._build_actions()
        self._build_log()

        self._fit_geometry(850, 1050)
        self.bind("<Configure>", self._schedule_layout, add="+")
        self.after_idle(self._reflow_layout)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(120, self._pump_queue)
        self.log("提示：粘贴视频链接或分享内容，自动提取标题；可勾选自动编号，避免覆盖旧图。")
        self.log("填写时间区间后即可生成；需要确认裁切范围时，点击「预览 / 微调字幕区域」。")

    # ------------------------------------------------------------------ 图标
    def _set_window_icon(self, win):
        """给窗口挂上 assets/icon.ico（标题栏 + 任务栏），拿不到就退回 PNG，失败也不报错。"""
        if os.path.exists(self.icon_ico):
            try:
                win.iconbitmap(self.icon_ico)            # 当前窗口
                win.iconbitmap(default=self.icon_ico)    # 以后新建的 Toplevel（含预览窗口）也跟上
                return
            except Exception:
                pass
        try:
            if os.path.exists(self.icon_png):
                if self._icon_photo is None:             # 必须留引用，否则会被 GC 掉变成空白图标
                    self._icon_photo = tk.PhotoImage(file=self.icon_png)
                win.iconphoto(False, self._icon_photo)
        except Exception:
            pass

    # ------------------------------------------------------------ 尺寸自适应
    def _window_scale(self):
        try:
            from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker
            return float(ScalingTracker.get_window_scaling(self)) or 1.0
        except Exception:
            try:
                self.geometry("100x100")
                self.update_idletasks()
                return max(1.0, self.winfo_width() / 100.0)
            except Exception:
                return 1.0

    def _available_screen(self):
        """Return the current monitor work area in physical pixels."""
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes
                class MonitorInfo(ctypes.Structure):
                    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
                user32 = ctypes.windll.user32
                user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
                user32.MonitorFromWindow.restype = wintypes.HANDLE
                user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
                info = MonitorInfo()
                info.cbSize = ctypes.sizeof(info)
                monitor = user32.MonitorFromWindow(self.winfo_id(), 2)
                if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                    return info.rcWork.right - info.rcWork.left, info.rcWork.bottom - info.rcWork.top
            except (OSError, AttributeError):
                pass
        return self.winfo_screenwidth(), self.winfo_screenheight()

    def _fit_geometry(self, want_w, want_h):
        scale = self._window_scale()
        screen_w, screen_h = self._available_screen()
        available_w = max(320, int((screen_w - 40) / scale))
        available_h = max(320, int((screen_h - 80) / scale))
        self.geometry(f"{min(want_w, available_w)}x{min(want_h, available_h)}")
        self.minsize(min(560, available_w), min(460, available_h))

    def _schedule_layout(self, event):
        if event.widget is not self:
            return
        if self._layout_job is not None:
            self.after_cancel(self._layout_job)
        self._layout_job = self.after(40, self._reflow_layout)

    def _reflow_layout(self):
        self._layout_job = None
        compact = self.winfo_width() / self.settings_panel._get_widget_scaling() < 720
        if compact == self._compact_layout:
            return
        self._compact_layout = compact
        self._header_hint.grid(row=1 if compact else 0, column=1 if compact else 2,
                               padx=0 if compact else 12, sticky="w")
        # Narrow windows place Cookies below the two time fields.
        for col in range(3):
            self._video_row.grid_columnconfigure(col, weight=0 if compact and col == 2 else 1,
                                                  uniform="video" if not compact or col < 2 else "")
        self._video_labels[2].grid(row=2 if compact else 0, column=0 if compact else 2,
                                   columnspan=2 if compact else 1,
                                   padx=0 if compact else (10, 0), pady=(10, 4) if compact else (0, 10))
        self.cookie_menu.grid(row=3 if compact else 1, column=0 if compact else 2,
                               columnspan=2 if compact else 1, padx=0 if compact else (10, 0))
        # Export labels move above each field, leaving room for the action buttons.
        self._output_rows.grid_columnconfigure(0, weight=1 if compact else 0)
        self._output_rows.grid_columnconfigure(1, weight=0 if compact else 1)
        for i, (label, field, button) in enumerate(zip(self._output_labels,
                (self.out_entry, self.name_entry), (self._browse_buttons, self._title_button))):
            label.grid(row=i*2 if compact else i, column=0,
                         columnspan=2 if compact else 1, padx=0 if compact else (0, 12),
                         pady=(0, 4) if compact else (0, 10), sticky="w")
            field.grid(row=i*2+1 if compact else i, column=0 if compact else 1,
                         pady=(0, 12) if compact else (0, 10), sticky="ew")
            button.grid(row=i*2+1 if compact else i, column=1 if compact else 2,
                          padx=(8, 0), pady=(0, 12) if compact else (0, 10), sticky="ew")
        self.name_example.configure(wraplength=max(240,
            int(self._output_rows.winfo_width() / self.settings_panel._get_widget_scaling()) - 20))
        # Preview remains fully labeled and accessible; the mode stays in two columns.
        self._preview_button.grid(row=1 if compact else 0, column=0 if compact else 2,
                                    columnspan=2 if compact else 1,
                                    pady=(10, 0) if compact else 0, sticky="ew")

    # ------------------------------------------------------------------ UI
    def _refresh_mode_style(self, *_):
        for value, (box, radio) in self._mode_widgets.items():
            selected = self.var_mode.get() == value
            box.configure(fg_color="#EAF4FF" if selected else "#F5F6FA",
                          border_color=ACCENT if selected else "#DAE0EA")
            radio.configure(text_color=ACCENT if selected else "#202631",
                            font=self.f_label if selected else self.f_in)

    def _refresh_name_example(self, *_):
        base = sc.sanitize_filename(self.name_entry.get().strip()) or "bilibili_台词拼接"
        if len(base) > 16:
            base = base[:13] + "…"
        suffix = ("_01" if self.var_auto_num.get() else "") + ("_时间戳" if self.var_ts.get() else "")
        self.name_example.configure(text=f"命名示例：{base}{suffix}.png")

    def _ui_icon(self, kind, size=18):
        key = (kind, size)
        if key in self._ui_icons:
            return self._ui_icons[key]
        im = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
        draw = ImageDraw.Draw(im)
        ink = "#263346"
        if kind == "app":
            draw.rounded_rectangle((0, 0, 95, 95), radius=23, fill=ACCENT)
            draw.rounded_rectangle((19, 22, 76, 73), radius=9, outline="white", width=5)
            for x in (30, 44, 58):
                draw.line((x, 47, x + 8, 47), fill="white", width=4)
        elif kind == "浏览…":
            draw.line((14, 37, 14, 25, 39, 25, 49, 37, 80, 37), fill=ink, width=6)
            draw.rounded_rectangle((14, 37, 82, 76), radius=7, outline=ink, width=6)
        elif kind == "预览 / 微调字幕区域":
            draw.ellipse((9, 28, 87, 68), outline=ink, width=6)
            draw.ellipse((36, 36, 60, 60), outline=ink, width=6)
        else:
            return None
        icon = ctk.CTkImage(light_image=im, dark_image=im, size=(size, size))
        self._ui_icons[key] = icon
        return icon

    def _card(self, pady=(0, 12)):
        card = ctk.CTkFrame(self.settings_panel, corner_radius=12, fg_color="#FFFFFF",
                            border_width=1, border_color="#E3E7EF")
        card.pack(fill="x", padx=4, pady=pady)
        return card

    def _section_heading(self, card, number, title):
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(14, 10))
        ctk.CTkLabel(row, text=number, width=34, height=30, corner_radius=9,
                     fg_color="#E8F2FF", text_color=ACCENT,
                     font=self.f_sec).pack(side="left")
        ctk.CTkLabel(row, text=title, height=30, font=self.f_sec,
                     text_color="#202631").pack(side="left", padx=10)

    def _entry(self, parent, **kwargs):
        return ctk.CTkEntry(parent, height=40, corner_radius=8, border_width=1,
                           border_color="#DAE0EA", fg_color="#F5F6FA",
                           text_color="#202631", font=self.f_in, **kwargs)

    def _secondary(self, parent, text, command, width=96):
        return ctk.CTkButton(parent, text=text, command=command, width=width,
                            image=self._ui_icon(text), compound="left",
                            height=40, corner_radius=8, border_width=1,
                            border_color="#DAE0EA", fg_color=GREY_BTN,
                            hover_color=GREY_BTN_HOVER, text_color="#202631",
                            font=self.f_label)

    def _checkbox(self, parent, text, variable):
        return ctk.CTkCheckBox(parent, text=text, variable=variable, font=self.f_in,
                              height=28, checkbox_width=20, checkbox_height=20, corner_radius=5,
                              fg_color=ACCENT, hover_color=ACCENT_HOVER,
                              text_color="#202631")

    def _build_header(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 18))
        self._head = head
        ctk.CTkLabel(head, text="", image=self._ui_icon("app", 42)).grid(
            row=0, column=0, rowspan=2, padx=(0, 14))
        self._header_title = ctk.CTkLabel(head, text="台词字幕拼接", font=self.f_title,
                                         text_color="#202631")
        self._header_title.grid(row=0, column=1, sticky="w")
        self._header_hint = ctk.CTkLabel(head, text="自动识别字幕区域 · 智能去重",
                                        font=self.f_small, text_color="#687386", anchor="w")
        self._header_hint.grid(row=0, column=2, padx=12, sticky="w")

    def _build_video_card(self):
        card = self._card()
        self._section_heading(card, "01", "视频与片段")
        ctk.CTkLabel(card, text="B 站视频网址", height=26, font=self.f_label,
                     text_color="#202631").pack(anchor="w", padx=18, pady=(0, 10))
        link = ctk.CTkFrame(card, fg_color="transparent")
        link.pack(fill="x", padx=18)
        self.url_entry = self._entry(link, placeholder_text="粘贴视频链接或分享内容")
        self.url_entry.pack(side="left", fill="x", expand=True)
        self.url_entry.insert(0, self.cfg["url"] or "")
        self.url_hint = ctk.CTkLabel(card, text="支持粘贴客户端分享内容，自动提取标题和链接",
                                     font=self.f_small, height=22, text_color="#687386",
                                     anchor="w", justify="left", wraplength=700)
        self.url_hint.pack(fill="x", padx=18, pady=(6, 0))
        card.bind("<Configure>",
                  lambda e: self.url_hint.configure(wraplength=max(200, int(e.width / card._get_widget_scaling()) - 40)))
        for seq in ("<Control-v>", "<Control-V>", "<<Paste>>", "<Shift-Insert>", "<Button-3>"):
            self.url_entry.bind(seq, lambda _e: self.after(50, self._on_url_input))
        self.url_entry.bind("<FocusOut>", lambda _e: self._on_url_input())
        self.url_entry.bind("<Return>", lambda _e: self._on_url_input())
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(12, 16))
        self._video_row = row
        self._video_labels = []
        for col, label in enumerate(("开始时间", "结束时间", "浏览器 Cookies")):
            row.grid_columnconfigure(col, weight=1, uniform="video")
            field_label = ctk.CTkLabel(row, text=label, height=26, font=self.f_label, text_color="#202631")
            self._video_labels.append(field_label)
            field_label.grid(
                row=0, column=col, sticky="w", padx=(0 if col == 0 else 10, 0), pady=(0, 10))
        self.start_entry = self._entry(row, width=120)
        self.start_entry.grid(row=1, column=0, sticky="ew")
        self.start_entry.insert(0, self.cfg["start"])
        self.end_entry = self._entry(row, width=120)
        self.end_entry.grid(row=1, column=1, sticky="ew", padx=(10, 0))
        self.end_entry.insert(0, self.cfg["end"])
        self.cookie_menu = CleanOptionMenu(row, values=sc.COOKIE_CHOICES, width=120,
                                             height=40, corner_radius=8, fg_color="#F5F6FA",
                                             button_color="#F5F6FA", button_hover_color="#DAE0EA",
                                             text_color="#202631", font=self.f_in,
                                             dropdown_font=self.f_in)
        self.cookie_menu.grid(row=1, column=2, sticky="ew", padx=(10, 0))
        self.cookie_menu.set(self.cfg.get("cookies_browser", "无"))

    def _build_output_card(self):
        card = self._card()
        self._section_heading(card, "02", "导出设置")
        rows = ctk.CTkFrame(card, fg_color="transparent")
        rows.pack(fill="x", padx=18)
        self._output_rows = rows
        self._output_labels = []
        rows.grid_columnconfigure(1, weight=1)
        for r, label in enumerate(("导出文件夹", "文件名前缀")):
            field_label = ctk.CTkLabel(rows, text=label, height=26, font=self.f_label, text_color="#202631")
            self._output_labels.append(field_label)
            field_label.grid(
                row=r, column=0, sticky="w", padx=(0, 12), pady=(0, 10))
        self.out_entry = self._entry(rows, width=160)
        self.out_entry.grid(row=0, column=1, sticky="ew", pady=(0, 10))
        self.out_entry.insert(0, self.cfg["output_dir"])
        browse = ctk.CTkFrame(rows, fg_color="transparent")
        self._browse_buttons = browse
        browse.grid(row=0, column=2, padx=(8, 0), pady=(0, 10))
        self._secondary(browse, "浏览…", self._browse_dir, 68).pack(side="left")
        self._secondary(browse, "打开文件夹", self._open_folder, 100).pack(side="left", padx=(8, 0))
        self.name_entry = self._entry(rows, width=160)
        self.name_entry.grid(row=1, column=1, sticky="ew", pady=(0, 10))
        self.name_entry.insert(0, self.cfg["filename"])
        self.name_entry.bind("<KeyRelease>", lambda _e: self._mark_title_manual())
        self.name_entry.bind("<<Paste>>", lambda _e: self.after(50, self._mark_title_manual))
        self._title_button = self._secondary(rows, "从链接取标题", self._on_fetch_title, 176)
        self._title_button.grid(
            row=1, column=2, padx=(8, 0), pady=(0, 10), sticky="ew")
        opts = ctk.CTkFrame(card, fg_color="transparent")
        opts.pack(fill="x", padx=18, pady=(2, 16))
        self.var_auto_num = tk.BooleanVar(value=bool(self.cfg.get("auto_number", True)))
        self.var_ts = tk.BooleanVar(value=bool(self.cfg.get("filename_timestamp")))
        self._checkbox(opts, "标题自动编号", self.var_auto_num).pack(side="left", padx=(0, 24))
        self._checkbox(opts, "加时间戳", self.var_ts).pack(side="left")
        self.name_example = ctk.CTkLabel(card, text="", height=22,
                                        font=self.f_small, text_color="#687386", anchor="w", wraplength=700)
        self.name_example.pack(fill="x", padx=18, pady=(0, 12))
        card.bind("<Configure>", lambda e: self.name_example.configure(
            wraplength=max(240, int(e.width / card._get_widget_scaling()) - 40)))
        self.var_auto_num.trace_add("write", self._refresh_name_example)
        self.var_ts.trace_add("write", self._refresh_name_example)
        self.name_entry.bind("<KeyRelease>", lambda _e: self._refresh_name_example(), add="+")
        self.name_entry.bind("<FocusOut>", lambda _e: self._refresh_name_example(), add="+")
        self._refresh_name_example()

    def _build_region_card(self):
        card = self._card()
        self._section_heading(card, "03", "字幕区域")
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=18)
        self._region_row = row
        row.grid_columnconfigure((0, 1), weight=1, uniform="region")
        self.var_mode = tk.StringVar(value=self.cfg.get("region_mode", "auto"))
        self._mode_widgets = {}
        for col, (label, value) in enumerate((("自动识别（推荐）", "auto"), ("手动指定", "manual"))):
            box = ctk.CTkFrame(row, fg_color="#F5F6FA", corner_radius=8,
                               border_width=1, border_color="#DAE0EA")
            box.grid(row=0, column=col, sticky="ew", padx=(0, 8))
            radio = ctk.CTkRadioButton(box, text=label, variable=self.var_mode, value=value,
                               font=self.f_in, radiobutton_width=18, radiobutton_height=18,
                               fg_color=ACCENT, hover_color=ACCENT_HOVER,
                               text_color="#202631", command=self._on_mode_change)
            radio.pack(anchor="w", padx=12, pady=9)
            self._mode_widgets[value] = (box, radio)
        self.var_mode.trace_add("write", self._refresh_mode_style)
        self._refresh_mode_style()
        self._preview_button = self._secondary(row, "预览 / 微调字幕区域", self._on_preview, 218)
        self._preview_button.grid(row=0, column=2)
        self.region_label = ctk.CTkLabel(card, text="状态：尚未识别（生成时会自动识别）",
                                       font=self.f_small, height=22, text_color="#687386", anchor="w",
                                       justify="left", wraplength=700)
        self.region_label.pack(fill="x", padx=18, pady=(8, 10))
        card.bind("<Configure>", lambda e: self.region_label.configure(wraplength=max(200, int(e.width / card._get_widget_scaling()) - 40)))
        opts = ctk.CTkFrame(card, fg_color="transparent")
        opts.pack(fill="x", padx=18, pady=(2, 16))
        opts.grid_columnconfigure((0, 1), weight=1, uniform="options")
        self.var_cover = tk.BooleanVar(value=bool(self.cfg.get("first_frame_full", True)))
        self.var_sep = tk.BooleanVar(value=bool(self.cfg.get("separator", True)))
        self.var_cropx = tk.BooleanVar(value=bool(self.cfg.get("crop_horizontal", False)))
        self.var_open = tk.BooleanVar(value=bool(self.cfg.get("open_after", True)))
        for i, (label, var) in enumerate((("首帧保留完整画面", self.var_cover),
                                         ("字幕条分隔线", self.var_sep),
                                         ("横向裁切到文字", self.var_cropx),
                                         ("完成后打开图片", self.var_open))):
            self._checkbox(opts, label, var).grid(row=i // 2, column=i % 2,
                                                  sticky="w", pady=(0, 10 if i < 2 else 0))

    def _build_actions(self):
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=2, column=0, sticky="ew", padx=20, pady=(2, 8))
        self.run_btn = ctk.CTkButton(row, text="一键生成拼接图", font=self.f_btn, height=46,
                                     corner_radius=10, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                     command=self._on_generate)
        self.run_btn.pack(side="left", fill="x", expand=True)
        cache_button = self._secondary(row, "清理缓存", self._clear_cache)
        cache_button.configure(height=46)
        cache_button.pack(side="left", padx=(10, 0))
        self.progress = ctk.CTkProgressBar(self, height=5, corner_radius=3,
                                           fg_color="#E3E7EF", progress_color=ACCENT)
        self.progress.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 10))
        self.progress.set(0)

    def _build_log(self):
        card = ctk.CTkFrame(self, corner_radius=12, fg_color="#FFFFFF",
                            border_width=1, border_color="#E3E7EF")
        card.grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 16))
        ctk.CTkLabel(card, text="处理日志", font=self.f_log_heading, text_color="#202631").pack(
            anchor="w", padx=18, pady=(12, 8))
        self.log_box = ctk.CTkTextbox(card, corner_radius=8, fg_color="#F5F6FA",
                                      text_color="#465266", border_width=0, font=self.f_log,
                                      wrap="word", height=86)
        self.log_box.pack(fill="both", expand=True, padx=14, pady=(0, 14))

    # ------------------------------------------------------------------ 工具
    def log(self, msg):
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end-2c")
        # see("end") 会把横向视图也拖到末尾，长句（带完整标题的那种）开头会被推出视野，
        # 这里把横向拉回最左边，保证「已拆出标题…」这行从头读起
        try:
            self.log_box.xview_moveto(0)
        except Exception:
            pass

    # 工作线程 → 主线程的消息通道（主线程轮询，避免跨线程操作 Tk）
    def post(self, kind, payload=None):
        self.msg_queue.put((kind, payload))

    def log_threadsafe(self, msg):
        self.post("log", msg)

    def _pump_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "progress":
                    self.progress.set(payload)
                elif kind == "busy":
                    self._set_busy(payload[0], payload[1])
                elif kind == "result":
                    self._done(payload[0], payload[1])
                elif kind == "error":
                    messagebox.showerror(payload[0], payload[1])
                elif kind == "title":
                    self._on_title_fetched(payload[0], payload[1])
                elif kind == "preview":
                    self._open_preview_window(payload)
                elif kind == "quit":
                    self.destroy()
                    return
        except queue.Empty:
            pass
        except Exception as e:                       # 兜底，别让界面卡死
            self.log(f"× 界面异常：{e}")
        self.after(120, self._pump_queue)

    # ------------------------------------------------- 链接粘贴 / 文件名自动命名
    def _set_url_hint(self, text, color="#86868B"):
        """网址栏下面的小字提示（回显识别到的标题 / 清理动作）。"""
        self.url_hint.configure(text=text, text_color=color)

    def _on_url_input(self):
        """网址栏粘贴或失焦后：拆出干净链接与标题，标题顺手填进文件名前缀。"""
        raw = self.url_entry.get().strip()
        if not raw:
            self._set_url_hint("")
            return
        url, title = sc.parse_link_input(raw)
        if not url:
            return
        changed = url != raw
        if changed:
            self.url_entry.delete(0, "end")
            self.url_entry.insert(0, url)
        if title:
            self._apply_title(title)
            self._set_url_hint(f"已识别标题：「{title}」→ 已填入「文件名前缀」（网址栏只保留干净链接）")
            if changed:
                self.log(f"· 已自动拆出标题「{title}」→ 文件名前缀；网址栏只留干净链接：{url}")
        elif changed:
            self._set_url_hint("网址栏已自动去掉 vd_source 等分享参数（标题稍后从 B 站接口取）")
            self.log(f"· 已自动清理链接里的分享参数：{url}")
        # 粘贴内容里没有标题（例如只有纯链接 / 短链）→ 后台找 B 站接口要一次标题
        if changed and not title and not self._is_title_manual_filled():
            self._fetch_title_async(url)

    def _is_title_manual_filled(self):
        """文件名前缀是不是用户自己填的（是的话就不自动覆盖）。"""
        cur = self.name_entry.get().strip()
        return bool(cur) and (self.title_manual or cur != self.auto_title)

    def _mark_title_manual(self):
        self.title_manual = True

    def _apply_title(self, title, force=False):
        """把标题写进文件名前缀；用户自己改过就不覆盖。"""
        title = sc.sanitize_filename(title)
        if not title:
            return
        if force or not self._is_title_manual_filled():
            self.name_entry.delete(0, "end")
            self.name_entry.insert(0, title)
            self.auto_title = title
            self.title_manual = False
            self._refresh_name_example()
        # 记住这版标题对应的链接（网址栏里可能还留着「【标题】链接」的原文，统一解析一遍）
        cur_url, _ = sc.parse_link_input(self.url_entry.get().strip())
        self.title_url = cur_url or self.title_url

    def _on_fetch_title(self):
        """「从链接取标题」按钮：优先用链接自带的标题，其次沿用该链接上次的标题，最后才联网。"""
        url, title = sc.parse_link_input(self.url_entry.get().strip())
        if not url:
            messagebox.showwarning("提示", "请先填写 B 站视频链接")
            return
        if title:
            self._apply_title(title, force=True)
            self._set_url_hint(f"已识别标题：「{title}」→ 已填入「文件名前缀」")
            self.log(f"· 已使用链接自带标题：{title}")
            return
        if url == self.title_url and self.auto_title:
            self._apply_title(self.auto_title, force=True)
            self._set_url_hint(f"沿用上次标题：「{self.auto_title}」→ 已填入「文件名前缀」")
            self.log(f"· 沿用该链接上次解析到的标题：{self.auto_title}")
            return
        self._set_url_hint("正在从 B 站接口获取标题…")
        self.log("· 正在从 B 站接口获取标题…")
        self._fetch_title_async(url, force=True)

    def _fetch_title_async(self, url, force=False):
        if self._title_fetch_url == url and not force:
            return
        self._title_fetch_url = url

        def work():
            title = sc.fetch_video_title(url)
            self.post("title", (url, title, force))

        threading.Thread(target=work, daemon=True).start()

    def _on_title_fetched(self, url, title, force=False):
        # Discard failed requests too: an old failure must not replace the
        # current link's successfully parsed title hint.
        if sc.clean_video_url(self.url_entry.get().strip()) != url:
            return
        if not title:
            self._set_url_hint("没能从 B 站取回标题，可在「文件名前缀」里手动填", color="#C0392B")
            self.log("! 没能从 B 站取回标题（网络不通或接口限制），可手动填文件名前缀")
            return
        self._apply_title(title, force=force)
        self._set_url_hint(f"已从 B 站接口取回标题：「{title}」→ 已填入「文件名前缀」")
        self.log(f"· 已从 B 站接口取回标题：{title}")

    def _on_mode_change(self):
        if self.var_mode.get() == "auto":
            self.cfg["region_mode"] = "auto"
            self.region_label.configure(text="状态：自动识别（生成时按视频内容重新检测）")
        else:
            self.cfg["region_mode"] = "manual"
            if self.cfg.get("top_ratio") is None:
                self.region_label.configure(text="状态：手动指定 —— 请先点右侧「预览 / 微调字幕区域」拖出范围")
            else:
                self._show_manual_region()

    def _show_manual_region(self):
        h = self.region["height"] if self.region else 1080
        y1 = int(self.cfg["top_ratio"] * h)
        y2 = int(self.cfg["bottom_ratio"] * h)
        self.region_label.configure(
            text=f"状态：手动区域 y = {y1} ~ {y2}（按视频高度 {h}p 折算，实际按比例套用到新视频）")

    def _browse_dir(self):
        init = self.out_entry.get().strip() or os.path.expanduser("~")
        path = filedialog.askdirectory(title="选择导出文件夹",
                                       initialdir=init if os.path.isdir(init) else None)
        if path:
            self.out_entry.delete(0, "end")
            self.out_entry.insert(0, os.path.normpath(path))

    def _open_folder(self):
        path = self.out_entry.get().strip()
        if not path:
            messagebox.showwarning("提示", "请先设置导出文件夹")
            return
        try:
            os.makedirs(path, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                os.system(f'open "{path}"')
            else:
                os.system(f'xdg-open "{path}"')
            self.log(f"· 已打开文件夹：{path}")
        except Exception as e:
            messagebox.showerror("错误", f"无法打开文件夹：{e}")

    def _clear_cache(self):
        sc.clear_cache()
        self.video_path = None
        self.video_key = None
        self.region = None
        self.region_label.configure(text="状态：尚未识别（生成时会自动识别）")
        self.log("· 已清理全部缓存视频")

    def _collect(self):
        cfg = dict(self.cfg)
        # 网址栏可能还留着「【标题】链接」的原文（例如整段输入后直接点生成）
        raw_url = self.url_entry.get().strip()
        url, title = sc.parse_link_input(raw_url)
        if url:
            if url != raw_url:
                self.url_entry.delete(0, "end")
                self.url_entry.insert(0, url)
            if title:
                self._apply_title(title)
            cfg["url"] = url
        else:
            cfg["url"] = raw_url
        cfg["start"] = self.start_entry.get().strip()
        cfg["end"] = self.end_entry.get().strip()
        cfg["output_dir"] = self.out_entry.get().strip()
        cfg["filename"] = self.name_entry.get().strip()
        cfg["region_mode"] = self.var_mode.get()
        cfg["first_frame_full"] = bool(self.var_cover.get())
        cfg["separator"] = bool(self.var_sep.get())
        cfg["crop_horizontal"] = bool(self.var_cropx.get())
        cfg["open_after"] = bool(self.var_open.get())
        cfg["cookies_browser"] = self.cookie_menu.get()
        cfg["filename_timestamp"] = bool(self.var_ts.get())
        cfg["auto_number"] = bool(self.var_auto_num.get())
        return cfg

    def _save(self):
        self.cfg = self._collect()
        sc.save_config(self.cfg)

    def _set_busy(self, busy, text="一键生成拼接图"):
        self.busy = busy
        self.run_btn.configure(state="disabled" if busy else "normal", text=text)
        if not busy:
            self.progress.set(0)

    def _on_close(self):
        self._save()
        self.destroy()

    # ------------------------------------------------------------------ 预览
    def _on_preview(self):
        if self.busy:
            return
        cfg = self._collect()
        if not cfg["url"] or not cfg["start"] or not cfg["end"]:
            messagebox.showwarning("提示", "请先填写视频链接和时间区间")
            return
        self._save()
        self._set_busy(True, "正在准备预览…")
        self.log("—— 预览字幕区域 ——")
        threading.Thread(target=self._preview_worker, args=(cfg,), daemon=True).start()

    def _preview_worker(self, cfg):
        try:
            key = f"{cfg['url']}|{cfg['start']}|{cfg['end']}"
            if self.video_key != key or not (self.video_path and os.path.exists(self.video_path)):
                work = sc.cache_dir_for(cfg["url"], cfg["start"], cfg["end"])
                self.video_path = sc.download_segment(cfg["url"], cfg["start"], cfg["end"], work,
                                                      log=self.log_threadsafe,
                                                      cookies_browser=cfg.get("cookies_browser", "无"))
                self.video_key = key
                self.region = None
            if self.region is None:
                self.region = sc.detect_subtitle_region(self.video_path, log=self.log_threadsafe)
            self.post("preview", cfg)
        except Exception as e:
            self.log_threadsafe(f"× 预览失败：{e}")
            self.post("error", ("预览失败", str(e)))
        finally:
            self.post("busy", (False, "一键生成拼接图"))

    def _open_preview_window(self, cfg):
        if self.preview_win is not None and self.preview_win.winfo_exists():
            self.preview_win.destroy()
        region = self.region
        h, w = region["height"], region["width"]

        manual = cfg.get("region_mode") == "manual" and cfg.get("top_ratio") is not None
        top0 = cfg["top_ratio"] if manual else region["y1"] / h
        bot0 = cfg["bottom_ratio"] if manual else region["y2"] / h

        win = ctk.CTkToplevel(self)
        self.preview_win = win
        win.title("预览 / 微调字幕区域")
        win.configure(fg_color="#F5F5F7")
        self._set_window_icon(win)
        win.transient(self)

        scale = self._window_scale()
        sw = win.winfo_screenwidth() / scale
        sh = win.winfo_screenheight() / scale
        win.geometry(f"{int(min(1000, sw - 40))}x{int(min(700, sh - 40))}")

        ctk.CTkLabel(win, text=f"视频 {w}×{h}｜自动识别文字行 y = {region['text_y1']} ~ {region['text_y2']}"
                               f"（覆盖率 {(region.get('coverage') or 0):.0%}）",
                     font=self.f_small, text_color="#424245").pack(anchor="w", padx=16, pady=(10, 4))

        canvas_lbl = ctk.CTkLabel(win, text="正在读取预览帧…", font=self.f_small)
        canvas_lbl.pack(fill="both", expand=True, padx=16)

        state = {"t": self._pick_preview_time(), "top": top0, "bot": bot0, "frame": None}

        def current_frame():
            if state["frame"] is None:
                state["frame"] = sc.grab_frame(self.video_path, state["t"])
                if state["frame"] is None:      # 个别时间点解码失败时换一帧重试
                    state["t"] = self._pick_preview_time(exclude=state["t"])
                    state["frame"] = sc.grab_frame(self.video_path, state["t"])
            return state["frame"]

        def render():
            frame = current_frame()
            if frame is None:
                canvas_lbl.configure(text="取帧失败，请点「换一帧」重试")
                return
            avail_w = max(320, min(980, win.winfo_width() - 50))
            avail_h = max(220, win.winfo_height() - 190)
            k = min(avail_w / frame.width, avail_h / frame.height, 1.0)
            disp = frame.resize((max(1, int(frame.width * k)), max(1, int(frame.height * k))),
                                Image.LANCZOS)
            ov = Image.new("RGBA", disp.size, (0, 0, 0, 0))
            d = ImageDraw.Draw(ov)
            y1 = int(state["top"] * disp.height)
            y2 = int(state["bot"] * disp.height)
            d.rectangle([0, y1, disp.width, y2], fill=(255, 59, 48, 60),
                        outline=(255, 59, 48, 230), width=2)
            img = Image.alpha_composite(disp.convert("RGBA"), ov).convert("RGB")
            ctk_img = ctk.CTkImage(light_image=img, size=img.size)
            canvas_lbl.configure(image=ctk_img, text="")
            canvas_lbl._img = ctk_img
            top_val.configure(text=f"上边界 {state['top'] * 100:.1f}%  (y={int(state['top'] * h)})")
            bot_val.configure(text=f"下边界 {state['bot'] * 100:.1f}%  (y={int(state['bot'] * h)})")
            t_val.configure(text=f"预览帧 {state['t']:.1f}s｜裁切高度 {int((state['bot'] - state['top']) * h)}px")

        ctl = ctk.CTkFrame(win, fg_color="transparent")
        ctl.pack(fill="x", padx=16, pady=(8, 2))
        top_val = ctk.CTkLabel(ctl, text="", font=self.f_small, text_color="#1D1D1F", width=190, anchor="w")
        top_val.grid(row=0, column=0, sticky="w")
        bot_val = ctk.CTkLabel(ctl, text="", font=self.f_small, text_color="#1D1D1F", width=190, anchor="w")
        bot_val.grid(row=1, column=0, sticky="w")

        def on_slider(who, _=None):
            t, b = s_top.get(), s_bot.get()
            if t > b - 1:
                if who == "top":
                    b = min(100.0, t + 1)
                    s_bot.set(b)
                else:
                    t = max(0.0, b - 1)
                    s_top.set(t)
            state["top"], state["bot"] = t / 100.0, b / 100.0
            render()

        s_top = ctk.CTkSlider(ctl, from_=0, to=100, number_of_steps=1000,
                              command=lambda v: on_slider("top", v),
                              progress_color=ACCENT, button_color=ACCENT,
                              button_hover_color=ACCENT_HOVER, fg_color="#D1D1D6", height=16)
        s_top.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=3)
        s_top.set(top0 * 100)
        s_bot = ctk.CTkSlider(ctl, from_=0, to=100, number_of_steps=1000,
                              command=lambda v: on_slider("bot", v),
                              progress_color=ACCENT, button_color=ACCENT,
                              button_hover_color=ACCENT_HOVER, fg_color="#D1D1D6", height=16)
        s_bot.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=3)
        s_bot.set(bot0 * 100)
        ctl.grid_columnconfigure(1, weight=1)

        bottom = ctk.CTkFrame(win, fg_color="transparent")
        bottom.pack(fill="x", padx=16, pady=(4, 12))
        t_val = ctk.CTkLabel(bottom, text="", font=self.f_small, text_color="#86868B")
        t_val.pack(side="left")

        def next_frame():
            state["t"] = self._pick_preview_time(exclude=state["t"])
            state["frame"] = None
            render()

        def reset_auto():
            state["top"], state["bot"] = region["y1"] / h, region["y2"] / h
            s_top.set(state["top"] * 100)
            s_bot.set(state["bot"] * 100)
            render()

        def apply():
            self.cfg["region_mode"] = "manual"
            self.cfg["top_ratio"] = state["top"]
            self.cfg["bottom_ratio"] = state["bot"]
            self.var_mode.set("manual")
            self._save()
            self._show_manual_region()
            self.log(f"· 已应用手动字幕区域：y = {int(state['top'] * h)} ~ {int(state['bot'] * h)}")
            win.destroy()

        ctk.CTkButton(bottom, text="换一帧", width=80, height=40, corner_radius=8,
                      fg_color=GREY_BTN, hover_color=GREY_BTN_HOVER, text_color="#1D1D1F",
                      font=self.f_in, command=next_frame).pack(side="left", padx=(14, 0))
        ctk.CTkButton(bottom, text="恢复自动识别", width=110, height=40, corner_radius=8,
                      fg_color=GREY_BTN, hover_color=GREY_BTN_HOVER, text_color="#1D1D1F",
                      font=self.f_in, command=reset_auto).pack(side="left", padx=(8, 0))
        ctk.CTkButton(bottom, text="应用", width=84, height=40, corner_radius=8,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      font=self.f_in, command=apply).pack(side="right")
        ctk.CTkButton(bottom, text="取消", width=74, height=40, corner_radius=8,
                      fg_color=GREY_BTN, hover_color=GREY_BTN_HOVER, text_color="#1D1D1F",
                      font=self.f_in, command=win.destroy).pack(side="right", padx=(0, 8))

        win.after(80, render)
        # 窗口尺寸变化时重排一次预览（防抖，避免拖动窗口时反复重绘）
        pending = {"id": None}

        def on_resize(event=None):
            if pending["id"]:
                win.after_cancel(pending["id"])
            pending["id"] = win.after(220, render)

        win.bind("<Configure>", on_resize)
        self.log("· 预览窗口已打开：拖动滑块微调字幕区域（红色半透明带 = 会被裁切保留的范围）")

    def _pick_preview_time(self, exclude=None):
        try:
            dur = max(0.5, sc.probe_video(self.video_path)["duration"])
        except Exception:
            dur = 5.0
        for _ in range(12):
            t = random.uniform(0.2, max(0.3, dur - 0.3))
            if exclude is None or abs(t - exclude) > 0.5:
                return t
        return dur / 2

    # ------------------------------------------------------------------ 生成
    def _on_generate(self):
        if self.busy:
            return
        cfg = self._collect()
        if not cfg["url"] or not cfg["start"] or not cfg["end"]:
            messagebox.showwarning("提示", "请完整填写视频链接和时间区间！")
            return
        if not cfg["output_dir"]:
            messagebox.showwarning("提示", "请先设置导出文件夹")
            return
        if cfg["region_mode"] == "manual" and cfg.get("top_ratio") is None:
            messagebox.showwarning("提示", "手动模式还没有范围，请先点「预览 / 微调字幕区域」拖出字幕范围")
            return
        self._save()
        self.log_box.delete("1.0", "end")
        self._set_busy(True, "正在拼接处理中…")
        self.progress.set(0.02)
        threading.Thread(target=self._worker, args=(cfg,), daemon=True).start()

    def _worker(self, cfg):
        t0 = time.time()
        try:
            result = sc.run_pipeline(cfg, log=self.log_threadsafe,
                                     progress=lambda p: self.post("progress", p))
            self.log_threadsafe(f"· 总耗时 {time.time() - t0:.1f}s")
            self.post("result", (result, cfg))
        except Exception as e:
            self.log_threadsafe(f"× 运行出错：{e}")
            self.post("error", ("错误", f"发生异常：\n{e}"))
        finally:
            self.post("busy", (False, "一键生成拼接图"))

    def _done(self, result, cfg):
        path = result["path"]
        crop = result["region"].get("final_crop")
        if crop:
            self.region_label.configure(
                text=f"状态：本次裁切 y = {crop[1]} ~ {crop[3]}（高 {crop[3] - crop[1]}px），"
                     f"共 {result['count']} 句字幕")
        messagebox.showinfo("完成", f"拼接成功！共 {result['count']} 句字幕\n\n图片已保存至：\n{path}")
        if cfg.get("open_after") and os.path.exists(path):
            try:
                if sys.platform == "win32":
                    os.startfile(path)
                elif sys.platform == "darwin":
                    os.system(f'open "{path}"')
                else:
                    os.system(f'xdg-open "{path}"')
            except Exception as e:
                self.log(f"! 打开图片失败：{e}")


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
