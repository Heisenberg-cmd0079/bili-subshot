# -*- coding: utf-8 -*-
# Required Notice: Copyright 2026 Heisenberg-cmd0079
# Licensed under the PolyForm Noncommercial License 1.0.0 (see LICENSE).
# 禁止商业使用；需要商业授权请联系作者。
"""
B站台词拼接核心模块
==================

流水线：
    1. 下载指定时间段的视频片段（yt-dlp，带缓存）
    2. 自动识别硬字幕所在的水平条带（多帧行覆盖度统计，不靠单帧猜测）
    3. 高频采样 + 文字掩码去重（只比字幕条带内的白色字形，忽略游戏/画面变化）
    4. 垂直拼接导出

设计要点（针对旧版“有概率截断字幕 / 需要自己调边距 / 重复帧”的问题）：
    * 字幕区域用「多帧统计」而不是「单帧 Otsu 二值化」，不会因为某一帧的
      亮色背景把文字范围推断到画面底部而切掉字幕。
    * 裁切范围 = 检测到的文字行 ± 自适应留白，留白按文字高度推算，无需手填。
    * 去重在「白色字形掩码」上做（IoU + 包含度），只对字幕文字敏感，
      画面本身的动画不会再把重复台词漏过去。
    * 每组字幕挑选“字形像素最多”的那一帧，避开淡入淡出/半透明帧。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

import cv2
import numpy as np
from PIL import Image

# --------------------------------------------------------------------------
# 常量 / 配置
# --------------------------------------------------------------------------

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
CACHE_ROOT = os.path.join(tempfile.gettempdir(), "zimupinjie")

SEARCH_BOTTOM_RATIO = 0.55      # 在画面下方 55% 内搜索字幕
DETECT_SAMPLE_COUNT = 60        # 区域识别用的采样帧数
DENSE_STEP = 0.25               # 抽帧间隔（秒）
SIM_IOU = 0.50                  # 判定“同一句字幕”的 IoU 阈值（净化字形后实测同句 >=0.73、异句 <=0.10）
SIM_CONTAIN = 0.82              # 判定“同一句字幕”的包含度阈值（实测同句 >=0.85、异句 <=0.20）
REPEAT_GAP = 2.5                # 两句字幕之间的空隙小于此时长且字形相同 → 视为同一句
GAP_TOL = 1.0                   # 字幕短暂消失（闪烁 / 转场）的容忍时长（秒）

WHITE_V = 185                   # 白色文字：亮度阈值
WHITE_S = 90                    # 白色文字：饱和度上限
DARK_V = 80                     # 黑描边：亮度上限
OUTLINE_ITER = 2                # 描边邻域的膨胀次数（掩码取“白字 ∩ 黑描边附近”）
GLYPH_FILL = 0.10               # 字形连通块的最低填充率（面积 / 外接矩形）

COOKIE_CHOICES = ["无", "Edge", "Chrome", "Firefox"]

DEFAULT_CONFIG = {
    "url": "",
    "start": "00:01:00",
    "end": "00:01:30",
    "filename": "bilibili_台词拼接",   # 文件名前缀（GUI 里粘贴带标题的链接会自动填成标题）
    "output_dir": os.path.join(APP_DIR, "output"),
    "region_mode": "auto",          # auto | manual
    "top_ratio": None,              # manual 模式下的字幕区域上边界（占画面高度比例）
    "bottom_ratio": None,           # manual 模式下的字幕区域下边界
    "first_frame_full": True,       # 首帧保留完整画面
    "separator": True,              # 相邻字幕条之间加分隔线
    "crop_horizontal": False,       # 横向裁切到字幕文字范围
    "open_after": True,             # 完成后打开图片
    "cookies_browser": "无",         # 用浏览器 cookies 拉取高码率
    "filename_timestamp": False,    # 文件名追加时间戳
    "auto_number": True,            # 文件名自动编号（标题_01 / 标题_02 …），避免覆盖旧图
    "keep_cache": False,            # 结束后保留缓存视频
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:      # 兼容带 BOM 的文件
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
    except Exception:
        pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({k: cfg.get(k) for k in DEFAULT_CONFIG}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# --------------------------------------------------------------------------
# 0. 链接解析 / 输出命名
# --------------------------------------------------------------------------
# B 站客户端复制的分享内容长这样：
#     【百K亚煞极电表神明，刷神明最快打法】https://www.bilibili.com/video/BV1aBhR69Ef6?vd_source=14d5...
# 这里负责把它拆成「干净链接」+「标题」，标题用于自动命名输出文件。

# 分享链接里的跟踪参数：去掉后，同一视频不同来源的链接会命中同一份下载缓存
TRACKING_QUERY_KEYS = {
    "vd_source", "spm_id_from", "from_source", "from_spmid", "share_source",
    "share_medium", "share_plat", "share_session_id", "share_tag", "share_times",
    "unique_k", "bbid", "ts", "seid", "buvid", "up_id", "msource", "tab",
    "broadcast_type", "spm_id",
}

_URL_RE = re.compile(r"https?://[^\s\u3000，,；;、）)】》>\"'<>]+", re.IGNORECASE)
_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
# 标题括号对（按优先级取）。
# 注意：B 站客户端的分享标题**自己就可能套一层括号**，例如
#     【示例：某视频的完整标题【第一话】】https://…
# 所以不能再用 `[^【】]{1,200}?` 这种「内容里不许出现括号」的正则：
# 那样第一个 `【` 根本配不上，引擎会退到内层那个 `【` 上，
# 于是只抠出内层的「第一话」，外层标题整段被丢掉
# （旧版就是这个毛病，用户看到的现象是「粘贴的长标题被截断了」）。
# 现在改成按下标做括号配对、取最外层内容，允许任意层嵌套。
_BRACKET_PAIRS = (("【", "】"), ("「", "」"), ("《", "》"), ("[", "]"))
_TITLE_MAXLEN = 200


def _bracket_titles(text):
    """
    按括号配对找出标题，生成 (标题, 起始下标, 结束下标)，允许标题里再套括号。

    例：
        '【示例：某视频的完整标题【第一话】】https://…'
        -> ('示例：某视频的完整标题【第一话】', 0, 18)
    """
    for open_c, close_c in _BRACKET_PAIRS:
        i = text.find(open_c)
        while i != -1:
            depth = 0
            end = -1
            limit = min(len(text), i + _TITLE_MAXLEN * 2 + 2)   # 防脏数据把这里变成 O(n²)
            for j in range(i, limit):
                ch = text[j]
                if ch == open_c:
                    depth += 1
                elif ch == close_c:
                    depth -= 1
                    if depth == 0:                              # 配到这一层的最外层右括号
                        end = j
                        break
            if end != -1:
                inner = text[i + 1:end].strip()
                if inner:
                    yield inner, i, end + 1
            i = text.find(open_c, i + 1)
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def sanitize_filename(name, maxlen=80) -> str:
    """清掉 Windows 文件名非法字符与首尾空白/点号。"""
    name = re.sub(r"[\x00-\x1f]", " ", str(name or ""))
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name.strip(" .")[:maxlen].strip(" .")


def clean_video_url(url) -> str:
    """去掉视频链接里的分享/跟踪参数（vd_source、spm_id_from …），保留 p、t 等有效参数。"""
    url = (url or "").strip()
    if not url:
        return ""
    try:
        parts = urllib.parse.urlsplit(url)
        if not parts.query:
            return url
        kept = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
                if k.lower() not in TRACKING_QUERY_KEYS]
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(kept), parts.fragment))
    except Exception:
        return url


def parse_link_input(text):
    """
    解析粘贴进来的内容（客户端复制的链接自带标题）。

    支持这些形式：
        【标题】https://www.bilibili.com/video/BVxxxx?vd_source=…
        「标题」https://b23.tv/xxxx
        https://www.bilibili.com/video/BVxxxx?vd_source=…   （没有标题）
        BV1aBhR69Ef6                                       （只有 BV 号）

    返回 (清理过的链接, 标题)；解析不出链接时返回 ("", "")，调用方可原样保留输入。
    """
    raw = str(text or "").replace("\r", "\n").strip()
    if not raw:
        return "", ""
    urls = list(_URL_RE.finditer(raw))
    bv = _BV_RE.search(raw)
    # 粘贴内容里的标题（取最外层括号，允许标题自己带括号）
    first_title = next(_bracket_titles(raw), None)
    url = ""
    if urls:
        # 标题后面紧跟着的那个链接优先；否则取最后一个链接
        # （用户没清空输入框就直接粘贴时，新链接在后面，取最后一个才符合直觉）
        chosen = None
        if first_title:
            after = [m for m in urls if m.start() >= first_title[2]]
            if after:
                chosen = after[0]
        chosen = chosen or urls[-1]
        url = clean_video_url(chosen.group(0))
    elif bv:
        url = f"https://www.bilibili.com/video/{bv.group(1)}"
    if not url:
        return "", ""

    title = first_title[0] if first_title else ""
    if not title and urls and chosen is not None:   # 没有括号时，取链接以外的文字
        rest = raw[:chosen.start()] + " " + raw[chosen.end():]
        rest = re.sub(r"[\s\u3000]+", " ", rest).strip()
        title = rest.strip(" -—–·|:：")
    return url, sanitize_filename(title)


def fetch_video_title(url, timeout=10.0) -> str:
    """尽力从 B 站接口取回视频标题（失败、无网络时返回 ""，不抛异常）。"""
    url = (url or "").strip()
    if not url:
        return ""
    try:
        bvid = ""
        hit = _BV_RE.search(url)
        if hit:
            bvid = hit.group(1)
        else:                                   # 短链（b23.tv）先跟一次跳转拿 BV 号
            try:
                req = urllib.request.Request(url, headers={"User-Agent": _UA})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    final = resp.geturl() or url
                hit = _BV_RE.search(final)
                bvid = hit.group(1) if hit else ""
            except Exception:
                pass
        api = ("https://api.bilibili.com/x/web-interface/view?bvid=" + bvid) if bvid else \
              ("https://api.bilibili.com/x/web-interface/view?url=" +
               urllib.parse.quote(url, safe=""))
        req = urllib.request.Request(api, headers={"User-Agent": _UA,
                                                   "Referer": "https://www.bilibili.com/"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        if isinstance(data, dict) and data.get("code") == 0:
            return sanitize_filename((data.get("data") or {}).get("title") or "")
    except Exception:
        pass
    return ""


def build_output_path(output_dir, base, timestamp=False, auto_number=False, ext=".png") -> str:
    """
    组装导出路径。

    auto_number=True 时按「前缀_01 / 前缀_02 …」编号（沿用导出目录里已有的最大编号 +1），
    同名旧图不会被覆盖；编号用满 99 后自动变成 3 位。
    """
    base = sanitize_filename(base) or "bilibili_台词拼接"
    suffix = "_" + time.strftime("%Y%m%d_%H%M%S") if timestamp else ""
    if not auto_number:
        return os.path.join(output_dir, base + suffix + ext)

    used = 0
    pat = re.compile(r"^" + re.escape(base) + r"_(\d+)(?:_.*)?$")
    try:
        for fn in os.listdir(output_dir):
            hit = pat.match(os.path.splitext(fn)[0])
            if hit:
                used = max(used, int(hit.group(1)))
    except OSError:
        pass

    n = used + 1
    while True:
        candidate = f"{base}_{n:02d}{suffix}{ext}"
        if not os.path.exists(os.path.join(output_dir, candidate)):
            return os.path.join(output_dir, candidate)
        n += 1


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def _no_window_kwargs() -> dict:
    """Windows 下隐藏子进程控制台窗口。"""
    if os.name != "nt":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {"startupinfo": si}


_K3 = None


def _kernel3() -> np.ndarray:
    global _K3
    if _K3 is None:
        _K3 = np.ones((3, 3), np.uint8)
    return _K3


def _glyph_limits(h):
    """
    按画面高度折算“像一句台词里的字”的连通块尺寸范围（以 1080p 为基准等比缩放）。
    返回 (min_h, max_h, min_w, max_w, min_area)。
    """
    k = max(0.4, float(h) / 1080.0)
    return (max(6, int(round(8 * k))), max(30, int(round(95 * k))),
            max(2, int(round(2 * k))), max(40, int(round(130 * k))),
            max(12, int(round(25 * k * k))))


def _filter_glyphs(g, limits):
    """按字形尺寸/面积/填充率过滤二值候选，不改变描边策略。"""
    min_h, max_h, min_w, max_w, min_area = limits
    n, lab, stats, _cent = cv2.connectedComponentsWithStats(g.astype(np.uint8), 8)
    if n <= 1:
        return g.astype(bool)
    keep = np.zeros(n, dtype=np.uint8)
    for i in range(1, n):
        _x, _y, ww, hh, area = stats[i]
        if min_h <= hh <= max_h and min_w <= ww <= max_w and area >= min_area \
                and area / float(ww * hh) > GLYPH_FILL:
            keep[i] = 1
    return keep[lab].astype(bool)


def text_mask(roi_bgr: np.ndarray, limits=None, refine=None, *,
              outline="dark", glyph_filter=True) -> np.ndarray:
    """字幕字形 bool 掩码。描边要求与字形过滤分别控制。

    outline="dark"：白色像素附近必须有暗像素，默认行为保持不变。
    outline="none"：不要求暗邻域，但仍用字形尺寸过滤挡住整片亮背景。
    若白色候选连成背景且过滤后为空，用局部亮度对比分离较亮白字，
    再执行相同字形过滤；这不能保证识别低对比白字或彩色字幕。
    glyph_filter=False：返回未做字形过滤的候选；refine 为兼容别名。
    """
    if outline not in ("dark", "none"):
        raise ValueError(f"未知描边策略：{outline!r}")
    if refine is not None:
        glyph_filter = bool(refine)
    hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
    v, s = hsv[:, :, 2], hsv[:, :, 1]
    white = (v > WHITE_V) & (s < WHITE_S)
    if outline == "dark":
        dark = (v < DARK_V).astype(np.uint8)
        near_dark = cv2.dilate(dark, _kernel3(), iterations=OUTLINE_ITER).astype(bool)
        g = white & near_dark
    else:
        g = white
    if not glyph_filter:
        return g
    limits = limits or _glyph_limits(roi_bgr.shape[0])
    filtered = _filter_glyphs(g, limits)
    if outline == "none" and not filtered.any() and white.any():
        # 开运算核大于允许的字形，移除局部较亮字块以估计亮背景。
        # 195 背景也通过 WHITE_V，直接连通块过滤无法把 245 白字分出来。
        max_h, max_w = limits[1], limits[3]
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT,
                                          (max_w + 1, max_h + 1))
        background = cv2.morphologyEx(v, cv2.MORPH_OPEN, kernel)
        contrast = cv2.subtract(v, background)
        filtered = _filter_glyphs(white & (contrast >= 20), limits)
    return filtered


def _runs_of(flags, gap=0, min_len=1):
    """把布尔序列切成连续（允许跨过 gap 个 False）的区间列表 [(a, b), ...]（含端点）。"""
    runs, start, holes = [], None, 0
    for i, ok in enumerate(flags):
        if ok:
            if start is None:
                start = i
            holes = 0
        elif start is not None:
            holes += 1
            if holes > gap:
                runs.append((start, i - holes))
                start, holes = None, 0
    if start is not None:
        runs.append((start, len(flags) - 1 - holes))
    return [r for r in runs if r[1] - r[0] + 1 >= min_len]



def _sample_frames(cap, fps, times):
    """按时间点顺序解码帧（内部用 grab 跳帧，比 seek 快很多）。"""
    targets = sorted({max(0, int(round(t * fps))) for t in times})
    if not targets:
        return
    ti, idx, nxt = 0, 0, targets[0]
    while ti < len(targets):
        if not cap.grab():
            return
        if idx == nxt:
            ok, frame = cap.retrieve()
            if ok:
                yield nxt / fps, frame
            ti += 1
            if ti >= len(targets):
                return
            nxt = targets[ti]
        idx += 1


def cache_dir_for(url: str, start: str, end: str) -> str:
    key = hashlib.md5(f"{url}|{start}|{end}".encode("utf-8")).hexdigest()[:16]
    return os.path.join(CACHE_ROOT, key)


def clear_cache(url: str = "", start: str = "", end: str = "") -> None:
    path = cache_dir_for(url, start, end) if url else CACHE_ROOT
    shutil.rmtree(path, ignore_errors=True)


# --------------------------------------------------------------------------
# 1. 下载片段
# --------------------------------------------------------------------------

def download_segment(url, start_time, end_time, work_dir, log=print, cookies_browser="无"):
    """下载 [start, end] 片段，返回视频文件路径（已存在则直接复用）。"""
    os.makedirs(work_dir, exist_ok=True)
    video_path = os.path.join(work_dir, "clip.mp4")
    if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
        log("√ 复用已下载的视频片段（如需重下请点「清理缓存」）")
        return video_path

    log("· 正在解析 B 站视频并截取片段，请稍候…")
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist", "--newline", "--no-colors",
        "--download-sections", f"*{start_time}-{end_time}",
        "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
        "--merge-output-format", "mp4",
        "-o", video_path,
        "--force-overwrites",
    ]
    if cookies_browser and cookies_browser != "无":
        cmd += ["--cookies-from-browser", cookies_browser.lower()]
    cmd.append(url)

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", **_no_window_kwargs()
    )
    tail = []
    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        tail.append(line)
        if len(tail) > 6:
            tail.pop(0)
        # 只把有意义的进度行刷到日志
        if any(k in line for k in ("[download]", "已下载", "%", "ERROR", "错误", "Merging", "ffmpeg")):
            log("   " + line)
    proc.wait()
    if proc.returncode != 0 or not os.path.exists(video_path):
        detail = "\n".join(tail[-4:]) or "未知错误"
        raise RuntimeError(f"视频下载失败（yt-dlp 退出码 {proc.returncode}）：\n{detail}")

    log(f"√ 片段下载完成：{os.path.basename(video_path)}")
    return video_path


# --------------------------------------------------------------------------
# 2. 字幕区域识别
# --------------------------------------------------------------------------

def _bottom_fallback(h, w, outline="dark"):
    """识别失败时的兜底区域：画面底部 14%。"""
    y2 = h
    y1 = h - int(h * 0.14)
    return dict(height=h, width=w, text_y1=y1, text_y2=y2, y1=y1, y2=y2,
                top_ratio=y1 / h, bottom_ratio=y2 / h, coverage=0.0, fallback=True, outline=outline)


def _row_density_profile(video_path, y0, h, fps, times, limits, outline="dark"):
    """按指定描边策略统计行密度，两种策略都保留字形过滤。"""
    cap = cv2.VideoCapture(video_path)
    rows = []
    try:
        for _t, frame in _sample_frames(cap, fps, times):
            m = text_mask(frame[y0:h], limits=limits, outline=outline, glyph_filter=True)
            rows.append(m.sum(axis=1).astype(np.float32))
    finally:
        cap.release()
    return rows


def detect_subtitle_region(video_path, log=print, search_bottom_ratio=SEARCH_BOTTOM_RATIO):
    """
    多帧统计识别硬字幕所在的水平条带。

    判据（针对漫画 / 白底 / 亮色画面做过专门处理）：
      1. 只统计「白字 + 黑描边 + 尺寸像文字」的连通块（见 text_mask），
         画面里的白底、亮块、大色块不会被当成文字；
      2. 逐行统计这些字形在多少帧里出现（存在率）——常驻字幕行接近 100%，
         画面内容的行则随镜头变化忽有忽无；
      3. 以存在率高的连续行段为种子，再逐帧向上下生长（允许跨过少量空行），
         取所有帧的并集。因此「同一句字幕分两行显示」时，上面那行也会被
         包含进来，不会只留下面一行。

    返回 dict：height / width / text_y1 / text_y2 / y1 / y2 / top_ratio / bottom_ratio
              / coverage / fallback / outline
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError("无法打开视频文件")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    dur = total / fps if fps > 0 else 0.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if dur <= 0.3:
        cap.release()
        raise RuntimeError("视频片段过短，无法识别字幕区域")

    y0 = int(h * (1 - search_bottom_ratio))
    zone_h = h - y0
    cap.release()                                  # 后面按需重新打开（可能要扫两遍）

    limits = _glyph_limits(h)
    row_thr = max(10.0, 0.012 * w)          # 一行算“有字”的最低字形像素量

    log(f"· 正在识别字幕区域（{h}p，采样 {DETECT_SAMPLE_COUNT} 帧）…")
    times = np.linspace(0.15, max(0.2, dur - 0.1), DETECT_SAMPLE_COUNT)
    outline = "dark"
    dens_rows = _row_density_profile(video_path, y0, h, fps, times, limits, outline)
    if not dens_rows:
        log("! 未能识别到字幕文字，回退到画面底部默认区域")
        return _bottom_fallback(h, w)

    dens_map = np.array(dens_rows, dtype=np.float32)      # (帧数, zone_h)
    exist = (dens_map > row_thr).mean(axis=0)             # 该行“有字形”的帧占比
    emax = float(exist.max())
    if emax < 0.20:
        outline = "none"
        log("! 未检测到稳定的白字描边字幕，已改用无描边白字判据重试（保留字形过滤，必要时分离亮背景）…")
        dens_rows = _row_density_profile(video_path, y0, h, fps, times, limits, outline)
        if dens_rows:
            dens_map = np.array(dens_rows, dtype=np.float32)
            exist = (dens_map > row_thr).mean(axis=0)
            emax = float(exist.max())
    if emax < 0.15:
        log("! 两种判据均未检测到稳定字形，回退到画面底部区域；"
            "该字幕样式（低对比白字无描边 / 彩色字幕）可能暂不支持，"
            "请确认字幕确实在所选区间内，或换用白字黑边字幕素材")
        return _bottom_fallback(h, w, outline=outline)
    frames_n = dens_map.shape[0]

    # ---- 种子行段：存在率高（字幕行几乎每帧都有字）的连续行 ----
    # 常驻的白色 UI 条 / 工具栏同样每帧都在，存在率甚至更高。区别在于：字幕会换句、
    # 行内像素量随之起伏，而工具栏纹丝不动 —— 于是再算一维“行内容变化程度”，
    # 只用来给候选段降权（不做硬过滤，否则字幕分两行时上面那行会被误伤）。
    base = np.maximum(dens_map.mean(axis=0), 1.0)
    churn = np.abs(np.diff(dens_map, axis=0)).mean(axis=0) / base if frames_n > 1 \
        else np.zeros(zone_h, dtype=np.float32)
    cmax = float(churn.max())
    use_churn = cmax > 0.15

    seeds = _runs_of(exist >= max(0.20, 0.45 * emax), gap=6, min_len=5)
    if not seeds:
        log("! 未找到稳定的字幕行，回退到画面底部默认区域")
        return _bottom_fallback(h, w, outline=outline)

    def seed_score(seg):
        a, b = seg
        center = (y0 + (a + b) / 2.0) / h
        # 覆盖率 × 行数（封顶 150）× 位置先验：越靠画面下方越可能是字幕
        score = float(exist[a:b + 1].mean()) * min(b - a + 1, 150) * (0.75 + 0.25 * center)
        if use_churn:
            score *= 0.05 + 0.95 * min(1.0, float(churn[a:b + 1].mean()) / (0.30 * cmax))
        return score

    a, b = max(seeds, key=seed_score)

    # ---- 逐帧向上下生长，取所有帧的并集：字幕分两行时上面那行也在并集里 ----
    core_h = b - a + 1
    core_cols = dens_map[:, a:b + 1]
    # 生长阈值只参考“真的出现过字幕”的那些帧，字幕稀疏时也不会被画面内容带跑
    hit = core_cols[core_cols.max(axis=1) > row_thr]
    grow_thr = max(2.0, 0.05 * float(np.median(hit)) if hit.size else 2.0)
    up_limit = int(round(core_h * 1.6)) + 12      # 上扩上限，避免顺着画面内容一路爬上去
    down_limit = int(round(core_h * 0.8)) + 12
    hole_max = 4                                  # 生长时允许跨过的空行数

    tops, bots = [], []
    for i in range(frames_n):
        dens = dens_map[i]
        top, bot, holes = a, b, 0
        y = a - 1
        while y >= 0 and a - y <= up_limit:
            if dens[y] >= grow_thr:
                top, holes = y, 0
            else:
                holes += 1
                if holes > hole_max:
                    break
            y -= 1
        holes = 0
        y = b + 1
        while y < zone_h and y - b <= down_limit:
            if dens[y] >= grow_thr:
                bot, holes = y, 0
            else:
                holes += 1
                if holes > hole_max:
                    break
            y += 1
        tops.append(top)
        bots.append(bot)

    y1 = y0 + int(round(float(np.percentile(tops, 3))))
    y2 = y0 + int(round(float(np.percentile(bots, 97)))) + 1
    y1 = max(0, min(y1, h - 8))
    y2 = max(y1 + 8, min(y2, h))

    text_h = y2 - y1
    pad = int(max(10, round(text_h * 0.25)))       # 自适应留白，替代手填边距
    cy1 = max(0, y1 - pad)
    cy2 = min(h, y2 + pad)
    cov = float(exist[a:b + 1].mean())

    log(f"· 字幕文字行 y = {y1} ~ {y2}（高 {text_h}px，常驻行覆盖率 {cov:.0%}），"
        f"自适应留白 {pad}px → 裁切 {cy1} ~ {cy2}")

    return dict(height=h, width=w, text_y1=y1, text_y2=y2, y1=cy1, y2=cy2,
                top_ratio=cy1 / h, bottom_ratio=cy2 / h, coverage=cov, fallback=False, outline=outline)


