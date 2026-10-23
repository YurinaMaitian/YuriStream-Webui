# -*- encoding: utf-8 -*-
"""
Web API 层：把 main 模块的全局状态暴露为 REST 接口
启动: python webapi.py
依赖: pip install fastapi uvicorn

设计要点:
- 查询类接口直接读 main 的全局变量（同进程）
- 写操作（增删主播）只改 config/URL_config.ini —— 它是系统的唯一事实源，
  main 主循环每 3 秒重读该文件，改动自动生效，无需重启
- 删除采用"注释即停用"的软删除语义（与程序原生行为一致：# 前缀 = 暂停监控）
- 在线播放：ts/flv/mkv 先转封装为普通 MP4（moov 前置），结果缓存在 play_cache/；
  Chrome 不支持 <video> 渐进播放 fMP4，普通 MP4 全平台可播；
  h265 文件转码为 h264（仅首次，之后缓存命中零成本）；原始文件永不修改
"""

import hashlib
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.responses import JSONResponse, FileResponse

# ---- .env 支持: 项目根目录的 .env 文件提供默认配置(注意: 不入库!) ----
# 优先级: 已存在的环境变量 > .env 文件(setdefault 语义)
_ENV_FILE = Path(__file__).resolve().parent / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip("'").strip('"'))


import main as recorder
from src import spider as _spider, stream as _stream
import asyncio  # 关键：import 不再启动录制（重构的成果）


@asynccontextmanager
async def lifespan(_app):
    threading.Thread(target=recorder.main, name="recorder-core", daemon=True).start()
    threading.Thread(target=_recorder_watchdog, daemon=True).start()
    threading.Thread(target=_live_reaper, daemon=True).start()
    yield


app = FastAPI(title="DouyinLiveRecorder API", version="0.5.5", lifespan=lifespan)


# ---------------- 边录边播（HLS） ----------------
# 思路: 录制线程把当前拉流地址写进 main.live_stream_urls;
# 这里对每个观看请求单独起一条 ffmpeg, 把直播流打包成 HLS, 前端 hls.js 消费。
# 与录制进程完全独立, 互不干扰; 延迟约 3 个分片 ≈ 6 秒。

LIVE_DIR = Path("play_cache") / "live"
_live_procs: dict = {}  # record_name -> Popen
_watch_procs: dict = {}  # key -> (Popen, start_ts)  仅转播模式


def _live_slug(record_name: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff-]", "_", record_name)[:40]


def _live_reaper():
    """收割者: 主播下播或打包进程死亡时, 终止 ffmpeg 并清理分片目录"""
    while True:
        time.sleep(15)
        urls = getattr(recorder, "live_stream_urls", {})
        for name, proc in list(_live_procs.items()):
            if name not in urls or proc.poll() is not None:
                try:
                    proc.terminate()
                except Exception:
                    pass
                _live_procs.pop(name, None)
                shutil.rmtree(LIVE_DIR / _live_slug(name), ignore_errors=True)
        now = time.time()
        for key, (proc, ts) in list(_watch_procs.items()):
            if proc.poll() is not None or now - ts > 6 * 3600:
                try:
                    proc.terminate()
                except Exception:
                    pass
                _watch_procs.pop(key, None)
                shutil.rmtree(LIVE_DIR / key, ignore_errors=True)


@app.get("/api/live")
def live_list():
    """当前可观看的直播（正在录制中的）"""
    urls = getattr(recorder, "live_stream_urls", {})
    streams = []
    for name in urls:
        slug = _live_slug(name)
        proc = _live_procs.get(name)
        streams.append(
            {
                "name": name,
                "hls": f"/api/live/{slug}/index.m3u8",
                "packager_running": proc is not None and proc.poll() is None,
            }
        )
    return {"count": len(streams), "streams": streams}


