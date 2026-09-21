#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IAMSEN Unified Multi-Platform Auth Hub
统一多平台凭据与认证中枢（遵循《前端设计风格规范 v2 · 浅色》）
- 官方商标卡片阵列：哔哩哔哩 · 小红书 · 抖音 · 微博
- 状态指示：12px 半悬浮现代 App 角标 (绿/灰)
- 模态系统：无边框浮层，全站对称标准元数据，全端多重智能正则清洗
"""

import hashlib
import hmac
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.parse
from datetime import datetime

import mc_feed

PORT = 8318
APP_DIR = os.path.dirname(os.path.abspath(__file__))
HERMES_DIR = "/home/ubuntu/.hermes"

# 元数据路径
BILI_META_PATH = os.path.join(APP_DIR, "cookie_meta.json")
XHS_META_PATH = os.path.join(APP_DIR, "xhs_meta.json")
DOUYIN_META_PATH = os.path.join(APP_DIR, "douyin_meta.json")
WEIBO_META_PATH = os.path.join(APP_DIR, "weibo_meta.json")
TIEBA_META_PATH = os.path.join(APP_DIR, "tieba_meta.json")
ZHIHU_META_PATH = os.path.join(APP_DIR, "zhihu_meta.json")

# Cookie 存储路径（各平台统一落在 ~/.hermes/）
BILI_COOKIE_PATH = os.path.join(HERMES_DIR, "bili_cookie.json")
XHS_COOKIE_PATH = os.path.join(HERMES_DIR, "xhs_cookie.json")
DOUYIN_COOKIE_PATH = os.path.join(HERMES_DIR, "douyin_cookie.json")
WEIBO_COOKIE_PATH = os.path.join(HERMES_DIR, "weibo_cookie.json")
TIEBA_COOKIE_PATH = os.path.join(HERMES_DIR, "tieba_cookie.json")
ZHIHU_COOKIE_PATH = os.path.join(HERMES_DIR, "zhihu_cookie.json")

# 知乎签名脚本（算法与 MediaCrawler 的 libs/zhihu.js 同源）与 node 可执行文件
# systemd 服务的 PATH 不含用户级 node，因此这里显式定位绝对路径
ZHIHU_SIGN_SCRIPT = os.path.join(APP_DIR, "zhihu_sign.js")
NODE_BIN = shutil.which("node") or "/home/ubuntu/.local/bin/node"

# ==================== 页面访问鉴权 ====================
AUTH_CONFIG_PATH = os.path.join(APP_DIR, ".auth.json")
SESSION_COOKIE_NAME = "auth_hub_session"
SESSION_TTL = 7 * 24 * 3600

def load_auth_config():
    try:
        with open(AUTH_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("password", ""), cfg.get("secret", "")
    except Exception:
        return "", ""

AUTH_PASSWORD, AUTH_SECRET = load_auth_config()

def issue_session_token() -> str:
    exp = str(int(time.time()) + SESSION_TTL)
    sig = hmac.new(AUTH_SECRET.encode(), exp.encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"

def verify_session_token(token: str) -> bool:
    if not token or not AUTH_SECRET: return False
    try:
        exp, sig = token.split(".", 1)
        if int(exp) < time.time(): return False
    except Exception:
        return False
    expect = hmac.new(AUTH_SECRET.encode(), exp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expect)

# ==================== B站状态与逻辑 ====================
bili_lock = threading.Lock()
bili_state = {
    "polling_state": "idle",
    "msg": "",
    "qr_b64": "",
    "qrcode_key": "",
}

def load_bili_meta():
    if os.path.exists(BILI_META_PATH):
        try:
            with open(BILI_META_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception: pass
    return {"updated_at": "从未更新", "status": "unknown"}

def save_bili_meta(updated_at):
    with open(BILI_META_PATH, "w", encoding="utf-8") as f:
        json.dump({"updated_at": updated_at, "status": "configured", "probed_at": int(time.time())}, f, ensure_ascii=False, indent=2)

# B站探活：凭据落在 /opt/rss-stack/.env 的 BILIBILI_COOKIE_DEFAULT，用官方 nav 接口的 isLogin 判定
bili_probe_lock = threading.Lock()
bili_last_probe_time = 0
bili_last_probe_status = False

def read_bili_cookie() -> str:
    """读统一存放点 ~/.hermes/bili_cookie.json（RSSHub 也由 rss-stack-up.sh 从这里现取）"""
    try:
        with open(BILI_COOKIE_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("cookie_str", "")
    except Exception: return ""

def save_bili_cookie(cookie_dict: dict, cookie_str: str):
    """B站凭据只落在统一存放点 ~/.hermes/bili_cookie.json"""
    os.makedirs(HERMES_DIR, exist_ok=True)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(BILI_COOKIE_PATH, "w", encoding="utf-8") as f:
        json.dump({"cookie_dict": cookie_dict, "cookie_str": cookie_str, "updated_at": now_str}, f, ensure_ascii=False, indent=2)

def check_bili_live_probe(cookie_str: str) -> bool:
    global bili_last_probe_time, bili_last_probe_status
    if not cookie_str or "SESSDATA" not in cookie_str: return False
    now = time.time()
    if now - bili_last_probe_time < 60: return bili_last_probe_status

    with bili_probe_lock:
        if now - bili_last_probe_time < 60: return bili_last_probe_status
        try:
            req = urllib.request.Request(
                "https://api.bilibili.com/x/web-interface/nav",
                headers={
                    "Cookie": cookie_str,
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    "Referer": "https://www.bilibili.com/",
                },
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            is_alive = bool(data.get("code") == 0 and (data.get("data") or {}).get("isLogin"))
            bili_last_probe_status = is_alive
            bili_last_probe_time = now
            return is_alive
        except Exception:
            return bili_last_probe_status

def probe_bili_meta(force=False):
    """探活一次并落盘。force=True 时忽略 60 秒节流（弹窗主动刷新用）"""
    global bili_last_probe_time
    cookie_str = read_bili_cookie()
    updated_at = load_bili_meta().get("updated_at", "从未更新")

    if not cookie_str:
        status = "unconfigured"
    else:
        if force: bili_last_probe_time = 0
        status = "configured" if check_bili_live_probe(cookie_str) else "expired"

    meta = {"updated_at": updated_at, "status": status, "probed_at": int(time.time())}
    with open(BILI_META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta

def generate_bili_qr():
    req = urllib.request.Request(
        "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
        headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        if res.get("code") != 0:
            raise Exception("生成二维码失败: " + str(res))
        url = res["data"]["url"]
        key = res["data"]["qrcode_key"]

        proc = subprocess.run(
            ["qrencode", "-s", "8", "-o", "-", url],
            capture_output=True,
            check=True
        )
        import base64
        qr_b64 = "data:image/png;base64," + base64.b64encode(proc.stdout).decode("utf-8")
        return url, key, qr_b64

def bili_poll_worker(key):
    global bili_state
    url = f"https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key={key}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

    start_time = time.time()
    while time.time() - start_time < 180:
        time.sleep(2)
        with bili_lock:
            if bili_state["qrcode_key"] != key: return

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                code = data.get("data", {}).get("code")

                with bili_lock:
                    if code == 86101:
                        bili_state["polling_state"] = "waiting"
                        bili_state["msg"] = "等待扫码"
                    elif code == 86090:
                        bili_state["polling_state"] = "scanned"
                        bili_state["msg"] = "已扫码，等待确认"
                    elif code == 86038:
                        bili_state["polling_state"] = "expired"
                        bili_state["msg"] = "已失效"
                        return
                    elif code == 0:
                        bili_state["polling_state"] = "success"
                        bili_state["msg"] = "更新成功"

                        cookies_raw = resp.headers.get_all("Set-Cookie", [])
                        cookie_dict = {}
                        for item in cookies_raw:
                            part = item.split(";")[0]
                            if "=" in part:
                                k, v = part.split("=", 1)
                                cookie_dict[k.strip()] = v.strip()
                        cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())
                        save_bili_cookie(cookie_dict, cookie_str)

                        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        save_bili_meta(now_str)

                        # RSSHub 的 B 站 cookie 由该脚本从统一存放点现取现注入（不在 .env 里存副本）
                        subprocess.run(["/usr/local/bin/rss-stack-up.sh"], check=True)
                        bili_state["msg"] = "已生效"
                        return
        except Exception as e:
            print("Bili Poll error:", e, file=sys.stderr)

    with bili_lock:
        if bili_state["qrcode_key"] == key and bili_state["polling_state"] not in ("success", "expired"):
            bili_state["polling_state"] = "expired"
            bili_state["msg"] = "已超时"

# ==================== 小红书 Token 管理与真实探活 ====================
xhs_probe_lock = threading.Lock()
xhs_last_probe_time = 0
xhs_last_probe_status = False

def check_xhs_live_probe(cookie_str: str) -> bool:
    global xhs_last_probe_time, xhs_last_probe_status
    if not cookie_str or "web_session" not in cookie_str: return False
    now = time.time()
    if now - xhs_last_probe_time < 60: return xhs_last_probe_status

    with xhs_probe_lock:
        if now - xhs_last_probe_time < 60: return xhs_last_probe_status
        try:
            from xhshow import Xhshow
            import requests
            uri = "/api/sns/web/v1/user/selfinfo"
            xhshow_client = Xhshow()
            signs = xhshow_client.sign_headers_get(uri=uri, cookies=cookie_str)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json, text/plain, */*",
                "Cookie": cookie_str,
                "X-S": signs.get("x-s", ""),
                "X-T": signs.get("x-t", ""),
                "x-S-Common": signs.get("x-s-common", ""),
                "X-B3-Traceid": signs.get("x-b3-traceid", ""),
            }
            resp = requests.get(f"https://edith.xiaohongshu.com{uri}", headers=headers, timeout=4)
            is_alive = (resp.json().get("code") == 0)
            xhs_last_probe_status = is_alive
            xhs_last_probe_time = now
            return is_alive
        except Exception:
            return xhs_last_probe_status

def load_xhs_meta():
    """只读上一次探活结果，不对平台发起真实请求"""
    if os.path.exists(XHS_META_PATH):
        try:
            with open(XHS_META_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception: pass
    if os.path.exists(XHS_COOKIE_PATH):
        try:
            with open(XHS_COOKIE_PATH, "r", encoding="utf-8") as f:
                cdata = json.load(f)
                return {"updated_at": cdata.get("updated_at", "未配置"), "status": "unknown"}
        except Exception: pass
    return {"updated_at": "未配置", "status": "unconfigured"}

def probe_xhs_meta(force=False):
    """探活一次并落盘。force=True 时忽略 60 秒节流（弹窗主动刷新用）"""
    global xhs_last_probe_time
    cookie_str = ""
    updated_at = "未配置"
    if os.path.exists(XHS_COOKIE_PATH):
        try:
            with open(XHS_COOKIE_PATH, "r", encoding="utf-8") as f:
                cdata = json.load(f)
                cookie_str = cdata.get("cookie_str", "")
                updated_at = cdata.get("updated_at", "未配置")
        except Exception: pass

    if not cookie_str:
        updated_at = "未配置"
        status = "unconfigured"
    elif "web_session" not in cookie_str:
        status = "unlogged"
    else:
        if force: xhs_last_probe_time = 0
        status = "configured" if check_xhs_live_probe(cookie_str) else "expired"

    # xhs_meta.json 另由 xhs_service.js 写入 a1 / 昵称 / IP 属地等字段，这里只就地更新两行标准字段
    meta = {}
    if os.path.exists(XHS_META_PATH):
        try:
            with open(XHS_META_PATH, "r", encoding="utf-8") as f:
                meta = json.load(f) or {}
        except Exception: meta = {}
    meta["probed_at"] = int(time.time())
    meta["updated_at"] = updated_at
    meta["status"] = status

    with open(XHS_META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta

def clean_raw_cookie(text: str) -> str:
    import re
    raw_text = text.strip()
    m1 = re.search(r'(?:^|\r|\n)\s*cookie\s*\r?\n\s*([^\r\n]+)', raw_text, re.I)
    m2 = re.search(r'(?:^|\r|\n)\s*cookie\s*:\s*([^\r\n]+)', raw_text, re.I)
    m3 = re.search(r'([a-zA-Z0-9_\-]+=[^;\r\n]+(?:;\s*[^;\r\n]+)*)', raw_text)
    if m1: return m1.group(1).strip()
    if m2: return m2.group(1).strip()
    if m3: return m3.group(1).strip()
    return raw_text

def save_xhs_cookie(cookie_raw):
    clean_str = clean_raw_cookie(cookie_raw)
    cookie_dict = {}
    for item in clean_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            cookie_dict[k.strip()] = v.strip()

    cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())

    os.makedirs(HERMES_DIR, exist_ok=True)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(XHS_COOKIE_PATH, "w", encoding="utf-8") as f:
        json.dump({"cookie_dict": cookie_dict, "cookie_str": cookie_str, "updated_at": now_str}, f, ensure_ascii=False, indent=2)

    return probe_xhs_meta()

# ==================== 抖音与微博 Token 管理 ====================
# 探活策略对齐 MediaCrawler：
#   微博 → GET https://m.weibo.cn/api/config，判断 data.login（media_platform/weibo/client.py: pong）
#   抖音 → MediaCrawler 的 pong 依赖浏览器态（localStorage.HasUserLogin / LOGIN_STATUS，见 login.py）
#          纯 HTTP 侧取登录用户资料接口 /aweme/v1/web/user/profile/self/ 的 status_code == 0 作为等价判据
douyin_probe_lock = threading.Lock()
douyin_last_probe_time = 0
douyin_last_probe_status = False

def check_douyin_live_probe(cookie_str: str) -> bool:
    global douyin_last_probe_time, douyin_last_probe_status
    if not cookie_str or "sessionid" not in cookie_str: return False
    now = time.time()
    if now - douyin_last_probe_time < 60: return douyin_last_probe_status

    with douyin_probe_lock:
        if now - douyin_last_probe_time < 60: return douyin_last_probe_status
        try:
            import requests
            params = {
                "device_platform": "webapp", "aid": "6383", "channel": "channel_pc_web",
                "pc_client_type": "1", "version_code": "290100", "version_name": "29.1.0",
                "cookie_enabled": "true", "screen_width": "1920", "screen_height": "1080",
                "browser_language": "zh-CN", "browser_platform": "Win32", "browser_name": "Chrome",
                "browser_version": "131.0.0.0", "browser_online": "true", "engine_name": "Blink",
                "engine_version": "131.0.0.0", "os_name": "Windows", "os_version": "10",
                "cpu_core_num": "8", "device_memory": "8", "platform": "PC",
                "downlink": "10", "effective_type": "4g", "round_trip_time": "50",
            }
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.douyin.com/",
                "Cookie": cookie_str,
            }
            resp = requests.get("https://www.douyin.com/aweme/v1/web/user/profile/self/", params=params, headers=headers, timeout=6)
            data = resp.json()
            is_alive = (data.get("status_code") == 0 and bool(data.get("user")))
            douyin_last_probe_status = is_alive
            douyin_last_probe_time = now
            return is_alive
        except Exception:
            return douyin_last_probe_status

weibo_probe_lock = threading.Lock()
weibo_last_probe_time = 0
weibo_last_probe_status = False

def check_weibo_live_probe(cookie_str: str) -> bool:
    global weibo_last_probe_time, weibo_last_probe_status
    if not cookie_str or "SUB" not in cookie_str: return False
    now = time.time()
    if now - weibo_last_probe_time < 60: return weibo_last_probe_status

    with weibo_probe_lock:
        if now - weibo_last_probe_time < 60: return weibo_last_probe_status
        try:
            import requests
            headers = {
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://m.weibo.cn/",
                "Cookie": cookie_str,
            }
            resp = requests.get("https://m.weibo.cn/api/config", headers=headers, timeout=6)
            is_alive = bool(resp.json().get("data", {}).get("login"))
            weibo_last_probe_status = is_alive
            weibo_last_probe_time = now
            return is_alive
        except Exception:
            return weibo_last_probe_status

def load_douyin_meta():
    """只读上一次探活结果，不对平台发起真实请求"""
    if os.path.exists(DOUYIN_META_PATH):
        try:
            with open(DOUYIN_META_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception: pass
    if os.path.exists(DOUYIN_COOKIE_PATH):
        try:
            with open(DOUYIN_COOKIE_PATH, "r", encoding="utf-8") as f:
                cdata = json.load(f)
                return {"updated_at": cdata.get("updated_at", "未配置"), "status": "unknown"}
        except Exception: pass
    return {"updated_at": "未配置", "status": "unconfigured"}

def probe_douyin_meta(force=False):
    """探活一次并落盘。force=True 时忽略 60 秒节流（弹窗主动刷新用）"""
    global douyin_last_probe_time
    cookie_str = ""
    updated_at = "未配置"
    if os.path.exists(DOUYIN_COOKIE_PATH):
        try:
            with open(DOUYIN_COOKIE_PATH, "r", encoding="utf-8") as f:
                cdata = json.load(f)
                cookie_str = cdata.get("cookie_str", "")
                updated_at = cdata.get("updated_at", "未配置")
        except Exception: pass

    if not cookie_str:
        meta = {"updated_at": "未配置", "status": "unconfigured"}
    elif "sessionid" not in cookie_str:
        meta = {"updated_at": updated_at, "status": "unlogged"}
    else:
        if force: douyin_last_probe_time = 0
        is_live = check_douyin_live_probe(cookie_str)
        meta = {"updated_at": updated_at, "status": "configured" if is_live else "expired"}

    meta["probed_at"] = int(time.time())
    with open(DOUYIN_META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta

def save_douyin_cookie(cookie_raw):
    clean_str = clean_raw_cookie(cookie_raw)
    cookie_dict = {}
    for item in clean_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            cookie_dict[k.strip()] = v.strip()

    cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())

    os.makedirs(HERMES_DIR, exist_ok=True)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(DOUYIN_COOKIE_PATH, "w", encoding="utf-8") as f:
        json.dump({"cookie_dict": cookie_dict, "cookie_str": cookie_str, "updated_at": now_str}, f, ensure_ascii=False, indent=2)

    return probe_douyin_meta()

def load_weibo_meta():
    """只读上一次探活结果，不对平台发起真实请求"""
    if os.path.exists(WEIBO_META_PATH):
        try:
            with open(WEIBO_META_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception: pass
    if os.path.exists(WEIBO_COOKIE_PATH):
        try:
            with open(WEIBO_COOKIE_PATH, "r", encoding="utf-8") as f:
                cdata = json.load(f)
                return {"updated_at": cdata.get("updated_at", "未配置"), "status": "unknown"}
        except Exception: pass
    return {"updated_at": "未配置", "status": "unconfigured"}

def probe_weibo_meta(force=False):
    """探活一次并落盘。force=True 时忽略 60 秒节流（弹窗主动刷新用）"""
    global weibo_last_probe_time
    cookie_str = ""
    updated_at = "未配置"
    if os.path.exists(WEIBO_COOKIE_PATH):
        try:
            with open(WEIBO_COOKIE_PATH, "r", encoding="utf-8") as f:
                cdata = json.load(f)
                cookie_str = cdata.get("cookie_str", "")
                updated_at = cdata.get("updated_at", "未配置")
        except Exception: pass

    if not cookie_str:
        meta = {"updated_at": "未配置", "status": "unconfigured"}
    elif "SUB" not in cookie_str:
        meta = {"updated_at": updated_at, "status": "unlogged"}
    else:
        if force: weibo_last_probe_time = 0
        is_live = check_weibo_live_probe(cookie_str)
        meta = {"updated_at": updated_at, "status": "configured" if is_live else "expired"}

    meta["probed_at"] = int(time.time())
    with open(WEIBO_META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta

def save_weibo_cookie(cookie_raw):
    clean_str = clean_raw_cookie(cookie_raw)
    cookie_dict = {}
    for item in clean_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            cookie_dict[k.strip()] = v.strip()

    cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())

    os.makedirs(HERMES_DIR, exist_ok=True)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(WEIBO_COOKIE_PATH, "w", encoding="utf-8") as f:
        json.dump({"cookie_dict": cookie_dict, "cookie_str": cookie_str, "updated_at": now_str}, f, ensure_ascii=False, indent=2)

    return probe_weibo_meta()

# ==================== 贴吧与知乎 Token 管理 ====================
# 探活策略：
#   贴吧 → GET https://tieba.baidu.com/dc/common/tbs，is_login == 1 视为有效（核心凭据 BDUSS）
#   知乎 → GET https://www.zhihu.com/api/v4/me，需 x-zst-81 / x-zse-96 签名（核心凭据 z_c0，签名另需 d_c0）
tieba_probe_lock = threading.Lock()
tieba_last_probe_time = 0
tieba_last_probe_status = False

def check_tieba_live_probe(cookie_str: str) -> bool:
    global tieba_last_probe_time, tieba_last_probe_status
    if not cookie_str or "BDUSS" not in cookie_str: return False
    now = time.time()
    if now - tieba_last_probe_time < 60: return tieba_last_probe_status

    with tieba_probe_lock:
        if now - tieba_last_probe_time < 60: return tieba_last_probe_status
        try:
            # 注意：tbs 接口会把 https 301 到 http，requests 在重定向时会丢弃手动设置的 Cookie 头
            # （导致 is_login 恒为 0），因此这里用 urllib 发请求
            req = urllib.request.Request(
                "https://tieba.baidu.com/dc/common/tbs",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    "Accept-Encoding": "identity",
                    "Referer": "https://tieba.baidu.com/",
                    "Cookie": cookie_str,
                },
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            is_alive = bool(data.get("is_login"))
            tieba_last_probe_status = is_alive
            tieba_last_probe_time = now
            return is_alive
        except Exception:
            return tieba_last_probe_status

zhihu_probe_lock = threading.Lock()
zhihu_last_probe_time = 0
zhihu_last_probe_status = False

def _zhihu_sign_headers(cookie_str: str):
    """调 node 执行 zhihu_sign.js，返回 {"x-zst-81", "x-zse-96"}；失败返回 None"""
    try:
        proc = subprocess.run(
            [NODE_BIN, ZHIHU_SIGN_SCRIPT, "/api/v4/me", cookie_str],
            capture_output=True, text=True, encoding="utf-8", timeout=10
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        return json.loads(proc.stdout)
    except Exception:
        return None

def check_zhihu_live_probe(cookie_str: str) -> bool:
    global zhihu_last_probe_time, zhihu_last_probe_status
    if not cookie_str or "z_c0" not in cookie_str: return False
    now = time.time()
    if now - zhihu_last_probe_time < 60: return zhihu_last_probe_status

    with zhihu_probe_lock:
        if now - zhihu_last_probe_time < 60: return zhihu_last_probe_status
        try:
            import requests
            sign = _zhihu_sign_headers(cookie_str)
            if not sign: return zhihu_last_probe_status
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.zhihu.com/",
                "Cookie": cookie_str,
                "x-zse-93": "101_3_3.0",
                "x-zst-81": sign.get("x-zst-81", ""),
                "x-zse-96": sign.get("x-zse-96", ""),
            }
            resp = requests.get("https://www.zhihu.com/api/v4/me", headers=headers, timeout=6)
            is_alive = (resp.status_code == 200 and bool(resp.json().get("id")))
            zhihu_last_probe_status = is_alive
            zhihu_last_probe_time = now
            return is_alive
        except Exception:
            return zhihu_last_probe_status

def load_tieba_meta():
    """只读上一次探活结果，不对平台发起真实请求"""
    if os.path.exists(TIEBA_META_PATH):
        try:
            with open(TIEBA_META_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception: pass
    if os.path.exists(TIEBA_COOKIE_PATH):
        try:
            with open(TIEBA_COOKIE_PATH, "r", encoding="utf-8") as f:
                cdata = json.load(f)
                return {"updated_at": cdata.get("updated_at", "未配置"), "status": "unknown"}
        except Exception: pass
    return {"updated_at": "未配置", "status": "unconfigured"}

def probe_tieba_meta(force=False):
    """探活一次并落盘。force=True 时忽略 60 秒节流（弹窗主动刷新用）"""
    global tieba_last_probe_time
    cookie_str = ""
    updated_at = "未配置"
    if os.path.exists(TIEBA_COOKIE_PATH):
        try:
            with open(TIEBA_COOKIE_PATH, "r", encoding="utf-8") as f:
                cdata = json.load(f)
                cookie_str = cdata.get("cookie_str", "")
                updated_at = cdata.get("updated_at", "未配置")
        except Exception: pass

    if not cookie_str:
        meta = {"updated_at": "未配置", "status": "unconfigured"}
    elif "BDUSS" not in cookie_str:
        meta = {"updated_at": updated_at, "status": "unlogged"}
    else:
        if force: tieba_last_probe_time = 0
        is_live = check_tieba_live_probe(cookie_str)
        meta = {"updated_at": updated_at, "status": "configured" if is_live else "expired"}

    meta["probed_at"] = int(time.time())
    with open(TIEBA_META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta

def save_tieba_cookie(cookie_raw):
    clean_str = clean_raw_cookie(cookie_raw)
    cookie_dict = {}
    for item in clean_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            cookie_dict[k.strip()] = v.strip()

    cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())

    os.makedirs(HERMES_DIR, exist_ok=True)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(TIEBA_COOKIE_PATH, "w", encoding="utf-8") as f:
        json.dump({"cookie_dict": cookie_dict, "cookie_str": cookie_str, "updated_at": now_str}, f, ensure_ascii=False, indent=2)

    return probe_tieba_meta()

def load_zhihu_meta():
    """只读上一次探活结果，不对平台发起真实请求"""
    if os.path.exists(ZHIHU_META_PATH):
        try:
            with open(ZHIHU_META_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception: pass
    if os.path.exists(ZHIHU_COOKIE_PATH):
        try:
            with open(ZHIHU_COOKIE_PATH, "r", encoding="utf-8") as f:
                cdata = json.load(f)
                return {"updated_at": cdata.get("updated_at", "未配置"), "status": "unknown"}
        except Exception: pass
    return {"updated_at": "未配置", "status": "unconfigured"}

def probe_zhihu_meta(force=False):
    """探活一次并落盘。force=True 时忽略 60 秒节流（弹窗主动刷新用）"""
    global zhihu_last_probe_time
    cookie_str = ""
    updated_at = "未配置"
    if os.path.exists(ZHIHU_COOKIE_PATH):
        try:
            with open(ZHIHU_COOKIE_PATH, "r", encoding="utf-8") as f:
                cdata = json.load(f)
                cookie_str = cdata.get("cookie_str", "")
                updated_at = cdata.get("updated_at", "未配置")
        except Exception: pass

    if not cookie_str:
        meta = {"updated_at": "未配置", "status": "unconfigured"}
    elif "z_c0" not in cookie_str:
        meta = {"updated_at": updated_at, "status": "unlogged"}
    else:
        if force: zhihu_last_probe_time = 0
        is_live = check_zhihu_live_probe(cookie_str)
        meta = {"updated_at": updated_at, "status": "configured" if is_live else "expired"}

    meta["probed_at"] = int(time.time())
    with open(ZHIHU_META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta

def save_zhihu_cookie(cookie_raw):
    clean_str = clean_raw_cookie(cookie_raw)
    cookie_dict = {}
    for item in clean_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            cookie_dict[k.strip()] = v.strip()

    cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())

    os.makedirs(HERMES_DIR, exist_ok=True)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(ZHIHU_COOKIE_PATH, "w", encoding="utf-8") as f:
        json.dump({"cookie_dict": cookie_dict, "cookie_str": cookie_str, "updated_at": now_str}, f, ensure_ascii=False, indent=2)

    return probe_zhihu_meta()

# ==================== HTTP 请求处理器 ====================
class Handler(http.server.BaseHTTPRequestHandler):
    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")

    # 无需登录即可访问的路径（登录页与站点图标）
    PUBLIC_PATHS = ("/login", "/api/login", "/assets/favicon.svg", "/assets/favicon.ico", "/assets/favicon.png")

    def is_authenticated(self) -> bool:
        header_token = self.headers.get("X-Auth-Token", "")
        if header_token and AUTH_PASSWORD and hmac.compare_digest(header_token, AUTH_PASSWORD):
            return True
        raw_cookie = self.headers.get("Cookie", "")
        for part in raw_cookie.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                if k.strip() == SESSION_COOKIE_NAME:
                    return verify_session_token(v.strip())
        return False

    def auth_gate(self, clean_path: str) -> bool:
        """鉴权闸门；未通过时就地写出响应并返回 False"""
        if clean_path in self.PUBLIC_PATHS or self.is_authenticated():
            return True
        if clean_path.startswith("/api/"):
            self.send_response(401)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(json.dumps({"success": False, "error": "未登录"}, ensure_ascii=False).encode("utf-8"))
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(LOGIN_HTML.encode("utf-8"))
        return False

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors_headers()
        self.end_headers()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        clean_path = self.path.split("?")[0].rstrip("/")
        if not self.auth_gate(clean_path):
            return
        if clean_path == "/login":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(LOGIN_HTML.encode("utf-8"))
        elif clean_path in ("", "/index.html", "/bili"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(HTML_CONTENT.encode("utf-8"))
        elif clean_path in ("/assets/bili_logo.png", "/assets/xhs_logo.png", "/assets/douyin_logo.png", "/assets/weibo_logo.png", "/assets/tieba_logo.png", "/assets/zhihu_logo.png", "/assets/favicon.ico", "/assets/favicon.png"):
            fname = clean_path.lstrip("/")
            fpath = os.path.join(APP_DIR, fname)
            if os.path.exists(fpath):
                self.send_response(200)
                ctype = "image/png" if fname.endswith(".png") else "image/x-icon"
                self.send_header("Content-Type", ctype)
                # 隐藏入口下不做边缘缓存，避免静态资源被 CDN 命中而暴露服务存在
                self.send_header("Cache-Control", "no-store")
                self.send_cors_headers()
                self.end_headers()
                with open(fpath, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(404)
                self.end_headers()
        elif clean_path in ("/assets/weibo_logo.svg", "/assets/favicon.svg"):
            fname = clean_path.lstrip("/")
            fpath = os.path.join(APP_DIR, fname) if fname != "assets/favicon.svg" else None
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Cache-Control", "no-store")
            self.send_cors_headers()
            self.end_headers()
            if fpath and os.path.exists(fpath):
                with open(fpath, "rb") as f: self.wfile.write(f.read())
            else:
                self.wfile.write(FAVICON_SVG.encode("utf-8"))
        elif clean_path == "/mc/patchnotes.xml":
            xml = mc_feed.generate_patchnotes_rss()
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Cache-Control", "public, max-age=600")
            self.end_headers()
            self.wfile.write(xml.encode("utf-8"))
            return
        elif clean_path == "/mc/news.xml":
            xml = mc_feed.generate_news_rss()
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Cache-Control", "public, max-age=600")
            self.end_headers()
            self.wfile.write(xml.encode("utf-8"))
            return
        elif clean_path in ("/api/status", "/api/bili/probe"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_cors_headers()
            self.end_headers()
            meta = probe_bili_meta("force=1" in self.path) if clean_path == "/api/bili/probe" else load_bili_meta()
            with bili_lock:
                res = {
                    "updated_at": meta.get("updated_at", "未知"),
                    "status": meta.get("status", "unknown"),
                    "probed_at": meta.get("probed_at"),
                    "polling_state": bili_state["polling_state"],
                    "msg": bili_state["msg"],
                    "qr_b64": bili_state["qr_b64"] if bili_state["polling_state"] in ("waiting", "scanned") else ""
                }
            self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
        elif clean_path in ("/api/xhs/status", "/api/xhs/probe"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_cors_headers()
            self.end_headers()
            meta = load_xhs_meta() if clean_path == "/api/xhs/status" else probe_xhs_meta("force=1" in self.path)
            self.wfile.write(json.dumps(meta, ensure_ascii=False).encode("utf-8"))
        elif clean_path in ("/api/douyin/status", "/api/douyin/probe", "/api/weibo/status", "/api/weibo/probe",
                            "/api/tieba/status", "/api/tieba/probe", "/api/zhihu/status", "/api/zhihu/probe"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_cors_headers()
            self.end_headers()
            if clean_path == "/api/douyin/status":
                meta = load_douyin_meta()
            elif clean_path == "/api/douyin/probe":
                meta = probe_douyin_meta("force=1" in self.path)
            elif clean_path == "/api/weibo/status":
                meta = load_weibo_meta()
            elif clean_path == "/api/weibo/probe":
                meta = probe_weibo_meta("force=1" in self.path)
            elif clean_path == "/api/tieba/status":
                meta = load_tieba_meta()
            elif clean_path == "/api/tieba/probe":
                meta = probe_tieba_meta("force=1" in self.path)
            elif clean_path == "/api/zhihu/status":
                meta = load_zhihu_meta()
            else:
                meta = probe_zhihu_meta("force=1" in self.path)
            self.wfile.write(json.dumps(meta, ensure_ascii=False).encode("utf-8"))
        elif clean_path == "/api/hub/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_cors_headers()
            self.end_headers()
            res = {
                "bili": load_bili_meta(),
                "xhs": load_xhs_meta(),
                "douyin": load_douyin_meta(),
                "weibo": load_weibo_meta(),
                "tieba": load_tieba_meta(),
                "zhihu": load_zhihu_meta(),
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        clean_path = self.path.split("?")[0].rstrip("/")
        if not self.auth_gate(clean_path):
            return
        if clean_path == "/api/login":
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                password = data.get("password", "") or ""
                if not AUTH_PASSWORD:
                    raise Exception("服务端未配置访问密码")
                if not hmac.compare_digest(password, AUTH_PASSWORD):
                    raise Exception("密码不正确")
                cookie = (f"{SESSION_COOKIE_NAME}={issue_session_token()}; Path=/; "
                          f"Max-Age={SESSION_TTL}; HttpOnly; Secure; SameSite=Lax")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Set-Cookie", cookie)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
            except Exception as e:
                self.send_response(401)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False).encode("utf-8"))
        elif clean_path == "/api/new-qr":
            try:
                url, key, qr_b64 = generate_bili_qr()
                with bili_lock:
                    bili_state["polling_state"] = "waiting"
                    bili_state["msg"] = "等待扫码"
                    bili_state["qrcode_key"] = key
                    bili_state["qr_b64"] = qr_b64

                t = threading.Thread(target=bili_poll_worker, args=(key,), daemon=True)
                t.start()

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "qr_b64": qr_b64}).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
        elif clean_path in ("/api/xhs/save", "/api/douyin/save", "/api/weibo/save", "/api/tieba/save", "/api/zhihu/save"):
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                data = json.loads(body)
                cookie_str = data.get("cookie", "")
                if not cookie_str:
                    raise Exception("缺少数据")
                
                if clean_path == "/api/xhs/save":
                    meta = save_xhs_cookie(cookie_str)
                elif clean_path == "/api/douyin/save":
                    meta = save_douyin_cookie(cookie_str)
                elif clean_path == "/api/weibo/save":
                    meta = save_weibo_cookie(cookie_str)
                elif clean_path == "/api/tieba/save":
                    meta = save_tieba_cookie(cookie_str)
                else:
                    meta = save_zhihu_cookie(cookie_str)

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "meta": meta}).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#0F172A" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
  <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
  <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
</svg>"""

LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>凭据中枢 · Auth Hub</title>
    <link rel="icon" type="image/svg+xml" href="assets/favicon.svg">
    <style>
        :root {
            --bg-page: #F6F8FA;
            --bg-panel: #FFFFFF;
            --bg-hover: #F1F5F9;
            --line: #E2E8F0;
            --tx-main: #0F172A;
            --tx-muted: #64748B;
            --accent: #0284C7;
            --danger: #E11D48;
            --r-md: 6px;
            --ease-out: cubic-bezier(.16, 1, .3, 1);
            --font-ui: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", Arial, sans-serif;
        }
        *, *::before, *::after { box-sizing: border-box; }
        html, body { margin: 0; height: 100%; }
        body {
            background: var(--bg-page);
            color: var(--tx-main);
            font: 400 13px/20px var(--font-ui);
            display: flex;
            align-items: center;
            justify-content: center;
            -webkit-font-smoothing: antialiased;
        }
        .login-box { width: 100%; max-width: 300px; padding: 0 16px; }
        .login-icon { width: 26px; height: 26px; margin: 0 auto 14px; display: block; stroke: var(--tx-main); }
        .login-title { font-weight: 500; font-size: 15px; text-align: center; margin-bottom: 4px; }
        .login-sub { color: var(--tx-muted); text-align: center; margin-bottom: 22px; }
        .login-input {
            width: 100%;
            height: 34px;
            background: var(--bg-panel);
            border: 1px solid var(--line);
            border-radius: var(--r-md);
            padding: 0 10px;
            font: 400 13px/20px var(--font-ui);
            color: var(--tx-main);
            outline: none;
            transition: border-color 120ms var(--ease-out);
        }
        .login-input:focus { border-color: var(--accent); }
        .login-btn {
            width: 100%;
            height: 34px;
            margin-top: 12px;
            background: transparent;
            border: 1px solid var(--line);
            border-radius: var(--r-md);
            color: var(--tx-main);
            font: 500 13px/20px var(--font-ui);
            cursor: pointer;
            transition: border-color 120ms var(--ease-out), background-color 120ms var(--ease-out);
        }
        .login-btn:hover { border-color: var(--accent); background: var(--bg-hover); }
        .login-btn:disabled { color: var(--tx-muted); cursor: not-allowed; }
        .login-feedback { min-height: 18px; margin-top: 10px; font-size: 12px; color: var(--danger); text-align: center; }
    </style>
