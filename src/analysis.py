# -*- coding: utf-8 -*-

"""
AI 内容分析: 录像音频 → faster-whisper 语音转写 → 大模型结构化时间轴总结(歌单/闲聊)

配置(环境变量或项目根目录 .env, webapi 启动时会自动加载 .env):
  ASR_MODEL    faster-whisper 模型尺寸, 默认 small
               2核CPU建议 base(快), 有GPU可换 large-v3
  ASR_DEVICE   cpu / cuda, 默认 cpu
  ASR_COMPUTE  int8 / float16 / default, CPU 用 int8
  LLM_BASE_URL OpenAI 兼容接口, 默认 https://api.openai.com/v1
  LLM_API_KEY  API Key (不配置则总结阶段报错, 转写仍可进行)
  LLM_MODEL    默认 gpt-4o-mini

缓存(均在 play_cache/analysis/, 源录像永不修改):
  {key}.wav   抽取的 16k 单声道音频
  {key}.txt   原始转写结果(JSONL, 重新生成时直接复用, 省 ASR 时间)
  {key}.json  最终总结(含用户手改内容; key 含录像 mtime, 重录后自动失效)
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx

# HuggingFace 官方源在国内经常被重置连接(WinError 10054),
# 默认走镜像下载模型; 如已有可用代理, 在 .env 里设 HF_ENDPOINT 覆盖即可
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

CACHE_DIR = Path("play_cache") / "analysis"

CHUNK_SECONDS = 600  # 每次送给 LLM 的转写窗口(秒)
CHUNK_OVERLAP = 30  # 窗口重叠, 防止段落被切断

SEG_TYPES = ("song", "chat", "game", "thanks", "other")

_jobs: dict = {}
_jobs_lock = threading.Lock()
_model_cache: dict = {}


class AnalysisError(RuntimeError):
    pass


def fmt_ts(seconds) -> str:
    s = max(0, int(seconds or 0))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def parse_ts(value) -> int:
    parts = [p for p in re.split(r"[:：]", str(value).strip()) if p.strip() != ""]
    if not parts:
        return 0
    sec = 0
    for p in parts:
        try:
            n = int(p)
        except ValueError:
            return 0
        sec = sec * 60 + n
    return sec


def _cache_key(video: Path) -> str:
    raw = f"{video.resolve()}|{int(video.stat().st_mtime)}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def _wav_path(video: Path) -> Path:
    return CACHE_DIR / f"{_cache_key(video)}.wav"


def _txt_path(video: Path) -> Path:
    return CACHE_DIR / f"{_cache_key(video)}.txt"


def _json_path(video: Path) -> Path:
    return CACHE_DIR / f"{_cache_key(video)}.json"


def _songs_path(video: Path) -> Path:
    return CACHE_DIR / f"{_cache_key(video)}.songs.json"


def _extract_audio(video: Path, wav: Path) -> None:
    if wav.exists() and wav.stat().st_mtime >= video.stat().st_mtime:
        return
    wav.parent.mkdir(parents=True, exist_ok=True)
    tmp = wav.with_suffix(".tmp")
    # 显式 -f wav: 临时文件无标准扩展名, ffmpeg 无法自动推断封装格式
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-acodec",
        "pcm_s16le",
        "-f",
        "wav",
        str(tmp),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise AnalysisError(f"音频提取失败: {r.stderr[-300:]}")
    tmp.replace(wav)


def _get_whisper_model():
    from faster_whisper import WhisperModel

    model_size = os.environ.get("ASR_MODEL", "small")
    device = os.environ.get("ASR_DEVICE", "cpu")
    compute = os.environ.get("ASR_COMPUTE", "int8")
    key = (model_size, device, compute)
    if key not in _model_cache:
        _model_cache[key] = WhisperModel(
            model_size, device=device, compute_type=compute
        )
    return _model_cache[key]


def _transcribe(wav: Path, txt: Path) -> list:
    # 转写缓存命中则直接返回(重新生成时省掉最耗时的 ASR 阶段)
    if txt.exists():
        segs = []
        for line in txt.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                segs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        if segs:
            return segs
    model = _get_whisper_model()
    # ASR_VAD=0 可关闭静音检测: Silero VAD 可能把带BGM的演唱误判为非语音而过滤掉歌词
    vad = os.environ.get("ASR_VAD", "1").strip() != "0"
    # condition_on_previous_text 默认关闭: 开启时直播长音频易出现上下文漂移/繁体字/乱循环
    condition = os.environ.get("ASR_PREV", "0").strip() == "1"
    segments, _info = model.transcribe(
        str(wav),
        language="zh",
        vad_filter=vad,
        beam_size=5,
        initial_prompt="以下是直播录像的简体中文语音转写, 内容包含日常闲聊、感谢礼物以及唱歌, 歌词可能重复押韵。",
        condition_on_previous_text=condition,
    )
    # cr=compression_ratio(唱歌/重复内容通常偏高), lp=avg_logprob
    segs = [
        {
            "start": round(s.start, 2),
            "end": round(s.end, 2),
            "text": s.text.strip(),
            "cr": round(getattr(s, "compression_ratio", 0) or 0, 3),
            "lp": round(getattr(s, "avg_logprob", 0) or 0, 3),
        }
        for s in segments
    ]
    if not segs:
        raise AnalysisError("未识别到任何语音内容(可能是纯音乐直播或文件无音轨)")
    txt.parent.mkdir(parents=True, exist_ok=True)
    txt.write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in segs), encoding="utf-8"
    )
    return segs


_SYSTEM_PROMPT = (
    "你是一个直播录像内容分析助手。用户会给你一段带时间戳的直播语音转写文本。"
    "你的任务是把内容整理成一份 B站直播回放简介用的时间轴。\n"
    "判断规则:\n"
    '1. 唱歌: 以下任一特征都说明在唱歌, 必须标 type="song": '
    "(a) 转写出现大量重复、押韵、语义像歌词的句子; "
    '(b) 主播口播 "接下来/下面/下一首/唱一首/点歌" 等(唱歌前兆), 其后的内容按唱歌处理; '
    '(c) 主播口播里直接报出的歌名(如 "下面这首老男孩") 视为确证, 直接采用; '
    '(d) 文本开头附带的"权威歌单识别结果"中列出的时段——歌名已确证, 直接采用, 不要改动; 其余唱歌段根据歌词猜歌名, 猜不出填 "未知曲目", 绝不能当闲聊;\n'
    '2. 闲聊/杂谈 type="chat", 游戏 "game", 其他 "other"; 感谢礼物/SC 不要单独分段, 并入相邻闲聊段; PK/连麦时段: 第二个人的声音是对面主播, 其言行不记入主播的总结, 对面唱的歌标 "other" 不进歌单, 整段简述为和XX连麦PK;\n'
    "3. 同一话题的连续内容合并为一段(每段通常 5-15 分钟), 不到 3 分钟的小段并入相邻话题段; 歌曲的结束时间要包含尾奏——比最后一句歌词晚 15-40 秒, 不要以歌词结束为准; "
    "连续唱歌时每首歌单独一段, 歌名不同绝不合并;\n"
    "写作风格(非常重要):\n"
    "4. title/summary 要像真人随手写的时间轴: 口语、简短(每条不超过 15 个字), "
    '观众就叫 "观众/大家/弹幕", 严禁 "连麦者"、"互动展示" 这类生硬书面语, '
    "严禁把多个话题用分号串成一坨;\n"
    '5. 时间格式 "分:秒" 或 "时:分:秒", 段落不重叠不留空洞;\n'
    "6. 只输出 JSON 数组本身, 不要解释、markdown 或其他内容。\n"
    '元素格式: {"start":"00:00","end":"03:15","type":"song","title":"雨爱","summary":""}\n'
    '示例输出: [{"start":"00:00","end":"03:15","type":"song","title":"雨爱","summary":""},'
    '{"start":"03:15","end":"12:40","type":"chat","title":"","summary":"聊搬家和装修"}]'
)


def _parse_json_array(text: str) -> list:
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        raise AnalysisError(f"LLM 返回内容无法解析(缺少 JSON 数组): {text[:200]}")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise AnalysisError(f"LLM 返回 JSON 解析失败: {e}")
    if not isinstance(data, list):
        raise AnalysisError("LLM 返回结果不是数组")
    return data


def _llm_summarize_chunk(chunk_text: str) -> list:
    base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    key = os.environ.get("LLM_API_KEY", "").strip()
    model = os.environ.get("LLM_MODEL_CHUNK", "").strip() or os.environ.get(
        "LLM_MODEL", "gpt-4o-mini"
    )
    if not key:
        raise AnalysisError("未配置 LLM_API_KEY 环境变量, 无法进行 AI 总结")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": chunk_text},
        ],
        "temperature": 0.2,
    }
    try:
        resp = httpx.post(
            f"{base}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {key}"},
            timeout=180,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    except AnalysisError:
        raise
    except Exception as e:
        raise AnalysisError(f"LLM 请求失败: {e}")
    return _parse_json_array(content)


def _normalize_segment(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    start = parse_ts(raw.get("start", 0))
    end = parse_ts(raw.get("end", 0))
    if end <= start:
        end = start + 1
    stype = str(raw.get("type", "other")).lower()
    if stype == "thanks":  # 感谢环节不单列, 并入闲聊
        stype = "chat"
    if stype not in SEG_TYPES:
        stype = "other"
    # title 仅保留给唱歌段; LLM 常把总结性文字乱塞进聊天段的 title, 直接丢弃
    title = str(raw.get("title", "") or "").strip() if stype == "song" else ""
    return {
        "start": start,
        "end": end,
        "type": stype,
        "title": title,
        "summary": str(raw.get("summary", "") or "").strip(),
    }


def _merge_segments(segs: list) -> list:
    # 兜底合并: 即使 LLM 拆碎了, 同类型且间隔 <= 120s 的相邻段也并成一段
    # (song 要求歌名相同才合并; 允许 gap 为负数——切块重叠会产生完全重复的歌)
    segs = sorted(segs, key=lambda x: (x["start"], x["end"]))
    out: list = []
    max_block = 15 * 60
    for s in segs:
        if out:
            p = out[-1]
            gap = s["start"] - p["end"]
            if (
                gap <= 120
                and p["type"] == s["type"]
                and (p["end"] - p["start"]) < max_block
            ):
                same_song = (
                    (p["type"] != "song")
                    or (p["title"] and p["title"] == s["title"])
                    or (not p["title"] and not s["title"])
                )
                if same_song:
                    p["end"] = max(p["end"], s["end"])
                    if not p["summary"] and s["summary"]:
                        p["summary"] = s["summary"]
                    continue
        out.append(dict(s))
    # 全局去重: 起点几乎相同且类型/标题/总结一致的残留重复(如 1:31:02 青花瓷 x2)
    deduped: list = []
    for s in out:
        if deduped:
            p = deduped[-1]
            if (
                abs(s["start"] - p["start"]) <= 30
                and s["type"] == p["type"]
                and s["title"] == p["title"]
                and s["summary"] == p["summary"]
            ):
                p["end"] = max(p["end"], s["end"])
                continue
        deduped.append(s)
    return deduped


_PK_RE = re.compile(r"(?i)(pk|连麦|对面|偷塔|血条|暴击|加成|守塔|我们家|拉票)")


def _build_chunk_text(segs: list, start: float, end: float) -> str:
    picked = [s for s in segs if s["end"] >= start and s["start"] <= end]
    lines = [f"[{fmt_ts(s['start'])}] {s['text']}" for s in picked]
    text = "\n".join(lines)
    if any(_PK_RE.search(s["text"]) for s in picked):
        text = (
            "（提示: 本段疑似PK/连麦时段, 如出现第二个人的声音, 那是连麦对象或对面主播: "
            '其言行不要记入主播的总结, 对面唱的歌不要收入歌单, 整段可简述为"和XX连麦PK"）\n'
            + text
        )
    # 歌词特征: 同一句(>=6字)重复出现 => 副歌, 大概率在唱歌, 提示 LLM 按 song 识别
    seen: dict = {}
    for s in picked:
        norm = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", s["text"])
        if len(norm) >= 6:
            seen[norm] = seen.get(norm, 0) + 1
    if any(c >= 2 for c in seen.values()):
        text = (
            "（提示: 本段转写出现大量重复/押韵的句子, 极可能是唱歌歌词, "
            "请按 song 识别并尽量根据歌词给出歌名）\n" + text
        )
    return text


_CUE_RE = re.compile(
    r"(接下来|下面|下一首|唱一?首|给大家唱|唱个歌|唱首歌|唱段|这首歌|那首歌|这首叫"
    r"|点歌|副歌|高潮|哼一|唱什么|唱首|献唱|开唱|想听什么|点什么歌)"
)


def _norm_line(text: str) -> str:
    return re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", text)


def _find_song_candidates(segs: list) -> list:
    """低成本圈定疑似唱歌窗口, 只对候选送 Shazam:
    规则1 口播提示词("下面唱一首"等) → 圈其后的 4 分钟
    规则2 whisper 压缩率异常(>=2.4, 重复内容特征) → 圈该段前后
    规则3 副歌重复(同句>=6字在 150s 内出现>=2次) → 圈该区域
    """
    windows = []
    for sg in segs:
        if _CUE_RE.search(sg["text"]):
            windows.append((max(0.0, sg["start"] - 5), sg["start"] + 240, 1))
        if sg.get("cr", 0) >= 2.4:
            windows.append((max(0.0, sg["start"] - 30), sg["end"] + 90, 2))
    norm = [_norm_line(sg["text"]) for sg in segs]
    n = len(segs)
    i = 0
    while i < n:
        if len(norm[i]) < 6:
            i += 1
            continue
        j, cnt = i, 0
        while j < n and segs[j]["end"] - segs[i]["start"] <= 150:
            if norm[j] and norm[j] == norm[i]:
                cnt += 1
            j += 1
        if cnt >= 2:
            windows.append(
                (max(0.0, segs[i]["start"] - 15), segs[min(j, n - 1)]["end"] + 30, 3)
            )
            i = j
        else:
            i += 1
    if not windows:
        return []
    windows.sort(key=lambda w: (w[2], w[0]))  # 优先级高(数字小)的排前
    merged = []
    for st, en, pr in sorted(windows, key=lambda w: w[0]):
        if merged and st - merged[-1][1] <= 60:
            merged[-1][1] = max(merged[-1][1], en)
            merged[-1][2] = min(merged[-1][2], pr)
        else:
            merged.append([st, en, pr])
    merged.sort(key=lambda w: (w[2], w[0]))
    if len(merged) > 80:  # 防御: 候选过多说明规则失灵, 只保留高优先级
        merged = merged[:80]
    return [(st, min(en, st + 300)) for st, en, _ in sorted(merged, key=lambda w: w[0])]


def _song_provider() -> str:
    """识别源: SONG_PROVIDER 优先; 兼容旧的 SHAZAM_ENABLE=1"""
    provider = os.environ.get("SONG_PROVIDER", "").strip()
    if provider:
        return provider
    return "shazam" if os.environ.get("SHAZAM_ENABLE", "").strip() == "1" else ""


def _shazam_recognize(sample: Path) -> dict | None:
    from shazamio import Shazam

    proxy = os.environ.get("SHAZAM_PROXY", "").strip()

    async def _go():
        shazam = Shazam()
        if proxy:
            try:
                # shazamio 会把 proxy 透传给 aiohttp 的请求
                return await shazam.recognize(str(sample), proxy=proxy)
            except TypeError:
                pass  # 旧版不支持透传代理, 退回直连
        return await shazam.recognize(str(sample))

    data = asyncio.run(_go())
    track = data.get("track") or {}
    title = (track.get("title") or "").strip()
    if not title:
        return None
    return {"title": title, "artist": (track.get("subtitle") or "").strip()}


def _acrcloud_recognize(sample: Path) -> dict | None:
    """ACRCloud(国内可直连, 支持哼唱): https://www.acrcloud.cn 注册免费额度"""
    host = re.sub(r"^https?://", "", os.environ.get("ACR_HOST", "").strip())
    key = os.environ.get("ACR_ACCESS_KEY", "").strip()
    secret = os.environ.get("ACR_ACCESS_SECRET", "").strip()
    if not (host and key and secret):
        return None
    timestamp = str(int(time.time()))
    string_to_sign = "\n".join(["POST", "/v1/identify", key, "audio", "1", timestamp])
    signature = base64.b64encode(
        hmac.new(
            secret.encode("ascii"), string_to_sign.encode("ascii"), hashlib.sha1
        ).digest()
    ).decode("ascii")
    size = sample.stat().st_size
    data = {
        "access_key": key,
        "data_type": "audio",
        "audio_format": "wav",
        "signature": signature,
        "sample_ms": str(size // 32),  # 16kHz 16bit 单声道 = 32 字节/毫秒
        "sample_bytes": str(size),
        "timestamp": timestamp,
    }
    resp = httpx.post(
        f"https://{host}/v1/identify",
        data=data,
        files={"sample": (sample.name, sample.read_bytes())},
        timeout=30,
    )
    j = resp.json()
    if j.get("status", {}).get("code") != 0:
        return None
    music = (j.get("metadata") or {}).get("music") or []
    if not music:
        return None
    m = music[0]
    artists = "、".join(
        a.get("name", "") for a in (m.get("artists") or []) if a.get("name")
    )
    return {"title": (m.get("title") or "").strip(), "artist": artists}


def _recognize_sample(sample: Path) -> dict | None:
    provider = _song_provider()
    try:
        if provider == "acrcloud":
            return _acrcloud_recognize(sample)
        return _shazam_recognize(sample)
    except Exception:
        return None


def _shazam_window(wav: Path, start: float, end: float, workdir: Path) -> dict | None:
    """从窗口内取 12 秒样本送识别; 中点失败再试 20% 处一次"""
    for frac in (0.5, 0.2):
        pos = start + (end - start) * frac
        clip_start = max(0.0, pos - 6)
        sample = workdir / f"sample_{int(pos)}.wav"
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{clip_start:.1f}",
            "-t",
            "12",
            "-i",
            str(wav),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "wav",
            str(sample),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            continue
        try:
            hit = _recognize_sample(sample)
            if hit:
                return hit
        finally:
            sample.unlink(missing_ok=True)
    return None


def _shazam_scan(job_id: str, wav: Path, candidates: list, cache: Path) -> list:
    """候选窗口逐个 Shazam 识别(串行+限速), 结果落盘缓存"""
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass
    hits = []
    total = len(candidates)
    # 请求预算: 防止规则失灵时识别请求失控(1窗口最多2次调用)
    budget = int(os.environ.get("RECOG_MAX_REQUESTS", "120") or 120)
    for i, (st, en) in enumerate(candidates):
        if i * 2 >= budget:
            print(
                f"[analysis] 识别请求已达预算上限({budget}), 跳过剩余 {total - i} 个候选",
                flush=True,
            )
            break
        pct = 55 + int(20 * (i + 1) / max(total, 1))
        _set_job(job_id, pct=pct, msg=f"歌曲识别中({i + 1}/{total})…")
        hit = _shazam_window(wav, st, en, cache.parent)
        if hit:
            hits.append({"start": round(st, 1), "end": round(en, 1), **hit})
        time.sleep(1.5)  # 限速, 保护非官方接口
    cache.write_text(json.dumps(hits, ensure_ascii=False, indent=2), encoding="utf-8")
    return hits


def _song_hint_text(hits: list, cs: float, ce: float) -> str:
    """把与当前 LLM 窗口重叠的权威歌单注入提示"""
    lines = [
        f"{fmt_ts(h['start'])}-{fmt_ts(h['end'])} 《{h['title']}》"
        + (f"-{h['artist']}" if h["artist"] else "")
        for h in hits
        if h["start"] < ce and h["end"] > cs
    ]
    if not lines:
        return ""
    return (
        "（权威歌单识别结果，歌名已确证，直接采用，勿标未知曲目："
        + "；".join(lines)
        + "）"
    )


def _apply_song_hits(merged: list, hits: list) -> list:
    """Shazam 结果回填: 覆盖 LLM 段落的歌名; LLM 漏掉的唱歌段补为独立段落"""
    if not hits:
        return merged
    for sg in merged:
        if sg["type"] != "song":
            continue
        for h in hits:
            if min(sg["end"], h["end"]) - max(sg["start"], h["start"]) > 20:
                sg["title"] = h["title"]
                break
    song_ranges = [(sg["start"], sg["end"]) for sg in merged if sg["type"] == "song"]
    for h in hits:
        if not any(
            min(sg_end, h["end"]) - max(sg_start, h["start"]) > 60
            for sg_start, sg_end in song_ranges
        ):
            merged.append(
                {
                    "start": int(h["start"]),
                    "end": int(h["end"]),
                    "type": "song",
                    "title": h["title"],
                    "summary": "",
                }
            )
    merged.sort(key=lambda x: (x["start"], x["end"]))
    return merged


def _make_chunks(segs: list, target: int) -> list:
    """边界感知切块: 在目标时刻 ±2 分钟内找转写段之间最大的语音间隙下刀,
    避免切断歌曲/话题; target<=0 时整场一次送(转写文本通常仅几万字)"""
    duration = segs[-1]["end"] if segs else 0
    if target <= 0:
        return [(0.0, duration + 1)] if segs else []
    cuts = [0.0]
    pos = float(target)
    while pos < duration:
        lo, hi = max(pos - 120.0, cuts[-1] + 60.0), pos + 120.0
        best_gap, best_cut = -1.0, pos
        prev_end = None
        for sg in segs:
            if prev_end is not None and lo <= prev_end <= hi:
                gap = sg["start"] - prev_end
                if gap > best_gap:
                    best_gap, best_cut = gap, prev_end + gap / 2
            prev_end = sg["end"]
        cuts.append(best_cut)
        pos = best_cut + target
    cuts.append(duration + 1)
    return [(cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1)]


_OVERALL_PROMPT = (
    "用户会给你一场直播的内容时间轴(按时间排序的 JSON 数组, 元素含 type/title/summary)。"
    "请用 2-3 句话(不超过 120 字)整体概括这场直播, 语言口语化, 突出亮点:"
    "唱了哪些歌、做了什么、有什么名场面。只输出概括文本本身, 不要标题、引号或任何其他内容。"
)


def _llm_overall_summary(compact_json: str) -> str:
    """基于已合并的时间轴生成开场整体总结; 失败不拖垮整个任务"""
    base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    key = os.environ.get("LLM_API_KEY", "").strip()
    model = os.environ.get("LLM_MODEL_OVERALL", "").strip() or os.environ.get(
        "LLM_MODEL", "gpt-4o-mini"
    )
    if not key:
        return ""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _OVERALL_PROMPT},
            {"role": "user", "content": compact_json},
        ],
        "temperature": 0.3,
    }
    try:
        resp = httpx.post(
            f"{base}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {key}"},
            timeout=120,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()
        return text.strip('"').strip()
    except Exception:
        return ""