def _start_packager(stream_url: str, slug: str):
    """启动 HLS 打包器并等待 playlist 就绪(最长 20s)。
    返回 (proc, ok, err) —— 前端只在就绪后挂载播放器, 避免 404 转圈。"""
    out_dir = LIVE_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in out_dir.glob("*"):  # 清掉旧分片, 防止陈旧 playlist 混入
        p.unlink()
    cmd = [
        "ffmpeg",
        "-y",
        "-user_agent",
        (
            "Mozilla/5.0 (Linux; Android 11; SAMSUNG SM-G973U) AppleWebKit/537.36 ("
            "KHTML, like Gecko) SamsungBrowser/14.2 Chrome/87.0.4280.141 Mobile Safari/537.36"
        ),
        "-rw_timeout",
        "15000000",
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_at_eof",
        "1",
        "-reconnect_delay_max",
        "60",
        "-i",
        stream_url,
        "-c",
        "copy",
        "-f",
        "hls",
        "-hls_time",
        "2",
        "-hls_list_size",
        "4",
        "-hls_flags",
        "delete_segments",
        "-hls_segment_filename",
        str(out_dir / "seg_%05d.ts"),
        str(out_dir / "index.m3u8"),
    ]
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"live_{int(time.time() * 1000)}.log"
    lf = open(log_path, "w", encoding="utf-8", errors="ignore")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=lf)
    m3u8 = out_dir / "index.m3u8"
    for _ in range(40):
        if proc.poll() is not None:  # ffmpeg 早夭 → 带日志返回错误
            lf.close()
            tail = log_path.read_text(encoding="utf-8", errors="ignore")[-500:]
            return (
                proc,
                False,
                f"ffmpeg 退出(码{proc.returncode}): {tail.strip() or '无日志'}",
            )
        if m3u8.exists() and any(out_dir.glob("seg_*.ts")):
            lf.close()
            return proc, True, None
        time.sleep(0.5)
    lf.close()
    return proc, True, None  # 超时放行, 前端有重试兜底


@app.post("/api/live/start")
def live_start(name: str):
    """为指定录制中的主播启动 HLS 打包（幂等，已运行则直接返回）"""
    urls = getattr(recorder, "live_stream_urls", {})
    if name not in urls:
        return JSONResponse({"error": "该主播当前不在录制中"}, status_code=404)
    proc = _live_procs.get(name)
    if proc is not None and proc.poll() is None:
        return {
            "ok": True,
            "already": True,
            "hls": f"/api/live/{_live_slug(name)}/index.m3u8",
        }

    slug = _live_slug(name)
    proc, ok, err = _start_packager(urls[name], slug)
    if not ok:
        return JSONResponse({"error": f"转播启动失败: {err}"}, status_code=500)
    _live_procs[name] = proc
    return {"ok": True, "hls": f"/api/live/{slug}/index.m3u8"}


@app.get("/api/live/{slug}/{filename}")
def live_file(slug: str, filename: str):
    """提供 HLS 分片与 playlist"""
    if not re.fullmatch(r"[\w.-]+", filename):
        return JSONResponse({"error": "bad file"}, status_code=400)
    f = (LIVE_DIR / slug / filename).resolve()
    if not str(f).startswith(str(LIVE_DIR.resolve())) or not f.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    media = "application/vnd.apple.mpegurl" if f.suffix == ".m3u8" else "video/mp2t"
    resp = FileResponse(f, media_type=media)
    if f.suffix == ".m3u8":
        resp.headers["Cache-Control"] = (
            "no-store"  # playlist 必须禁缓存, 否则 hls.js 拿到过期列表
        )
    return resp


