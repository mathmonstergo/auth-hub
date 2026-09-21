#!/usr/bin/env python3
"""auth-hub 凭据 → MediaCrawler 桥接器

把 ~/.hermes/ 下统一存放的平台凭据读出来直接喂给 MediaCrawler 跑一次采集，
不用手工抄 cookie（凭据仍是 auth-hub 那一份，这里只读不改）。

用法:
    mc_creds.py <平台> <模式> <目标> [额外的 MediaCrawler 参数...]

示例:
    mc_creds.py wb   detail 5345298768724625     # 爬这条微博的一级+二级评论
    mc_creds.py bili detail BV1GJ411x7h7         # 爬这个视频的评论
    mc_creds.py xhs  search "重庆 火锅"           # 关键词搜索后爬评论
    mc_creds.py dy   detail 7525082444551310602 --headless no
    mc_creds.py tieba detail <帖子ID>            # 爬贴吧帖子的评论
    mc_creds.py zhihu detail <内容ID>            # 爬知乎回答/文章的评论

平台: xhs | dy | bili | wb | tieba | zhihu
模式: detail（按帖子 ID）| search（按关键词）| creator（按创作者）
"""

import json
import os
import subprocess
import sys

HERMES_DIR = "/home/ubuntu/.hermes"
MC_DIR = "/home/ubuntu/projects/MediaCrawler"
MC_PYTHON = os.path.join(MC_DIR, ".venv", "bin", "python")

CRED_PATHS = {
    "xhs": os.path.join(HERMES_DIR, "xhs_cookie.json"),
    "dy": os.path.join(HERMES_DIR, "douyin_cookie.json"),
    "bili": os.path.join(HERMES_DIR, "bili_cookie.json"),
    "wb": os.path.join(HERMES_DIR, "weibo_cookie.json"),
    "tieba": os.path.join(HERMES_DIR, "tieba_cookie.json"),
    "zhihu": os.path.join(HERMES_DIR, "zhihu_cookie.json"),
}


def read_cookie(platform: str) -> str:
    path = CRED_PATHS.get(platform)
    if not path:
        sys.exit(f"不支持的平台：{platform}（可选：{'、'.join(CRED_PATHS)}）")
    if not os.path.exists(path):
        sys.exit(f"没找到 {platform} 的凭据：{path}（先去 https://auth.iamsen.com/067b4abf303c28f7/ 保存一次）")
    with open(path, "r", encoding="utf-8") as f:
        cookie_str = json.load(f).get("cookie_str", "")
    if not cookie_str:
        sys.exit(f"{path} 里的 cookie 是空的")
    return cookie_str


def main():
    args = sys.argv[1:]
    if len(args) < 3:
        print(__doc__)
        sys.exit(1)

    platform, mode, target = args[0], args[1], args[2]
    extra = args[3:]

    if mode not in ("detail", "search", "creator"):
        sys.exit(f"模式只能是 detail / search / creator，收到：{mode}")

    if platform == "dy":
        sys.exit(
            "MediaCrawler 的抖音链路要一个能正常打开 douyin.com 的浏览器页面来算签名，\n"
            "而本机浏览器访问 douyin.com 会直接被 403（curl 同 UA 却是 200，属于浏览器指纹被拦）。\n"
            "抖音评论请改用同目录下的纯 HTTP 版：/opt/auth-hub/dy_comments.py <视频ID>"
        )

    cmd = [
        MC_PYTHON, "main.py",
        "--platform", platform,
        "--lt", "cookie",
        "--type", mode,
        "--cookies", read_cookie(platform),
        "--get_comment", "yes",
        "--get_sub_comment", "yes",
        "--save_data_option", "json",
        "--headless", "yes",
    ]
    if mode == "search":
        cmd += ["--keywords", target]
    elif mode == "detail":
        cmd += ["--specified_id", target]
    else:
        cmd += ["--creator_id", target]
    cmd += extra

    print(f"[mc_creds] {platform} / {mode} / {target}（cookie 取自 auth-hub，已隐藏）")
    os.chdir(MC_DIR)
    raise SystemExit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
