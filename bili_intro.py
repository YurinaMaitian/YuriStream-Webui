# -*- coding: utf-8 -*-
"""
B站投稿简介生成器 (bili_intro.py)  —— 放在项目根目录, 与 webapi.py 同级

数据来源与优先级 (每层都可缺, 层层兜底):
  主播名:   meta.json anchor → 录像所在目录名(folder_by_author) → 文件名前缀
  日期:     meta.json start_time → 文件名时间戳(主播_2026-10-30_21-47-00.ts) → 今天
  直播间:   meta.json url → profiles.json 的 room_url → (show_home/解析) home_url 自动解析
  主页:     profiles.json 的 home_url (默认不在简介显示, show_home=true 才显示)
  歌单:     play_cache/analysis/<key>.json 的 segments (full / songs 两种模式通用)

旧录像(无 meta.json)只要在 profiles.json 给主播填一次链接即可正常使用。

CLI:
  python bili_intro.py "downloads/抖音直播/主播/主播_2026-10-30_21-47-00.ts"
  python bili_intro.py <video> --json          # 完整上传 payload
  python bili_intro.py --resolve-room <抖音主页URL>   # 主页 → 直播间地址
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

# ---------------- 默认配置 (config/uploader_profiles.json 可按主播名覆盖) ----------------
DEFAULT_PROFILE: dict = {
    "tid": 146,  # 音乐综合; 常用: 翻唱31 / 音乐现场1011
    "tag": "直播回放,弹唱,翻唱",
    "title_pattern": "【{anchor}】{date} 直播回放",
    "declaration": "本视频为录播转载，不含商业推广，如有侵权问题请联系删减或下架",
    "include_unknown_songs": True,  # 未知曲目也列出 (观众评论区补歌名)
    "min_song_seconds": 12,  # 短于该时长的歌不进投稿歌单(v9: 数据层降级不删, 导出层过滤)
    "include_low_conf": True,
    "room_url": "",  # 原直播间地址 (建议: 填这个, 简介主链接)
    "home_url": "",  # 主播主页 (可选: room_url 为空时自动解析出直播间)
    "show_home": False,  # 简介里是否显示主页地址行 (默认关: sec_uid 链接太长)
    "max_tag_len": 80,
}

PROFILES_PATH = Path("config/uploader_profiles.json")

# 转化链来自 src/room.py get_unique_id(): 抖音用户主页 → iesdouyin share 页 → unique_id
_IESDOUYIN_HEADERS = {
    "User-Agent": "ios/7.830 (ios 17.0; ; iPhone 15 (A2846/A3089/A3090/A3092))",
    "Cookie": (
        "ttwid=1%7C4ejCkU2bKY76IySQENJwvGhg1IQZrgGEupSyTKKfuyk%7C1740470403%7Cbc9a"
        "d2ee341f1a162f9e27f4641778030d1ae91e31f9df6553a8f2efa3bdb7b4; __ac_nonce=06"
        "83e59f3009cc48fbab0; __ac_signature=_02B4Z6wo00f01mG6waQAAIDB9JUCzFb6.TZhmsU"
        "AAPBf34; __ac_referer=__ac_blank"
    ),
}


# ---------------- 工具函数 ----------------


def fmt_ms(seconds) -> str:
    """歌单行用紧凑格式 (h:)mm:ss —— B站简介时间戳可点击跳转"""
    s = max(0, int(seconds or 0))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _norm_anchor(name: str) -> str:
    """record_name 形如 '序号3 主播名', 剥掉序号前缀"""
    return re.sub(r"^序号\d+\s*", "", (name or "").strip()).strip()


def _anchor_from_path(video_path: str) -> str:
    """meta 缺失时: 录像目录名(folder_by_author=是时即主播名) → 文件名前缀"""
    try:
        p = Path(video_path)
        parent = p.parent.name.strip()
        if parent and parent not in (".", "downloads"):
            return parent
        stem = p.stem
        m = re.match(r"(.+?)_\d{4}-\d{2}-\d{2}_", stem)
        return m.group(1).strip("_ ") if m else stem
    except Exception:
        return ""


def _time_from_meta_or_name(meta: dict, video_path: str) -> tuple[str, str]:
    """(date, HH:MM); 依次尝试 meta → 文件名时间戳 → 今天"""
    raw = str(meta.get("start_time") or meta.get("finished_at") or "")
    m = re.search(r"(\d{4}-\d{2}-\d{2})[ T_](\d{2}):(\d{2})", raw)
    if not m:
        m = re.search(
            r"(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})", Path(video_path).name
        )
        if m:
            return m.group(1), f"{m.group(2)}:{m.group(3)}"
        return datetime.now().strftime("%Y-%m-%d"), ""
    return m.group(1), f"{m.group(2)}:{m.group(3)}"


def _load_profiles() -> dict:
    try:
        if PROFILES_PATH.exists():
            data = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[bili_intro] 档案读取失败({e}), 使用默认配置")
    return {}


def load_profile(anchor: str) -> dict:
    """默认档案 ← '默认' 段 ← 主播名段, 逐层覆盖"""
    profile = dict(DEFAULT_PROFILE)
    data = _load_profiles()
    for key in ("默认", anchor):
        if isinstance(data.get(key), dict):
            profile.update(data[key])
    return profile


# ---------------- 主页 → 直播间地址 ----------------


def resolve_room_from_home(home_url: str, timeout: int = 15) -> str:
    """抖音用户主页 → 稳定直播间地址 (主播未开播也能拿到; 转化链同 src/room.py get_unique_id)

    主页URL里的 MS4wLjAB... 是 sec_uid → 请求 iesdouyin share 页 → 正则 unique_id
    → https://live.douyin.com/{unique_id} (离线时打开仅显示未开播, 地址长期稳定)
    """
    m = re.search(r"douyin\.com/user/([\w\-]+)", home_url or "")
    if not m:
        return ""
    import httpx

    sec_uid = m.group(1)
    r = httpx.get(
        f"https://www.iesdouyin.com/share/user/{sec_uid}",
        headers=_IESDOUYIN_HEADERS,
        follow_redirects=True,
        timeout=timeout,
    )
    matches = re.findall(r'unique_id":"(.*?)","verification_type', r.text)
    return f"https://live.douyin.com/{matches[-1]}" if matches else ""


def get_effective_home(meta: dict, video_path: str, profile: dict | None = None) -> str:
    """meta.home_url → profiles.json home_url"""
    home = str(meta.get("home_url") or "").strip()
    if home:
        return home
    anchor = _norm_anchor(meta.get("anchor", "")) or _anchor_from_path(video_path)
    prof = profile or load_profile(anchor)
    return str(prof.get("home_url") or "").strip()


# ---------------- 核心构建 ----------------


def build_intro(
    meta: dict, analysis: dict, video_path: str = "", profile: dict | None = None
) -> str:
    anchor = (
        _norm_anchor(meta.get("anchor", ""))
        or _anchor_from_path(video_path)
        or "未知主播"
    )
    profile = profile or load_profile(anchor)
    date, start_time = _time_from_meta_or_name(meta, video_path)

    room_url = str(meta.get("url") or "").strip().split("?")[0]
    if not room_url:
        room_url = str(profile.get("room_url") or "").strip().split("?")[0]
    if not room_url:
        home = get_effective_home(meta, video_path, profile)
        try:
            room_url = resolve_room_from_home(home)
        except Exception:
            room_url = ""
    home_url = get_effective_home(meta, video_path, profile).split("?")[0]

    segments = analysis.get("segments", [])
    duration = fmt_ms(max((int(s.get("end", 0)) for s in segments), default=0))

    songs = [
        s
        for s in segments
        if s.get("type") == "song"
        and int(s.get("end", 0)) - int(s.get("start", 0))
        >= int(profile.get("min_song_seconds", 12))  # v9: 短片段不进歌单
        and (profile["include_low_conf"] or s.get("conf") == "high")
        and (
            profile["include_unknown_songs"]
            or (s.get("title") or "").strip() not in ("", "未知曲目")
        )
    ]
    song_lines = (
        "\n".join(
            f"{fmt_ms(s['start'])} 《{(s.get('title') or '').strip() or '未知曲目'}》"
            for s in songs
        )
        or "（本场未识别到歌曲）"
    )

    lines = []
    if home_url and profile.get("show_home"):
        lines.append(f"主页地址：{home_url}")
    lines.append(f"原直播间：{room_url}")
    lines.append(f"直播日期：{date}" + (f" {start_time}" if start_time else ""))
    lines.append(f"完整时长：{duration}")
    header = "\n".join(lines)

    return f"{header}\n\n※ {profile['declaration']}\n\n本场歌单：\n{song_lines}"


def build_title(meta: dict, video_path: str = "", profile: dict | None = None) -> str:
    anchor = (
        _norm_anchor(meta.get("anchor", ""))
        or _anchor_from_path(video_path)
        or "未知主播"
    )
    profile = profile or load_profile(anchor)
    date, _ = _time_from_meta_or_name(meta, video_path)
    return profile["title_pattern"].format(anchor=anchor, date=date)[:80]


def build_upload_payload(
    meta: dict, analysis: dict, video_path: str, profile: dict | None = None
) -> dict:
    """供 webapi / biliup-rs / 上传接口直接消费的完整参数包"""
    anchor = (
        _norm_anchor(meta.get("anchor", ""))
        or _anchor_from_path(video_path)
        or "未知主播"
    )
    profile = profile or load_profile(anchor)
    tags = [t.strip() for t in re.split(r"[,，]", profile["tag"]) if t.strip()]
    room_url = str(meta.get("url") or profile.get("room_url") or "").split("?")[0]
    if not room_url:
        try:
            room_url = resolve_room_from_home(
                get_effective_home(meta, video_path, profile)
            )
        except Exception:
            room_url = ""
    return {
        "video": video_path,
        "title": build_title(meta, video_path, profile),
        "desc": build_intro(meta, analysis, video_path, profile),
        "tid": profile["tid"],
        "tag": ",".join(tags)[: profile["max_tag_len"]],
        "copyright": 2,  # 转载, 主播约定①, 不允许档案覆盖成自制
        "source": room_url,  # B站转载必填原链接
    }


# ---------------- CLI ----------------


def main() -> None:
    ap = argparse.ArgumentParser(description="从录像 meta + AI 分析生成 B站投稿简介")
    ap.add_argument("video", nargs="?", help="录像文件路径")
    ap.add_argument("--analysis", help="analysis.json 路径 (默认按 webapi 同规则查找)")
    ap.add_argument("--json", action="store_true", help="输出完整上传 payload JSON")
    ap.add_argument(
        "--resolve-room",
        metavar="HOME_URL",
        help="抖音用户主页 → 稳定直播间地址 (未开播也可解析)",
    )
    args = ap.parse_args()

    if args.resolve_room:
        room = resolve_room_from_home(args.resolve_room)
        print(room or "解析失败: 请检查主页URL是否为 douyin.com/user/ 形式")
        return
    if not args.video:
        ap.error("缺少录像路径 (或先用 --resolve-room 解析直播间地址)")

    video = Path(args.video)
    meta: dict = {}
    for cand in (
        Path(str(video) + ".meta.json"),
        video.with_name(video.name + ".meta.json"),
    ):
        if cand.exists():
            try:
                meta = json.loads(cand.read_text(encoding="utf-8"))
            except Exception:
                pass
            break

    ana_p = Path(args.analysis) if args.analysis else None
    if ana_p is None:
        import hashlib

        raw = f"{video.resolve()}|{int(video.stat().st_mtime)}"
        key = hashlib.md5(raw.encode()).hexdigest()[:16]
        ana_p = Path("play_cache/analysis") / f"{key}.json"
    analysis = json.loads(ana_p.read_text(encoding="utf-8")) if ana_p.exists() else {}

    if args.json:
        print(
            json.dumps(
                build_upload_payload(meta, analysis, str(video)),
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(build_title(meta, str(video)) + "\n" + "=" * 40 + "\n")
        print(build_intro(meta, analysis, str(video)))


if __name__ == "__main__":
    main()