def _cache_gc(max_age_days: int = 7, max_gb: float = 10) -> None:
    """播放缓存自动清理：总大小超 max_gb 或单文件超过 max_age_days 未播放时，
    从最旧的开始删除。只动 play_cache/，绝不碰原始录像。"""
    cache = Path("play_cache")
    if not cache.exists():
        return
    files = sorted(cache.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    limit = max_gb * (1 << 30)
    total = sum(p.stat().st_size for p in files)
    now = time.time()
    removed = 0
    for p in files:
        if total <= limit and (now - p.stat().st_mtime) < max_age_days * 86400:
            break
        sz = p.stat().st_size
        p.unlink(missing_ok=True)
        total -= sz
        removed += 1
    if removed:
        print(f"[webapi] 播放缓存清理: 删除 {removed} 个过期/超额缓存文件", flush=True)


def _recorder_watchdog():
    """启动 8 秒后自检一次：监控线程是否活着、配置是否已加载"""
    time.sleep(8)
    _cache_gc()
    alive = any(
        t.name == "recorder-core" and t.is_alive() for t in threading.enumerate()
    )
    ready = hasattr(recorder, "video_save_path")
    if not alive:
        print(
            "[webapi] 警告: 监控核心线程已退出！请查看上方控制台日志"
            "（常见原因: URL_config.ini 为空导致 input() 卡死或 EOFError）"
        )
    elif not ready:
        print(
            "[webapi] 警告: 监控线程存活但配置尚未加载完成，查询接口将临时使用默认录像目录"
        )


# ---------------- 查询接口 ----------------


@app.get("/api/status")
def get_status():
    """全局状态：监控数 / 正在录制（含时长、画质）"""
    now = datetime.now()
    recording_list = []
    for name in recorder.recording:
        start_time, quality = recorder.recording_time_list.get(name, (now, "未知"))
        recording_list.append(
            {
                "name": name,
                "quality": quality,
                "duration_seconds": int((now - start_time).total_seconds()),
            }
        )
    return {
        "monitoring": recorder.monitoring,
        "recording_count": len(recording_list),
        "recording": recording_list,
        "max_request": getattr(recorder, "max_request", None),
        "error_count": recorder.error_count,
    }


_name_pending: set = set()  # 已排队解析的 url(规范化)
_name_resolved: set = set()  # 已成功解析的 url
_name_sem = threading.Semaphore(2)


def _extract_line_name(line: str) -> str:
    """从配置行中提取 "主播: 名字" 段"""
    m = re.search(r"主播:\s*(.+?)\s*$", line)
    return m.group(1) if m else ""


def _write_name_to_line(url: str, name: str) -> None:
    """把解析到的名字回写到配置行(保留启停状态), 只写无名行"""
    cfg = Path(recorder.url_config_file)
    try:
        with recorder.file_update_lock:
            lines = cfg.read_text(encoding="utf-8").splitlines()
            out = []
            for line in lines:
                lu = _line_url(line)
                if (
                    lu
                    and _normalize_url(lu) == _normalize_url(url)
                    and "主播:" not in line
                ):
                    prefix = "#" if line.lstrip().startswith("#") else ""
                    out.append(f"{prefix}{lu},主播: {name}")
                else:
                    out.append(line)
            cfg.write_text("\n".join(out) + "\n", encoding="utf-8")
    except Exception:
        pass


def _resolve_name_async(url: str, key: str) -> None:
    """后台一次性解析主播名并回写(信号量限流, 防触发风控)"""

    def job():
        with _name_sem:
            try:
                r = _resolve_port_info(url)
                if r.get("ok"):
                    _write_name_to_line(url, r["info"]["anchor_name"])
                    _name_resolved.add(key)
            finally:
                _name_pending.discard(key)

    threading.Thread(target=job, daemon=True).start()


@app.get("/api/anchors")
def get_anchors():
    """主播列表: 直接以 URL_config.ini 文件为准(不再依赖主循环内存状态,
    文件被修改后界面下一次轮询即反映, 无 3 秒延迟)。
    附带: 无名条目触发一次性后台解析回写名字。"""
    names = getattr(recorder, "recording_names", {})
    active: list = []
    paused: list = []
    seen = set()
    cfg = Path(recorder.url_config_file)
    if cfg.exists():
        for line in cfg.read_text(encoding="utf-8-sig").splitlines():
            lu = _line_url(line)
            if not lu:
                continue
            key = _normalize_url(lu)
            if key in seen:
                continue  # 历史重复行只显示一条
            seen.add(key)
            name = (
                _extract_line_name(line)
                or names.get(lu, "")
                or _name_cache.get(key, "")
            )
            item = {"url": lu, "name": name}
            (paused if line.lstrip().startswith("#") else active).append(item)

    for item in active + paused:
        key = _normalize_url(item["url"])
        if not item["name"] and key not in _name_pending and key not in _name_resolved:
            _name_pending.add(key)
            _resolve_name_async(item["url"], key)
    return {"active": active, "paused": paused}


VIDEO_EXT = {".ts", ".flv", ".mp4", ".mkv", ".m4a", ".mp3"}


def _record_root() -> Path:
    """录像根目录。video_save_path 要等 main() 跑到配置加载才有，
    未就绪时回退到模块头就初始化好的 default_path"""
    return Path(
        getattr(recorder, "video_save_path", "")
        or getattr(recorder, "default_path", "downloads")
    )


def _safe_resolve(path: str) -> Path | None:
    """解析相对路径并防止路径穿越，非法返回 None"""
    root = _record_root().resolve()
    target = (root / path).resolve()
    if not str(target).startswith(str(root)) or not target.is_file():
        return None
    return target


@app.get("/api/recordings")
def get_recordings():
    """录像文件清单，按修改时间倒序"""
    root = _record_root()
    files = []
    if root.exists():
        for p in sorted(root.rglob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.is_file() and p.suffix.lower() in VIDEO_EXT:
                st = p.stat()
                files.append(
                    {
                        "name": p.name,
                        "path": str(p.relative_to(root)),
                        "size": st.st_size,
                        "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(
                            timespec="seconds"
                        ),
                    }
                )
    return {"root": str(root), "count": len(files), "files": files[:500]}


# ---------------- 写接口：增删主播 ----------------


def _resolve_port_info(url: str) -> dict:
    """解析直播间 URL → {ok, platform, info, error}（供 /api/parse 和仅转播共用）"""
    try:
        quality = recorder.get_quality_code(
            getattr(recorder, "video_record_quality", "原画")
        )
    except Exception:
        quality = "OD"
    gc = lambda k: getattr(recorder, k, "")
    try:
        if "douyin.com" in url:
            if "v.douyin.com" not in url and "/user/" not in url:
                data = asyncio.run(
                    _spider.get_douyin_web_stream_data(url=url, cookies=gc("dy_cookie"))
                )
            else:
                data = asyncio.run(
                    _spider.get_douyin_app_stream_data(url=url, cookies=gc("dy_cookie"))
                )
            info = asyncio.run(_stream.get_douyin_stream_url(data, quality, None))
            platform = "抖音"
        elif "live.kuaishou.com" in url:
            data = asyncio.run(
                _spider.get_kuaishou_stream_data(url=url, cookies=gc("ks_cookie"))
            )
            info = asyncio.run(_stream.get_kuaishou_stream_url(data, quality))
            platform = "快手"
        elif "live.bilibili.com" in url:
            data = asyncio.run(
                _spider.get_bilibili_room_info(url=url, cookies=gc("bili_cookie"))
            )
            info = asyncio.run(
                _stream.get_bilibili_stream_url(
                    data,
                    video_quality=quality,
                    proxy_addr=None,
                    cookies=gc("bili_cookie"),
                )
            )
            platform = "B站"
        elif "huya.com" in url:
            data = asyncio.run(
                _spider.get_huya_stream_data(url=url, cookies=gc("hy_cookie"))
            )
            info = asyncio.run(_stream.get_huya_stream_url(data, quality))
            platform = "虎牙"
        elif "douyu.com" in url:
            data = asyncio.run(
                _spider.get_douyu_info_data(url=url, cookies=gc("douyu_cookie"))
            )
            info = asyncio.run(
                _stream.get_douyu_stream_url(
                    data,
                    video_quality=quality,
                    cookies=gc("douyu_cookie"),
                    proxy_addr=None,
                )
            )
            platform = "斗鱼"
        else:
            return {
                "ok": False,
                "error": "暂不支持该平台（支持：抖音/快手/B站/虎牙/斗鱼）",
            }
    except Exception as e:
        return {"ok": False, "error": f"解析失败: {e}"}
    if not info or not info.get("anchor_name"):
        return {"ok": False, "error": "未获取到主播信息（可能触发风控，稍后重试）"}
    return {"ok": True, "platform": platform, "info": info}

    out_dir = LIVE_DIR / key
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in out_dir.glob("*"):
        p.unlink()
    cmd = [
        "ffmpeg",
        "-y",
        "-user_agent",
        (
            "Mozilla/5.0 (Linux; Android 11; SAMSUNG SM-G973U) AppleWebKit/537.36 ("
            "KHTML, like Gecko) SamsungBrowser/14.2 Chrome/87.0.4280.141 Mobile Safari/537.36"
        ),
        "-rw_timeout",
        "15000000",
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_at_eof",
        "1",
        "-reconnect_delay_max",
        "60",
        "-i",
        stream_url,
        "-c",
        "copy",
        "-f",
        "hls",
        "-hls_time",
        "2",
        "-hls_list_size",
        "4",
        "-hls_flags",
        "delete_segments",
        "-hls_segment_filename",
        str(out_dir / "seg_%05d.ts"),
        str(out_dir / "index.m3u8"),
    ]
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    lf = open(
        log_dir / f"live_{int(time.time() * 1000)}.log",
        "w",
        encoding="utf-8",
        errors="ignore",
    )
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=lf)
    _watch_procs[key] = (proc, time.time())
    return {
        "ok": True,
        "hls": f"/api/live/{key}/index.m3u8",
        "name": info["anchor_name"],
    }