def _select_manual_outline(video_path, y1, y2, info, log=print):
    """手动框选只限定区域，仍需选择且保留该区域适用的描边策略。"""
    times = np.linspace(0.15, max(0.2, info["duration"] - 0.1), DETECT_SAMPLE_COUNT)
    rows = _row_density_profile(video_path, y1, y2, info["fps"], times,
                                _glyph_limits(info["height"]), outline="dark")
    row_thr = max(10.0, 0.012 * info["width"])
    if rows and float((np.array(rows) > row_thr).mean(axis=0).max()) >= 0.20:
        return "dark"
    log("· 手动区域未检测到稳定的描边白字，已改用无描边白字判据（保留字形过滤）")
    return "none"


def probe_video(video_path):
    """读取视频基本信息。"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError("无法打开视频文件")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    info = dict(fps=fps, frames=total, duration=total / fps if fps else 0.0,
                width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    cap.release()
    return info


def grab_frame(video_path, t, y1=None, y2=None, x1=None, x2=None):
    """取某一时刻的画面，可选裁切。"""
    cap = cv2.VideoCapture(video_path)
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        return None
    if None not in (y1, y2, x1, x2):
        frame = frame[y1:y2, x1:x2]
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


# --------------------------------------------------------------------------
# 3. 抽帧 + 去重
# --------------------------------------------------------------------------

def _same_text(m1, m2, iou_thr=SIM_IOU, cont_thr=SIM_CONTAIN):
    c1, c2 = int(m1.sum()), int(m2.sum())
    if c1 == 0 or c2 == 0:
        return False
    inter = int(np.logical_and(m1, m2).sum())
    if inter == 0:
        return False
    iou = inter / (c1 + c2 - inter)
    contain = inter / min(c1, c2)
    return iou >= iou_thr or contain >= cont_thr


def _glyph_count(mask):
    """
    统计掩码里“像文字”的连通块数量（一整块 UI 亮条/图标的块会被 ww 上限挡掉）。
    真正的字幕行至少有一个这种小字块。
    """
    n, _lab, stats, _cent = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    count = 0
    for i in range(1, n):
        _x, _y, ww, hh, area = stats[i]
        if 10 <= hh <= 90 and 4 <= ww <= 110 and area >= 40 and area / float(ww * hh) > 0.12:
            count += 1
    return count


def _detect_text_xrange(recs, min_px, w):
    """
    从所有采样掩码的列投影里推断字幕文字的横向范围（用于把画面里的其它白色
    元素排除在去重比较之外）。返回半开区间 (x1, x2) 或 (0, w)。
    """
    col = np.zeros(w, dtype=np.float32)
    used = 0
    for _, cnt, packed, shape in recs:
        if cnt < min_px:
            continue
        col += np.unpackbits(packed, axis=1)[:, :shape[1]].sum(axis=0)
        used += 1
    if used == 0:
        return 0, w

    active = col > max(1.0, 0.08 * used)
    segs, cur, gap = [], None, 0
    for i, ok in enumerate(active):
        if ok:
            if cur is None:
                cur = i
            gap = 0
        elif cur is not None:
            gap += 1
            if gap > 40:                       # 允许 40px 的字间距
                segs.append((cur, i - gap))
                cur, gap = None, 0
    if cur is not None:
        segs.append((cur, len(active) - 1 - gap))

    if not segs:
        return 0, w
    # 丢掉体量很小的杂散元素（UI 文字、图标），保留真正的字幕行段
    masses = [float(col[a:b + 1].sum()) for a, b in segs]
    keep = [s for s, m in zip(segs, masses) if m >= 0.08 * max(masses)]
    xa = min(s[0] for s in keep)
    xb = max(s[1] for s in keep)
    if xb - xa < 40:
        return 0, w
    return xa, xb + 1


def _grow_text_rows(dens, a, b, frac=0.25):
    """
    从 [a,b] 行出发，只在「紧邻且仍然很亮」的行上向上下扩张（不允许跨过空白行），
    用来兜住检测可能轻微低估的文字边缘。返回 (top, bot)。
    """
    thr = max(4.0, frac * float(dens[a:b + 1].max()))
    top, bot = a, b
    while top - 1 >= 0 and dens[top - 1] > thr:
        top -= 1
    while bot + 1 < len(dens) and dens[bot + 1] > thr:
        bot += 1
    return max(0, top), min(len(dens) - 1, bot)


def extract_subtitle_frames(video_path, band_y1, band_y2, crop_y1=None, crop_y2=None,
                            log=print, step=DENSE_STEP, crop_horizontal=False,
                            allow_expand=True, outline="dark"):
    """
    高频采样 → 只比较字幕文字行内的白色字形掩码 → 按“一句字幕”分组 →
    每组取最完整的一帧 → 再按所有保留帧的实际文字行范围确定最终裁切（保证不截断）。

    band_y1/band_y2 : 检测到的“文字行”范围（用于比较与去重）
    crop_y1/crop_y2 : 最终显示的裁切范围；缺省 = 文字行 ± 按字高推算的留白
    outline : 检测阶段选定的描边策略，两轮抽帧都保持字形过滤。
    返回 (frames, times, crop)；frames 为 PIL 图像（已裁切）。
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError("无法打开视频文件")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    dur = total / fps if fps > 0 else 0.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    band_y1 = max(0, min(band_y1, h - 4))
    band_y2 = max(band_y1 + 4, min(band_y2, h))
    band_h = band_y2 - band_y1
    limits = _glyph_limits(h)

    if crop_y1 is None or crop_y2 is None:
        pad = int(max(10, round(band_h * 0.25)))
        crop_y1, crop_y2 = band_y1 - pad, band_y2 + pad
    crop_y1 = max(0, min(int(crop_y1), band_y1))
    crop_y2 = min(h, max(int(crop_y2), band_y2))

    # 分析区域：上下各留出一个字幕带的高度，保证
    #   ① 字形连通块不会在 ROI 边界处被切断；
    #   ② ROI 一定覆盖最终裁切范围。旧版 ROI 用的是固定 50px 余量，当裁切范围比它更宽时
    #      `cy1 - gy1` 会变成负数，numpy 负索引从数组尾部取数，结果只截到画面最底部的一条，
    #      两行字幕的上面一行就被切掉了。
    gy1 = max(0, min(band_y1 - band_h, crop_y1) - 8)
    gy2 = min(h, max(band_y2 + band_h, crop_y2) + 8)
    b0, b1 = band_y1 - gy1, band_y2 - gy1

    times = np.arange(0.15, max(0.3, dur - 0.1), step)
    log(f"· 正在抽帧分析（每 {step}s 一帧，共约 {len(times)} 帧）…")

    recs = []      # (t, 字形像素数, packbits 掩码, 掩码形状)；掩码只取“文字带”内的行
    for t, frame in _sample_frames(cap, fps, times):
        m = text_mask(frame[gy1:gy2], limits=limits, outline=outline, glyph_filter=True)[b0:b1]
        recs.append((float(t), int(m.sum()), np.packbits(m, axis=1), m.shape))
    cap.release()
    if not recs:
        raise RuntimeError("未能从视频中抽取到任何画面")

    counts = np.array([r[1] for r in recs], dtype=np.float32)
    ref = float(np.percentile(counts, 90))
    min_px = max(120, int(0.12 * ref))         # 低于此字形像素数视为“这一帧没有字幕”

    # 横向文字范围（仅在需要横向裁切时使用；去重比较用整行，实测更稳）
    xa, xb = _detect_text_xrange(recs, min_px, w)
    if crop_horizontal and xb - xa < w * 0.98:
        log(f"· 字幕横向范围 x = {xa} ~ {xb}")

    # ---- 按“同一句字幕”分组 ----
    # 旧版只拿“上一帧”比一次，字幕淡入淡出/短暂消失/画面闪一下就会把同一句拆成好几句，
    # 输出里就出现了整段重复。这里改为：与组内最近几帧比较（容忍逐字出现与抖动），
    # 并且短暂空窗不立刻断组。
    groups, cur, lookback = [], None, []
    holes = 0                                     # 当前组之后连续“没有字幕”的帧数
    for t, cnt, packed, shape in recs:
        m = np.unpackbits(packed, axis=1)[:, :shape[1]].astype(bool)
        cnt = int(m.sum())
        if cnt < min_px:
            if cur is not None:
                cur["t1"] = t
                holes += 1
                if holes * step > GAP_TOL:
                    groups.append(cur)
                    cur, lookback = None, []
            continue
        if cur is None:
            cur = dict(t0=t, t1=t, best_t=t, best_cnt=cnt, mask=m.copy())
            lookback, holes = [m.copy()], 0
            continue
        if any(_same_text(prev, m) for prev in lookback[-3:]):
            cur["t1"] = t
            holes = 0
            if cnt > cur["best_cnt"]:
                cur.update(best_t=t, best_cnt=cnt, mask=m.copy())
            lookback.append(m.copy())
            if len(lookback) > 4:
                lookback.pop(0)
        else:
            groups.append(cur)
            cur = dict(t0=t, t1=t, best_t=t, best_cnt=cnt, mask=m.copy())
            lookback, holes = [m.copy()], 0
    if cur is not None:
        groups.append(cur)

    # ---- 合并“同一句字幕被短暂打断 / 位移”造成的多个分组 ----
    merged = []
    for g in groups:
        if merged and _same_text(merged[-1]["mask"], g["mask"]) \
                and g["t0"] - merged[-1]["t1"] <= REPEAT_GAP:
            merged[-1]["t1"] = g["t1"]
            if g["best_cnt"] > merged[-1]["best_cnt"]:
                merged[-1].update(best_t=g["best_t"], best_cnt=g["best_cnt"], mask=g["mask"])
            continue
        merged.append(g)

    log(f"· 去重完成：{len(recs)} 帧 → {len(merged)} 句不同字幕")

    # ---- 丢掉“不像台词”的分组（整块 UI 亮条 / 几乎空白） ----
    if merged:
        med_cnt = float(np.median([g["best_cnt"] for g in merged]))
        keep = []
        for g in merged:
            if _glyph_count(g["mask"]) == 0 or g["best_cnt"] < max(60, 0.05 * med_cnt):
                log(f"· 已忽略 1 处非字幕画面（t≈{g['best_t']:.1f}s）")
                continue
            keep.append(g)
        merged = keep

    if not merged:
        raise RuntimeError("去重后没有剩余字幕帧，请先确认时间区间和字幕区域；"
                           "低对比白字无描边 / 彩色字幕可能暂不支持，请换用白字黑边字幕素材")

    # ---- 单次顺序解码：每组在其代表时间附近挑字形最完整的一帧 ----
    # 分析区域 gy1/gy2 已在前面按「字幕带 + 裁切范围」确定，这里不再重新缩小
    wanted = []            # (group_index, 起帧, 止帧)
    for i, g in enumerate(merged):
        f0 = int(round(max(0.0, g["best_t"] - 0.08) * fps))
        f1 = int(round((g["best_t"] + 0.08) * fps))
        wanted.append((i, f0, f1))
    best = [None] * len(merged)                 # dict(cnt, roi, top, bot, t)
    fallback = [None] * len(merged)             # 兜底：不校验文字一致性

    cap = cv2.VideoCapture(video_path)
    idx, wi = 0, 0
    while wi < len(wanted):
        gi, f0, f1 = wanted[wi]
        if idx > f1:
            wi += 1
            continue
        if not cap.grab():
            break
        if idx >= f0:
            ok, frame = cap.retrieve()
            if ok:
                g = merged[gi]
                roi = frame[gy1:gy2].copy()
                mfull = text_mask(roi, limits=limits, outline=outline, glyph_filter=True)
                m = mfull[b0:b1]                            # 只看文字行
                dens = mfull.sum(axis=1).astype(np.float32)  # 行像素量（用于向上/下生长）
                cnt = int(m.sum())
                cand = dict(cnt=cnt, roi=roi, top=None, bot=None, t=idx / fps, mask=m)
                # 必须和该组的代表文字一致，避免把上一句的帧当成这一句
                if _same_text(g["mask"], m):
                    rec = best[gi]
                    if rec is None or cnt > rec["cnt"]:
                        top, bot = _grow_text_rows(dens, b0, b1)
                        cand["top"], cand["bot"] = top, bot
                        best[gi] = cand
                rec = fallback[gi]
                if rec is None or cnt > rec["cnt"]:
                    top, bot = _grow_text_rows(dens, b0, b1)
                    cand["top"], cand["bot"] = top, bot
                    fallback[gi] = cand
        idx += 1
    cap.release()

    for i in range(len(merged)):
        if best[i] is None:
            best[i] = fallback[i]
    ok_recs = [r for r in best if r is not None]
    if not ok_recs:
        raise RuntimeError("未能取到任何字幕帧，请检查时间区间或字幕区域")

    # ---- 最终裁切：以检测到的文字行为准，并按所有保留帧的真实文字边缘兜底 ----
    top = min(r["top"] for r in ok_recs)
    bot = max(r["bot"] for r in ok_recs)
    text_top = min(gy1 + top, band_y1)
    text_bot = max(gy1 + bot, band_y2 - 1)
    text_h = max(1, text_bot - text_top + 1)
    pad = int(max(8, round(text_h * 0.22)))
    cy1, cy2 = crop_y1, crop_y2
    if allow_expand:
        # 个别帧文字比检测范围更高/更低时自动扩展，最多再扩一个文字带高度，避免截断
        cy1 = max(max(0, band_y1 - band_h), min(cy1, text_top - pad))
        cy2 = min(min(h, band_y2 + band_h), max(cy2, text_bot + 1 + pad))
    if crop_horizontal:
        cx1, cx2 = xa, xb
    else:
        cx1, cx2 = 0, w
    log(f"· 最终裁切：y {cy1} ~ {cy2}（高 {cy2 - cy1}px），x {cx1} ~ {cx2}")

    # 防御：把裁切窗口夹在分析区域内，绝不用负索引（负索引会从数组尾部取数，
    # 表现出来就是“字幕条内容跑到画面最底部、上面一行被切掉”）
    roi_h = gy2 - gy1
    s1 = max(0, min(cy1 - gy1, roi_h - 1))
    s2 = max(s1 + 1, min(cy2 - gy1, roi_h))
    if (s1, s2) != (cy1 - gy1, cy2 - gy1):
        log(f"! 裁切范围超出分析区域，已收敛为 {s1} ~ {s2}")

    frames, kept_times = [], []
    for i, g in enumerate(merged):
        r = best[i]
        if r is None:
            continue
        sub = r["roi"][s1:s2, cx1:cx2]
        frames.append(Image.fromarray(cv2.cvtColor(sub, cv2.COLOR_BGR2RGB)))
        kept_times.append(round(r["t"], 2))

    if not frames:
        raise RuntimeError("去重后没有剩余字幕帧，请先确认时间区间和字幕区域；"
                           "低对比白字无描边 / 彩色字幕可能暂不支持，请换用白字黑边字幕素材")
    return frames, kept_times, (cx1, cy1, cx2, cy2)


