# -*- coding: utf-8 -*-
r"""
B站台词拼接神器 —— 回归测试（链接解析 / 自动编号命名 / GUI 粘贴行为）

跑法：
    python -X utf8 tests_naming.py

覆盖：
    1. parse_link_input()  —— 客户端复制的「【标题】链接」、纯链接、b23 短链、裸 BV 号、
                              书名号/方括号标题、标题里的书名号、非法字符清理、参数保留
    2. build_output_path() —— 标题_01 起编、沿用最大编号、不被无编号旧图干扰、
                              关闭编号、加时间戳、空前缀兜底、非法字符
    3. GUI                 —— 粘贴后拆标题、前缀自动跟随、手改后不被覆盖、
                              「从链接取标题」按钮、后台标题回填与过期丢弃
"""

import os
import importlib.util
import io
import contextlib
from importlib.machinery import SourceFileLoader
import tkinter as tk
import re
import shutil
import sys
import tempfile
from unittest.mock import patch

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

import subtitle_core as sc
import numpy as np
from PIL import Image

FAIL = []
CHECKS = 0


def eq(label, got, want):
    global CHECKS
    CHECKS += 1
    ok = got == want
    print(("√ " if ok else "× ") + f"{label}: {got!r}" + ("" if ok else f"  (期望 {want!r})"))
    if not ok:
        FAIL.append(label)


SAMPLE = ("【百K亚煞极电表神明，刷神明最快打法】"
          "https://www.bilibili.com/video/BV1aBhR69Ef6?vd_source=example")

# 实测反馈：客户端分享的标题**自己带一层括号**（副标题），
# 旧版用 [^【】]{1,200}? 配不上外层，只抠出内层的「第一话」，
# 用户看到的现象是「粘进网址栏的长标题被截断了」。
NESTED = ("【示例：某视频的完整标题【第一话】】"
          "https://www.bilibili.com/video/BV1xx411c7mD?vd_source=example")
NESTED_TITLE = "示例：某视频的完整标题【第一话】"
NESTED_URL = "https://www.bilibili.com/video/BV1xx411c7mD"


def test_parse_link_input():
    print("== parse_link_input ==")
    eq("客户端粘贴（带标题）", sc.parse_link_input(SAMPLE),
       ("https://www.bilibili.com/video/BV1aBhR69Ef6", "百K亚煞极电表神明，刷神明最快打法"))
    eq("标题+空格+链接", sc.parse_link_input("【测试标题】 https://www.bilibili.com/video/BV1aBhR69Ef6?vd_source=abc&p=2"),
       ("https://www.bilibili.com/video/BV1aBhR69Ef6?p=2", "测试标题"))
    eq("纯链接", sc.parse_link_input("https://www.bilibili.com/video/BV1aBhR69Ef6?vd_source=x"),
       ("https://www.bilibili.com/video/BV1aBhR69Ef6", ""))
    eq("只有BV号", sc.parse_link_input("BV1aBhR69Ef6"),
       ("https://www.bilibili.com/video/BV1aBhR69Ef6", ""))
    eq("短链+方括号标题", sc.parse_link_input("【某个视频】https://b23.tv/abcdEFG"),
       ("https://b23.tv/abcdEFG", "某个视频"))
    eq("书名号标题", sc.parse_link_input("《某视频》https://www.bilibili.com/video/BV1aBhR69Ef6"),
       ("https://www.bilibili.com/video/BV1aBhR69Ef6", "某视频"))
    eq("标题里的书名号", sc.parse_link_input("【看完《进击》之后】https://www.bilibili.com/video/BV1aBhR69Ef6"),
       ("https://www.bilibili.com/video/BV1aBhR69Ef6", "看完《进击》之后"))
    eq("非法字符被清理", sc.parse_link_input('【a/b:c*d?e"f<g>h|i】https://www.bilibili.com/video/BV1aBhR69Ef6')[1],
       "a_b_c_d_e_f_g_h_i")
    eq("空输入", sc.parse_link_input("   "), ("", ""))
    eq("网址前后有文字", sc.parse_link_input("分享自B站 https://www.bilibili.com/video/BV1aBhR69Ef6 快来看"),
       ("https://www.bilibili.com/video/BV1aBhR69Ef6", "分享自B站 快来看"))
    eq("保留 p 参数", sc.parse_link_input("https://www.bilibili.com/video/BV1aBhR69Ef6?p=3&vd_source=z")[0],
       "https://www.bilibili.com/video/BV1aBhR69Ef6?p=3")
    # 用户没清空输入框就直接粘贴新链接：取标题后面那个（新的）链接
    eq("旧链接+新标题链接", sc.parse_link_input(
        "https://www.bilibili.com/video/BV1aBhR69Ef6?vd_source=old"
        "【新视频】https://www.bilibili.com/video/BV1nKTC6CEBq?vd_source=new"),
       ("https://www.bilibili.com/video/BV1nKTC6CEBq", "新视频"))
    eq("两条纯链接取后一条", sc.parse_link_input(
        "https://www.bilibili.com/video/BV1aBhR69Ef6 https://www.bilibili.com/video/BV1nKTC6CEBq")[0],
       "https://www.bilibili.com/video/BV1nKTC6CEBq")
    # 标题嵌套括号：必须取最外层，一个字都不能少
    eq("嵌套括号标题（整段不丢字）", sc.parse_link_input(NESTED), (NESTED_URL, NESTED_TITLE))
    eq("嵌套两层括号", sc.parse_link_input("【外层【内层】后记】https://www.bilibili.com/video/BV1aBhR69Ef6"),
       ("https://www.bilibili.com/video/BV1aBhR69Ef6", "外层【内层】后记"))
    eq("右括号缺失时不丢链接",
       sc.parse_link_input("【还没配对的标题https://www.bilibili.com/video/BV1aBhR69Ef6")[0],
       "https://www.bilibili.com/video/BV1aBhR69Ef6")
    eq("长标题（超 200 字）不吞链接",
       sc.parse_link_input("【" + "很长" * 120 + "】https://www.bilibili.com/video/BV1aBhR69Ef6")[0],
       "https://www.bilibili.com/video/BV1aBhR69Ef6")