def _normalize_url(u: str) -> str:
    """URL 规范化：去 scheme、去尾斜杠、小写，用于跨形态等价比较"""
    u = u.strip()
    u = re.sub(r"^https?://", "", u)
    return u.rstrip("/").lower()


def _line_url(line: str) -> str:
    """从 URL_config.ini 的一行中提取 URL 段。
    兼容形态: '#质量,url,主播: 名字' / 'url' / '#url' 等(含 BOM 头)"""
    l = line.lstrip("#").strip().lstrip("\ufeff").strip()
    if not l:
        return ""
    parts = re.split("[,，]", l)
    for p in parts:
        p = p.strip()
        if re.match(r"^(https?://)?[\w.-]+\.[a-zA-Z]{2,}", p):
            return p
    return ""


class AnchorIn(BaseModel):
    url: str
    name: str = ""  # 写入 "主播: 名字" 段
    paused: bool = False  # True=仅入列表(停用态), 不启动监控


@app.post("/api/live/watch")
def live_watch(item: AnchorIn):
    """仅转播不录制: 现场解析直播流地址并打包 HLS, 不写入监控列表、不落盘录像。
    主播下播后打包进程自动退出; 6 小时超时自动收摊。"""
    url = item.url.strip()
    if "://" not in url:
        url = "https://" + url
    r = _resolve_port_info(url)
    if not r["ok"]:
        return JSONResponse({"error": r["error"]}, status_code=400)
    info = r["info"]
    if not info.get("is_live"):
        return JSONResponse({"error": "主播当前未在直播"}, status_code=400)
    stream_url = info.get("flv_url") or info.get("record_url")
    if not stream_url:
        return JSONResponse({"error": "未获取到直播流地址"}, status_code=400)

    key = "w_" + hashlib.md5(url.encode()).hexdigest()[:10]
    existing = _watch_procs.get(key)
    if existing and existing[0].poll() is None:
        return {
            "ok": True,
            "hls": f"/api/live/{key}/index.m3u8",
            "name": info["anchor_name"],
        }
    proc, ok, err = _start_packager(stream_url, key)
    if not ok:
        return JSONResponse({"error": f"转播启动失败: {err}"}, status_code=500)
    _watch_procs[key] = (proc, time.time())
    return {
        "ok": True,
        "hls": f"/api/live/{key}/index.m3u8",
        "name": info["anchor_name"],
    }


