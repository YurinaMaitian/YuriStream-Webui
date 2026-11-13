# -*- coding: utf-8 -*-

"""
抖音弹幕采集 collector（douyinLive sidecar 客户端）

架构角色:
- 本模块只做"客户端": 连本地 douyinLive 服务(默认 ws://127.0.0.1:1088/ws/{room}),
  按 method 过滤, 落盘 jsonl, 维护分段台账。签名/protobuf/抖音协议一概不碰。
- 时间基准 anchor 由录制线程在首次启动 ffmpeg 时注入(wall clock epoch 秒),
  collector 自己不产生时间基准 —— 断线重连后用同一 anchor 继续算, 零漂移。
- ts = 收到消息时的墙钟 - anchor, 负值钳到 0(冷启动握手期提前到达的弹幕)。

落盘产物(与录像同目录):
- {base}.danmaku.jsonl     一场直播一份, 持续追加, 每行 {"ts", "user", "text"}
- {base}.danmaku.segments.json  分段台账 {anchor, jsonl, segments:[{file,spawn,end}]}

生命周期(关键设计):
- 挂在 monitor 线程/record_name 上, 不跟随单个 ffmpeg 进程:
  ffmpeg 断流重录时 collector 不断, 缺口弹幕仍写 jsonl, 由合并阶段统一丢弃/重映射。
- stop() 时机: clear_record_info(主播被注释/删除) 时由 main.py 调用。
"""

import configparser
import json
import os
import re
import threading
import time
from collections import deque
from pathlib import Path

WS_BASE = os.environ.get("DANMU_WS_BASE", "ws://127.0.0.1:1088/ws/")
RECONNECT_DELAY = 5.0
MAX_RECONNECT_DELAY = 60.0
SEGMENT_GAP_THRESHOLD = 3.0  # 相邻段 spawn-end 间隔超过该值视为断连缺口(秒)
CHAT_METHOD = "WebcastChatMessage"
LIVE_BUFFER_SIZE = 200  # 内存环形缓冲(供未来直播 SSE 用)

PER_ANCHOR_FILE = Path("config") / "danmaku_config.ini"

_sessions: dict = {}
_lock = threading.Lock()


# ---------------- 按主播开关配置 ----------------


def _norm_url(url: str) -> str:
    url = url.strip().lower()
    url = re.sub(r"^https?://", "", url)
    return url.split("?")[0].rstrip("/")


def per_anchor_prefs() -> dict:
    """读取按主播覆盖 {normalized_url: bool}; 文件不存在返回 {}"""
    if not PER_ANCHOR_FILE.exists():
        return {}
    cp = configparser.ConfigParser()
    try:
        cp.read(PER_ANCHOR_FILE, encoding="utf-8")
        return {k: v.strip().lower() == "on" for k, v in cp.items("PerAnchor")}
    except Exception:
        return {}


def pref_for_url(record_url: str) -> bool | None:
    """None = 未单独设置(跟随全局开关)"""
    return per_anchor_prefs().get(_norm_url(record_url))


def set_pref(record_url: str, enabled: bool) -> None:
    """webapi 用: 写入按主播开关(幂等 upsert)"""
    PER_ANCHOR_FILE.parent.mkdir(exist_ok=True)
    cp = configparser.ConfigParser()
    if PER_ANCHOR_FILE.exists():
        cp.read(PER_ANCHOR_FILE, encoding="utf-8")
    if "PerAnchor" not in cp:
        cp.add_section("PerAnchor")
    cp.set("PerAnchor", _norm_url(record_url), "on" if enabled else "off")
    with open(PER_ANCHOR_FILE, "w", encoding="utf-8") as f:
        cp.write(f)


# ---------------- 工具 ----------------

_room_id_cache: dict = {}


def room_id_from_url(record_url: str) -> str | None:
    """数字 room_id(web_rid) 提取。
    - live.douyin.com/{id} 直接正则;
    - 用户主页/短链: 换算出 web_rid 后再喂给 douyinLive(它不接受内部雪花 id_str)。
      get_unique_id 走用户主页→抖音号(未开播也可换算, 缓存跨场复用);
      直播分享短链(重定向带 reflow)由 get_sec_user_id 的 room_id 分支承接。
    未开播时主页下不存在直播间, 无从连接(此时也无录制在跑)。"""
    m = re.search(r"live\.douyin\.com/(\d+)", record_url)
    if m:
        return m.group(1)
    key = record_url.split("?")[0]
    if key in _room_id_cache:
        return _room_id_cache[key]
    try:
        import asyncio
        from src.room import get_unique_id, get_sec_user_id, UnsupportedUrlError

        try:
            rid = asyncio.run(get_unique_id(record_url))
        except UnsupportedUrlError:
            rid, _sec = asyncio.run(get_sec_user_id(record_url))
        if rid:
            _room_id_cache[key] = str(rid)
            return str(rid)
    except Exception as e:
        print(f"[danmaku] 房间号换算失败({record_url}): {e}")
    return None