</head>
<body>
    <div class="login-box">
        <svg class="login-icon" viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
            <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
        </svg>
        <div class="login-title">凭据中枢</div>
        <div class="login-sub">请输入访问密码</div>
        <input id="login-pw" class="login-input" type="password" placeholder="访问密码" autocomplete="current-password">
        <button id="login-btn" class="login-btn">进入</button>
        <div id="login-feedback" class="login-feedback"></div>
    </div>
    <script>
        const pwEl = document.getElementById('login-pw');
        const btnEl = document.getElementById('login-btn');
        const fbEl = document.getElementById('login-feedback');

        function submitLogin() {
            const value = pwEl.value;
            if (!value) { fbEl.innerText = '请输入访问密码'; return; }
            btnEl.disabled = true;
            btnEl.innerText = '验证中…';
            fbEl.innerText = '';
            fetch('api/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password: value })
            }).then(r => r.json()).then(d => {
                if (d.success) {
                    location.href = './';
                } else {
                    fbEl.innerText = d.error || '密码不正确';
                    btnEl.disabled = false;
                    btnEl.innerText = '进入';
                    pwEl.value = '';
                }
            }).catch(() => {
                fbEl.innerText = '网络异常，请重试';
                btnEl.disabled = false;
                btnEl.innerText = '进入';
            });
        }

        btnEl.addEventListener('click', submitLogin);
        pwEl.addEventListener('keydown', e => { if (e.key === 'Enter') submitLogin(); });
        pwEl.focus();
    </script>