# --------------------------------------------------------------------------
# 4. 拼接导出
# --------------------------------------------------------------------------

def prepare_cover(frame, crop_box):
    """按最终半开裁切窗口保留首帧上方画面，与字幕条同宽。"""
    cx1, _cy1, cx2, cy2 = crop_box
    return frame.crop((cx1, 0, cx2, max(1, min(cy2, frame.height))))


def plan_cover(frames, first_frame_full, cover_ok):
    """仅在封面可用时替代首条字幕，取封面失败时保留全部字幕。"""
    if first_frame_full and cover_ok:
        return list(frames[1:]), True
    return list(frames), False


def stitch_images(crops, out_path, cover=None, separator=True,
                  sep_color=(38, 38, 40), log=print):
    """把字幕条垂直拼接成一张长图。cover 为可选的首帧完整画面。"""
    if not crops and cover is None:
        raise RuntimeError("没有可拼接的字幕帧")

    width = crops[0].width if crops else cover.width
    strip_h = crops[0].height if crops else 0
    crops = [c for c in crops if c.size == (width, strip_h)]
    if cover is not None and cover.width != width:
        log(f"! 封面宽度 {cover.width}px 与字幕条 {width}px 不一致，已跳过封面")
        cover = None
    cover_h = cover.size[1] if cover is not None else 0
    sep = 2 if separator else 0

    # 首帧整图与第一条字幕之间也要有分隔线，否则首帧底部（字幕下方的 UI/空白）
    # 会被误认为“多拼了一条空字幕”
    sep_cover = sep if (cover is not None and crops) else 0
    total_h = (cover_h + sep_cover + len(crops) * strip_h
               + sep * (len(crops) - 1 if len(crops) > 1 else 0))
    canvas = Image.new("RGB", (width, total_h), (0, 0, 0))

    y = 0
    if cover is not None:
        canvas.paste(cover, (0, 0))
        y = cover_h
        if sep_cover:
            canvas.paste(Image.new("RGB", (width, sep_cover), sep_color), (0, y))
            y += sep_cover
    for i, c in enumerate(crops):
        if i and sep:
            canvas.paste(Image.new("RGB", (width, sep), sep_color), (0, y))
            y += sep
        canvas.paste(c, (0, y))
        y += strip_h

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    canvas.save(out_path)
    extra = f"首帧完整画面 + {len(crops)} 条字幕条" if cover is not None else f"{len(crops)} 条字幕条"
    log(f"√ 导出成功：{os.path.abspath(out_path)}（{width}×{total_h}，{extra}）")
    return out_path