@app.post("/api/parse")
def parse_anchor(item: AnchorIn):
    """解析直播间 URL：返回平台/主播名/直播状态/标题，不入库、不启动监控"""
    url = item.url.strip()
    if not re.match(r"^(https?://)?[\w.-]+\.[a-zA-Z]{2,}", url) or len(url) < 12:
        return JSONResponse({"error": "URL 不合法"}, status_code=400)
    if "://" not in url:
        url = "https://" + url

    try:
        quality = recorder.get_quality_code(
            getattr(recorder, "video_record_quality", "原画")
        )
    except Exception:
        quality = "OD"
    gc = lambda k: getattr(recorder, k, "")

    try:
        if "douyin.com" in url:
            if "v.douyin.com" not in url and "/user/" not in url:
                data = asyncio.run(
                    _spider.get_douyin_web_stream_data(url=url, cookies=gc("dy_cookie"))
                )
            else:
                data = asyncio.run(
                    _spider.get_douyin_app_stream_data(url=url, cookies=gc("dy_cookie"))
                )
            info = asyncio.run(_stream.get_douyin_stream_url(data, quality, None))
            platform = "抖音"
        elif "live.kuaishou.com" in url:
            data = asyncio.run(
                _spider.get_kuaishou_stream_data(url=url, cookies=gc("ks_cookie"))
            )
            info = asyncio.run(_stream.get_kuaishou_stream_url(data, quality))
            platform = "快手"
        elif "live.bilibili.com" in url:
            data = asyncio.run(
                _spider.get_bilibili_room_info(url=url, cookies=gc("bili_cookie"))
            )
            info = asyncio.run(
                _stream.get_bilibili_stream_url(
                    data,
                    video_quality=quality,
                    proxy_addr=None,
                    cookies=gc("bili_cookie"),
                )
            )
            platform = "B站"
        elif "huya.com" in url:
            data = asyncio.run(
                _spider.get_huya_stream_data(url=url, cookies=gc("hy_cookie"))
            )
            info = asyncio.run(_stream.get_huya_stream_url(data, quality))
            platform = "虎牙"
        elif "douyu.com" in url:
            data = asyncio.run(
                _spider.get_douyu_info_data(url=url, cookies=gc("douyu_cookie"))
            )
            info = asyncio.run(
                _stream.get_douyu_stream_url(
                    data,
                    video_quality=quality,
                    cookies=gc("douyu_cookie"),
                    proxy_addr=None,
                )
            )
            platform = "斗鱼"
        else:
            return {
                "ok": False,
                "error": "暂不支持解析该平台（支持：抖音/快手/B站/虎牙/斗鱼），可直接添加由监控线程自行解析",
            }
    except Exception as e:
        return {"ok": False, "error": f"解析失败: {e}"}

    if not info or not info.get("anchor_name"):
        return {"ok": False, "error": "未获取到主播信息（可能触发风控，稍后重试）"}
    return {
        "ok": True,
        "platform": platform,
        "name": info["anchor_name"],
        "is_live": bool(info.get("is_live")),
        "title": info.get("title", "") or "",
    }