_RECONCILE_PROMPT = (
    "用户会给你一场直播内容时间轴的初稿(按时间排序的 JSON 数组, 元素含 start/end/type/title/summary)。"
    "请做全局校对并输出修正后的最终版本:\n"
    "1. 合并相邻的同话题闲聊段——即使措辞不同, 只要话题连续就并成一段(每段 5-15 分钟);\n"
    '2. 同一首歌被拆成多段或重复出现的合并为一段; 已确定的歌名(title 非 "未知曲目")不要改动;\n'
    '3. 修复明显的重叠、时间倒挂、时间空洞; 时间格式 "分:秒" 或 "时:分:秒";\n'
    '4. 修正类型误判: 夹在唱歌中间的感谢口播归入歌曲段; 对面主播/连麦对象唱的歌 type 改为 "other" '
    "(不进歌单), 其闲聊不记入主播的话题;\n"
    "5. 保持口语简短风格(每条 summary 不超过 15 个字);\n"
    "6. 只输出修正后的 JSON 数组本身, 不要任何解释。"
)


def _llm_reconcile(segs: list) -> list:
    """终稿全局把关: 整份时间轴交给 LLM 做合并/纠错/类型修正; 失败则退回代码合并结果"""
    base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    key = os.environ.get("LLM_API_KEY", "").strip()
    if not key or not segs:
        return segs
    model = os.environ.get("LLM_MODEL_RECONCILE", "").strip() or os.environ.get(
        "LLM_MODEL", "gpt-4o-mini"
    )
    compact = [
        {
            "start": fmt_ts(sg["start"]),
            "end": fmt_ts(sg["end"]),
            "type": sg["type"],
            "title": sg["title"],
            "summary": sg["summary"],
        }
        for sg in segs
    ]
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _RECONCILE_PROMPT},
            {"role": "user", "content": json.dumps(compact, ensure_ascii=False)},
        ],
        "temperature": 0.2,
    }
    try:
        resp = httpx.post(
            f"{base}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {key}"},
            timeout=240,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        fixed = []
        for raw in _parse_json_array(content):
            norm = _normalize_segment(raw)
            if norm:
                fixed.append(norm)
        return fixed if fixed else segs
    except Exception:
        return segs