def test_build_output_path():
    print("\n== build_output_path（自动编号） ==")
    tmp = tempfile.mkdtemp(prefix="zimu_name_")
    try:
        p1 = sc.build_output_path(tmp, "百K亚煞极电表神明，刷神明最快打法", auto_number=True)
        eq("首张编号", os.path.basename(p1), "百K亚煞极电表神明，刷神明最快打法_01.png")
        open(p1, "wb").close()
        p2 = sc.build_output_path(tmp, "百K亚煞极电表神明，刷神明最快打法", auto_number=True)
        eq("第二张编号", os.path.basename(p2), "百K亚煞极电表神明，刷神明最快打法_02.png")
        open(p2, "wb").close()
        open(os.path.join(tmp, "已存在的标题.png"), "wb").close()
        eq("已有同名旧图时另起编号",
           os.path.basename(sc.build_output_path(tmp, "已存在的标题", auto_number=True)),
           "已存在的标题_01.png")
        eq("另一个标题从01开始",
           os.path.basename(sc.build_output_path(tmp, "别的标题", auto_number=True)), "别的标题_01.png")
        open(os.path.join(tmp, "标题B_05.png"), "wb").close()
        eq("沿用最大编号",
           os.path.basename(sc.build_output_path(tmp, "标题B", auto_number=True)), "标题B_06.png")
        eq("关闭编号",
           os.path.basename(sc.build_output_path(tmp, "某标题", auto_number=False)), "某标题.png")
        ts = os.path.basename(sc.build_output_path(tmp, "某标题", timestamp=True, auto_number=True))
        ok = bool(re.match(r"^某标题_\d{2}_\d{8}_\d{6}\.png$", ts))
        eq("时间戳+编号格式", ok, True)
        eq("非法字符前缀", os.path.basename(sc.build_output_path(tmp, "a/b:c", auto_number=True)), "a_b_c_01.png")
        eq("空前缀兜底", os.path.basename(sc.build_output_path(tmp, "", auto_number=True)),
           "bilibili_台词拼接_01.png")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_stitch_contract():
    print("\n== stitch_contract ==")
    frame = Image.new("RGB", (800, 400), "red")
    frame.paste((0, 255, 0), (500, 0, 800, 400))
    cover = sc.prepare_cover(frame, (500, 0, 800, 360))
    eq("封面与字幕条同宽", cover.size, (300, 360))
    eq("封面取自正确横坐标", cover.getpixel((0, 0)), (0, 255, 0))
    eq("封面高度不超过首帧", sc.prepare_cover(frame, (500, 0, 800, 500)).size, (300, 400))
    eq("封面成功才替代首句", sc.plan_cover(["f0", "f1", "f2"], True, True), (["f1", "f2"], True))
    eq("封面失败不丢首句", sc.plan_cover(["f0", "f1", "f2"], True, False), (["f0", "f1", "f2"], False))
    eq("单句只输出封面", sc.plan_cover(["f0"], True, True), ([], True))
    eq("关闭封面保留首句", sc.plan_cover(["f0", "f1"], False, True), (["f0", "f1"], False))
    with tempfile.TemporaryDirectory(prefix="zimu_stitch_") as tmp:
        out = os.path.join(tmp, "test.png")
        sc.stitch_images([], out, cover=Image.new("RGB", (300, 400)), log=lambda *_: None)
        with Image.open(out) as result:
            eq("仅封面可导出", result.size, (300, 400))
        strip = Image.new("RGB", (300, 20), "blue")
        sc.stitch_images([strip], out, cover=cover, log=lambda *_: None)
        with Image.open(out) as result:
            eq("封面加条带含分隔线", result.size, (300, 382))
            eq("拼接后封面仍是正确区域", result.getpixel((0, 0)), (0, 255, 0))
            eq("封面下方分隔线", result.getpixel((0, 360)), (38, 38, 40))
            eq("首条位于分隔线下方", result.getpixel((0, 362)), (0, 0, 255))
        logs = []
        sc.stitch_images([strip], out, cover=Image.new("RGB", (800, 400)), log=logs.append)
        with Image.open(out) as result:
            eq("宽度不匹配时跳过封面", result.size, (300, 20))
        eq("跳过封面有明确日志", any("已跳过封面" in msg for msg in logs), True)
        try:
            sc.stitch_images([], out, log=lambda *_: None)
        except RuntimeError:
            empty_rejected = True
        else:
            empty_rejected = False
        eq("空条带且无封面必须失败", empty_rejected, True)
    mask = np.zeros((10, 300), dtype=bool)
    mask[:, 100:200] = True
    recs = [(0, int(mask.sum()), np.packbits(mask, axis=1), mask.shape)]
    eq("文字横向范围为半开区间", sc._detect_text_xrange(recs, 1, 300), (100, 200))
    eq("无文字时返回完整半开范围", sc._detect_text_xrange([], 1, 300), (0, 300))


