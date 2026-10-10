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
import re
import shutil
import sys
import tempfile

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

import subtitle_core as sc

FAIL = []


def eq(label, got, want):
    ok = got == want
    print(("√ " if ok else "× ") + f"{label}: {got!r}" + ("" if ok else f"  (期望 {want!r})"))
    if not ok:
        FAIL.append(label)


SAMPLE = ("【百K亚煞极电表神明，刷神明最快打法】"
          "https://www.bilibili.com/video/BV1aBhR69Ef6?vd_source=14d51abe2563768e9569d025bf3a3d98")

# 实测反馈（BV1yThazdENF）：客户端分享的标题**自己带一层括号**，
# 旧版用 [^【】]{1,200}? 配不上外层，只抠出内层「小岛秀夫·补完计划 第1话」，
# 用户看到的现象是「粘进网址栏的长标题被截断了」。
NESTED = ("【拍电影写小说一事无成，小岛秀夫有多废柴【小岛秀夫·补完计划 第1话】】"
          "https://www.bilibili.com/video/BV1yThazdENF?vd_source=14d51abe2563768e9569d025bf3a3d98")
NESTED_TITLE = "拍电影写小说一事无成，小岛秀夫有多废柴【小岛秀夫·补完计划 第1话】"
NESTED_URL = "https://www.bilibili.com/video/BV1yThazdENF"


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
        print(("√ " if ok else "× ") + f"时间戳+编号: {ts}")
        if not ok:
            FAIL.append("时间戳+编号")
        eq("非法字符前缀", os.path.basename(sc.build_output_path(tmp, "a/b:c", auto_number=True)), "a_b_c_01.png")
        eq("空前缀兜底", os.path.basename(sc.build_output_path(tmp, "", auto_number=True)),
           "bilibili_台词拼接_01.png")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_gui():
    print("\n== GUI 粘贴 / 文件名自动跟随 ==")
    try:
        import gui_app
        app = gui_app.App()
    except Exception as e:                       # 无显示环境 / 缺依赖时跳过
        print(f"! 跳过 GUI 测试（无法创建窗口：{e}）")
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
        app.destroy()


if __name__ == "__main__":
    test_parse_link_input()
    test_build_output_path()
    test_gui()
    print("\n结果：" + ("全部通过" if not FAIL else f"失败 {len(FAIL)} 项 -> {FAIL}"))
    sys.exit(1 if FAIL else 0)
