#!/home/ubuntu/projects/MediaCrawler/.venv/bin/python
"""抖音评论采集（纯 HTTP 版，不需要浏览器）

为什么不用 MediaCrawler 自带的抖音链路：它依赖一个能正常打开 douyin.com 的浏览器
页面（取 localStorage 里的 msToken、并把签名 JS 注入页面执行），而本机浏览器访问
douyin.com 会被直接 403（curl 同 UA 却 200，属于浏览器指纹被拦）。所以这里改纯 HTTP：

  - a_bogus / sign_reply 由 MediaCrawler 自带的 libs/douyin.js 本地用 execjs 算出
  - 登录态直接复用 auth-hub 统一存放的 ~/.hermes/douyin_cookie.json
  - 请求头与参数按上游 DouYinClient 的口径补齐（x-tt-argus / uifid / 完整设备参数）

用法：
    dy_comments.py <视频ID 或 视频链接> [--max 40] [--no-sub] [--proxy socks5://127.0.0.1:1080] [--json 输出.json]

示例：
    dy_comments.py 7629552148099561829
    dy_comments.py https://www.douyin.com/video/7629552148099561829 --max 100 --json /tmp/dy.json

说明：搜索接口不带签名也能用，但请求稍密就会被静默限流（返回空列表），
所以本脚本只做评论抓取，视频 ID 请另行提供。
"""

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.parse
from datetime import datetime

import execjs
import requests

MC_DIR = "/home/ubuntu/projects/MediaCrawler"
COOKIE_PATH = "/home/ubuntu/.hermes/douyin_cookie.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
REQUEST_INTERVAL = 1.5  # 每次翻页之间的间隔，避免把账号撞进风控