def test_text_mask_modes():
    print("\n== text_mask_modes ==")
    dark = np.full((120, 300, 3), 30, dtype=np.uint8)
    dark[70:94, 98:116] = 5
    dark[72:92, 100:114] = 245
    eq("白字黑边保留原有行为", bool(sc.text_mask(dark, outline="dark").any()), True)
    bright = np.full((120, 300, 3), 195, dtype=np.uint8)
    bright[72:92, 100:114] = 245
    eq("亮底不存在暗像素", bool((bright < sc.DARK_V).any()), False)
    eq("亮底严格描边模式为空", int(sc.text_mask(bright, outline="dark").sum()), 0)
    loose = sc.text_mask(bright, outline="none")
    eq("亮底无描边模式非空", bool(loose.any()), True)
    eq("无描边掩码覆盖字块", bool(loose[72:92, 100:114].all()), True)
    eq("亮背景不被当成整片文字", int(loose.sum()), 280)
    plain = np.full((120, 300, 3), 245, dtype=np.uint8)
    eq("纯白底被字形过滤挡掉", int(sc.text_mask(plain, outline="none", glyph_filter=True).sum()), 0)
    eq("关闭字形过滤会保留全部白色", int(sc.text_mask(plain, outline="none", glyph_filter=False).sum()), 36000)
    eq("refine兼容别名", np.array_equal(sc.text_mask(dark, refine=False), sc.text_mask(dark, glyph_filter=False)), True)
    mid = np.full((120, 300, 3), 120, dtype=np.uint8)
    mid[72:92, 100:114] = 245
    eq("无描边模式也保留孤立白字", int(sc.text_mask(mid, outline="none").sum()), 280)
    eq("底部兜底有明确策略", sc._bottom_fallback(120, 300)["outline"], "dark")
    eq("底部兜底保留降级策略", sc._bottom_fallback(120, 300, outline="none")["outline"], "none")
    try:
        sc.text_mask(mid, outline="invalid")
    except ValueError:
        rejected = True
    else:
        rejected = False
    eq("未知描边策略拒绝使用", rejected, True)


