import urllib.request
import json
import time
import email.utils
from datetime import datetime
import xml.sax.saxutils as saxutils
import re

def title_to_slug(title):
    s = title.lower().replace('.', '-')
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'[\s_]+', '-', s).strip('-')
    if not s.startswith('minecraft'):
        s = 'minecraft-' + s
    return s

_cache = {
    "patchnotes_time": 0,
    "patchnotes_xml": "",
    "news_time": 0,
    "news_xml": "",
    "content_cache": {}
}

def format_rfc822(iso_str):
    try:
        # e.g. 2026-09-14T12:58:38.000Z or 2026-09-05
        if "T" in iso_str:
            clean_str = iso_str.split(".")[0].replace("Z", "")
            dt = datetime.strptime(clean_str, "%Y-%m-%dT%H:%M:%S")
        else:
            dt = datetime.strptime(iso_str, "%Y-%m-%d")
        return email.utils.format_datetime(dt)
    except Exception:
        return email.utils.format_datetime(datetime.utcnow())

def get_patch_body(content_path):
    if content_path in _cache["content_cache"]:
        return _cache["content_cache"][content_path]
    try:
        url = "https://launchercontent.mojang.com/v2/" + content_path
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
            body = data.get("body", "")
            _cache["content_cache"][content_path] = body
            return body
    except Exception as e:
        return ""

def generate_patchnotes_rss():
    now = time.time()
    if _cache["patchnotes_xml"] and now - _cache["patchnotes_time"] < 600:
        return _cache["patchnotes_xml"]

    url = "https://launchercontent.mojang.com/v2/javaPatchNotes.json"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))

    entries = data.get("entries", [])[:15]
    items_xml = []

    for item in entries:
        title = item.get("title", "Minecraft Update")
        date_rfc = format_rfc822(item.get("date", ""))
        guid = item.get("id", item.get("version", ""))
        img_info = item.get("image", {})
        img_url = ""
        if img_info and "url" in img_info:
            img_url = "https://launchercontent.mojang.com" + img_info["url"]

        content_path = item.get("contentPath", "")
        body_html = ""
        if content_path:
            body_html = get_patch_body(content_path)
        if not body_html:
            body_html = "<p>" + saxutils.escape(item.get("shortText", "")) + "</p>"

        desc = ""
        if img_url:
            desc += f'<p><img src="{img_url}" referrerpolicy="no-referrer" style="max-width:100%;"/></p>'
        desc += body_html

        # Mojang official link
        slug = title_to_slug(title)
        link = f"https://www.minecraft.net/en-us/article/{slug}"

        item_xml = f"""    <item>
        <title>{saxutils.escape(title)}</title>
        <link>{link}</link>
        <guid isPermaLink="false">{guid}</guid>
        <pubDate>{date_rfc}</pubDate>
        <description><![CDATA[{desc}]]></description>
    </item>"""
        items_xml.append(item_xml)

    now_rfc = email.utils.format_datetime(datetime.utcnow())
    feed_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
    <title>Minecraft Official Java Patch Notes</title>
    <link>https://www.minecraft.net/en-us/articles</link>
    <description>Official first-hand Minecraft Java Edition patch notes, snapshots and releases directly from Mojang.</description>
    <language>en</language>
    <lastBuildDate>{now_rfc}</lastBuildDate>
    <ttl>60</ttl>
{chr(10).join(items_xml)}
</channel>
</rss>"""

    _cache["patchnotes_xml"] = feed_xml
    _cache["patchnotes_time"] = now
    return feed_xml

def generate_news_rss():
    now = time.time()
    if _cache["news_xml"] and now - _cache["news_time"] < 600:
        return _cache["news_xml"]

    url = "https://launchercontent.mojang.com/v2/news.json"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))

    entries = data.get("entries", [])[:20]
    items_xml = []

    for item in entries:
        title = item.get("title", "Minecraft News")
        date_rfc = format_rfc822(item.get("date", ""))
        guid = item.get("id", "")
        link = item.get("readMoreLink", "https://www.minecraft.net/en-us/articles")
        text = item.get("text", "")
        
        img_info = item.get("newsPageImage", item.get("playPageImage", {}))
        img_url = ""
        if img_info and "url" in img_info:
            img_url = "https://launchercontent.mojang.com" + img_info["url"]

        desc = ""
        if img_url:
            desc += f'<p><img src="{img_url}" referrerpolicy="no-referrer" style="max-width:100%;"/></p>'
        desc += f'<p>{saxutils.escape(text)}</p>'
        desc += f'<p><a href="{link}" target="_blank">Read full article on Minecraft.net &rarr;</a></p>'

        item_xml = f"""    <item>
        <title>{saxutils.escape(title)}</title>
        <link>{link}</link>
        <guid isPermaLink="false">{guid}</guid>
        <pubDate>{date_rfc}</pubDate>
        <description><![CDATA[{desc}]]></description>
    </item>"""
        items_xml.append(item_xml)

    now_rfc = email.utils.format_datetime(datetime.utcnow())
    feed_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
    <title>Minecraft.net Official News &amp; Articles</title>
    <link>https://www.minecraft.net/en-us/articles</link>
    <description>Official primary articles, deep-dives, DLC releases and announcements from Minecraft.net.</description>
    <language>en</language>
    <lastBuildDate>{now_rfc}</lastBuildDate>
    <ttl>60</ttl>
{chr(10).join(items_xml)}
</channel>
</rss>"""

    _cache["news_xml"] = feed_xml
    _cache["news_time"] = now
    return feed_xml

if __name__ == "__main__":
    print("Testing PatchNotes RSS...")
    p_xml = generate_patchnotes_rss()
    print("PatchNotes RSS length:", len(p_xml))
    print("Testing News RSS...")
    n_xml = generate_news_rss()
    print("News RSS length:", len(n_xml))
    print("Sample PatchNote title:", p_xml.split("<title>")[2].split("</title>")[0])