# --------------------------------------------------------------------------
# 5. 一键流水线
# --------------------------------------------------------------------------

def run_pipeline(cfg, log=print, progress=None):
    """
    完整流程。cfg 至少包含 url / start / end / output_dir / filename / region_mode 等。
    返回 dict(path=..., count=..., region=...)
    """
    url = (cfg.get("url") or "").strip()
    start = (cfg.get("start") or "").strip()
    end = (cfg.get("end") or "").strip()
    if not url or not start or not end:
        raise ValueError("请完整填写视频链接和时间区间")

    # 允许直接传「【标题】链接」：拆出干净链接，保证缓存键稳定
    parsed_url, _link_title = parse_link_input(url)
    if parsed_url:
        url = parsed_url

    output_dir = cfg.get("output_dir") or os.path.join(APP_DIR, "output")
    os.makedirs(output_dir, exist_ok=True)

    work_dir = cache_dir_for(url, start, end)
    video_path = download_segment(url, start, end, work_dir, log=log,
                                  cookies_browser=cfg.get("cookies_browser", "无"))
    if progress:
        progress(0.35)

    info = probe_video(video_path)
    log(f"· 片段信息：{info['width']}×{info['height']}，{info['duration']:.1f}s，{info['fps']:.2f}fps")

    if cfg.get("region_mode") == "manual" and cfg.get("top_ratio") is not None \
            and cfg.get("bottom_ratio") is not None:
        h = info["height"]
        y1 = int(round(float(cfg["top_ratio"]) * h))
        y2 = int(round(float(cfg["bottom_ratio"]) * h))
        y1, y2 = max(0, min(y1, h - 8)), max(y1 + 8, min(y2, h))
        log(f"· 使用手动指定字幕区域：y = {y1} ~ {y2}")
        region = dict(y1=y1, y2=y2, text_y1=y1, text_y2=y2, fallback=False,
                      coverage=None, top_ratio=y1 / h, bottom_ratio=y2 / h, manual=True,
                      outline=_select_manual_outline(video_path, y1, y2, info, log=log))
    else:
        region = detect_subtitle_region(video_path, log=log)
    if progress:
        progress(0.45)

    band_y1 = region.get("text_y1", region["y1"])
    band_y2 = region.get("text_y2", region["y2"])
    frames, times, crop = extract_subtitle_frames(
        video_path, band_y1, band_y2,
        crop_y1=region["y1"], crop_y2=region["y2"],
        log=log, crop_horizontal=bool(cfg.get("crop_horizontal")),
        allow_expand=not region.get("manual", False), outline=region["outline"])
    region["final_crop"] = crop
    if progress:
        progress(0.85)

    want_cover = bool(cfg.get("first_frame_full", True))
    cover = None
    if want_cover:
        frame = grab_frame(video_path, times[0])
        if frame is not None:
            cover = prepare_cover(frame, crop)
        else:
            log("! 未能取得首帧封面，已保留首条字幕")
    strips, use_cover = plan_cover(frames, want_cover, cover is not None)
    if not use_cover:
        cover = None

    name = (cfg.get("filename") or "").strip() or "bilibili_台词拼接"
    out_path = build_output_path(output_dir, name,
                                 timestamp=bool(cfg.get("filename_timestamp")),
                                 auto_number=bool(cfg.get("auto_number", False)))
    log(f"· 输出文件名：{os.path.basename(out_path)}")

    stitch_images(strips, out_path, cover=cover,
                  separator=bool(cfg.get("separator", True)), log=log)
    if progress:
        progress(1.0)

    if not cfg.get("keep_cache", False):
        shutil.rmtree(work_dir, ignore_errors=True)
        log("· 已清理临时视频")

    return dict(path=os.path.abspath(out_path), count=len(frames),
                region=region, times=times)