def get_web_id() -> str:
    """生成随机 webid（与上游 help.py 的实现保持一致）"""

    def e(t):
        if t is not None:
            return str(t ^ (int(16 * random.random()) >> (t // 4)))
        return ''.join([str(int(1e7)), '-', str(int(1e3)), '-', str(int(4e3)),
                        '-', str(int(8e3)), '-', str(int(1e11))])

    return ''.join(e(int(x)) if x in '018' else x for x in e(None)).replace('-', '')[:19]


COMMON_PARAMS = {
    "device_platform": "webapp", "aid": "6383", "channel": "channel_pc_web",
    "version_code": "190600", "version_name": "19.6.0", "update_version_code": "170400",
    "pc_client_type": "1", "cookie_enabled": "true", "browser_language": "zh-CN",
    "browser_platform": "MacIntel", "browser_name": "Chrome", "browser_version": "125.0.0.0",
    "browser_online": "true", "engine_name": "Blink", "os_name": "Mac OS", "os_version": "10.15.7",
    "cpu_core_num": "8", "device_memory": "8", "engine_version": "109.0", "platform": "PC",
    "screen_width": "2560", "screen_height": "1440", "effective_type": "4g", "round_trip_time": "50",
}


class DouyinComments:
    def __init__(self, proxy: str = None):
        self.proxies = {"http": proxy, "https": proxy} if proxy else None
        if not os.path.exists(COOKIE_PATH):
            sys.exit(f"没找到抖音凭据：{COOKIE_PATH}（先去 https://auth.iamsen.com/067b4abf303c28f7/ 保存一次）")
        with open(COOKIE_PATH, encoding="utf-8") as f:
            cred = json.load(f)
        self.cookie_str = cred.get("cookie_str", "")
        self.cookie_dict = cred.get("cookie_dict", {})
        if "sessionid" not in self.cookie_str:
            sys.exit("凭据里没有 sessionid，抖音评论需要登录态")
        self.sign_obj = execjs.compile(
            open(f"{MC_DIR}/libs/douyin.js", encoding="utf-8-sig").read())

    def _headers(self, referer: str) -> dict:
        return {
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Cookie": self.cookie_str,
            "Referer": referer,
            "x-tt-argus": "1",
            "uifid": self.cookie_dict.get("UIFID") or self.cookie_dict.get("UIFID_TEMP", ""),
        }

    def request(self, uri: str, params: dict, referer: str) -> dict:
        """带 a_bogus 签名请求；签名基于「最终参数串」计算，顺序不能乱"""
        full = {**COMMON_PARAMS, "webid": get_web_id(), **params}
        sign_fn = "sign_reply" if "/reply" in uri else "sign_datail"
        full["a_bogus"] = self.sign_obj.call(sign_fn, urllib.parse.urlencode(full), UA)
        resp = requests.get(f"https://www.douyin.com{uri}", params=full,
                            headers=self._headers(referer), timeout=20, proxies=self.proxies)
        if not resp.text:
            raise RuntimeError(f"{uri} 返回空响应体，通常是被风控拦了，稍后再试")
        data = resp.json()
        if data.get("status_code") not in (0, None):
            raise RuntimeError(f"{uri} status_code={data.get('status_code')}")
        return data

    def fetch(self, aweme_id: str, max_comments: int = 40, with_sub: bool = True) -> dict:
        referer = f"https://www.douyin.com/video/{aweme_id}"
        comments, cursor, has_more = [], 0, 1
        while has_more and len(comments) < max_comments:
            data = self.request("/aweme/v1/web/comment/list/",
                                {"aweme_id": aweme_id, "cursor": cursor, "count": 20, "item_type": 0},
                                referer)
            batch = data.get("comments") or []
            if not batch:
                break
            comments.extend(batch)
            cursor = data.get("cursor", cursor + 20)
            has_more = data.get("has_more", 0)
            print(f"  一级评论 {len(comments)} 条（has_more={has_more}）", file=sys.stderr)
            if has_more and len(comments) < max_comments:
                time.sleep(REQUEST_INTERVAL)

        sub_comments = []
        if with_sub:
            targets = [c for c in comments if (c.get("reply_comment_total") or 0) > 0]
            for c in targets:
                sub_cursor, sub_more = 0, 1
                while sub_more:
                    data = self.request(
                        "/aweme/v1/web/comment/list/reply/",
                        {"comment_id": c["cid"], "cursor": sub_cursor, "count": 20,
                         "item_type": 0, "item_id": aweme_id},
                        referer)
                    subs = data.get("comments") or []
                    if not subs:
                        break
                    sub_comments.extend(subs)
                    sub_cursor = data.get("cursor", sub_cursor + 20)
                    sub_more = data.get("has_more", 0)
                    if sub_more:
                        time.sleep(REQUEST_INTERVAL)
                time.sleep(REQUEST_INTERVAL)
        return {"aweme_id": aweme_id, "comments": comments, "sub_comments": sub_comments}


def parse_aweme_id(raw: str) -> str:
    match = re.search(r"/video/(\d+)", raw) or re.search(r"\b(\d{15,20})\b", raw)
    if not match:
        sys.exit(f"没能从「{raw}」里解析出视频 ID，请直接给纯数字 ID")
    return match.group(1)


def main():
    parser = argparse.ArgumentParser(description="抖音评论采集（纯 HTTP）")
    parser.add_argument("target", help="视频 ID 或视频链接")
    parser.add_argument("--max", type=int, default=40, help="一级评论上限（默认 40）")
    parser.add_argument("--no-sub", action="store_true", help="不抓二级评论")
    parser.add_argument("--proxy", help="出网代理，例如 socks5://127.0.0.1:1080（反向隧道的出口）")
    parser.add_argument("--json", help="把结果写到指定 JSON 文件")
    args = parser.parse_args()

    aweme_id = parse_aweme_id(args.target)
    print(f"[dy_comments] 视频 {aweme_id}，凭据取自 auth-hub", file=sys.stderr)
    client = DouyinComments(proxy=args.proxy)
    result = client.fetch(aweme_id, max_comments=args.max, with_sub=not args.no_sub)
    result["fetched_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for c in result["comments"][:5]:
        print(f"  - {(c.get('user') or {}).get('nickname')}: {(c.get('text') or '')[:50]}")
    print(f"一级 {len(result['comments'])} 条 / 二级 {len(result['sub_comments'])} 条")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"已写入 {args.json}")


if __name__ == "__main__":
    main()
