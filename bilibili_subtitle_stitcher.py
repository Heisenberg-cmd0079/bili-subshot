# -*- coding: utf-8 -*-
# Required Notice: Copyright 2026 Heisenberg-cmd0079
# Licensed under the PolyForm Noncommercial License 1.0.0 (see LICENSE).
# 禁止商业使用；需要商业授权请联系作者。
"""
B站台词拼接 —— 命令行版
========================

例：
    python -X utf8 bilibili_subtitle_stitcher.py ^
        --url "https://www.bilibili.com/video/BV1qnuq6dEga/" ^
        --start 00:21:41 --end 00:22:35 ^
        --out "output" --name 台词拼接

不带参数运行会使用 config.json 里的设置（与图形界面共用同一份配置）。
"""

import argparse
import os
import sys

import subtitle_core as sc


def main(argv=None):
    cfg = sc.load_config()
    p = argparse.ArgumentParser(description="B站台词拼接（自动识别字幕区域 + 智能去重）")
    p.add_argument("--url", default=cfg["url"] or "https://www.bilibili.com/video/BV1xx411c7mD",
                   help="视频链接；也可直接粘贴「【标题】https://…」整段内容")
    p.add_argument("--start", default=cfg["start"], help="开始时间，如 00:21:41")
    p.add_argument("--end", default=cfg["end"], help="结束时间，如 00:22:35")
    p.add_argument("--out", default=cfg["output_dir"], help="导出文件夹")
    p.add_argument("--name", default=cfg["filename"], help="输出文件名前缀（不带编号与扩展名）")
    p.add_argument("--title", default=None, help="文件名前缀（优先于 --name 与链接自带标题）")
    p.add_argument("--no-number", dest="auto_number", action="store_false",
                   default=bool(cfg.get("auto_number", True)),
                   help="不加自动编号（前缀_01）；关闭后同名文件会被覆盖")
    p.add_argument("--number", dest="auto_number", action="store_true",
                   default=argparse.SUPPRESS, help="强制加自动编号")
    p.add_argument("--cookies", default=cfg.get("cookies_browser", "无"),
                   choices=sc.COOKIE_CHOICES, help="用浏览器 cookies 拉取更高码率")
    p.add_argument("--no-cover", action="store_true", help="首帧不做完整画面")
    p.add_argument("--no-separator", action="store_true", help="字幕条之间不加分隔线")
    p.add_argument("--crop-horizontal", action="store_true", help="横向裁切到字幕文字范围")
    p.add_argument("--keep-cache", action="store_true", help="结束后保留下载的临时视频")
    p.add_argument("--region", default=None,
                   help="手动指定字幕区域（画面高度百分比），如 80,92；不填则自动识别")
    args = p.parse_args(argv)

    # 支持直接粘贴「【标题】链接」：自动拆出链接与标题，标题用作文件名前缀
    url, link_title = sc.parse_link_input(args.url)
    if not url:
        url = args.url.strip()

    cfg.update(dict(
        url=url, start=args.start, end=args.end,
        output_dir=args.out,
        filename=args.title or link_title or args.name,
        auto_number=args.auto_number,
        cookies_browser=args.cookies,
        first_frame_full=not args.no_cover,
        separator=not args.no_separator,
        crop_horizontal=args.crop_horizontal,
        keep_cache=args.keep_cache,
        open_after=False,
    ))
    if args.region:
        a, b = (float(x) for x in args.region.replace("，", ",").split(","))
        cfg["region_mode"] = "manual"
        cfg["top_ratio"], cfg["bottom_ratio"] = a / 100.0, b / 100.0
    else:
        cfg["region_mode"] = "auto"

    try:
        result = sc.run_pipeline(cfg, log=lambda m: print(m, flush=True))
    except Exception as e:
        print(f"\n× 失败：{e}", file=sys.stderr)
        return 1
    print(f"\n√ 完成：{result['path']}（{result['count']} 句字幕）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