@app.post("/api/anchors")
def add_anchor(item: AnchorIn):
    """添加监控（幂等 upsert + 合并重复行）。
    item.paused=True 时以停用形态写入（仅入列表, 不启动监控）；
    同一 URL 的历史重复行合并为一条。"""
    url = item.url.strip()
    if not re.match(r"^(https?://)?[\w.-]+\.[a-zA-Z]{2,}", url) or len(url) < 12:
        return JSONResponse({"error": "URL 不合法"}, status_code=400)
    if "://" not in url:
        url = "https://" + url

    target = _normalize_url(url)
    cfg = Path(recorder.url_config_file)
    with recorder.file_update_lock:
        lines = cfg.read_text(encoding="utf-8").splitlines() if cfg.exists() else []
        out, canonical = [], None
        for line in lines:
            lu = _line_url(line)
            if lu and _normalize_url(lu) == target:
                if canonical is None:
                    canonical = line.lstrip().lstrip("#")
                # 其余匹配行直接丢弃 = 合并历史重复
            else:
                out.append(line)
        if canonical is None:
            canonical = f"{url},主播: {item.name}" if item.name else url
        elif item.name and "主播:" not in canonical:
            canonical = f"{canonical.split(',')[0]},主播: {item.name}"
        if item.paused:
            out.append("#" + canonical)
            note = "已添加到列表（默认停用，点「启用」开始监控）"
        else:
            out.append(canonical)
            note = "已启用监控，3 秒内接管开播"
        cfg.write_text("\n".join(out) + "\n", encoding="utf-8")
    return {"ok": True, "url": url, "note": note}