</body>
</html>
"""

HTML_CONTENT = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>凭据中枢 · Auth Hub</title>
    <link rel="icon" type="image/svg+xml" href="assets/favicon.svg">
    <style>
        :root {
            --hairline: 0.5px;
            --bg-page: #F6F8FA;
            --bg-panel: #FFFFFF;
            --bg-faint: #F8FAFC;
            --bg-hover: #F1F5F9;
            --bg-active: #E2E8F0;
            --bg-disabled: #E2E8F0;
            --line: #E2E8F0;
            --line-hover: #CBD5E1;
            --line-strong: #94A3B8;
            --line-weak: #F1F5F9;
            --tx-main: #0F172A;
            --tx-body: #1F2328;
            --tx-sub: #475569;
            --tx-muted: #64748B;
            --tx-placeholder: #94A3B8;
            --tx-hover: #000000;
            --tx-inverse: #FFFFFF;
            --accent: #0284C7;
            --accent-strong: #0369A1;
            --ok: #10B981;
            --ok-text: #047857;
            --danger: #E11D48;
            --warn: #D97706;
            --warn-text: #B45309;
            --r-sm: 4px;
            --r-md: 6px;
            --r-lg: 8px;
            --r-xl: 12px;
            --r-full: 9999px;
            --ease-out: cubic-bezier(.16, 1, .3, 1);
            --t-color: 120ms;
            --t-float: 180ms;
            --font-ui: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", Arial, sans-serif;
            --font-mono: 'SF Mono', 'JetBrains Mono', Consolas, Menlo, monospace;
            --sh-panel: 0 1px 3px 0 rgba(31, 35, 40, 0.04);
            --elev-stroke-color: rgba(0,0,0,.16);
            --sh-stroke: 0 0 0 var(--hairline) var(--elev-stroke-color);
            --sh-float: var(--sh-stroke), 0 3px 8px 0 rgba(0,0,0,.04), 0 0 20px 0 rgba(0,0,0,.05);
        }

        *, *::before, *::after { box-sizing: border-box; }
        html, body {
            margin: 0;
            height: 100%;
            overflow: hidden;
            background: var(--bg-page);
            color: var(--tx-body);
            font: 400 13px/20px var(--font-ui);
            -webkit-font-smoothing: antialiased;
        }

        h1, h2, h3, strong { font-weight: 500; }
        .num, .mono { font-variant-numeric: tabular-nums; font-family: var(--font-mono); }

        :focus { outline: none; }
        :focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; border-radius: inherit; }

        .app-shell {
            height: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
            overflow-y: auto;
            padding: 64px 16px;
        }

        .header-box {
            width: 100%;
            max-width: 560px;
            margin-bottom: 32px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .header-icon {
            width: 26px;
            height: 26px;
            color: var(--tx-main);
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }
        .header-title {
            font-size: 15px;
            line-height: 22px;
            font-weight: 500;
            color: var(--tx-main);
            margin: 0;
        }

        /* 官方商标卡片网格：多平台自适应对称网格 */
        .auth-grid {
            width: 100%;
            max-width: 560px;
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 14px;
        }

        .brand-card {
            background: transparent;
            border: var(--hairline) solid transparent;
            border-radius: var(--r-xl);
            padding: 16px 8px;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            user-select: none;
            transition: background-color var(--t-color) ease, border-color var(--t-color) ease;
        }
        .brand-card:hover {
            background: var(--bg-hover);
            border-color: var(--line-weak);
        }
        .brand-card:active {
            background: var(--bg-active);
        }

        /* 官方商标容器（统一 68px 白底圆角，承载 12px 半悬浮状态角标） */
        .brand-logo-wrap {
            position: relative;
            width: 68px;
            height: 68px;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: #FFFFFF;
            border-radius: 16px;
            border: var(--hairline) solid var(--line);
        }
        .brand-logo-img {
            width: 100%;
            height: 100%;
            border-radius: 15px;
            object-fit: contain;
            display: block;
        }

        /* 12px 半悬浮桌面 App 角标 */
        .brand-status-dot {
            position: absolute;
            top: -3px;
            right: -3px;
            width: 12px;
            height: 12px;
            border-radius: var(--r-full);
            background: var(--line-strong);
            border: 2px solid var(--bg-page);
            z-index: 5;
            transition: background-color var(--t-color) ease;
        }
        .brand-status-dot.is-ok {
            background: var(--ok);
        }
        .brand-status-dot.is-warn {
            background: var(--warn);
        }
        .brand-status-dot.is-stale {
            background: transparent;
            box-shadow: inset 0 0 0 2px var(--line-strong);
        }
        .brand-status-dot.is-probing {
            background: transparent;
            box-shadow: inset 0 0 0 2px var(--line-strong);
            animation: dot-probing 900ms var(--ease-out) infinite;
        }

        .brand-name {
            font-size: 13px;
            line-height: 18px;
            font-weight: 500;
            color: var(--tx-main);
        }

        /* 模态弹窗系统：无边框，发丝描边是阴影第一层 */
        .modal-mask {
            display: none;
            position: fixed;
            inset: 0;
            background: rgba(15, 23, 42, 0.35);
            z-index: 90;
            align-items: center;
            justify-content: center;
            padding: 16px;
        }
        .modal-mask.is-open { display: flex; }

        .modal-dialog {
            width: 100%;
            max-width: 420px;
            background: var(--bg-panel);
            border: 0;
            border-radius: var(--r-xl);
            box-shadow: var(--sh-float);
            padding: 20px;
            position: relative;
            animation: modalFadeIn var(--t-float) var(--ease-out) forwards;
        }
        @keyframes modalFadeIn {
            from { opacity: 0; transform: scale(0.98); }
            to { opacity: 1; transform: scale(1); }
        }

        .modal-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 16px;
        }
        .modal-title {
            font-size: 14px;
            line-height: 20px;
            font-weight: 500;
            color: var(--tx-main);
            display: inline-flex;
            align-items: center;
            gap: 8px;
        }
        .modal-brand-thumb {
            width: 20px;
            height: 20px;
            border-radius: 4px;
            object-fit: contain;
        }

        .btn-close {
            background: transparent;
            border: none;
            color: var(--tx-muted);
            cursor: pointer;
            padding: 4px;
            border-radius: var(--r-sm);
            display: flex;
            align-items: center;
            justify-content: center;
            transition: color var(--t-color) ease;
        }
        .btn-close:hover { color: var(--tx-main); }
        .btn-close svg { width: 16px; height: 16px; stroke: currentColor; }

        .meta-box {
            background: var(--bg-faint);
            border: var(--hairline) solid var(--line-weak);
            border-radius: var(--r-md);
            padding: 8px 12px;
            margin-bottom: 16px;
        }
        .meta-line {
            display: flex;
            justify-content: space-between;
            font-size: 12px;
            line-height: 22px;
        }
        .meta-k { color: var(--tx-muted); }
        .meta-v { color: var(--tx-main); }

        .status-badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-size: 12px;
        }
        .status-point {
            width: 6px;
            height: 6px;
            border-radius: var(--r-full);
            background: var(--line-strong);
        }
        .status-point.ok { background: var(--ok); }
        .status-point.warn { background: var(--warn); }
        .status-point.probing {
            background: transparent;
            box-shadow: inset 0 0 0 1.5px var(--line-strong);
            animation: dot-probing 900ms var(--ease-out) infinite;
        }
        @keyframes dot-probing {
            0%, 100% { opacity: 1; }
            50% { opacity: .2; }
        }

        .qr-stage {
            width: 180px;
            height: 180px;
            margin: 0 auto 16px;
            background: #FFFFFF;
            border: var(--hairline) solid var(--line);
            border-radius: var(--r-lg);
            padding: 6px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .qr-stage.is-active { border-color: var(--accent); }
        .qr-stage img { width: 100%; height: 100%; display: block; }
        .qr-hint { font-size: 12px; color: var(--tx-placeholder); text-align: center; }

        .input-text {
            width: 100%;
            height: 70px;
            background: var(--bg-panel);
            border: var(--hairline) solid var(--line);
            border-radius: var(--r-md);
            padding: 8px 10px;
            font-size: 12px;
            line-height: 18px;
            font-family: var(--font-mono);
            color: var(--tx-main);
            margin-bottom: 12px;
            resize: none;
        }
        .input-text:focus { border-color: var(--accent); }

        .btn-action {
            width: 100%;
            background: var(--tx-main);
            color: var(--tx-inverse);
            border: none;
            padding: 8px 14px;
            font-size: 13px;
            line-height: 20px;
            font-weight: 500;
            border-radius: var(--r-md);
            cursor: pointer;
            transition: background-color var(--t-color) ease;
        }
        .btn-action:hover { background: #000000; }
        .btn-action:disabled {
            background: var(--bg-disabled);
            color: var(--tx-placeholder);
            cursor: not-allowed;
        }

        .feedback-line {
            font-size: 12px;
            line-height: 18px;
            min-height: 18px;
            margin: 8px 0 12px;
            text-align: center;
            color: var(--accent-strong);
        }
        .feedback-line.ok { color: var(--ok-text); }
        .feedback-line.err { color: var(--danger); }
        .feedback-line.warn { color: var(--warn-text); }
    </style>
</head>
<body>
    <div class="app-shell">
        <header class="header-box">
            <div class="header-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                    <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
                    <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
                </svg>
            </div>
            <h1 class="header-title">凭据中枢</h1>
        </header>

        <!-- 官方商标卡片网格：多平台统一陈列 -->
        <main class="auth-grid">
            <!-- 哔哩哔哩 -->
            <div class="brand-card" onclick="openModal('bili')">
                <div class="brand-logo-wrap">
                    <span id="dot-bili" class="brand-status-dot"></span>
                    <img class="brand-logo-img" src="assets/bili_logo.png" alt="哔哩哔哩">
                </div>
                <span class="brand-name">哔哩哔哩</span>
            </div>

            <!-- 小红书 -->
            <div class="brand-card" onclick="openModal('xhs')">
                <div class="brand-logo-wrap">
                    <span id="dot-xhs" class="brand-status-dot"></span>
                    <img class="brand-logo-img" src="assets/xhs_logo.png" alt="小红书">
                </div>
                <span class="brand-name">小红书</span>
            </div>

            <!-- 抖音 -->
            <div class="brand-card" onclick="openModal('douyin')">
                <div class="brand-logo-wrap">
                    <span id="dot-douyin" class="brand-status-dot"></span>
                    <img class="brand-logo-img" src="assets/douyin_logo.png?v=2" alt="抖音">
                </div>
                <span class="brand-name">抖音</span>
            </div>

            <!-- 微博 -->
            <div class="brand-card" onclick="openModal('weibo')">
                <div class="brand-logo-wrap">
                    <span id="dot-weibo" class="brand-status-dot"></span>
                    <img class="brand-logo-img" src="assets/weibo_logo.png?v=2" alt="微博">
                </div>
                <span class="brand-name">微博</span>
            </div>

            <!-- 百度贴吧 -->
            <div class="brand-card" onclick="openModal('tieba')">
                <div class="brand-logo-wrap">
                    <span id="dot-tieba" class="brand-status-dot"></span>
                    <img class="brand-logo-img" src="assets/tieba_logo.png" alt="百度贴吧">
                </div>
                <span class="brand-name">百度贴吧</span>
            </div>

            <!-- 知乎 -->
            <div class="brand-card" onclick="openModal('zhihu')">
                <div class="brand-logo-wrap">
                    <span id="dot-zhihu" class="brand-status-dot"></span>
                    <img class="brand-logo-img" src="assets/zhihu_logo.png" alt="知乎">
                </div>
                <span class="brand-name">知乎</span>
            </div>
        </main>
    </div>

    <!-- 模态弹窗系统 (字段全站绝对统一) -->
    <div id="modal-mask" class="modal-mask" onclick="handleMaskClick(event)">
        <!-- 弹窗：B站 -->
        <div id="dialog-bili" class="modal-dialog" style="display:none;">
            <div class="modal-header">
                <span class="modal-title">
                    <img class="modal-brand-thumb" src="assets/bili_logo.png" alt="Bili">
                    哔哩哔哩凭据维护
                </span>
                <button class="btn-close" onclick="closeModal()">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                </button>
            </div>
            <div class="meta-box">
                <div class="meta-line">
                    <span class="meta-k">凭据状态</span>
                    <span class="status-badge">
                        <span id="bili-dialog-dot" class="status-point ok"></span>
                        <span id="bili-dialog-status" class="meta-v">正常</span>
                    </span>
                </div>
                <div class="meta-line">
                    <span class="meta-k">更新时间</span>
                    <span id="bili-time" class="meta-v num">--</span>
                </div>
            </div>
            <div class="qr-stage" id="bili-qr-stage">
                <div class="qr-hint" id="bili-qr-hint">点击下方按钮生成登录二维码</div>
                <img id="bili-qr-img" style="display:none;" alt="二维码">
            </div>
            <div id="bili-feedback" class="feedback-line"></div>
            <button id="bili-action-btn" class="btn-action" onclick="fetchBiliQR()">生成登录二维码</button>
        </div>

        <!-- 弹窗：小红书 -->
        <div id="dialog-xhs" class="modal-dialog" style="display:none;">
            <div class="modal-header">
                <span class="modal-title">
                    <img class="modal-brand-thumb" src="assets/xhs_logo.png" alt="XHS">
                    小红书 Web Token
                </span>
                <button class="btn-close" onclick="closeModal()">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                </button>
            </div>
            <div class="meta-box">
                <div class="meta-line">
                    <span class="meta-k">凭据状态</span>
                    <span class="status-badge">
                        <span id="xhs-dialog-dot" class="status-point warn"></span>
                        <span id="xhs-dialog-status" class="meta-v">检测中</span>
                    </span>
                </div>
                <div class="meta-line">
                    <span class="meta-k">更新时间</span>
                    <span id="xhs-time" class="meta-v num">--</span>
                </div>
            </div>
            <textarea id="xhs-token-input" class="input-text" placeholder="支持直接粘贴整段标头或Cookie文本，自动正则提取..."></textarea>
            <div id="xhs-feedback" class="feedback-line"></div>
            <button id="xhs-action-btn" class="btn-action" onclick="saveTokenGeneric('xhs')">保存凭据</button>
        </div>

        <!-- 弹窗：抖音 -->
        <div id="dialog-douyin" class="modal-dialog" style="display:none;">
            <div class="modal-header">
                <span class="modal-title">
                    <img class="modal-brand-thumb" src="assets/douyin_logo.png?v=2" alt="抖音">
                    抖音 Web Token
                </span>
                <button class="btn-close" onclick="closeModal()">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                </button>
            </div>
            <div class="meta-box">
                <div class="meta-line">
                    <span class="meta-k">凭据状态</span>
                    <span class="status-badge">
                        <span id="douyin-dialog-dot" class="status-point warn"></span>
                        <span id="douyin-dialog-status" class="meta-v">未配置</span>
                    </span>
                </div>
                <div class="meta-line">
                    <span class="meta-k">更新时间</span>
                    <span id="douyin-time" class="meta-v num">--</span>
                </div>
            </div>
            <textarea id="douyin-token-input" class="input-text" placeholder="支持直接粘贴整段标头或Cookie文本，自动正则提取..."></textarea>
            <div id="douyin-feedback" class="feedback-line"></div>
            <button id="douyin-action-btn" class="btn-action" onclick="saveTokenGeneric('douyin')">保存凭据</button>
        </div>

        <!-- 弹窗：微博 -->
        <div id="dialog-weibo" class="modal-dialog" style="display:none;">
            <div class="modal-header">
                <span class="modal-title">
                    <img class="modal-brand-thumb" src="assets/weibo_logo.png?v=2" alt="微博">
                    微博 Web Token
                </span>
                <button class="btn-close" onclick="closeModal()">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                </button>
            </div>
            <div class="meta-box">
                <div class="meta-line">
                    <span class="meta-k">凭据状态</span>
                    <span class="status-badge">
                        <span id="weibo-dialog-dot" class="status-point warn"></span>
                        <span id="weibo-dialog-status" class="meta-v">未配置</span>
                    </span>
                </div>
                <div class="meta-line">
                    <span class="meta-k">更新时间</span>
                    <span id="weibo-time" class="meta-v num">--</span>
                </div>
            </div>
            <textarea id="weibo-token-input" class="input-text" placeholder="支持直接粘贴整段标头或Cookie文本，自动正则提取..."></textarea>
            <div id="weibo-feedback" class="feedback-line"></div>
            <button id="weibo-action-btn" class="btn-action" onclick="saveTokenGeneric('weibo')">保存凭据</button>
        </div>

        <!-- 弹窗：贴吧 -->
        <div id="dialog-tieba" class="modal-dialog" style="display:none;">
            <div class="modal-header">
                <span class="modal-title">
                    <img class="modal-brand-thumb" src="assets/tieba_logo.png" alt="贴吧">
                    百度贴吧 Web Cookie
                </span>
                <button class="btn-close" onclick="closeModal()">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                </button>
            </div>
            <div class="meta-box">
                <div class="meta-line">
                    <span class="meta-k">凭据状态</span>
                    <span class="status-badge">
                        <span id="tieba-dialog-dot" class="status-point warn"></span>
                        <span id="tieba-dialog-status" class="meta-v">未配置</span>
                    </span>
                </div>
                <div class="meta-line">
                    <span class="meta-k">更新时间</span>
                    <span id="tieba-time" class="meta-v num">--</span>
                </div>
            </div>
            <textarea id="tieba-token-input" class="input-text" placeholder="支持直接粘贴整段标头或Cookie文本，自动正则提取..."></textarea>
            <div id="tieba-feedback" class="feedback-line"></div>
            <button id="tieba-action-btn" class="btn-action" onclick="saveTokenGeneric('tieba')">保存凭据</button>
        </div>

        <!-- 弹窗：知乎 -->
        <div id="dialog-zhihu" class="modal-dialog" style="display:none;">
            <div class="modal-header">
                <span class="modal-title">
                    <img class="modal-brand-thumb" src="assets/zhihu_logo.png" alt="知乎">
                    知乎 Web Cookie
                </span>
                <button class="btn-close" onclick="closeModal()">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                </button>
            </div>
            <div class="meta-box">
                <div class="meta-line">
                    <span class="meta-k">凭据状态</span>
                    <span class="status-badge">
                        <span id="zhihu-dialog-dot" class="status-point warn"></span>
                        <span id="zhihu-dialog-status" class="meta-v">未配置</span>
                    </span>
                </div>
                <div class="meta-line">
                    <span class="meta-k">更新时间</span>
                    <span id="zhihu-time" class="meta-v num">--</span>
                </div>
            </div>
            <textarea id="zhihu-token-input" class="input-text" placeholder="支持直接粘贴整段标头或Cookie文本，自动正则提取..."></textarea>
            <div id="zhihu-feedback" class="feedback-line"></div>
            <button id="zhihu-action-btn" class="btn-action" onclick="saveTokenGeneric('zhihu')">保存凭据</button>
        </div>
    </div>

    <script>
        const mask = document.getElementById('modal-mask');
        let currentModal = null;

        function openModal(id) {
            document.querySelectorAll('.modal-dialog').forEach(d => d.style.display = 'none');
            const target = document.getElementById('dialog-' + id);
            if (target) {
                target.style.display = 'block';
                mask.classList.add('is-open');
                currentModal = id;
                if (id === 'bili') refreshBili(true);
                if (id === 'xhs') refreshXhs(true);
                if (id === 'douyin') refreshGeneric('douyin', true);
                if (id === 'weibo') refreshGeneric('weibo', true);
                if (id === 'tieba') refreshGeneric('tieba', true);
                if (id === 'zhihu') refreshGeneric('zhihu', true);
            }
        }

        function closeModal() {
            mask.classList.remove('is-open');
            document.querySelectorAll('.modal-dialog').forEach(d => d.style.display = 'none');
            currentModal = null;
        }

        function handleMaskClick(e) {
            if (e.target === mask) closeModal();
        }

        window.addEventListener('keydown', function(e) {
            if (e.key === 'Escape' && mask.classList.contains('is-open')) {
                closeModal();
            }
        });

        // 卡片状态点四态：绿(结果新鲜且正常) · 橙(结果新鲜但已失效) · 空心灰(未知/结果过期) · 脉冲(探测中)
        const probingPlatforms = new Set();
        const PROBE_FRESH_SECONDS = 60;

        function setCardDot(platform, state) {
            const dot = document.getElementById('dot-' + platform);
            if (!dot) return;
            dot.classList.toggle('is-ok', state === 'ok');
            dot.classList.toggle('is-warn', state === 'warn');
            dot.classList.toggle('is-stale', state === 'stale');
            dot.classList.toggle('is-probing', state === 'probing');
        }

        function isProbeFresh(meta) {
            return !!(meta && meta.probed_at && (Date.now() / 1000 - meta.probed_at <= PROBE_FRESH_SECONDS));
        }

        // 探活结果只在 60 秒内算数，过期后一律回落为「未知」，绝不再自动探测
        function dotStateFrom(meta) {
            if (!isProbeFresh(meta)) return 'stale';
            if (meta.status === 'configured') return 'ok';
            if (meta.status === 'expired' || meta.status === 'unlogged') return 'warn';
            return 'stale';
        }

        // 进页面时并发探活一次；服务端有 60 秒节流，反复刷新不会连续打平台接口
        function probePlatform(platform) {
            probingPlatforms.add(platform);
            setCardDot(platform, 'probing');
            fetch('api/' + platform + '/probe?t=' + Date.now())
                .then(r => r.json())
                .then(d => setCardDot(platform, dotStateFrom(d)))
                .catch(() => setCardDot(platform, 'stale'))
                .finally(() => probingPlatforms.delete(platform));
        }

        function probeAllPlatforms() {
            ['bili', 'xhs', 'douyin', 'weibo', 'tieba', 'zhihu'].forEach(probePlatform);
        }

        // 常态轮询只读服务端落盘的状态，从不触碰平台接口
        function pollHubDots() {
            fetch('api/hub/status?t=' + Date.now())
                .then(r => r.json())
                .then(d => {
                    ['bili', 'xhs', 'douyin', 'weibo', 'tieba', 'zhihu'].forEach(platform => {
                        if (probingPlatforms.has(platform)) return;
                        setCardDot(platform, dotStateFrom(d[platform]));
                    });
                }).catch(() => {});
        }

        // B站逻辑
        const biliTime = document.getElementById('bili-time');
        const biliDialogDot = document.getElementById('bili-dialog-dot');
        const biliDialogStatus = document.getElementById('bili-dialog-status');
        const biliQrStage = document.getElementById('bili-qr-stage');
        const biliQrImg = document.getElementById('bili-qr-img');
        const biliQrHint = document.getElementById('bili-qr-hint');
        const biliFeedback = document.getElementById('bili-feedback');
        const biliActionBtn = document.getElementById('bili-action-btn');

        function refreshBili(withProbe) {
            if (withProbe) {
                probingPlatforms.add('bili');
                setCardDot('bili', 'probing');
                biliDialogDot.className = 'status-point probing';
                biliDialogStatus.innerText = '检测中…';
            }
            fetch(withProbe ? 'api/bili/probe?force=1&t=' + Date.now() : 'api/status?t=' + Date.now())
                .then(r => r.json())
                .then(d => {
                    biliTime.innerText = d.updated_at || '--';
                    if (d.status === 'unconfigured') {
                        biliDialogDot.className = 'status-point warn';
                        biliDialogStatus.innerText = '未配置';
                    } else if (!isProbeFresh(d)) {
                        biliDialogDot.className = 'status-point warn';
                        biliDialogStatus.innerText = '未知';
                    } else if (d.status === 'configured') {
                        biliDialogDot.className = 'status-point ok';
                        biliDialogStatus.innerText = '正常';
                    } else {
                        biliDialogDot.className = 'status-point warn';
                        biliDialogStatus.innerText = '已失效';
                    }
                    if (d.qr_b64) {
                        biliQrImg.src = d.qr_b64;
                        biliQrImg.style.display = 'block';
                        biliQrHint.style.display = 'none';
                        biliQrStage.classList.add('is-active');
                    }
                    if (d.polling_state === 'idle') {
                        biliFeedback.innerText = '';
                    } else if (d.polling_state === 'waiting' || d.polling_state === 'scanned') {
                        biliFeedback.className = 'feedback-line';
                        biliFeedback.innerText = d.msg;
                        biliActionBtn.disabled = true;
                    } else if (d.polling_state === 'success') {
                        biliFeedback.className = 'feedback-line ok';
                        biliFeedback.innerText = '✓ ' + d.msg;
                        biliActionBtn.disabled = false;
                        biliActionBtn.innerText = '再次生成';
                        pollHubDots();
                    } else if (d.polling_state === 'expired') {
                        biliFeedback.className = 'feedback-line err';
                        biliFeedback.innerText = d.msg;
                        biliActionBtn.disabled = false;
                        biliActionBtn.innerText = '重新生成';
                        biliQrStage.classList.remove('is-active');
                    }
                }).catch(() => {}).finally(() => probingPlatforms.delete('bili'));
        }

        function fetchBiliQR() {
            biliActionBtn.disabled = true;
            biliActionBtn.innerText = '正在生成...';
            biliFeedback.className = 'feedback-line';
            biliFeedback.innerText = '请求二维码...';

            fetch('api/new-qr', { method: 'POST' })
                .then(r => r.json())
                .then(d => {
                    if (d.success) {
                        biliQrImg.src = d.qr_b64;
                        biliQrImg.style.display = 'block';
                        biliQrHint.style.display = 'none';
                        biliQrStage.classList.add('is-active');
                        biliFeedback.innerText = '请用手机 App 扫码';
                        biliActionBtn.innerText = '等待确认...';
                    } else {
                        biliFeedback.className = 'feedback-line err';
                        biliFeedback.innerText = '生成失败：' + (d.error || '未知错误');
                        biliActionBtn.disabled = false;
                        biliActionBtn.innerText = '重试';
                    }
                }).catch(e => {
                    biliFeedback.className = 'feedback-line err';
                    biliFeedback.innerText = '网络异常：' + e;
                    biliActionBtn.disabled = false;
                    biliActionBtn.innerText = '重试';
                });
        }

        // 正则智能清洗核心
        function extractCookieFromRaw(raw) {
            if (!raw) return '';
            var t = raw.trim();
            var m1 = t.match(/(?:^|\r|\n)\s*cookie\s*\r?\n\s*([^\r\n]+)/i);
            if (m1 && m1[1]) return m1[1].trim();
            var m2 = t.match(/(?:^|\r|\n)\s*cookie\s*:\s*([^\r\n]+)/i);
            if (m2 && m2[1]) return m2[1].trim();
            var m3 = t.match(/([a-zA-Z0-9_\-]+=[^;\r\n]+(?:;\s*[^;\r\n]+)*)/);
            if (m3 && m3[1]) return m3[1].trim();
            return t;
        }

        // 小红书业务
        const xhsTime = document.getElementById('xhs-time');
        const xhsDialogDot = document.getElementById('xhs-dialog-dot');
        const xhsDialogStatus = document.getElementById('xhs-dialog-status');

        function refreshXhs(withProbe) {
            if (withProbe) {
                probingPlatforms.add('xhs');
                setCardDot('xhs', 'probing');
                xhsDialogDot.className = 'status-point probing';
                xhsDialogStatus.innerText = '检测中…';
                xhsTime.innerText = '--';
            }
            fetch('api/xhs/' + (withProbe ? 'probe?force=1&' : 'status?') + 't=' + Date.now())
                .then(r => r.json())
                .then(d => {
                    xhsTime.innerText = d.updated_at || '--';
                    if (d.status === 'unconfigured') {
                        xhsDialogDot.className = 'status-point warn';
                        xhsDialogStatus.innerText = '未配置';
                    } else if (!isProbeFresh(d)) {
                        xhsDialogDot.className = 'status-point warn';
                        xhsDialogStatus.innerText = '未知';
                    } else if (d.status === 'configured') {
                        xhsDialogDot.className = 'status-point ok';
                        xhsDialogStatus.innerText = '正常';
                    } else if (d.status === 'unlogged') {
                        xhsDialogDot.className = 'status-point warn';
                        xhsDialogStatus.innerText = '未登录';
                    } else {
                        xhsDialogDot.className = 'status-point warn';
                        xhsDialogStatus.innerText = '已失效';
                    }
                    pollHubDots();
                }).catch(() => {
                    xhsDialogDot.className = 'status-point warn';
                    xhsDialogStatus.innerText = '检测失败';
                }).finally(() => probingPlatforms.delete('xhs'));
        }

        // 通用平台状态刷新 (抖音 / 微博)：withProbe=true 时当场探活一次并展示进程
        function refreshGeneric(platform, withProbe) {
            const timeEl = document.getElementById(platform + '-time');
            const dotEl = document.getElementById(platform + '-dialog-dot');
            const statusEl = document.getElementById(platform + '-dialog-status');
            if (withProbe && statusEl && dotEl) {
                probingPlatforms.add(platform);
                setCardDot(platform, 'probing');
                dotEl.className = 'status-point probing';
                statusEl.innerText = '检测中…';
                if (timeEl) timeEl.innerText = '--';
            }
            fetch('api/' + platform + (withProbe ? '/probe?force=1&' : '/status?') + 't=' + Date.now())
                .then(r => r.json())
                .then(d => {
                    if (timeEl) timeEl.innerText = d.updated_at || '--';
                    if (statusEl && dotEl) {
                        if (d.status === 'unconfigured') {
                            dotEl.className = 'status-point warn';
                            statusEl.innerText = '未配置';
                        } else if (!isProbeFresh(d)) {
                            dotEl.className = 'status-point warn';
                            statusEl.innerText = '未知';
                        } else if (d.status === 'configured') {
                            dotEl.className = 'status-point ok';
                            statusEl.innerText = '正常';
                        } else if (d.status === 'unlogged') {
                            dotEl.className = 'status-point warn';
                            statusEl.innerText = '未登录';
                        } else {
                            dotEl.className = 'status-point warn';
                            statusEl.innerText = '已失效';
                        }
                    }
                    pollHubDots();
                }).catch(() => {
                    if (statusEl && dotEl) {
                        dotEl.className = 'status-point warn';
                        statusEl.innerText = '检测失败';
                    }
                }).finally(() => probingPlatforms.delete(platform));
        }

        // 通用保存逻辑 (小红书 / 抖音 / 微博)
        function saveTokenGeneric(platform) {
            const input = document.getElementById(platform + '-token-input');
            const feedback = document.getElementById(platform + '-feedback');
            const btn = document.getElementById(platform + '-action-btn');
            const rawVal = input ? input.value.trim() : '';

            if (!rawVal) {
                if (feedback) {
                    feedback.className = 'feedback-line err';
                    feedback.innerText = '请先输入文本';
                }
                return;
            }

            const cleanCookie = extractCookieFromRaw(rawVal);
            if (btn) {
                btn.disabled = true;
                btn.innerText = '正在保存...';
            }

            fetch('api/' + platform + '/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ cookie: cleanCookie })
            }).then(r => r.json()).then(d => {
                if (btn) {
                    btn.disabled = false;
                    btn.innerText = '保存凭据';
                }
                if (d.success) {
                    if (feedback) {
                        feedback.className = 'feedback-line ok';
                        feedback.innerText = '✓ 凭据已保存生效';
                    }
                    if (input) input.value = '';
                    if (platform === 'xhs') refreshXhs();
                    else refreshGeneric(platform);
                } else {
                    if (feedback) {
                        feedback.className = 'feedback-line err';
                        feedback.innerText = '保存失败：' + d.error;
                    }
                }
            }).catch(e => {
                if (btn) {
                    btn.disabled = false;
                    btn.innerText = '保存凭据';
                }
                if (feedback) {
                    feedback.className = 'feedback-line err';
                    feedback.innerText = '网络异常：' + e;
                }
            });
        }

        pollHubDots();
        probeAllPlatforms();
        setInterval(pollHubDots, 5000);
        setInterval(() => {
            if (currentModal === 'bili') refreshBili();
        }, 1500);
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Auth Hub running on 127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