class _SyntheticCapture:
    """只从内存合成图取帧，不访问视频文件或编解码器。"""
    def __init__(self, frame, count=50):
        self.frame = frame
        self.count = count
        self.index = -1
        self.released = False

    def isOpened(self):
        return True

    def get(self, prop):
        return {sc.cv2.CAP_PROP_FPS: 25.0,
                sc.cv2.CAP_PROP_FRAME_COUNT: self.count,
                sc.cv2.CAP_PROP_FRAME_WIDTH: self.frame.shape[1],
                sc.cv2.CAP_PROP_FRAME_HEIGHT: self.frame.shape[0]}[prop]

    def grab(self):
        self.index += 1
        return self.index < self.count

    def retrieve(self):
        return True, self.frame.copy()

    def release(self):
        self.released = True


def test_outline_propagation():
    print("\n== outline_propagation（内存合成帧） ==")
    bright = np.full((120, 300, 3), 195, dtype=np.uint8)
    bright[72:92, 100:114] = 245
    captures = []
    def capture(_path):
        cap = _SyntheticCapture(bright)
        captures.append(cap)
        return cap
    with patch.object(sc.cv2, "VideoCapture", side_effect=capture):
        with patch.object(sc, "text_mask", wraps=sc.text_mask) as masks:
            region = sc.detect_subtitle_region("memory-only", log=lambda *_: None)
            eq("自动检测保存无描边策略", region["outline"], "none")
            detection_calls = len(masks.call_args_list)
            frames, times, crop = sc.extract_subtitle_frames(
                "memory-only", region["text_y1"], region["text_y2"],
                crop_y1=region["y1"], crop_y2=region["y2"], outline=region["outline"],
                crop_horizontal=True, log=lambda *_: None)
        eq("两轮抽帧均完成并保留字幕", (len(frames), len(times)), (1, 1))
        eq("两轮抽帧同宽裁切", frames[0].width, crop[2] - crop[0])
        # 最后一次检测调用之后的所有掩码调用均属于两轮抽帧。
        extract_calls = masks.call_args_list[detection_calls:]
        eq("抽帧含两轮掩码计算", len(extract_calls) > 8, True)
        eq("两轮掩码沿用无描边策略", all(c.kwargs.get("outline") == "none" for c in extract_calls), True)
        eq("两轮掩码保持字形过滤", all(c.kwargs.get("glyph_filter") is True for c in extract_calls), True)
        info = dict(height=120, width=300, fps=25.0, duration=2.0)
        eq("手动区域选择无描边策略", sc._select_manual_outline("memory-only", 60, 105, info, log=lambda *_: None), "none")
    eq("合成捕获器全部释放", all(c.released for c in captures), True)
    dark = np.full((120, 300, 3), 30, dtype=np.uint8)
    dark[70:94, 98:116] = 5
    dark[72:92, 100:114] = 245
    with patch.object(sc.cv2, "VideoCapture", side_effect=lambda *_: _SyntheticCapture(dark)):
        region = sc.detect_subtitle_region("memory-only", log=lambda *_: None)
        eq("自动检测优先保留描边策略", region["outline"], "dark")
        eq("手动区域优先保留描边策略", sc._select_manual_outline("memory-only", 60, 105, info, log=lambda *_: None), "dark")
    with patch.object(sc.cv2, "VideoCapture", side_effect=lambda *_: _SyntheticCapture(np.full_like(bright, 245))):
        logs = []
        region = sc.detect_subtitle_region("memory-only", log=logs.append)
        eq("两种判据失败仍保留降级策略", (region["fallback"], region["outline"]), (True, "none"))
        eq("失败提示说明支持限制", any("暂不支持" in message for message in logs), True)