def _set_job(job_id: str, **kwargs) -> None:
    with _jobs_lock:
        _jobs.setdefault(job_id, {}).update(kwargs)


def job_status(job_id: str) -> dict:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return {"status": "failed", "msg": "任务不存在或已过期"}
    return dict(job)


def start_analysis(video: Path) -> str:
    job_id = uuid.uuid4().hex[:12]
    _set_job(job_id, status="running", pct=0, msg="排队中…", path=str(video))
    threading.Thread(
        target=_run_analysis,
        args=(job_id, video),
        name=f"analysis-{job_id}",
        daemon=True,
    ).start()
    return job_id


def _run_analysis(job_id: str, video: Path) -> None:
    try:
        _set_job(job_id, pct=5, msg="提取音频…")
        wav = _wav_path(video)
        _extract_audio(video, wav)

        _set_job(job_id, pct=15, msg="语音转写中(CPU 转写较慢, 请耐心等待)…")
        segs = _transcribe(wav, _txt_path(video))

        # 歌曲识别(可选, SHAZAM_ENABLE=1): 规则圈候选 → 只对候选送 Shazam
        hits = []
        if _song_provider():
            try:
                candidates = _find_song_candidates(segs)
                # 全量粗扫(可选): 按固定间隔全程取样, 与候选重叠处自动跳过
                # RECOG_SCAN_INTERVAL=60 → 2小时约120次请求(原唱档约¥2.4/场), 基本不漏歌
                interval = int(os.environ.get("RECOG_SCAN_INTERVAL", "0") or 0)
                if interval > 0 and segs:
                    duration = segs[-1]["end"]
                    t = 0.0
                    added = 0
                    while t < duration:
                        if not any(st - 15 <= t <= en for st, en in candidates):
                            candidates.append((t, t + 12))
                            added += 1
                        t += interval
                    if added:
                        candidates.sort()
                        print(
                            f"[analysis] 粗扫模式: 候选 {len(candidates) - added} 段 + 补扫 {added} 点",
                            flush=True,
                        )
                if candidates:
                    _set_job(
                        job_id, pct=55, msg=f"歌曲识别中(候选 {len(candidates)} 段)…"
                    )
                    hits = _shazam_scan(job_id, wav, candidates, _songs_path(video))
                else:
                    _set_job(job_id, pct=55, msg="未检测到唱歌候选, 跳过歌曲识别")
            except Exception as e:
                # Shazam 只是增强: 任何失败都降级为纯 LLM 模式, 不拖垮任务
                print(
                    f"[analysis] Shazam 识别不可用({e}), 降级为纯 LLM 模式", flush=True
                )
                _set_job(job_id, pct=55, msg=f"Shazam 不可用({e}), 改用纯 LLM 识别")

        # LLM_CHUNK_SECONDS: 切块目标时长(默认600); 0 = 整场一次送(需长上下文模型)
        chunk_target = int(os.environ.get("LLM_CHUNK_SECONDS", "600") or 600)
        chunks = _make_chunks(segs, chunk_target)

        collected = []
        for i, (cs, ce) in enumerate(chunks):
            text = _build_chunk_text(segs, cs - CHUNK_OVERLAP, ce)
            hint = _song_hint_text(hits, cs, ce)
            if hint:
                text = hint + "\n" + text
            if not text.strip():
                continue
            pct = 75 + int(20 * (i + 1) / max(len(chunks), 1))
            _set_job(job_id, pct=pct, msg=f"AI 总结中({i + 1}/{len(chunks)})…")
            for raw in _llm_summarize_chunk(text):
                norm = _normalize_segment(raw)
                if norm:
                    collected.append(norm)

        _set_job(job_id, pct=94, msg="全局校对中…")
        merged = _llm_reconcile(_apply_song_hits(_merge_segments(collected), hits))
        _set_job(job_id, pct=97, msg="生成整体总结…")
        compact = [
            {
                "start": fmt_ts(sg["start"]),
                "type": sg["type"],
                "title": sg["title"],
                "summary": sg["summary"],
            }
            for sg in merged
        ]
        overall = _llm_overall_summary(json.dumps(compact, ensure_ascii=False))
        result = {
            "version": 1,
            "video": video.name,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "asr_model": os.environ.get("ASR_MODEL", "small"),
            "llm_model": os.environ.get("LLM_MODEL", "gpt-4o-mini"),
            "overall": overall,
            "segments": merged,
        }
        out = _json_path(video)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _set_job(job_id, status="done", pct=100, msg="完成", result=result)
    except AnalysisError as e:
        _set_job(job_id, status="failed", msg=str(e))
    except Exception as e:
        _set_job(job_id, status="failed", msg=f"未知错误: {e}")


def get_analysis(video: Path) -> dict | None:
    p = _json_path(video)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def save_analysis(video: Path, segments: list, overall: str | None = None) -> dict:
    # 保存段落(含用户手改), 保留原元数据
    old = get_analysis(video) or {}
    segs = []
    for raw in segments or []:
        norm = _normalize_segment(raw)
        if norm:
            segs.append(norm)
    segs.sort(key=lambda x: (x["start"], x["end"]))
    result = {
        "version": 1,
        "video": old.get("video", video.name),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "asr_model": old.get("asr_model", os.environ.get("ASR_MODEL", "small")),
        "llm_model": old.get("llm_model", os.environ.get("LLM_MODEL", "gpt-4o-mini")),
        "edited": True,
        "overall": (overall.strip() if overall is not None else old.get("overall", "")),
        "segments": segs,
    }
    p = _json_path(video)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