def _base_path(save_file_path: str) -> str:
    """去掉扩展名与 _%03d 分片模板尾缀 → 场次档案基名"""
    stem = re.sub(r"\.[^.]+$", "", save_file_path)
    return re.sub(r"_%03d$", "", stem)


def _extract_chat(msg: dict) -> tuple | None:
    """只保留真人弹幕, 返回 (user, text)"""
    if msg.get("method") != CHAT_METHOD:
        return None
    user = str((msg.get("user") or {}).get("nickname") or "?").strip()
    text = msg.get("content") or (msg.get("payload") or {}).get("content") or ""
    text = str(text).strip()
    if not text:
        return None
    return user, text


# ---------------- 会话管理(由 main.py 调用) ----------------


def on_ffmpeg_start(
    record_name: str, record_url: str, save_file_path: str, enabled: bool
) -> None:
    """每个 ffmpeg 进程启动时调用: 首次调用创建会话(定 anchor), 每次记录分段"""
    room_id = room_id_from_url(record_url)
    if not room_id:
        return
    with _lock:
        s = _sessions.get(record_name)
        if s is None:
            if not enabled:
                return
            s = _Session(record_name, room_id, _base_path(save_file_path))
            _sessions[record_name] = s
            s.start()
        s.segment_start(save_file_path, time.time())


def on_ffmpeg_end(record_name: str) -> None:
    """ffmpeg 进程正常结束/异常退出都调用: 只收尾当前分段, 会话继续存活"""
    with _lock:
        s = _sessions.get(record_name)
    if s is not None:
        s.segment_end(time.time())


def stop(record_name: str) -> None:
    """主播被注释/删除时调用: 结束整个会话"""
    with _lock:
        s = _sessions.pop(record_name, None)
    if s is not None:
        s.stop()


def get_session(record_name: str):
    """webapi 用: 取会话(环形缓冲/路径信息)"""
    with _lock:
        return _sessions.get(record_name)


# ---------------- 会话实现 ----------------


class _Session:
    def __init__(self, record_name: str, room_id: str, base: str):
        self.record_name = record_name
        self.room_id = room_id
        self.jsonl_path = base + ".danmaku.jsonl"
        self.segments_path = base + ".danmaku.segments.json"
        self.anchor: float | None = None
        self.segments: list = []
        self._stop_event = threading.Event()
        self._file_lock = threading.Lock()
        self._live_buffer: deque = deque(maxlen=LIVE_BUFFER_SIZE)
        self._clamped_early = 0
        self._live_seq = 0
        self._live_lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run, name=f"danmaku-{record_name}", daemon=True
        )

    # -- 生命周期 --
    def start(self) -> None:
        print(f"[danmaku] 会话启动: {self.record_name} room={self.room_id}")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self.segment_end(time.time())
        print(
            f"[danmaku] 会话结束: {self.record_name} "
            f"共 {len(self.segments)} 段, 提前钳0弹幕 {self._clamped_early} 条"
        )

    # -- 分段台账 --
    def segment_start(self, file_path: str, wall: float) -> None:
        if self.anchor is None:
            self.anchor = wall  # 场次锚点: 首个 ffmpeg spawn 墙钟
        self.segments.append({"file": str(file_path), "spawn": wall, "end": None})
        self._persist_segments()

    def segment_end(self, wall: float) -> None:
        if self.segments and self.segments[-1]["end"] is None:
            self.segments[-1]["end"] = wall
            self._persist_segments()

    def _persist_segments(self) -> None:
        payload = {
            "anchor": self.anchor,
            "jsonl": self.jsonl_path,
            "segments": self.segments,
        }
        tmp = Path(str(self.segments_path) + ".tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.segments_path)
        except Exception as e:
            print(f"[danmaku] 台账写入失败: {e}")

    # -- WS 采集 --
    def _run(self) -> None:
        delay = RECONNECT_DELAY
        while not self._stop_event.is_set():
            try:
                self._ws_loop_once()
                delay = RECONNECT_DELAY  # 成功跑完一轮(断开)后重置退避
            except Exception as e:
                if not self._stop_event.is_set():
                    print(f"[danmaku] 连接异常, {delay:.0f}s 后重连: {e}")
            if self._stop_event.wait(delay):
                break
            delay = min(delay * 2, MAX_RECONNECT_DELAY)

    def _ws_loop_once(self) -> None:
        import asyncio
        import websockets

        async def _loop():
            url = WS_BASE + self.room_id
            async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                print(f"[danmaku] 已连接 {self.record_name} ← {url}")
                while not self._stop_event.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=15)
                    except asyncio.TimeoutError:
                        continue  # 仅用于周期性检查 stop_event
                    self._handle_message(raw)

        asyncio.run(_loop())

    def _handle_message(self, raw: str) -> None:
        try:
            r = _extract_chat(json.loads(raw))
        except Exception:
            return
        if r is None:
            return
        user, text = r
        ts = time.time() - self.anchor
        if ts < 0:
            ts = 0.0
            self._clamped_early += 1
        event = {"ts": round(ts, 2), "user": user, "text": text}
        with self._live_lock:
            self._live_seq += 1
            self._live_buffer.append([self._live_seq, event])
        with self._file_lock:
            try:
                with open(self.jsonl_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event, ensure_ascii=False) + "\n")
            except Exception as e:
                print(f"[danmaku] jsonl 写入失败: {e}")

    def live_events_since(self, after_seq: int) -> tuple:
        """直播 SSE 用: 返回 (当前序号, after_seq 之后的事件)"""
        with self._live_lock:
            items = [it for it in self._live_buffer if it[0] > after_seq]
            return self._live_seq, [it[1] for it in items]