def test_gui_failure_contract():
    """注入加载/窗口故障，验证失败不会被当成正常跳过。"""
    global CHECKS
    print("\n== GUI 失败与跳过契约 ==")
    module = sys.modules[__name__]
    def probe(loader, required="0", platform="linux"):
        global CHECKS
        before, checks = list(FAIL), CHECKS
        output = io.StringIO()
        with patch.object(module, "_load_gui_app", loader), \
                patch.dict(os.environ, {"ZIMU_REQUIRE_GUI": required}), \
                patch.object(sys, "platform", platform), contextlib.redirect_stdout(output):
            test_gui()
        failed = len(FAIL) > len(before)
        FAIL[:] = before
        CHECKS = checks
        return failed, "跳过 GUI" in output.getvalue()
    def broken_import():
        raise ImportError("injected missing module")
    class BrokenWindow:
        def App(self):
            raise RuntimeError("injected initialization bug")
    class NoDisplay:
        def App(self):
            raise tk.TclError("no display name and no $DISPLAY environment variable")
    eq("模块加载失败必须失败", probe(broken_import), (True, False))
    eq("普通窗口错误必须失败", probe(lambda: BrokenWindow()), (True, False))
    eq("仅缺显示环境允许跳过", probe(lambda: NoDisplay()), (False, True))
    eq("强制GUI时缺显示必须失败", probe(lambda: NoDisplay(), required="1"), (True, False))
    eq("Windows窗口异常不按Linux缺显示跳过", probe(lambda: NoDisplay(), platform="win32"), (True, False))


def _load_gui_app():
    """按路径加载 .pyw，不依赖平台的源码扩展名注册。"""
    loader = SourceFileLoader("gui_app", os.path.join(APP_DIR, "gui_app.pyw"))
    spec = importlib.util.spec_from_loader("gui_app", loader)
    mod = importlib.util.module_from_spec(spec)
    previous = sys.modules.get("gui_app")
    sys.modules["gui_app"] = mod
    try:
        loader.exec_module(mod)
    except Exception:
        if previous is None:
            sys.modules.pop("gui_app", None)
        else:
            sys.modules["gui_app"] = previous
        raise
    return mod


def _missing_display(exc):
    return (sys.platform != "win32" and isinstance(exc, tk.TclError)
            and any(text in str(exc).lower() for text in (
                "no display name and no $display environment variable",
                "couldn't connect to display")))