@app.delete("/api/anchors")
def remove_anchor(url: str, hard: bool = False):
    """停用/删除监控（批量匹配，幂等）。默认软删除（行首加 # = 停用语义），
    所有匹配行一起处理；hard=true 物理删除全部匹配行。
    正在录制的线程会在 1 秒内感知并优雅退出。"""
    cfg = Path(recorder.url_config_file)
    if not cfg.exists():
        return JSONResponse({"error": "配置文件不存在"}, status_code=404)

    target = _normalize_url(url)
    with recorder.file_update_lock:
        lines = cfg.read_text(encoding="utf-8").splitlines()
        out, hit = [], False
        for line in lines:
            lu = _line_url(line)
            if lu and _normalize_url(lu) == target:
                hit = True
                if hard:
                    continue  # 物理删除所有匹配行
                if not line.lstrip().startswith("#"):
                    out.append("#" + line)  # 停用所有启用中的匹配行
                else:
                    out.append(line)  # 已注释的保持原样
            else:
                out.append(line)
        if not hit:
            return JSONResponse({"error": "未找到该地址"}, status_code=404)
        cfg.write_text("\n".join(out) + "\n", encoding="utf-8")
    return {"ok": True, "url": url, "hard_delete": hard}


# ---------------- 在线播放与下载 ----------------


def _probe_codec(target: Path) -> str | None:
    """从 ffmpeg -i 的 stderr 里解析视频编码（无需 ffprobe）"""
    try:
        p = subprocess.run(
            ["ffmpeg", "-i", str(target)], capture_output=True, text=True, timeout=20
        )
        m = re.search(r"Video:\s*(\w+)", p.stderr)
        return m.group(1) if m else None
    except Exception:
        return None


def _ensure_mp4(target: Path, transcode: bool) -> Path:
    """ts/flv/mkv → 普通 MP4（moov 前置，Chrome 可渐进播放），结果缓存。
    源文件更新过（比缓存新）则重新生成。h265 走 h264 转码。"""
    cache_dir = Path("play_cache")
    cache_dir.mkdir(exist_ok=True)
    key = hashlib.md5(f"{target}|{transcode}".encode()).hexdigest()[:16]
    out = cache_dir / f"{key}.mp4"
    if out.exists() and out.stat().st_mtime >= target.stat().st_mtime:
        return out

    tmp = cache_dir / f"{key}.tmp.mp4"
    cmd = ["ffmpeg", "-y", "-i", str(target)]
    if transcode:
        cmd += [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-vf",
            "format=yuv420p",
            "-c:a",
            "copy",
        ]
    else:
        cmd += ["-c", "copy"]
    cmd += ["-f", "mp4", "-movflags", "+faststart", str(tmp)]

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 转封装失败: {r.stderr[-800:]}")
    tmp.replace(out)  # 原子替换，避免并发读到半个文件
    return out


@app.get("/api/probe")
def probe(path: str):
    """调试接口：查看文件编码与播放策略"""
    target = _safe_resolve(path)
    if target is None:
        return JSONResponse({"error": "文件不存在"}, status_code=404)
    codec = _probe_codec(target)
    return {
        "path": path,
        "ext": target.suffix.lower(),
        "size": target.stat().st_size,
        "codec": codec,
        "will_transcode": codec in ("hevc", "h265"),
    }


@app.get("/api/play")
def play(path: str, transcode: bool | None = None):
    """网页在线播放：转封装为普通 MP4 返回（首次转换，之后走缓存）。
    h265 自动转 h264；?transcode=true/false 可强制指定。"""
    target = _safe_resolve(path)
    if target is None:
        return JSONResponse({"error": "文件不存在"}, status_code=404)

    ext = target.suffix.lower()
    if ext in {".mp4", ".m4a", ".mp3"}:
        return FileResponse(target)

    codec = _probe_codec(target)
    need = transcode if transcode is not None else codec in ("hevc", "h265")
    print(f"[play] {target.name}  codec={codec}  transcode={need}", flush=True)
    try:
        mp4 = _ensure_mp4(target, need)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    # 不传 filename → 无 Content-Disposition 头，规避中文文件名 latin-1 编码限制
    return FileResponse(mp4, media_type="video/mp4")


@app.get("/api/download")
def download(path: str):
    """按相对路径下载原始录像文件（不重封装，保真）"""
    target = _safe_resolve(path)
    if target is None:
        return JSONResponse({"error": "文件不存在"}, status_code=404)
    return FileResponse(target, filename=target.name)


if __name__ == "__main__":
    uvicorn.run(
        app, host="127.0.0.1", port=int(os.environ.get("YS_PORT", "8000"))
    )  # 服务器设 YS_PORT=8002