# ---------------- 合并视频的弹幕重映射(webapi 用) ----------------


def _probe_duration(p: Path) -> float:
    import subprocess

    try:
        r = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(p),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return float(r.stdout.strip() or 0)
    except Exception:
        return 0.0


def _seg_matches(seg_file: str, src: Path) -> bool:
    """台账分段文件名匹配实际录像文件名。
    宽松规则: 1) _%03d 分片模板(只替换 %03d, 保留下划线分隔符);
    2) 扩展名不同但 stem 相同 —— ts 录制+自动转 mp4 时, 台账记的是 ffmpeg 输入(.ts),
    磁盘上播放的是转封装产物(.mp4), stem 一致即视为同一段。
    """
    name = Path(seg_file).name
    if "%03d" in name:
        pat = re.escape(name).replace("%03d", r"(\d{3})")
        if re.fullmatch(pat, src.name) is not None:
            return True
        # 精确失配 → 放宽扩展名再试一次(转封装 .ts → .mp4)
        pat_anyext = re.sub(r"\\.[^.]+$", r"\.[^.]+$", pat)
        return re.fullmatch(pat_anyext, src.name) is not None
    if name == src.name:
        return True
    return Path(name).stem == src.stem


def build_merged_danmaku(sources: list[Path], out_path: Path) -> dict | None:
    """
    合并视频完成后生成 {out}.danmaku.json:
    - 正常自分段: 各段 ts 相对场次锚点已是同一时间轴, 直接前缀累加, 无缺口;
    - 断连缺口: 检测 spawn 间隔 > 阈值的相邻段, 缺口内弹幕只在这个视图丢弃(源 jsonl 不动),
      段2弹幕直接接到段1末尾(有画面处), 并返回 gaps 供前端提醒。
    """
    # 1. 收集源目录全部台账, 匹配每个源文件所属分段
    ledgers: dict = {}
    for src in sources:
        for led in sorted(src.parent.glob("*.danmaku.segments.json")):
            if led in ledgers:
                continue
            try:
                data = json.loads(led.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("anchor") is not None and data.get("segments"):
                ledgers[led] = data

    matched: list = []
    for src in sources:
        for led, data in ledgers.items():
            for seg in data["segments"]:
                if _seg_matches(seg["file"], src):
                    matched.append(
                        {
                            **seg,
                            "anchor": float(data["anchor"]),
                            "jsonl": data.get("jsonl"),
                        }
                    )
                    break

    if not matched:
        return None

    # 2. 按时序排段, 计算每段在合并时间轴上的前缀偏移与总时长
    matched.sort(key=lambda s: float(s["spawn"]))
    total = 0.0
    for m in matched:
        f = Path(m["file"])
        dur = 0.0
        if "%03d" not in m["file"] and f.exists():
            dur = _probe_duration(f)
        if dur <= 0:
            dur = max(0.0, float(m.get("end") or m["spawn"]) - float(m["spawn"]))
        m["dur"] = dur
        m["prefix"] = total
        total += dur

    # 3. 断连缺口检测(相邻段之间)
    gaps: list = []
    for a, b in zip(matched, matched[1:]):
        gap = float(b["spawn"]) - float(a.get("end") or a["spawn"])
        if gap > SEGMENT_GAP_THRESHOLD:
            gaps.append(
                {
                    "after": Path(a["file"]).name,
                    "before": Path(b["file"]).name,
                    "seconds": round(gap, 1),
                }
            )

    # 4. 事件重映射: wall = anchor + ts → 所在段 → prefix + (wall - spawn); 落在段外(缺口)丢弃
    events: list = []
    dropped_in_gap = 0
    seen_jsonl: set = set()
    for m in matched:
        jp = m.get("jsonl")
        if not jp or jp in seen_jsonl or not Path(jp).exists():
            continue
        seen_jsonl.add(jp)
        anchor = float(m["anchor"])
        seg_list = [s for s in matched if s.get("jsonl") == jp]
        try:
            lines = Path(jp).read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for line in lines:
            try:
                ev = json.loads(line)
            except Exception:
                continue
            wall = anchor + float(ev.get("ts", 0))
            seg = next(
                (
                    s
                    for s in seg_list
                    if float(s["spawn"]) <= wall <= float(s.get("end") or s["spawn"])
                ),
                None,
            )
            if seg is None:
                dropped_in_gap += 1
                continue
            events.append(
                {
                    "ts": round(seg["prefix"] + (wall - float(seg["spawn"])), 2),
                    "user": ev.get("user", ""),
                    "text": ev.get("text", ""),
                }
            )

    events.sort(key=lambda e: e["ts"])
    out_file = out_path.parent / f"{out_path.stem}.danmaku.json"
    out_file.write_text(
        json.dumps(
            {
                "gaps": gaps,
                "dropped_in_gap": dropped_in_gap,
                "count": len(events),
                "events": events,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(
        f"[danmaku] 合并弹幕视图已生成: {out_file.name} "
        f"{len(events)} 条, 缺口 {len(gaps)} 处, 缺口丢弃 {dropped_in_gap} 条"
    )
    return {
        "file": out_file.name,
        "count": len(events),
        "gaps": gaps,
        "dropped_in_gap": dropped_in_gap,
    }


MAX_PLAYBACK_EVENTS = 50000


def playback_events_for(file_path: Path) -> dict | None:
    """
    为单个录像文件生成"文件本地时间轴"的弹幕事件(回放播放器用):
    - 合并产物: 存在 {stem}.danmaku.json(合并时生成)则直接采用(含 gaps 信息);
    - 普通文件: 匹配台账分段, ts_out = ts - (spawn - anchor);
    - 自分段文件(xxx_001.ts): 匹配 _%03d 模板段, 偏移再累计之前分片实际时长(ffprobe)。
    返回 None 表示该文件没有任何弹幕档案。
    """
    merged = file_path.parent / f"{file_path.stem}.danmaku.json"
    if merged.exists():
        try:
            data = json.loads(merged.read_text(encoding="utf-8"))
            return {
                "count": data.get("count", 0),
                "gaps": data.get("gaps", []),
                "merged": True,
                "events": (data.get("events") or [])[:MAX_PLAYBACK_EVENTS],
            }
        except Exception:
            pass

    session = seg = None
    for led in sorted(file_path.parent.glob("*.danmaku.segments.json")):
        try:
            data = json.loads(led.read_text(encoding="utf-8"))
        except Exception:
            continue
        for s in data.get("segments", []):
            if _seg_matches(s["file"], file_path):
                session, seg = data, s
                break
        if session is not None:
            break
    if session is None:
        return None

    anchor = float(session["anchor"])
    offset = float(seg["spawn"]) - anchor
    if "%03d" in seg["file"]:
        pattern = re.compile(
            re.escape(Path(seg["file"]).name).replace(r"%03d", r"(\d{3})")
        )
        m = pattern.fullmatch(file_path.name)
        idx = int(m.group(1)) if m else 1
        stem_glob = re.escape(Path(seg["file"]).stem).replace(r"%03d", "*")
        for prev in sorted(file_path.parent.glob(f"{stem_glob}{file_path.suffix}")):
            mm = pattern.fullmatch(prev.name)
            if mm and int(mm.group(1)) < idx and prev.exists():
                offset += _probe_duration(prev)

    events: list = []
    jp = session.get("jsonl")
    spawn = float(seg["spawn"])
    end = float(seg.get("end") or seg["spawn"])
    if jp and Path(jp).exists():
        for line in Path(jp).read_text(encoding="utf-8").splitlines():
            try:
                ev = json.loads(line)
            except Exception:
                continue
            wall = anchor + float(ev.get("ts", 0))
            if spawn - 0.5 <= wall <= end + 0.5:
                tso = float(ev["ts"]) - offset
                if tso >= -0.5:
                    events.append(
                        {
                            "ts": round(max(tso, 0.0), 2),
                            "user": ev.get("user", ""),
                            "text": ev.get("text", ""),
                        }
                    )
    events.sort(key=lambda e: e["ts"])
    return {
        "count": len(events),
        "gaps": [],
        "merged": False,
        "events": events[:MAX_PLAYBACK_EVENTS],
    }