def test_gui():
    print("\n== GUI 粘贴 / 文件名自动跟随 ==")
    start_checks = CHECKS
    # 测试进程内永久使用离线桩，后台线程结束前也不会恢复真实网络函数。
    sc.fetch_video_title = lambda *args, **kwargs: ""
    try:
        gui_app = _load_gui_app()
    except Exception as e:
        eq(f"GUI 模块加载失败：{type(e).__name__}: {e}", False, True)
        return
    try:
        app = gui_app.App()
    except Exception as e:
        if _missing_display(e) and os.environ.get("ZIMU_REQUIRE_GUI") != "1":
            print(f"! 跳过 GUI 测试（缺少显示环境：{e}；GUI 执行 0 条检查）")
        else:
            eq(f"GUI 窗口创建失败：{type(e).__name__}: {e}", False, True)
        return
    try:
        app.update()
        app.url_entry.delete(0, "end")
        app.url_entry.insert(0, SAMPLE)
        app._on_url_input()
        app.update()
        eq("网址栏已清干净", app.url_entry.get(), "https://www.bilibili.com/video/BV1aBhR69Ef6")
        eq("文件名前缀=标题", app.name_entry.get(), "百K亚煞极电表神明，刷神明最快打法")

        app.url_entry.delete(0, "end")
        app.url_entry.insert(0, "【第二个视频】https://www.bilibili.com/video/BV1nKTC6CEBq?vd_source=zzz")
        app._on_url_input()
        app.update()
        eq("换链接后前缀更新", app.name_entry.get(), "第二个视频")
        eq("编号开关默认开", bool(app.var_auto_num.get()), True)

        app.name_entry.delete(0, "end")
        app.name_entry.insert(0, "我自己起的名字")
        app._mark_title_manual()
        app.url_entry.delete(0, "end")
        app.url_entry.insert(0, "【第三个视频】https://www.bilibili.com/video/BV1qnuq6dEga")
        app._on_url_input()
        app.update()
        eq("手改过的前缀被保留", app.name_entry.get(), "我自己起的名字")

        app.name_entry.delete(0, "end")
        app.url_entry.delete(0, "end")
        app.url_entry.insert(0, "【第四个视频】https://www.bilibili.com/video/BV1gBeN6HEMs")
        app._on_url_input()
        app.update()
        eq("清空后恢复自动填标题", app.name_entry.get(), "第四个视频")

        app.name_entry.delete(0, "end")
        app._mark_title_manual()
        app.url_entry.delete(0, "end")
        app.url_entry.insert(0, "【第五个视频】https://www.bilibili.com/video/BV1RCYX6bEH5")
        app._on_fetch_title()
        app.update()
        eq("按钮用链接自带标题", app.name_entry.get(), "第五个视频")

        app.name_entry.delete(0, "end")
        app._mark_title_manual()
        app.url_entry.delete(0, "end")
        app.url_entry.insert(0, "https://www.bilibili.com/video/BV1RCYX6bEH5")
        app._on_fetch_title()
        app.update()
        eq("按钮沿用上次标题（不联网）", app.name_entry.get(), "第五个视频")

        app.name_entry.delete(0, "end")
        app._mark_title_manual()
        app.url_entry.delete(0, "end")
        app.url_entry.insert(0, "https://www.bilibili.com/video/BV1aBhR69Ef6")
        app._on_title_fetched("https://www.bilibili.com/video/BV1aBhR69Ef6", "接口返回的标题", True)
        app.update()
        eq("接口标题回填", app.name_entry.get(), "接口返回的标题")

        app.url_entry.delete(0, "end")
        app.url_entry.insert(0, "https://www.bilibili.com/video/BV1gBeN6HEMs")
        app._on_title_fetched("https://www.bilibili.com/video/BV1aBhR69Ef6", "过期标题", True)
        app.update()
        eq("过期标题被丢弃", app.name_entry.get(), "接口返回的标题")

        app.url_entry.delete(0, "end")
        app.url_entry.insert(0, SAMPLE)
        cfg = app._collect()
        app.update()
        eq("_collect 归一化链接", cfg["url"], "https://www.bilibili.com/video/BV1aBhR69Ef6")
        eq("_collect 前缀=标题", cfg["filename"], "百K亚煞极电表神明，刷神明最快打法")
        eq("_collect 带编号开关", cfg["auto_number"], True)

        app.url_entry.delete(0, "end")
        app.url_entry.insert(0, "BV1aBhR69Ef6")
        app._on_url_input()
        app.update()
        eq("裸 BV 号补全链接", app.url_entry.get(), "https://www.bilibili.com/video/BV1aBhR69Ef6")

        # 反馈场景：客户端复制「【长标题【副标题】】链接」，网址栏拆完后
        # 标题必须完整落到「文件名前缀」，并在网址栏下方回显（不是被截断）
        app.url_entry.delete(0, "end")
        app.url_entry.insert(0, NESTED)
        app._on_url_input()
        app.update()
        eq("嵌套标题：网址栏只留干净链接", app.url_entry.get(), NESTED_URL)
        eq("嵌套标题：前缀完整（不再只剩内层）", app.name_entry.get(), NESTED_TITLE)
        eq("嵌套标题：网址栏下方回显完整标题",
           NESTED_TITLE in app.url_hint.cget("text"), True)
        cfg2 = app._collect()
        eq("嵌套标题：_collect 前缀完整", cfg2["filename"], NESTED_TITLE)
        eq("嵌套标题：_collect 链接干净", cfg2["url"], NESTED_URL)
    finally:
        executed = CHECKS - start_checks
        print(f"GUI 执行了 {executed} 条行为检查（期望 19 条）")
        eq("GUI 行为检查数量", executed, 19)
        app.destroy()


if __name__ == "__main__":
    test_parse_link_input()
    test_build_output_path()
    test_stitch_contract()
    test_text_mask_modes()
    test_outline_propagation()
    test_gui_failure_contract()
    test_gui()
    print(f"\n共执行 {CHECKS} 条检查")
    print("\n结果：" + ("全部通过" if not FAIL else f"失败 {len(FAIL)} 项 -> {FAIL}"))
    sys.exit(1 if FAIL else 0)
