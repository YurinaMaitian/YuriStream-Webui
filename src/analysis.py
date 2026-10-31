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

吞歌修复版(2026-10-30)改动:
  1. _shazam_window 返回列表: 指纹命中互相矛盾时窗口二分重扫(窗口跨两首歌不再只留一首)
  2. _split_segment_by_hits: 一段被多首不同歌命中时拆分(修 _apply_song_hits 只取第一个命中的吞歌)
  3. _mergeable_title: 未知曲目/空标题一律不合并(_merge_segments/_merge_adjacent_songs/_bridge_split_songs 三处)
  4. 识别候选排序: 12s 粗扫点排最后, 预算耗尽不再系统性漏掉直播后半场
  5. _drop_short_songs 软化: 20-45s 无证据短段保留并标 low(宁多列勿吞); _conf_tier 短段最高 mid
  6. _normalize_segment 保留 engine/rec_times + reconcile/dedup 后 _reattach_evidence 回补
     (原逻辑在完整模式下会把 reco 段的证据字段洗掉, 置信分层全落成 low)
  7. _known_titles 优先收割人工改过的结果, 且跳过 conf=low(错配歌名进 hotwords 会自我强化)
  8. reconcile 提示词: 唱歌段一律不合并(点歌重唱是常态, 防同名歌跨时段被并)

v2(2026-10-30)改动:
  9. 弱命中否决: <=20s 短窗(12s粗扫点)投票无独立性, 与长窗异名强命中相邻/重叠时出局
     (治《时光》30:01 这类粗扫点幻听; 孤立短窗命中保留——真有只唱十几秒的)
 10. 识别别名表 lyrics/recog_alias.json: 顽固性 humming 误配登记一次永久生效
     (治《带我走》→《弦上连蹇》; 值可为正确歌名或空串=一律降级未知)
 11. 歌词反向校验: hum 识别结果在歌词库中存在但与本段文本检索完全无重合且 top1>=0.5
     → 降级未知进待审
 12. 未知曲目 conf 由 low(存疑)改为 mid(待审)——未知=需要人工命名, 语义上不是存疑
 13. 待定段附带歌词检索 top3 候选(cands 字段), 供前端一键改歌名

v3(2026-10-30)改动:
 14. _reattach_evidence 对号方式改"起点±3s 优先 + 最大重叠(>=50%较短者)兜底":
     把关LLM修复重叠/空洞时会整体挪动段边界 5-30s, 纯±3s 对不上 → 证据字段全丢 →
     全场误标存疑(本版修复的最大 bug)
 15. 弱命中判定从"窗口<=20s"改为"样本间距<12s"(12s采样clip互相重叠即无独立投票),
     堵住 _recognize_llm_songs 对短段外扩±5s 凑出 23s 窗口绕过弱命中判定的漏洞;
     旧缓存按 <48s 窗口估算, 《时光》类幻听在两条路径上都会被拦
 16. weak/engine/rec_times 传播到全部建段路径(歌单模式装配/回填/拆分/二轮/补漏);
     弱命中不再构成短段豁免证据; _conf_tier 中 weak: 哼唱→low, 指纹上限mid
 17. 别名表缺失时输出带绝对路径的提示日志; 前端人工改对识别错名后
     自动学习进 lyrics/recog_alias.json(改一次, 永久生效; v4 起 fp 错配也学习)
 18. 每段 conf 判定输出决策日志(src/engine/rec/weak), 排查置信问题不用再猜

v4(2026-10-30)改动(修 v3 引入的回退):
 19. 反向校验否决改为"文本证据矛盾降权": v3 在 32 首小歌词库+边界偏移窗口上把
     关键词/红色高跟鞋/起风了/太聪明/蒲公英的约定等正确歌名误抹为未知——
     文本证据从此只许把 conf 降为 mid 进待审, 永不许改标题;
     且 LLM 歌词判断 high 确认原标题时检索无权降权
 20. _veto_weak_hits 停用: 长窗≠可信(rec=0 的 fp 也会错), 误杀正确弱命中;
     弱命中统一走段级规则(短弱丢弃/长弱压conf), 时光类幻听仍会被拦
 21. fp 需 rec_times>=50 才给 high(《有趣在职的情怀》这种 rec=0 的错配不再自动变绿)
 22. 识别命中缓存去重(二轮/补漏每轮追加导致重复条目积累)
 23. 上游降权(mid/low)在 _conf_tier 里只许压低不许提级

v5(2026-10-30)改动(根因修复, 依据 full 模式实跑日志):
 24. 防窗口外溢护栏: 段边界有±20-30s偏移, 段文本尾部常混入下一首的开头歌词,
     歌词LLM因此频繁把段判成"下一首歌"(09:56→红色高跟鞋/34:20→老男孩/1:03:54→
     我们的爱 三连惨案, 《关键词》被错翻后与真身相邻同名合并而消失)。
     翻案/降权目标=相邻歌段标题时一律视为外溢噪声, 不算证据
 25. 低rec识别(rec_times<50)计入"原标题不可信": 解锁 LLM high 单独翻案
     (25:15《有趣在职的情怀》→《牵丝戏》这类本地32首小库无法检索佐证的正确翻案);
     同时短段(<60s)只信检索实锤, 防短段被文本噪声带偏(2:03:47《晴》→情歌 被拦)
 26. 拆分命中消费记账: reconcile平铺的重叠大段覆盖同一对命中会拆出两套重叠子段
     (05:33与09:19双重拆分), 已消费的命中不再驱动后续拆分
 27. 版本标注括号代码侧确定性剥除((Live)/(笛子演奏版)/(DJ版)等, 全模式生效;
     原规则只在歌单模式的提示词里, 且LLM经常忘), 语言版本括号保留
 28. 收尾再桥接合并一次: 二轮/补漏追加的同歌碎片中间夹短闲聊段时并回
     (2:03:47/2:04:29 两个《晴》), 早期那次桥接管不到晚到的段

v6(2026-10-30)改动(修 v5 实跑暴露的对齐/耐久问题):
 29. 唱歌段开始时间戳对齐音频级音乐段起点(只提前不推后):
     命中窗口中点切界偏进歌里几十秒(关键词标9:56实际9:19),
     inaSpeechSegmenter 音乐起点实测与真实开唱仅差秒级, 以它为准
 30. 单命中回填改取"重叠最多"的命中(原取第一个>20s):
     拆分被消费记账拦截的段范围内常压着前一首歌的命中尾巴,
     10:33 段因此错名无情画夹进关键词中间; 取最大重叠后与相邻关键词并段
 31. 时间轴收尾整形 _resolve_overlaps: 对齐/拆分/桥接后的重叠边界截齐
 32. start_analysis 幂等: 同视频同模式有在跑任务时复用。
     双击产生的排队任务跑完会覆盖用户中途保存的手改, 且识别/LLM花两份钱;
     另: 覆盖含人工手改(edited)的旧结果前自动备份 .edited-backup.json
 33. 未知曲目短段松绑: chars>=100 时允许 LLM high 翻案
     (v5 短段护栏误拦 1:18:39→绝对占有相对自由 的正确翻案)
 34. 歌词LLM三票并行(ThreadPoolExecutor): 串行~6.5分钟→并行≈单票耗时
     (ACRCloud扫描保持串行限速——非官方接口, 并行有封禁风险, 且有缓存只跑一次)

v7(2026-10-31)改动(根因修复: 同代码两次跑出天差地别歌单的问题):
 35. 段落切片缓存 {key}.tiles.json: chunk总结+把关是LLM现场生成(temp>0),
     每跑一遍切法都不同——上次切得好这次切出8分钟巨型段, 一切下游随之崩塌。
     锁定一次好切片, 重跑稳定复现; chunk温度降为0; 切片差时删.tiles.json重掷
 36. 巨型唱歌段(>7min)按音乐段起点拆分: 段文本被多数歌词主导→命名错一半+
     吞掉其余歌(36:17段8分钟被判成老男孩)。子段取最大重叠命中, 无命中→未知
     交歌词仲裁(文本变短后仲裁反而更准); 单首长歌无内部切点不受影响
 37. fp低热度(rec<50)命中默认降未知: fp误配是位置性噪声, 同一错名在不同位置
     掩盖不同的歌; 例外=位置匹配的别名(aliased标记)或歌词LLM high确认原标题
 38. 别名表v2: hum误配全局生效(字符串值), fp误配引擎+位置锁定
     ({"to":..,"engine":"fp","at":秒,"tol":300})——修《有趣在职的情怀》在31:02/35:38
     被全局别名转成幻影牵丝戏的惨案; 自动学习按引擎分别写入两种格式
 39. 可疑标题降级未知改为全src生效: reconcile搬运后证据字段丢失(src=llm)的
     《Trio Sonata...》类垃圾标题不再漏网(LLM high确认原标题的除外)
 40. 二轮/补漏识别先查.songs.json缓存命中再发网络请求: 重跑接近零成本
 41. 巨型段conf上限mid(拆分失败兜底); 切片质量自检(>=2个巨型段时提示重掷)
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
import zlib
from datetime import datetime
from pathlib import Path

import httpx

# HuggingFace 官方源在国内经常被重置连接(WinError 10054),
# 默认走镜像下载模型; 如已有可用代理, 在 .env 里设 HF_ENDPOINT 覆盖即可
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

CACHE_DIR = Path("play_cache") / "analysis"


def _log(msg: str) -> None:
    """[analysis] 日志: 同时打控制台和追加到 logs/analysis.log(主程序会清屏, 以文件为准)"""
    line = f"{datetime.now().strftime('%H:%M:%S')} [pid{os.getpid()}] {msg}"
    print(line, flush=True)
    try:
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        with open(log_dir / "analysis.log", "a", encoding="utf-8") as _fp:
            _fp.write(line + "\n")
    except Exception:
        pass


CHUNK_SECONDS = 600  # 每次送给 LLM 的转写窗口(秒)
CHUNK_OVERLAP = 30  # 窗口重叠, 防止段落被切断

SEG_TYPES = ("song", "chat", "game", "thanks", "other")

_jobs: dict = {}
_jobs_lock = threading.Lock()
_analysis_lock = (
    threading.Lock()
)  # 全局分析互斥: 同一时刻只允许一个分析任务(防并发污染缓存)
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


# 段落切片缓存版本: 切片提示词/合并/把关逻辑变更时 +1, 使旧缓存失效
TILES_VERSION = 2


def _tiles_path(video: Path) -> Path:
    return CACHE_DIR / f"{_cache_key(video)}.tiles.json"


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
    threads = _env_int("CPU_THREADS", 4)  # whisper 推理线程数(服务器2核就设2)
    key = (model_size, device, compute, threads)
    if key not in _model_cache:
        try:
            _model_cache[key] = WhisperModel(
                model_size,
                device=device,
                compute_type=compute,
                cpu_threads=threads,
                num_workers=1,
            )
        except Exception as e:
            # 模型下载/加载失败自动降级, 任务不中断
            fallback = "medium" if model_size != "medium" else "small"
            _log(f"whisper模型 {model_size} 加载失败({e}), 回落 {fallback}")
            fb_compute = "int8" if device == "cpu" else compute
            _model_cache[key] = WhisperModel(
                fallback,
                device=device,
                compute_type=fb_compute,
                cpu_threads=threads,
                num_workers=1,
            )
    return _model_cache[key]


def _text_cr(text: str) -> float:
    """zlib 压缩率, 与 whisper 的 compression_ratio 同口径(重复内容偏高)"""
    raw = text.encode("utf-8")
    return round(len(zlib.compress(raw)) / max(1, len(raw)), 3)


def _get_sensevoice_model():
    from funasr import AutoModel

    key = ("sv",)
    if key not in _model_cache:
        threads = _env_int("CPU_THREADS", 0)
        if threads > 0:
            try:
                import torch

                torch.set_num_threads(threads)
            except Exception:
                pass
        device = os.environ.get("SV_DEVICE", "") or os.environ.get("ASR_DEVICE", "cpu")
        _model_cache[key] = AutoModel(
            model="iic/SenseVoiceSmall",
            model_revision="master",
            vad_model="fsmn-vad",
            vad_kwargs={"max_single_segment_time": 30000},
            device=device,
            disable_update=True,
        )
    return _model_cache[key]


def _transcribe_sensevoice(wav: Path) -> list:
    """SenseVoiceSmall 全量快转(2核CPU友好, 中文闲聊准), 带 VAD 段级时间戳"""
    from funasr.utils.postprocess_utils import rich_transcription_postprocess

    model = _get_sensevoice_model()
    res = model.generate(
        input=str(wav),
        cache={},
        language="zh",
        use_itn=True,
        batch_size=int(os.environ.get("SV_BATCH", "4") or 4),
        merge_vad=True,
        merge_length_s=15,
    )
    segs = []
    for r in res:
        ts = r.get("timestamp") or []
        if ts:
            st, en = ts[0][0] / 1000.0, ts[-1][1] / 1000.0
        else:
            st = en = 0.0
        text = rich_transcription_postprocess(r.get("text", "")).strip()
        if not text:
            continue
        segs.append(
            {
                "start": round(st, 2),
                "end": round(en, 2),
                "text": text,
                "cr": _text_cr(text),
                "lp": 0.0,
            }
        )
    if not segs or all(sg["end"] <= 0 for sg in segs):
        raise AnalysisError("SenseVoice 未返回有效时间戳")
    return segs


_known_titles_cache: list | None = None


def _known_titles() -> str:
    """从 play_cache/analysis/*.json 收割历史已确认歌名(识别命中+手改保存的),
    用于 whisper hotwords 偏置与(后续)本地歌词检索的曲库清单"""
    global _known_titles_cache
    if _known_titles_cache is not None:
        return " ".join(_known_titles_cache)
    titles_auto: list = []
    titles_edited: list = []
    try:
        for f in CACHE_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue  # 跳过识别缓存等数组结构(.songs.json)
            # 人工改过的结果优先: 自动结果里的错配歌名进 hotwords 会自我强化(反馈回路)
            bucket = titles_edited if data.get("edited") else titles_auto
            for sg in data.get("segments", []):
                t = (sg.get("title") or "").strip()
                if (
                    sg.get("type") == "song"
                    and t
                    and t != "未知曲目"
                    and len(t) <= 14
                    and sg.get("conf") != "low"
                ):
                    bucket.append(t)
    except Exception:
        pass
    _known_titles_cache = sorted(set(titles_edited or titles_auto))
    if _known_titles_cache:
        _log(f"已收割历史歌名 {len(_known_titles_cache)} 首(用于ASR偏置)")
    return " ".join(_known_titles_cache)


def _whisper_second_pass(wav: Path, segs: list, music_segs: list) -> list:
    """对 music 段重叠的转写用 whisper 重转(歌词更准), 只接受更完整的结果"""
    model = _get_whisper_model()
    tmp_dir = CACHE_DIR / "clips"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    replaced = 0
    out = []
    for sg in segs:
        hit = next(
            (
                m
                for m in music_segs
                if min(sg["end"], m["end"]) - max(sg["start"], m["start"]) > 5
            ),
            None,
        )
        if hit and sg["end"] - sg["start"] >= 10:
            clip = tmp_dir / f"clip_{int(sg['start'] * 10)}.wav"
            dur = sg["end"] - sg["start"] + 2
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                f"{max(0.0, sg['start'] - 1):.1f}",
                "-t",
                f"{dur:.1f}",
                "-i",
                str(wav),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "wav",
                str(clip),
            ]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0:
                try:
                    parts, _ = model.transcribe(
                        str(clip),
                        language="zh",
                        vad_filter=False,
                        beam_size=5,
                        log_prob_threshold=-1.0,
                        initial_prompt="以下是直播录像的简体中文语音转写, 内容包含日常闲聊、感谢礼物以及唱歌, 歌词可能重复押韵。",
                        hotwords=_known_titles() or None,
                        word_timestamps=True,
                    )
                    # word级置信标注: 低置信的字用*包裹, 下游LLM对这些字打折
                    words = []
                    for pt in parts:
                        for w in pt.words or []:
                            wd = (w.word or "").strip()
                            if not wd:
                                continue
                            words.append(
                                wd if (w.probability or 0) >= 0.4 else f"*{wd}*"
                            )
                    text = " ".join(words)
                    if text and len(text.replace("*", "")) >= len(
                        sg["text"].replace("*", "")
                    ):
                        sg = dict(sg)
                        sg["text"] = text
                        sg["cr"] = _text_cr(text)
                        replaced += 1
                except Exception:
                    pass
                finally:
                    clip.unlink(missing_ok=True)
        out.append(sg)
    if replaced:
        _log(f"歌词段 whisper 二遍: 重转 {replaced}/{len(segs)} 段")
    return out


def _transcribe_whisper(wav: Path) -> list:
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
    segs = [
        {
            "start": round(sg.start, 2),
            "end": round(sg.end, 2),
            "text": sg.text.strip(),
            "cr": round(getattr(sg, "compression_ratio", 0) or 0, 3),
            "lp": round(getattr(sg, "avg_logprob", 0) or 0, 3),
        }
        for sg in segments
    ]
    if not segs:
        raise AnalysisError("未识别到任何语音内容(可能是纯音乐直播或文件无音轨)")
    return segs


def _transcribe(wav: Path, txt: Path, video: Path) -> list:
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
    # ASR_PROVIDER=sensevoice: SenseVoice 全量快转 + music 段 whisper 二遍; 异常自动回落 whisper
    if os.environ.get("ASR_PROVIDER", "sensevoice").strip() == "sensevoice":
        try:
            segs = _transcribe_sensevoice(wav)
            _log(f"SenseVoice 快转完成: {len(segs)} 段")
            # 先落盘: 后续阶段卡住/中断时不丢失转写结果
            txt.parent.mkdir(parents=True, exist_ok=True)
            txt.write_text(
                "\n".join(json.dumps(sg, ensure_ascii=False) for sg in segs),
                encoding="utf-8",
            )
            music_segs = []
            if _env_int("MUSIC_SEG_ENABLE", 1):
                try:
                    _log(
                        "[analysis] inaSpeechSegmenter 音乐段检测中(静默, 2小时音频约10-30分钟)…"
                    )
                    music_segs = _detect_music_segments(wav, _music_cache_path(video))
                    _log(f"音乐段检测完成: {len(music_segs)} 段")
                except Exception as e:
                    _log(f"音频级音乐段检测不可用({e}), 跳过歌词二遍")
            if music_segs:
                segs = _whisper_second_pass(wav, segs, music_segs)
        except Exception as e:
            _log(f"SenseVoice 不可用({e}), 回落 faster-whisper")
            segs = _transcribe_whisper(wav)
    else:
        segs = _transcribe_whisper(wav)
    txt.parent.mkdir(parents=True, exist_ok=True)
    txt.write_text(
        "\n".join(json.dumps(sg, ensure_ascii=False) for sg in segs), encoding="utf-8"
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
    '(d) 文本开头附带的"权威歌单识别结果"中列出的时段——歌名已确证, 直接采用, 不要改动; 其余唱歌段根据歌词猜歌名, 没把握一律填 "未知曲目"(猜错比不猜更糟, 严禁编造看似歌名的词), 绝不能当闲聊;\n'
    '2. 闲聊/杂谈 type="chat", 游戏 "game", 其他 "other"; 感谢礼物/SC 不要单独分段, 并入相邻闲聊段; PK/连麦时段: 第二个人的声音是对面主播, 其言行不记入主播的总结, 对面唱的歌标 "other" 不进歌单, 整段简述为和XX连麦PK;\n'
    "3. 同一话题的连续内容合并为一段(每段通常 5-15 分钟), 不到 3 分钟的小段并入相邻话题段; 歌曲的结束时间要包含尾奏——比最后一句歌词晚 15-40 秒, 不要以歌词结束为准; 一首歌中途被看弹幕/感谢礼物打断仍属同一首, 合并为一段; "
    "连续唱歌时每首歌单独一段, 歌名不同绝不合并;\n"
    "写作风格(非常重要):\n"
    "4. title/summary 要像真人随手写的时间轴: 口语、简短(每条不超过 15 个字), "
    '观众就叫 "观众/大家/弹幕", 严禁 "连麦者"、"互动展示" 这类生硬书面语, '
    '严禁把多个话题用分号串成一坨; 闲聊 summary 直接写话题内容, 不要以"聊/聊聊"开头;\n'
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
        # 结构化抽取用0: 温度>0会让同一转写每次切出不同的段(切片掷骰子的根源之一)
        "temperature": 0.0,
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
    seg = {
        "start": start,
        "end": end,
        "type": stype,
        "title": title,
        "summary": str(raw.get("summary", "") or "").strip(),
        "src": str(raw.get("src", "llm")),  # 歌名来源: llm=猜测 / reco=识别服务
        "conf": str(raw.get("conf", "")),  # 置信度分层: high/mid/low
    }
    # 证据字段(engine/rec_times/cands)若存在必须保留: 丢失后置信分层会把 reco 段误判为 low
    for k in ("engine", "rec_times", "cands"):
        if k in raw:
            seg[k] = raw[k]
    return seg


def _reattach_evidence(fixed: list, originals: list) -> list:
    """LLM 把关/去重后段落被 normalize 过一遍, 证据字段(engine/rec_times/conf)会丢,
    从原始段捡回——否则这两步之后置信分层整体失灵(reco段全掉成low)。
    对号方式: 起点±3s 优先; 把关LLM常整体微调边界(修复重叠/空洞时歌的起点会挪5-30s),
    对不上时按最大时间重叠兜底(需覆盖较短者的50%)。"""
    for x in fixed:
        cand = [sg for sg in originals if abs(int(sg["start"]) - int(x["start"])) <= 3]
        if not cand:
            best_ov, best_sg = 0.0, None
            for sg in originals:
                if sg.get("type") != x.get("type"):
                    continue
                ov = min(sg["end"], x["end"]) - max(sg["start"], x["start"])
                shorter = min(sg["end"] - sg["start"], x["end"] - x["start"])
                if ov > best_ov and shorter > 0 and ov / shorter >= 0.5:
                    best_ov, best_sg = ov, sg
            if best_sg is not None:
                cand = [best_sg]
        if not cand:
            continue
        m = min(cand, key=lambda sg: abs(int(sg["start"]) - int(x["start"])))
        for k in ("src", "engine", "rec_times", "conf", "cands", "weak"):
            if m.get(k) not in (None, ""):
                x[k] = m[k]
    return fixed


def _mergeable_title(a: str, b: str) -> bool:
    """两个 song 段是否允许因"同歌"合并。
    空标题/未知曲目一律不允许: 两首相邻的不同的歌经常同为"未知曲目",
    合并就是吞歌——宁可在时间轴上多列一行, 也不能把两首歌并成一首。"""
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b or a == "未知曲目" or b == "未知曲目":
        return False
    return a == b


def _merge_segments(segs: list) -> list:
    # 兜底合并: 即使 LLM 拆碎了, 同类型且间隔 <= 120s 的相邻段也并成一段
    # (song 要求歌名相同且非未知才合并; 允许 gap 为负数——切块重叠会产生完全重复的歌)
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
                same_song = (p["type"] != "song") or _mergeable_title(
                    p["title"], s["title"]
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


def _music_cache_path(video: Path) -> Path:
    return CACHE_DIR / f"{_cache_key(video)}.music.json"


def _detect_music_segments(wav: Path, cache: Path) -> list:
    """inaSpeechSegmenter 音频级音乐段检测: 补上文本规则抓不到的 ASR 盲区(纯音乐/唱歌)
    依赖: pip install inaSpeechSegmenter (会拉 tensorflow, 2核CPU请先小片段测速)"""
    if cache.exists():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(cached, list):
                return cached
        except Exception:
            pass
    _log(
        "[analysis] 加载 inaSpeechSegmenter 模型(首次需下载权重到 ~/.keras/inaSpeechSegmenter/, 网络慢会卡在这)…"
    )
    from inaSpeechSegmenter import Segmenter

    # 只用 music 标签: 关闭性别检测(少下一个模型, 且 speech 段少跑一遍推理, 更快)
    seg = Segmenter(
        vad_engine="smn",
        detect_gender=False,
        batch_size=int(os.environ.get("INA_BATCH", "256") or 256),
    )
    music = []
    for label, st, en in seg(str(wav)):
        if label == "music" and en - st >= 8:
            music.append({"start": round(st, 1), "end": round(en, 1)})
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(music, ensure_ascii=False), encoding="utf-8")
    return music


def _merge_windows(windows: list, gap: float = 60, max_len: float = 300) -> list:
    """候选窗口合并: 间隔 <= gap 秒合并, 单窗最长 max_len 秒"""
    out: list = []
    for st, en in sorted(windows):
        en = min(en, st + max_len)
        # 合并前提: 间隔小 且 合并后总跨度不超过 max_len(否则相邻的不同歌会被吞进一个窗口,
        # 投票点全落在前一首歌里, 后面的歌永远得不到采样)
        if out and st - out[-1][1] <= gap and en - out[-1][0] <= max_len:
            out[-1][1] = max(out[-1][1], en)
        else:
            out.append([st, en])
    return [(a, b) for a, b in out]


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


def _env_int(name: str, default: int) -> int:
    """容错解析数字型 env: 只取开头数字, 带行内注释/非法值时回落默认值"""
    m = re.match(r"\s*(-?\d+)", str(os.environ.get(name, "") or ""))
    return int(m.group(1)) if m else default


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
    return {
        "title": title,
        "artist": (track.get("subtitle") or "").strip(),
        "score": 1.0,
        "engine": "fp",
        "rec_times": 9999,
    }


_acr_diag_done = {"net": False, "code": False}


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
    # 字段与 ACRCloud 官方 SDK 对齐: signature_version 必填, 缺了会返回 3006 invalid arguments
    data = {
        "access_key": key,
        "data_type": "audio",
        "signature_version": "1",
        "signature": signature,
        "sample_ms": str(size // 32),  # 16kHz 16bit 单声道 = 32 字节/毫秒
        "sample_bytes": str(size),
        "timestamp": timestamp,
    }
    try:
        resp = httpx.post(
            f"https://{host}/v1/identify",
            data=data,
            files={"sample": (sample.name, sample.read_bytes())},
            timeout=30,
        )
        j = resp.json()
    except Exception as e:
        if not _acr_diag_done["net"]:
            _acr_diag_done["net"] = True
            _log(f"ACRCloud 请求失败(仅提示一次): {e}")
        return None
    if j.get("status", {}).get("code") != 0:
        if not _acr_diag_done["code"]:
            _acr_diag_done["code"] = True
            _log(f"ACRCloud 返回(仅提示一次): {j.get('status')}")
        return None
    metadata = j.get("metadata") or {}
    # 指纹引擎(原唱匹配)优先: 命中即权威结果
    music = metadata.get("music") or []
    if music:
        m = music[0]
        artists = "、".join(
            a.get("name", "") for a in (m.get("artists") or []) if a.get("name")
        )
        return {
            "title": (m.get("title") or "").strip(),
            "artist": artists,
            "score": m.get("score") or 0.9,
            "engine": "fp",
            "rec_times": int(m.get("rec_times") or 0),
        }
    # 翻唱/弹唱走 humming 引擎(不打阈值, 交给调用方投票)
    humming = metadata.get("humming") or []
    if humming:
        best = max(humming, key=lambda x: x.get("score") or 0)
        artists = "、".join(
            a.get("name", "") for a in (best.get("artists") or []) if a.get("name")
        )
        return {
            "title": (best.get("title") or "").strip(),
            "artist": artists,
            "score": best.get("score") or 0.0,
            "engine": "hum",
            "rec_times": int(best.get("rec_times") or 0),
        }
    return None


def _recognize_sample(sample: Path) -> dict | None:
    provider = _song_provider()
    try:
        if provider == "acrcloud":
            return _acrcloud_recognize(sample)
        return _shazam_recognize(sample)
    except Exception:
        return None


def _shazam_window(
    wav: Path,
    start: float,
    end: float,
    workdir: Path,
    votes: int | None = None,
    _depth: int = 0,
) -> list:
    """投票制识别: 窗口内均匀取 votes 个 12 秒样本分别识别。
    指纹命中(engine=fp)直接采信; 哼唱结果需多数决且平均分过 ACR_HUM_SCORE 阈值(默认0.4),
    单点噪声(前奏/说话)因此无法蒙混过关。
    指纹命中互相矛盾(命中不同的歌)说明窗口横跨两首歌: 二分后各半窗重扫一次,
    防止后一首歌被多数票吞掉。
    返回命中列表(0~2个), 每个元素带 start/end(实际采样的子窗口)。"""
    votes = votes or _env_int("RECOG_VOTES", 3)
    span = max(end - start, 1.0)
    results = []
    for k in range(votes):
        pos = start + span * (k + 1) / (votes + 1)
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
                results.append(hit)
        finally:
            sample.unlink(missing_ok=True)
    # 弱命中判定: 看样本间距而非窗口长度——12s 采样clip间距小于其自身时长时,
    # 各样本内容互相重叠, 投票没有独立性(23s 窗口 3 样本间距仅 5.75s, 照样是假投票)
    weak = span / (votes + 1) < 12
    if not results:
        return []
    fp = [h for h in results if h.get("engine") == "fp"]
    if fp:
        fp_keys = {(h["title"], h.get("artist") or "") for h in fp}
        if len(fp_keys) > 1 and _depth == 0 and end - start >= 60:
            # 一个窗口命中两首不同的歌: 几乎一定是窗口跨歌, 二分拆开各扫一次
            mid = (start + end) / 2
            _log(
                f"识别窗口 {fmt_ts(start)}-{fmt_ts(end)} 指纹冲突({len(fp_keys)}首), 二分重扫防吞歌"
            )
            return _shazam_window(
                wav, start, mid, workdir, votes=2, _depth=1
            ) + _shazam_window(wav, mid, end, workdir, votes=2, _depth=1)
        best = max(fp, key=lambda h: h.get("score") or 0)
        return [
            {
                "start": round(start, 1),
                "end": round(end, 1),
                "title": best["title"],
                "artist": best["artist"],
                "score": best.get("score"),
                "engine": "fp",
                "rec_times": int(best.get("rec_times") or 0),
                "weak": weak,
            }
        ]
    # 哼唱多数决: 得票过半 且 平均分过阈值(默认0.5, ACR_HUM_SCORE可调)
    tally: dict = {}
    for h in results:
        tally.setdefault((h["title"], h["artist"]), []).append(h)
    (title, artist), hs = max(
        tally.items(),
        key=lambda kv: (len(kv[1]), sum(x.get("score") or 0 for x in kv[1])),
    )
    avg = sum(x.get("score") or 0 for x in hs) / len(hs)
    # 哼唱命中收紧: 必须带歌手名(无歌手名的匹配多为冷门误报) 且 均分过阈值
    if (
        artist
        and len(hs) >= votes // 2 + 1
        and avg >= float(os.environ.get("ACR_HUM_SCORE", "0.5") or 0.5)
    ):
        best = max(hs, key=lambda x: x.get("score") or 0)
        return [
            {
                "start": round(start, 1),
                "end": round(end, 1),
                "title": title,
                "artist": artist,
                "score": round(avg, 3),
                "engine": "hum",
                "rec_times": int(best.get("rec_times") or 0),
                "weak": weak,
            }
        ]
    return []


def _dedup_hits(hits: list) -> list:
    """识别缓存去重: 二轮/补漏识别每轮运行都往 .songs.json 追加命中,
    多场下来同一位置会积累大量重复条目(日志里同一否决刷9次的元凶)"""
    seen = set()
    out = []
    for h in sorted(hits, key=lambda x: (x.get("start", 0), x.get("title", ""))):
        key = (int(h.get("start", 0)) // 10, h.get("title", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


def _is_weak_hit(h: dict) -> bool:
    """弱命中: 窗口内样本间距不足, 投票无独立性(多为 12s 粗扫点/短段外扩窗口)。
    旧缓存无 weak 字段时按窗口长度估算: <48s 的窗口在 3 票制下样本必然重叠"""
    if "weak" in h:
        return bool(h["weak"])
    return (h.get("end", 0) - h.get("start", 0)) < 48


def _veto_weak_hits(hits: list) -> list:
    """已停用(保留调用点): 原逻辑"弱命中与相邻异名强命中冲突时出局"假设长窗=可信,
    但长窗指纹也会错配冷门条目(rec=0 的 fp), 会误杀正确的弱命中。
    弱命中改由段级规则处理, 不再删 hit:
    弱+短(<20s无其他证据)在 _drop_short_songs 丢弃; 弱+长在 _conf_tier 压低置信"""
    return hits


def _shazam_scan(job_id: str, wav: Path, candidates: list, cache: Path) -> list:
    """候选窗口逐个 Shazam 识别(串行+限速), 结果落盘缓存"""
    if cache.exists():
        try:
            return _veto_weak_hits(
                _dedup_hits(json.loads(cache.read_text(encoding="utf-8")))
            )
        except Exception:
            pass
    hits = []
    total = len(candidates)
    # 请求预算: 防止规则失灵时识别请求失控(1窗口最多2次调用)
    budget = _env_int("RECOG_MAX_REQUESTS", 120)
    for i, (st, en) in enumerate(candidates):
        if i * max(2, _env_int("RECOG_VOTES", 3)) >= budget:
            _log(f"识别请求已达预算上限({budget}), 跳过剩余 {total - i} 个候选")
            break
        pct = 55 + int(20 * (i + 1) / max(total, 1))
        _set_job(job_id, pct=pct, msg=f"歌曲识别中({i + 1}/{total})…")
        # 返回值自带实际采样的(子)窗口 start/end; 指纹冲突二分时一窗可能出两个命中
        hits.extend(_shazam_window(wav, st, en, cache.parent))
        time.sleep(1.5)  # 限速, 保护非官方接口
    # 相邻同歌的多个命中合并为一个时间段
    merged_hits = []
    for h in sorted(hits, key=lambda x: x["start"]):
        if (
            merged_hits
            and h["title"] == merged_hits[-1]["title"]
            and h["start"] - merged_hits[-1]["end"] <= 90
        ):
            merged_hits[-1]["end"] = max(merged_hits[-1]["end"], h["end"])
        else:
            merged_hits.append(dict(h))
    merged_hits = _veto_weak_hits(_dedup_hits(merged_hits))
    cache.write_text(
        json.dumps(merged_hits, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return merged_hits


# ============ 识别结果别名表: 顽固性误配的人工档案 ============
# 同一主播的编曲/跑调方式稳定, humming 会把某首歌每场都错配成同一首冷门歌
# (如《带我走》→《弦上连蹇》)。在 lyrics/recog_alias.json 里登记一次, 永久生效:
#   {"弦上连蹇": "带我走"}   → 识别成弦上连蹇时改为我走
#   {"弦上连蹇": ""}         → 空串 = 一律降级为未知曲目进待审

_recog_alias_cache: dict | None = None


def _load_recog_alias() -> dict:
    global _recog_alias_cache
    if _recog_alias_cache is not None:
        return _recog_alias_cache
    _recog_alias_cache = {}
    p = Path("lyrics") / "recog_alias.json"
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _recog_alias_cache = {
                    str(k): v for k, v in data.items() if isinstance(v, (str, dict))
                }
                if _recog_alias_cache:
                    _log(f"识别别名表加载: {len(_recog_alias_cache)} 条")
        except Exception as e:
            _log(f"识别别名表读取失败({e}), 忽略")
    else:
        _log(
            f"识别别名表未找到: {p.resolve()} "
            '(顽固性误配可创建该文件登记; hum误配直接写 {"错名": "正确名"}, '
            'fp误配用 {"错名": {"to": "正确名", "engine": "fp", "at": 秒, "tol": 300}})'
        )
    return _recog_alias_cache


def _learn_alias_from_edits(old: dict, new_segs: list) -> None:
    """人工在前端把识别错的歌名改对并保存时, 自动学习为别名(仅 humming 误配):
    她的编曲/跑调稳定, 同一误配每场复现, 学一次永久纠正"""
    try:
        old_map = {
            int(s["start"]): s
            for s in (old.get("segments") or [])
            if s.get("type") == "song"
        }
        learned: dict = {}
        for sg in new_segs:
            if sg.get("type") != "song":
                continue
            o = old_map.get(int(sg["start"]))
            if not o:
                continue
            t_old = (o.get("title") or "").strip()
            t_new = (sg.get("title") or "").strip()
            if (
                t_old
                and t_new
                and t_old != t_new
                and t_old != "未知曲目"
                and t_new != "未知曲目"
                and o.get("src") == "reco"
            ):
                if o.get("engine") == "fp":
                    # fp 误配是位置性噪声: 只锁定本场该时间点附近生效,
                    # 防止同一错名在别的位置掩盖别的歌(幻影牵丝戏惨案)
                    learned[t_old] = {
                        "to": t_new,
                        "engine": "fp",
                        "at": int(sg["start"]),
                        "tol": 300,
                    }
                else:
                    # hum 误配由主播声线/跑调习惯决定, 每场复现: 全局生效
                    learned[t_old] = t_new
        if not learned:
            return
        p = Path("lyrics") / "recog_alias.json"
        alias = _load_recog_alias()
        changed = False
        for k, v in learned.items():
            cur = alias.get(k)
            cur_to = cur.get("to") if isinstance(cur, dict) else cur
            v_to = v.get("to") if isinstance(v, dict) else v
            if cur_to != v_to:
                alias[k] = v
                changed = True
        if changed:
            p.parent.mkdir(exist_ok=True)
            p.write_text(
                json.dumps(alias, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            _log(
                f"别名表自动学习 {len(learned)} 条: "
                + "; ".join(f"《{k}》→《{v}》" for k, v in learned.items())
            )
    except Exception as e:
        _log(f"别名学习失败({e}), 忽略")


def _apply_recog_alias(merged: list) -> list:
    """对 song 段标题应用别名表(在歌词仲裁之前, 让仲裁基于修正后的标题工作)。
    值两种形态:
      "正确歌名"                                    → 全局生效(hum误配: 同一主播声线偏差稳定)
      {"to": "...", "engine": "fp", "at": 秒, "tol": 300}
        → 引擎/位置锁定。fp 指纹误配是位置性噪声: 同一错名会在不同位置误配不同的歌
          (《有趣在职的情怀》在25:15是牵丝戏、在31:02是别的歌), 全局转换会批量造幻影
    位置匹配成功的段打 aliased 标记, 仲裁阶段据此豁免"低热度fp降未知"规则"""
    alias = _load_recog_alias()
    if not alias:
        return merged
    for sg in merged:
        if sg.get("type") != "song":
            continue
        t = (sg.get("title") or "").strip()
        if not t or t not in alias:
            continue
        rule = alias[t]
        if isinstance(rule, str):
            rule = {"to": rule}
        eng = rule.get("engine")
        if eng and (sg.get("engine") or "") != eng:
            continue
        at = rule.get("at")
        if at is not None:
            tol = int(rule.get("tol") or 300)
            if abs(int(sg["start"]) - int(at)) > tol:
                _log(
                    f"别名位置不匹配跳过: {fmt_ts(sg['start'])} 《{t}》"
                    f"(登记于{fmt_ts(int(at))}±{tol}s, fp误配不全局转换)"
                )
                continue
        fixed = str(rule.get("to") or "").strip()
        _log(f"识别别名修正: {fmt_ts(sg['start'])} 《{t}》→《{fixed or '未知曲目'}》")
        sg["title"] = fixed or "未知曲目"
        sg["aliased"] = True
    return merged


def _patches_path(video: Path) -> Path:
    return CACHE_DIR / f"{_cache_key(video)}.patches.json"


def _load_patches(video: Path) -> list:
    try:
        p = _patches_path(video)
        if p.exists():
            return list(json.loads(p.read_text(encoding="utf-8")).get("patches") or [])
    except Exception:
        pass
    return []


def _learn_patches_from_edits(old: dict, new_segs: list, video: Path) -> None:
    """人工补丁层(逐视频, v8): 审核保存时把改名/挪起点/删行/补行全部记成补丁,
    重新生成后按位置自动重放——人工结论永不丢失, 审核一次终身有效。
    与别名表互补: 别名管"识别引擎的稳定误配"(可跨场复用, 但要求src=reco且旧名非未知),
    补丁管"这一场这个位置就该是这首歌"——未知→命名、src=llm 的改名都能记。
    合并行的处理: 被并入新行的旧段记为"改名成新行歌名", 重放时碎片改同名后
    由 merge 并回一行, 效果等同用户的合并操作, 不会把歌吞掉"""
    try:
        old_songs = [s for s in (old.get("segments") or []) if s.get("type") == "song"]
        if not old_songs:
            return
        new_songs = [s for s in new_segs if s.get("type") == "song"]
        patches = _load_patches(video)
        changed = False

        def upsert(patch: dict) -> None:
            nonlocal changed
            for i, q in enumerate(patches):
                if abs(int(q.get("start", -(10**9))) - int(patch["start"])) > 10:
                    continue
                if "ensure" in patch:
                    same = "ensure" in q
                else:
                    # 改名/删除补丁按 (起点±10s, from标题) 双键去重:
                    # 同起点的双行(已知+未知)各自产补丁, 只按起点会互相顶掉
                    # (删绝对占有和删未知同起点, 后者顶掉前者 → 重复簇复活)
                    same = "from" in q and (q.get("from") or "") == (
                        patch.get("from") or ""
                    )
                if same:
                    if q != patch:
                        patches[i] = patch
                        changed = True
                    return
            patches.append(patch)
            changed = True

        matched_new: set = set()
        for o in old_songs:
            o_start = int(o["start"])
            o_title = (o.get("title") or "").strip() or "未知曲目"
            # ①起点基本没变(±6s) → 同行编辑。
            #   新行必须"未占用"(一行新数据不能同时是多行旧数据的编辑结果,
            #   否则同起点双行会被双双匹配到同一行, 脑补出从未发生的改名),
            #   且多候选时同名优先(同起点的 已知+未知 双行各归各)
            pool = [
                n
                for n in new_songs
                if id(n) not in matched_new and abs(int(n["start"]) - o_start) <= 6
            ]
            cand = None
            if pool:
                same = [
                    n
                    for n in pool
                    if ((n.get("title") or "").strip() or "未知曲目") == o_title
                ]
                cand = same[0] if same else pool[0]
            merged_into = False
            if cand is None:
                # ②旧段起点落在某新行区间内部 → 被合并吸收: 记为改名(+挪起点),
                #   重放时碎片改同名后由 merge 并回, 不会吞歌。
                #   必须用 < end-10 留余量: 相邻行的 end 常等于本行 start
                #   (resolve_overlaps 切的边界), 裸 <= 会把"挪起点"误判成合并,
                #   学出《我们的爱》→《带我走》这种毒补丁
                for n in new_songs:
                    if id(n) in matched_new:
                        continue
                    if int(n["start"]) - 8 <= o_start < int(n["end"]) - 10:
                        cand, merged_into = n, True
                        break
            if cand is None:
                # ③整行删除: 重放时仅当跑出完全相同的旧标题才删(防误伤新结果)
                upsert({"start": o_start, "from": o_title, "to": None})
                _log(f"人工补丁学习: 删除 {fmt_ts(o_start)} 《{o_title}》")
                continue
            matched_new.add(id(cand))
            n_title = (cand.get("title") or "").strip() or "未知曲目"
            n_start = int(cand["start"])
            patch = {"start": o_start, "from": o_title, "to": n_title}
            if abs(n_start - o_start) > 3:
                patch["new_start"] = n_start
            if n_title != o_title or "new_start" in patch:
                upsert(patch)
                what = (
                    f"《{o_title}》→《{n_title}》" if n_title != o_title else "挪起点"
                )
                if merged_into:
                    what = f"合并入行({what})"
                _log(f"人工补丁学习: {fmt_ts(o_start)} {what}")
        # 用户手动新增的行(旧结果无对应) → 补录补丁, 重放时若无歌覆盖该区间则插入
        for n in new_songs:
            if id(n) in matched_new:
                continue
            upsert(
                {
                    "start": int(n["start"]),
                    "end": int(n["end"]),
                    "ensure": (n.get("title") or "").strip() or "未知曲目",
                }
            )
            _log(
                f"人工补丁学习: 补录 {fmt_ts(int(n['start']))}"
                f" 《{(n.get('title') or '').strip()}》"
            )
        if changed:
            p = _patches_path(video)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                json.dumps(
                    {
                        "video": video.name,
                        "updated": datetime.now().isoformat(timespec="seconds"),
                        "patches": sorted(
                            patches, key=lambda x: int(x.get("start", 0))
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
    except Exception as e:
        _log(f"人工补丁学习失败({e}), 忽略")


def _apply_manual_patches(merged: list, video: Path) -> list:
    """重放人工补丁(见 _learn_patches_from_edits)。在仲裁/清洗/对齐之后、
    最终置信分层之前执行: 人工结论是最高优先级证据, 命中的行直接置 high。
    顺序: 改名/挪起点 → 删除 → 补录, 调用方随后再 merge+整形一次"""
    patches = _load_patches(video)
    if not patches:
        return merged
    songs = [sg for sg in merged if sg.get("type") == "song"]
    drop: set = set()
    for p in patches:
        p_start = int(p.get("start", 0))
        if "ensure" in p:
            title = (p.get("ensure") or "").strip()
            # 补录未知曲目没有意义(用户加行一定会命名, 未知补录只会污染复制粘贴的歌单)
            if not title or title == "未知曲目":
                continue
            e_start, e_end = p_start, int(p.get("end") or p_start + 60)
            covered = any(
                id(sg) not in drop
                and min(sg["end"], e_end) - max(sg["start"], e_start) > 10
                for sg in songs
            )
            if not covered:
                _log(f"人工补丁补录: {fmt_ts(e_start)} 《{title}》")
                # summary 留空: 用户会直接复制粘贴歌单, 任何标注文字都是污染
                merged.append(
                    {
                        "start": e_start,
                        "end": e_end,
                        "type": "song",
                        "title": title,
                        "summary": "",
                        "src": "manual",
                        "patched": True,
                        "conf": "high",
                    }
                )
                songs.append(merged[-1])
            continue
        # 同起点可能存在多行(已知+未知双碎片): 优先命中标题与补丁 from 一致的那行
        pool = [
            sg
            for sg in songs
            if id(sg) not in drop and abs(int(sg["start"]) - p_start) <= 12
        ]
        target = None
        if pool:
            frm = (p.get("from") or "").strip()
            same = [
                sg
                for sg in pool
                if ((sg.get("title") or "").strip() or "未知曲目") == frm
            ]
            target = same[0] if same else pool[0]
        if target is None:
            continue
        if p.get("to") is None:
            # 删除补丁: 仅当重跑出完全相同的旧标题才生效
            if ((target.get("title") or "").strip() or "未知曲目") == (
                p.get("from") or ""
            ):
                _log(
                    f"人工补丁删除: {fmt_ts(target['start'])} 《{target.get('title')}》"
                )
                drop.add(id(target))
            continue
        to = (p.get("to") or "").strip()
        cur = (target.get("title") or "").strip()
        if to and cur != to:
            _log(
                f"人工补丁改名: {fmt_ts(target['start'])} 《{cur or '未知曲目'}》→《{to}》"
            )
            target["title"] = to
        target["patched"] = True
        target["conf"] = "high"
        ns = p.get("new_start")
        if ns is not None and abs(int(ns) - int(target["start"])) > 3:
            _log(
                f"人工补丁起点: {fmt_ts(target['start'])}→{fmt_ts(int(ns))}"
                f" 《{target['title']}》"
            )
            target["start"] = max(0, int(ns))
    if drop:
        merged = [sg for sg in merged if id(sg) not in drop]
    merged.sort(key=lambda x: (x["start"], x["end"]))
    return merged


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


def _split_segment_by_hits(
    sg: dict, hits: list, used_ids: set | None = None
) -> list | None:
    """一个段被 >=2 个不同歌名的识别命中覆盖时, 按命中窗口拆成多段(防吞歌核心):
    LLM 常把两首相邻的歌并成一个大段, 旧逻辑只取第一个命中, 后一首永久丢失。
    连续同歌名命中视为同一首; 分界取相邻命中窗口的中点。不足两首返回 None。
    used_ids: 已被前一次拆分消费过的命中(id 集合)。reconcile 平铺的时间轴可能出现
    两个互相重叠的大段覆盖同一对命中, 若不记账会拆出两套重叠子段(双重拆分)。"""
    ov = sorted(
        (
            h
            for h in hits
            if min(sg["end"], h["end"]) - max(sg["start"], h["start"]) > 20
            and (used_ids is None or id(h) not in used_ids)
        ),
        key=lambda h: h["start"],
    )
    groups: list = []
    for h in ov:
        if groups and groups[-1][0]["title"] == h["title"]:
            groups[-1].append(h)
        else:
            groups.append([h])
    if len(groups) < 2:
        return None
    bounds = [float(sg["start"])]
    for g1, g2 in zip(groups, groups[1:]):
        bounds.append((g1[-1]["end"] + g2[0]["start"]) / 2)
    bounds.append(float(sg["end"]))
    subs = []
    for gi, g in enumerate(groups):
        h = max(g, key=lambda x: x.get("score") or 0)
        subs.append(
            {
                **sg,
                "start": int(bounds[gi]),
                "end": int(bounds[gi + 1]),
                "title": h["title"],
                "src": "reco",
                "engine": h.get("engine", ""),
                "rec_times": h.get("rec_times", 0),
                "weak": any(x.get("weak") for x in g),
            }
        )
    _log(
        f"一段多歌拆分: {fmt_ts(sg['start'])} 起 → "
        + " / ".join(f"《{s['title']}》" for s in subs)
    )
    if used_ids is not None:
        used_ids.update(id(x) for g in groups for x in g)
    return subs


def _apply_song_hits(merged: list, hits: list) -> list:
    """Shazam 结果回填: 覆盖 LLM 段落的歌名; LLM 漏掉的唱歌段补为独立段落。
    一段跨多歌时先拆分再回填, 不再只取第一个命中。"""
    if not hits:
        return merged
    out: list = []
    split_used: set = set()  # 已被拆分消费的命中, 防重叠大段双重拆分
    for sg in merged:
        if sg["type"] == "song":
            subs = _split_segment_by_hits(sg, hits, split_used)
            if subs:
                out.extend(subs)
                continue
            # 单命中回填取"重叠最多"的命中而非第一个>20s的:
            # 拆分被消费记账拦截后落到这里的段, 范围内往往压着前一首歌的命中尾巴,
            # 取第一个会错名(10:33 段被回填成无情画、夹进关键词中间的惨案)
            best_h, best_ov = None, 20
            for h in hits:
                ov = min(sg["end"], h["end"]) - max(sg["start"], h["start"])
                if ov > best_ov:
                    best_h, best_ov = h, ov
            if best_h is not None:
                h = best_h
                sg["title"] = h["title"]
                sg["src"] = "reco"
                sg["engine"] = h.get("engine", "") or "legacy"
                sg["rec_times"] = h.get("rec_times", 0)
                sg["weak"] = bool(h.get("weak"))
        out.append(sg)
    merged = out
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
                    "src": "reco",
                    "engine": h.get("engine", ""),
                    "rec_times": h.get("rec_times", 0),
                    "weak": bool(h.get("weak")),
                }
            )
            song_ranges.append((int(h["start"]), int(h["end"])))
    merged.sort(key=lambda x: (x["start"], x["end"]))
    return merged


def _recognize_llm_songs(job_id: str, wav: Path, merged: list, video: Path) -> list:
    """二轮识别: 对 LLM 标出但识别服务未覆盖的 song 段, 用该段自身边界送识别。
    专治 LLM 猜错/编歌名(《交叉路口》类), 命中后以曲库正式歌名覆盖。"""
    if not _song_provider():
        return merged
    # 记录下标而非对象引用: 段内跨歌时需要拆分替换, 倒序遍历不影响未处理的下标
    targets = [
        i
        for i, sg in enumerate(merged)
        if sg["type"] == "song" and sg.get("src") != "reco"
    ]
    if not targets:
        return merged
    cache = _songs_path(video)
    try:
        hits = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else []
    except Exception:
        hits = []
    total = len(targets)
    for n, i in enumerate(reversed(targets)):
        sg = merged[i]
        _set_job(job_id, pct=95, msg=f"补充识别中({n + 1}/{total})…")
        # 先查缓存命中: 重跑时同一切片下的二轮识别结果已在 .songs.json 里,
        # 直接复用, 不重复发起网络请求(重跑接近零成本的关键)
        hh = [
            h
            for h in hits
            if min(sg["end"], h["end"]) - max(sg["start"], h["start"]) > 20
        ]
        if not hh:
            st = max(0.0, sg["start"] - 5.0)
            en = min(sg["end"] + 5.0, st + 300)
            hh = _shazam_window(wav, st, en, cache.parent)
            if hh:
                hits.extend(hh)
            time.sleep(1.0)
        if hh:
            # 缓存复用时命中可能多个: 统一按重叠量降序, hh[0]即最贴合本段的
            hh.sort(
                key=lambda h: min(sg["end"], h["end"]) - max(sg["start"], h["start"]),
                reverse=True,
            )
            subs = _split_segment_by_hits(sg, hh)
            if subs:
                # 段内混了两首歌: 拆开, 后一首不再被整段标题吞掉
                merged[i : i + 1] = subs
            else:
                sg["title"] = hh[0]["title"]
                sg["src"] = "reco"
                # 补上 engine/rec_times/weak: 旧逻辑漏写, 导致这类段在置信分层里被误压到 low
                sg["engine"] = hh[0].get("engine", "")
                sg["rec_times"] = hh[0].get("rec_times", 0)
                sg["weak"] = bool(hh[0].get("weak"))
    try:
        cache.write_text(
            json.dumps(_dedup_hits(hits), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass
    return merged


def _recognize_uncovered_music(
    job_id: str, wav: Path, merged: list, music_segs: list, video: Path
) -> list:
    """补漏: music 段既没被识别命中、LLM 也没标成歌(被误判闲聊)的,
    单独送投票识别, 命中则补为 song 段。专治"边唱边互动"被文本层漏判的歌。"""
    if not _song_provider() or not music_segs:
        return merged
    uncovered = [
        m
        for m in music_segs
        if not any(
            min(sg["end"], m["end"]) - max(sg["start"], m["start"]) > 30
            for sg in merged
            if sg["type"] == "song"
        )
    ]
    if not uncovered:
        return merged
    cache = _songs_path(video)
    try:
        hits = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else []
    except Exception:
        hits = []
    for i, m in enumerate(uncovered):
        # 先查缓存命中: 该音乐段的补漏结果上次已写入 .songs.json, 重跑不重复请求
        new_hits = [
            h
            for h in hits
            if min(m["end"], h["end"]) - max(m["start"], h["start"]) > 20
        ]
        if not new_hits:
            _set_job(job_id, pct=95, msg=f"补漏识别中({i + 1}/{len(uncovered)})…")
            # 一个 music 段可能跨两首歌(指纹冲突时二分), 每个命中各补一段
            new_hits = _shazam_window(wav, m["start"], m["end"], cache.parent)
            hits.extend(new_hits)
            time.sleep(1.0)
        for hit in new_hits:
            merged.append(
                {
                    "start": int(hit["start"]),
                    "end": int(hit["end"]),
                    "type": "song",
                    "title": hit["title"],
                    "summary": "",
                    "src": "reco",
                    "engine": hit.get("engine", ""),
                    "rec_times": hit.get("rec_times", 0),
                    "weak": bool(hit.get("weak")),
                }
            )
    merged.sort(key=lambda x: (x["start"], x["end"]))
    try:
        cache.write_text(
            json.dumps(_dedup_hits(hits), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass
    return merged


def _merge_adjacent_songs(segs: list) -> list:
    """相邻同歌名 song 段合并(同一首歌被 music 检测/识别切成多段的情况)。
    未知曲目不参与合并: 两首相邻的不同的歌可能同为未知, 合并即吞歌。"""
    out: list = []
    for sg in segs:
        if (
            out
            and sg["type"] == "song"
            and out[-1]["type"] == "song"
            and _mergeable_title(sg["title"], out[-1]["title"])
            and sg["start"] - out[-1]["end"] <= 60
        ):
            out[-1]["end"] = max(out[-1]["end"], sg["end"])
            continue
        out.append(sg)
    return out


_DEDUP_PROMPT = (
    "用户会给你一份直播歌单初稿(按时间排序的 JSON 数组, 元素含 start/end/title/summary)。"
    "请只做最小化整理:\n"
    "1. 不要合并、不要拆分任何段(相邻同歌已在代码层合并过);\n"
    '2. 按时间升序排列, 时间格式 "分:秒" 或 "时:分:秒";\n'
    "3. 不要新增歌、不要删歌; 但两类 title 允许修正: "
    '(a) 明显是歌词句子而非歌名的(超长、无歌名特征), 改为 "未知曲目"; '
    '(b) 去掉括号内的乐器/编曲版本标注, 如 "霜雪千年 (笛子演奏版)"→"霜雪千年", '
    "以及 (Live)/(伴奏)/(DJ版)/(纯音乐版) 一律去掉; 但有区分意义的括号保留(如语言版本), 不确定就保留原样;\n"
    "4. 只输出整理后的 JSON 数组本身, 不要任何解释。"
)


def _dedup_songs_llm(job_id: str, segs: list) -> list:
    """歌单模式的一次轻量 LLM 保底(去重/排序), 失败则退回原样"""
    if not segs:
        return segs
    base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    key = os.environ.get("LLM_API_KEY", "").strip()
    model = os.environ.get("LLM_MODEL_RECONCILE", "").strip() or os.environ.get(
        "LLM_MODEL", "gpt-4o-mini"
    )
    if not key:
        return segs
    compact = [
        {
            "start": fmt_ts(sg["start"]),
            "end": fmt_ts(sg["end"]),
            "type": "song",
            "title": sg["title"],
            "summary": sg["summary"],
            "src": sg.get("src", "llm"),
        }
        for sg in segs
    ]
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _DEDUP_PROMPT},
            {"role": "user", "content": json.dumps(compact, ensure_ascii=False)},
        ],
        "temperature": 0.0,
    }
    try:
        resp = httpx.post(
            f"{base}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {key}"},
            timeout=120,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        fixed = []
        for raw in _parse_json_array(content):
            norm = _normalize_segment(raw)
            if norm and norm["type"] == "song":
                fixed.append(norm)
        if not fixed:
            return segs
        # 拆解跨段合并: LLM 若把多个原始段合成一条(首尾对不上任何单段), 替换回原始段
        final: list = []
        for x in fixed:
            exact = any(
                abs(int(sg["start"]) - x["start"]) <= 3
                and abs(int(sg["end"]) - x["end"]) <= 5
                for sg in segs
            )
            covered = [
                sg
                for sg in segs
                if sg["start"] >= x["start"] - 3 and sg["end"] <= x["end"] + 3
            ]
            if not exact and len(covered) >= 2:
                _log(
                    f"保底LLM跨段合并已拆解: {fmt_ts(x['start'])} 《{x['title']}》({len(covered)}段)"
                )
                for sg in covered:
                    if not any(
                        abs(int(sg["start"]) - int(y["start"])) <= 3 for y in final
                    ):
                        final.append(dict(sg))
            else:
                final.append(x)
        # 强制回补: LLM 丢掉的段一律捡回(保底环节不允许删歌)
        kept = {int(y["start"]) for y in final}
        for sg in segs:
            if not any(abs(int(sg["start"]) - ks) <= 3 for ks in kept):
                _log(f"保底LLM丢失段已回补: {fmt_ts(sg['start'])} 《{sg['title']}》")
                final.append(dict(sg))
        _reattach_evidence(final, segs)  # LLM 行不带证据字段, 按起点对号捡回
        final.sort(key=lambda x: (x["start"], x["end"]))
        return final
    except Exception:
        return segs


# ============ 本地歌词检索(B5): 字符n-gram + 拼音双通道, 抗ASR错字/跑调 ============

_lyric_index: dict | None = None


def _load_lyric_index() -> dict:
    """加载 lyrics/ 目录的 LRC 歌词库, 按行建索引(需 pip install pypinyin scikit-learn)"""
    global _lyric_index
    if _lyric_index is not None:
        return _lyric_index
    _lyric_index = {"n": 0}
    lyrics_dir = Path("lyrics")
    if not lyrics_dir.exists():
        return _lyric_index
    try:
        import numpy as np
        from sklearn.feature_extraction.text import TfidfVectorizer
        from pypinyin import lazy_pinyin
    except ImportError as e:
        _log(f"歌词检索依赖缺失({e}), 跳过(需 pip install pypinyin scikit-learn)")
        return _lyric_index
    songs: list = []
    docs: list = []
    doc_song: list = []
    for f in sorted(lyrics_dir.glob("*.*")):
        if f.suffix.lower() not in (".lrc", ".txt"):
            continue
        sid = len(songs)
        songs.append(f.stem)
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = re.sub(r"\[.*?\]", "", line).strip()  # 去LRC时间戳
            if len(line) >= 4:
                docs.append(line)
                doc_song.append(sid)
    if not docs:
        return _lyric_index
    han_vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
    xh = han_vec.fit_transform(docs)
    py_docs = [" ".join(lazy_pinyin(d)) for d in docs]
    py_vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
    xp = py_vec.fit_transform(py_docs)
    _lyric_index = {
        "songs": songs,
        "doc_song": np.array(doc_song),
        "han_vec": han_vec,
        "xh": xh,
        "py_vec": py_vec,
        "xp": xp,
        "docs": docs,
        "n": len(songs),
    }
    _log(f"歌词库加载: {len(songs)} 首歌 / {len(docs)} 行")
    return _lyric_index


def _lyric_search(text: str, topn: int = 3) -> list:
    """歌词检索: 汉字+拼音双通道取并集, 按歌聚合取最高分行, 返回 topn 候选"""
    idx = _load_lyric_index()
    if not idx.get("n") or not text.strip():
        return []
    import numpy as np
    from pypinyin import lazy_pinyin

    text = re.sub(r"\*", "", text)  # 去掉置信标注的星号
    q_han = idx["han_vec"].transform([text])
    q_py = idx["py_vec"].transform([" ".join(lazy_pinyin(text))])
    han = (idx["xh"] @ q_han.T).toarray().ravel()
    pyx = (idx["xp"] @ q_py.T).toarray().ravel()
    # 短查询(<30字)补 Rapidfuzz partial_ratio 通道: ASR断句乱时"查询是某行子串"仍能命中
    rf = None
    if len(text) < 30:
        try:
            from rapidfuzz import fuzz

            rf = [fuzz.partial_ratio(text, d) / 100.0 for d in idx["docs"]]
        except ImportError:
            rf = None
    best_line = {}
    for d in range(len(idx["docs"])):
        sc = max(han[d], pyx[d], rf[d] if rf else 0.0)
        sid = idx["doc_song"][d]
        if sc > best_line.get(sid, (0, ""))[0]:
            best_line[sid] = (sc, idx["docs"][d])
    ranked = sorted(best_line.items(), key=lambda kv: kv[1][0], reverse=True)[:topn]
    return [
        {"title": idx["songs"][sid], "score": round(sc, 3), "line": line}
        for sid, (sc, line) in ranked
        if sc >= 0.15
    ]


_LYRIC_ID_PROMPT = (
    "你是中文流行歌曲识别专家。用户会给你若干段直播唱歌的语音转写文本"
    "(可能有错别字/漏字, 可能是翻唱或弹唱)。对每段判断最可能对应的原曲。\n"
    "规则:\n"
    '1. 只填你确信存在的知名歌曲, 不确定一律 "未知曲目"——严禁编造歌名, 严禁把歌词句子当歌名;\n'
    "2. 翻唱/弹唱按旋律对应的原曲回答;\n"
    '2b. 每段附带了"本地歌词检索候选"(可能有误): 候选与你的判断一致时 confidence 可以给 high; '
    '没有把握就选"未知曲目";\n'
    "3. 每段输入以 [分:秒] 时间戳开头, 输出必须原样带回该时间戳: "
    '{"start":"分:秒","title":"歌名","confidence":"high|low|none"}; '
    "数组与输入段一一对应, 不要任何解释。"
)


def _identify_songs_by_lyrics(job_id: str, merged: list, transcript: list) -> tuple:
    """歌词辅助识别: 转写文本 → 本地歌词库检索top3候选 → LLM结合候选做判断。
    LLM 不可用时, 检索均分≥0.6 的 top1 直接采信。依赖模型内置歌曲知识, 无需联网。"""
    targets = [sg for sg in merged if sg["type"] == "song"]
    if not targets:
        return {}
    _log(f"歌词识别阶段: {len(targets)} 段待判定")
    cands_map: dict = {}
    snippets = []
    for idx, sg in enumerate(targets):
        lines = [
            t["text"]
            for t in transcript
            if t["end"] > sg["start"] and t["start"] < sg["end"]
        ]
        text = " ".join(lines)
        if len(text) < 30:
            # 段内文本不足(边界切在间奏上): 外扩±15s凑歌词, 否则文本层没弹药翻案
            lines = [
                t["text"]
                for t in transcript
                if t["end"] > sg["start"] - 15 and t["start"] < sg["end"] + 15
            ]
            text = " ".join(lines)
        cands = _lyric_search(text)
        cands_map[idx] = cands
        cand_str = " / ".join(f"{c['title']}({c['score']})" for c in cands) or "无"
        # 段标识 = 开始时间戳(索引不可信, 时间戳对号防漂移)
        tag = f"{int(sg['start'] // 60):02d}:{int(sg['start'] % 60):02d}"
        snippets.append(
            (idx, f"[{tag}] {text[:500]}\n候选: {cand_str}", len(text), sg["start"])
        )
    base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    key = os.environ.get("LLM_API_KEY", "").strip()
    model = os.environ.get("LLM_MODEL_RECONCILE", "").strip() or os.environ.get(
        "LLM_MODEL", "gpt-4o-mini"
    )
    out_map: dict = {}
    votes_n = _env_int("LYRIC_VOTES", 3)  # 自洽投票次数(治边缘输入上的答案摇摆)
    # 证据门: 检索top1<0.35 且 文本<40字 → 无证据输入, 直接不进LLM(判"未知/保留原识别")
    ELIG_SCORE, ELIG_CHARS = 0.35, 40
    eligible = [
        x[0]
        for x in snippets
        if (cands_map.get(x[0]) and cands_map[x[0]][0]["score"] >= ELIG_SCORE)
        or x[2] >= ELIG_CHARS
    ]
    _log(
        f"证据门: {len(eligible)}/{len(snippets)} 段有证据进入LLM, "
        f"{len(snippets) - len(eligible)} 段证据不足直接跳过"
    )
    if key and eligible:
        total_chars = sum(x[2] for x in snippets)
        _log(
            f"歌词LLM请求: {len(snippets)}段 / 约{total_chars}字 / {votes_n}次投票, 按时间戳对号…"
        )
        all_maps: list = []
        # 三票互相独立, 并行发出: 串行要约6.5分钟, 并行≈单票耗时(约2分钟)
        user_content = "\n\n".join(snippets[k][1] for k in eligible)

        def _vote_once(v: int):
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": _LYRIC_ID_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                "temperature": 0.3,  # 轻微扰动换取投票多样性
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
                arr = _parse_json_array(content)
                mv = {}
                for raw in arr:
                    if not isinstance(raw, dict):
                        continue
                    sec = parse_ts(raw.get("start", ""))
                    idx = min(
                        range(len(snippets)),
                        key=lambda k: abs(snippets[k][3] - sec),
                        default=None,
                    )
                    if idx is None or abs(snippets[idx][3] - sec) > 3:
                        continue
                    mv[idx] = {
                        "title": str(raw.get("title", "") or "").strip(),
                        "confidence": str(raw.get("confidence", "none")),
                    }
                return v, mv, None
            except Exception as e:
                return v, None, e

        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=max(1, votes_n)) as _ex:
            for v, mv, err in sorted(
                _ex.map(_vote_once, range(votes_n)), key=lambda r: r[0]
            ):
                if err is not None:
                    _log(f"歌词LLM第{v + 1}次请求失败: {err}")
                else:
                    all_maps.append(mv)
                    _log(f"歌词LLM第{v + 1}/{votes_n}次投票: {len(mv)}段判定")
        # 多数决: 同一歌名≥2票 => high; 仅1票(即使自称high)降为low, 需其他证据
        for idx in eligible:
            counter: dict = {}
            for mv in all_maps:
                r = mv.get(idx) or {}
                t = r.get("title", "")
                if not t or t == "未知曲目":
                    continue
                counter[t] = counter.get(t, 0) + 1
            if not counter:
                continue
            best_t = max(counter, key=counter.get)
            info = {"title": best_t, "chars": snippets[idx][2]}
            info["confidence"] = "high" if counter[best_t] >= 2 else "low"
            out_map[idx] = info
    # LLM 未覆盖/失败时: 检索高分 top1 直接采信
    for idx, cands in cands_map.items():
        if idx not in out_map and cands and cands[0]["score"] >= 0.6:
            out_map[idx] = {"title": cands[0]["title"], "confidence": "high"}
    _log(f"歌词LLM解析: {len(out_map)}/{len(snippets)} 段拿到判定")
    for idx, body, ln, start in snippets:
        r = out_map.get(idx, {})
        _log(
            f"歌词识别[{int(start // 60):02d}:{int(start % 60):02d}] 文本{ln}字 → {r.get('title', '无结果')}({r.get('confidence', '-')})"
        )
    return out_map, cands_map


def _sane_title(title: str) -> bool:
    """识别标题可信度快检: 超长/带逗号引号(歌词句/古典乐全名)视为可疑"""
    t = (title or "").strip()
    if not t or t == "未知曲目":
        return False
    if len(t) > 14:
        return False
    if any(c in t for c in (",", "，", '"', "“", "”", "(D")):
        return False
    return True


# 版本标注括号: (Live)/(笛子演奏版)/(DJ版)/(纯音乐版)/(伴奏) 等→代码侧确定性清除
# 注意: 不用通用"版"字匹配, 保护 (国语版)/(粤语版) 等有区分意义的语言版本
_VERSION_SUFFIX_RE = re.compile(
    r"[（(][^（）()]{0,20}"
    r"(?:live|现场|伴奏|dj|纯音乐|演奏|弹唱|翻唱|cover|remix|剪辑|串烧)"
    r"[^（）()]{0,10}[）)]\s*$",
    re.IGNORECASE,
)


def _clean_title(title: str) -> str:
    """确定性剥掉歌名尾部的版本标注括号, 全模式生效(不再依赖LLM记得删)"""
    t = (title or "").strip()
    prev = None
    while prev != t:
        prev = t
        t = _VERSION_SUFFIX_RE.sub("", t).strip()
    return t


def _split_giant_song_segments(merged: list, music_segs: list, hits: list) -> list:
    """巨型唱歌段(>7分钟)按音频级音乐段起点拆分:
    chunk LLM 切片有温度随机性, 偶尔会掷出跨多首歌的巨型段(实测单段1506字/8分钟+),
    段文本被多数歌词主导 → 命名错一多半且把其余歌吞掉(36:17段8分钟被判成老男孩,
    心墙/虚拟全被吞)。拆小后各子段独立取最大重叠命中, 无命中→未知曲目交歌词仲裁
    (仲裁在拆分后执行, 文本窗口变小, 判断反而更准)。
    单首长歌不受影响: 连续演唱=一个完整音乐段, 内部没有可用切点。"""
    max_dur = _env_int("GIANT_SONG_SECONDS", 420)
    if not music_segs:
        return merged
    out: list = []
    for sg in merged:
        if sg["type"] != "song" or sg["end"] - sg["start"] <= max_dur:
            out.append(sg)
            continue
        cuts = [
            float(m["start"])
            for m in music_segs
            if sg["start"] + 30 < float(m["start"]) < sg["end"] - 30
        ]
        if not cuts:
            _log(
                f"巨型唱歌段({(sg['end'] - sg['start']) // 60}min)内部无音乐段切点, "
                f"保留原段并降权: {fmt_ts(sg['start'])} 《{sg['title']}》"
            )
            sg["conf"] = "mid"
            out.append(sg)
            continue
        bounds = [float(sg["start"])] + cuts + [float(sg["end"])]
        subs = []
        for bi in range(len(bounds) - 1):
            st, en = int(bounds[bi]), int(bounds[bi + 1])
            best_h, best_ov = None, 20
            for h in hits:
                ov = min(en, h["end"]) - max(st, h["start"])
                if ov > best_ov:
                    best_h, best_ov = h, ov
            sub = {**sg, "start": st, "end": en, "summary": ""}
            sub.pop("conf", None)  # 证据变了, 旧置信结论作废
            if best_h is not None:
                sub.update(
                    title=best_h["title"],
                    src="reco",
                    engine=best_h.get("engine", ""),
                    rec_times=best_h.get("rec_times", 0),
                    weak=bool(best_h.get("weak")),
                )
            else:
                # 原标题由巨型段的多数文本主导, 贴给任何子段都可能错——
                # 宁给未知(漏名, cost 2)不给错名(cost 10), 歌词仲裁会重新命名
                sub.update(
                    title="未知曲目", src="llm", engine="", rec_times=0, weak=False
                )
                sub.pop("cands", None)
            subs.append(sub)
        _log(
            f"巨型唱歌段拆分({(sg['end'] - sg['start']) // 60}min): {fmt_ts(sg['start'])} → "
            + " / ".join(f"《{s['title']}》@{fmt_ts(s['start'])}" for s in subs)
        )
        out.extend(subs)
    return out


def _align_song_starts_to_music(merged: list, music_segs: list) -> list:
    """唱歌段开始时间戳对齐音频级音乐段起点, 两个方向:
    ①提前对齐: 命中窗口中点切出的边界常偏进歌里几十秒(关键词标9:56, 实际9:19开唱),
      而 inaSpeechSegmenter 的音乐起点与真实开唱只差秒级——以它为准;
    ②推后对齐(ALIGN_LATER_MAX, 默认120s, 0=关闭): 段起点落在纯口播区
      (不在任何音乐段内)且开唱在阈值内时, 起点推到音乐起点——主播先报歌名、
      回几句弹幕才开唱的场景, 章节不该从聊天开始(v8 实测: 用户手动后挪了
      7 个起点, 幅度 22~107s, 全部符合这个模式)。推后只推到音乐段起点,
      绝不推进歌里; 微调(<6s)不动, 避免时间戳抖动"""
    if not music_segs:
        return merged
    starts = sorted(float(m["start"]) for m in music_segs)
    later_max = _env_int("ALIGN_LATER_MAX", 120)
    for sg in merged:
        if sg["type"] != "song" or not (sg.get("title") or "").strip():
            continue
        cand = [s for s in starts if 0 < sg["start"] - s <= 45 and s < sg["end"] - 10]
        if cand:
            new_start = int(max(cand))  # 离段起点最近(最晚)的音乐起点
            _log(
                f"起点对齐音乐段: {fmt_ts(sg['start'])}→{fmt_ts(new_start)}"
                f" 《{sg['title']}》"
            )
            sg["start"] = new_start
            continue
        if later_max <= 0:
            continue
        # 段起点本身已在音乐段里(歌正唱着) → 不动, 推后会切开唱
        if any(
            float(m["start"]) - 2 <= sg["start"] <= float(m["end"]) + 2
            for m in music_segs
        ):
            continue
        nxt = [
            s for s in starts if 6 < s - sg["start"] <= later_max and s < sg["end"] - 10
        ]
        if nxt:
            new_start = int(min(nxt))  # 段起点之后第一个音乐起点
            _log(
                f"起点对齐音乐段(推后{new_start - sg['start']}s): "
                f"{fmt_ts(sg['start'])}→{fmt_ts(new_start)} 《{sg['title']}》"
            )
            sg["start"] = new_start
    return merged


def _absorb_shadowed_unknowns(merged: list) -> list:
    """吸收同起点的双检测未知行: 切片碎片与幽灵命中经音乐段对齐后常撞在
    同一起点(1:18:53《绝对占有相对自由》+《未知曲目》双行案例: 碎片被对齐
    推后11s、幽灵行被对齐提前27s, 正好撞车), 同一起点(±15s)的一行有名、
    一行未知 = 同一场表演被检测了两次——歌已被有名行展示, 删除未知行并把
    它的跨度并进有名行(否则重叠整形会把先到的有名行裁成1s, 歌体时长全丢)。
    必须在对齐之后、resolve_overlaps 之前执行。
    安全性: 两首不同的歌真实起点不可能相差≤15s, 正经未知行不会被误吸"""
    songs = [sg for sg in merged if sg.get("type") == "song"]
    titled = [
        sg for sg in songs if (sg.get("title") or "").strip() not in ("", "未知曲目")
    ]
    drop: set = set()
    for u in songs:
        if (u.get("title") or "").strip() != "未知曲目" or id(u) in drop:
            continue
        for t in titled:
            if t is u or id(t) in drop:
                continue
            if abs(int(u["start"]) - int(t["start"])) <= 15:
                _log(
                    f"吸收同起点双检测未知行: {fmt_ts(u['start'])} 并入"
                    f"《{t['title']}》(跨度延伸至{fmt_ts(max(int(t['end']), int(u['end'])))})"
                )
                t["end"] = max(int(t["end"]), int(u["end"]))
                drop.add(id(u))
                break
    if drop:
        merged = [sg for sg in merged if id(sg) not in drop]
    return merged


def _resolve_overlaps(merged: list) -> list:
    """时间轴收尾整形: 前段 end 超过后段 start 时截断前段。
    起点对齐/拆分/桥接后相邻段可能重叠(无情画end压着关键词start),
    只改边界不删段不改名, 无吞歌风险。"""
    merged.sort(key=lambda x: (x["start"], x["end"]))
    for a, b in zip(merged, merged[1:]):
        if a["end"] > b["start"]:
            a["end"] = b["start"]
            if a["end"] <= a["start"]:
                a["end"] = a["start"] + 1
    return merged


def _arbitrate_titles(merged: list, lyric_ids: dict, cands_map: dict) -> list:
    """双证据仲裁: LLM 歌词判断 high 时优先于跑调翻唱的指纹匹配;
    LLM 没把握时, 可疑的识别标题降级为 未知曲目(歌单宁缺毋滥)"""
    targets = [sg for sg in merged if sg["type"] == "song"]
    for idx, sg in enumerate(targets):
        lid = lyric_ids.get(idx) or {}
        lt = (lid.get("title") or "").strip()
        pos = fmt_ts(sg["start"])
        cands = cands_map.get(idx) or []
        top1 = cands[0] if cands else {}
        # 相邻歌段标题集合: 防窗口外溢护栏。
        # 段边界有±20-30s偏移, 段文本尾部常混入下一首的开头歌词, 歌词LLM因此
        # 频繁把段判成"下一首歌"(09:56→红色高跟鞋/34:20→老男孩/1:03:54→我们的爱 三连惨案)。
        # 翻案/降权目标若等于相邻歌段标题, 视为外溢噪声, 一律不算证据
        neighbor_titles = {
            (targets[j]["title"] or "").strip()
            for j in (idx - 1, idx + 1)
            if 0 <= j < len(targets)
        } - {"", "未知曲目"}
        # 待定段附带歌词检索 top3 候选, 前端可一键改歌名(不用手敲);
        # 挂在循环开头, 保证后续各降级分支(continue)也能带上
        if cands:
            sg["cands"] = [
                {"title": c["title"], "score": c["score"]} for c in cands[:3]
            ]
        # 双重确认(v5): LLM high 单独不构成翻案证据, 需满足:
        # ①检索top1同歌支持, 或②原标题不可信(非法/未知/低rec指纹);
        # 且 翻案目标≠相邻歌段标题(防窗口外溢), 短段(<60s)只信检索实锤
        retrieval_support = (
            bool(top1) and top1.get("title") == lt and (top1.get("score") or 0) >= 0.35
        )
        weak_current = (
            (not _sane_title(sg["title"]))
            or sg["title"] in ("", "未知曲目")
            or (sg.get("src") == "reco" and int(sg.get("rec_times") or 0) < 50)
        )
        # 段名截断: 现有标题是LLM判定结果的子串(如《相对自由》⊂《绝对占有相对自由》),
        # 说明切片/来源只记住了半截歌名, LLM high 时直接补全。
        # 该路径不受 weak_current 限制(src=llm 的段没有rec可判weak), 但仍受邻居护栏约束
        cur_title = (sg["title"] or "").strip()
        truncated_title = (
            len(cur_title) >= 3 and bool(lt) and cur_title != lt and cur_title in lt
        )
        # 同名护栏(主播惯例: 一场不重复唱同一首): 翻案目标若在别行已被强证据
        # 占据(rec>=50或人工补丁确认), 本行的翻案必是窗口外溢/误配幻影——
        # 1:25:02碎片被误判《一样的月光》high, 翻案后与1:28:21真身同名,
        # 后续merge把真身吞掉(漏歌); v7的30:14→《虚拟》(42:45有rec=92438真身)同理
        dup_claimed = bool(lt) and any(
            t2 is not sg
            and (t2.get("title") or "").strip() == lt
            and (
                int(t2.get("rec_times") or 0) >= 50
                or t2.get("patched")
                or t2.get("aliased")
            )
            for t2 in targets
        )
        if (
            lt
            and lt not in ("", "未知曲目")
            and lt != cur_title
            and lid.get("confidence") == "high"
            and dup_claimed
        ):
            _log(
                f"歌词仲裁: {pos} 《{cur_title or '未知曲目'}》翻案目标《{lt}》"
                f"与他行强证据同名, 阻断(一场不重唱)"
            )
        # 别名修正过的标题是人工级证据(位置锁定的历史纠错结论),
        # 不允许被当次歌词投票推翻——骰子 variance 不能翻盘已确证的知识
        # (1:03:54《带我走》被歌词骰子翻成《绝对占有相对自由》的实测惨案)
        if (
            sg.get("aliased")
            and lt
            and lt not in ("", "未知曲目")
            and lt != cur_title
            and lid.get("confidence") == "high"
        ):
            _log(f"歌词仲裁: {pos} 《{cur_title}》别名已确证, 阻断翻案→《{lt}》")
        if (
            lt
            and lt != "未知曲目"
            and lid.get("confidence") == "high"
            and lid.get("chars", 0) >= 25
            and (retrieval_support or weak_current or truncated_title)
            and lt not in neighbor_titles
            and not dup_claimed
            and not sg.get("aliased")
            and (
                sg["end"] - sg["start"] >= 60
                or retrieval_support
                or truncated_title
                # 短段是"未知"时没什么可失去的: 文本量足够(>=100字)就信LLM,
                # 否则 v5 的短段护栏会把 1:18:39→绝对占有相对自由 这类正确翻案也拦掉
                or (sg["title"] in ("", "未知曲目") and lid.get("chars", 0) >= 100)
            )
        ):
            if sg["title"] != lt:
                _log(f"歌词仲裁: {pos} 《{sg['title']}》→《{lt}》")
                sg["title"] = lt
                sg["src"] = "llm"
            else:
                _log(f"歌词仲裁: {pos} 确认《{lt}》")
            continue
        _log(f"歌词仲裁: {pos} 保留《{sg['title']}》({lid.get('confidence', '-')})")
        rt = int(sg.get("rec_times") or 0)
        claimed = (sg["title"] or "").strip()
        llm_confirms = bool(lt) and lt == claimed and lid.get("confidence") == "high"
        if sg.get("src") == "reco" and sg.get("engine") == "legacy" and 0 < rt < 50:
            # 来源不明的旧缓存冷门命中, 对热门翻唱场基本是错配。
            # v8 起 hum 不再适用本规则: hum 冷门命中常是真冷门歌
            # (《我们的爱》全球仅识别1次但确实是它, 被误杀过),
            # hum 的稳定性误配由别名表负责, 标题保留进待审比抹成未知更有用
            _log(f"冷门匹配降级为未知(全球仅识别{rt}次): 《{sg['title']}》")
            sg["title"] = "未知曲目"
            continue
        if (
            sg.get("src") == "reco"
            and sg.get("engine") == "fp"
            and rt < 50
            and claimed not in ("", "未知曲目")
            and not sg.get("aliased")
            and not llm_confirms
        ):
            # 低热度指纹命中默认不可信: fp错配是位置性噪声, 同一错名会在不同位置
            # 掩盖不同的歌(有趣在职的情怀在25:15是牵丝戏、31:02是别的歌)。
            # 例外: 位置匹配的别名已修正(aliased) / 歌词LLM high确认原标题
            _log(f"低热度指纹命中降级为未知(rec={rt}): 《{claimed}》")
            sg["title"] = "未知曲目"
            continue
        if (
            claimed
            and claimed != "未知曲目"
            and not _sane_title(claimed)
            and not llm_confirms
        ):
            # 可疑标题降级为未知: 不限src——reconcile搬运后证据字段可能丢失(src=llm),
            # 《Trio Sonata...》这种古典乐全名不管什么来源都不该进歌单
            _log(f"可疑识别标题降级为未知: 《{claimed}》")
            sg["title"] = "未知曲目"
            continue
        # 文本证据矛盾降权(v4, 取代 v3 的"反向校验否决"):
        # v3 会把识别歌名直接抹成未知——但本地歌词库通常只有几十首、段窗口边界有偏移,
        # 检索 top1>=0.5 在这种薄证据上大量误杀正确歌名(关键词/红色高跟鞋/太聪明惨案)。
        # v4 只降权到 mid 进待审, 不改标题: 文本证据有资格说"我没把握", 没资格说"你是错的";
        # 且 LLM 歌词判断高置信确认了原标题时, 文本检索无权降权(50:38 起风了案例)
        lib_songs = set(_load_lyric_index().get("songs") or [])
        if (
            sg.get("src") == "reco"
            and claimed in lib_songs
            and cands
            and all(c["title"] != claimed for c in cands)
            and (cands[0].get("score") or 0) >= 0.5
            and not llm_confirms
            and (not lt or lt not in neighbor_titles)
        ):
            _log(
                f"文本证据矛盾, 降权待审: {pos} 《{claimed}》"
                f"(检索top1《{cands[0]['title']}》{cands[0]['score']}, LLM:{lt or '无'})"
            )
            sg["conf"] = "mid"
    return merged


def _dedup_same_title_weak(merged: list) -> list:
    """同名去重(主播惯例: 一场不重复唱同一首), 必须在合并链之后调用:
    同一首歌被切成的相邻碎片由 merge 先并回一行(25:15+27:44 牵丝戏碎片对,
    一强一弱, 若在合并前跑本规则会把弱的降成未知、永远无法并回——实测误伤)。
    合并后仍跨大时段重名的才是真嫌疑: 恰有唯一强证据行(rec>=50/人工补丁)时,
    其余弱证据同名行必是幻影(误配/外溢翻案残留), 降级为未知并保留原名进候选
    (前端可一键恢复)。强强/弱弱同名不动: 强强交人工, 弱弱本来就该一起留着审"""
    songs = [sg for sg in merged if sg.get("type") == "song"]
    dup_groups: dict = {}
    for sg in songs:
        t = (sg.get("title") or "").strip()
        if t and t != "未知曲目":
            dup_groups.setdefault(t, []).append(sg)
    for t, rows in dup_groups.items():
        if len(rows) < 2:
            continue
        strong_rows = [
            r
            for r in rows
            if r.get("patched")
            or r.get("aliased")
            or int(r.get("rec_times") or 0) >= 50
        ]
        if len(strong_rows) != 1:
            continue
        for r in rows:
            if any(r is sr for sr in strong_rows):
                continue
            _log(
                f"同名弱证据降级: {fmt_ts(r['start'])} 《{t}》→未知曲目"
                f"(强证据同名在{fmt_ts(strong_rows[0]['start'])})"
            )
            r["cands"] = [{"title": t, "score": 0}] + (r.get("cands") or [])
            r["title"] = "未知曲目"
    return merged


def _conf_tier(sg: dict) -> str:
    """置信度分层: high=自动采纳 / mid=待审 / low=高度存疑"""
    if sg["type"] != "song":
        return ""
    if sg.get("conf") == "low":
        return "low"  # 上游已判存疑(如无证据短段), 不再提级
    if not (sg.get("title") or "").strip() or sg["title"] == "未知曲目":
        return "mid"  # 未知=待审(需要人工命名/从候选里挑), 语义上不是"存疑"
    if sg.get("weak"):
        # 弱命中(投票样本无独立性): 哼唱直接存疑, 指纹上限待审
        return "mid" if sg.get("engine") == "fp" else "low"
    tier = "mid"  # LLM 歌词仲裁翻案的, 需人工扫一眼
    if sg.get("src") == "reco":
        if sg.get("engine") == "fp":
            # 指纹也会错配长尾库冷门条目(rec=0 的 fp 实际是错的: 《有趣在职的情怀》案例),
            # 有热度背书才配全自动采纳
            tier = "high" if int(sg.get("rec_times") or 0) >= 50 else "mid"
        else:
            rt = int(sg.get("rec_times") or 0)
            if rt >= 1000:
                tier = "high"
            elif rt >= 50:
                tier = "mid"
            else:
                tier = "low"
    # 短段可信度上限 mid: 45s 内的片段证据天然单薄, 不允许全自动采纳
    if sg["end"] - sg["start"] < 45 and tier == "high":
        tier = "mid"
    # 巨型段(>7min)上限 mid: 单段跨多首歌的风险高(拆分失败的兜底)
    if sg["end"] - sg["start"] > 420 and tier == "high":
        tier = "mid"
    # 上游(歌词仲裁矛盾降权/短段标记)的 conf 只许压低, 不许提级
    if sg.get("conf") == "mid" and tier == "high":
        tier = "mid"
    return tier


def _bridge_split_songs(segs: list) -> list:
    """歌被弹幕/感谢短暂打断的碎片合并回同一首歌:
    模式: 歌A → 短闲聊(<=90s) → 歌A(同歌名) → 合并为一段(吞掉中间闲聊)"""
    out: list = []
    for sg in segs:
        if (
            len(out) >= 2
            and out[-1]["type"] == "chat"
            and out[-1]["end"] - out[-1]["start"] <= 90
            and sg["type"] == "song"
            and out[-2]["type"] == "song"
            and _mergeable_title(sg["title"], out[-2]["title"])
        ):
            out[-2]["end"] = max(out[-2]["end"], sg["end"])
            if not out[-2]["summary"] and sg["summary"]:
                out[-2]["summary"] = sg["summary"]
            out.pop()  # 去掉中间的短闲聊
            continue
        out.append(sg)
    return out


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
    '2. 唱歌段一律不要合并: 相邻同歌已在代码层合并过, 点歌重唱是直播常态, 即使同名同歌出现在不同时刻也必须各自保留为独立段; 已确定的歌名(title 非 "未知曲目")不要改动;\n'
    '3. 修复明显的重叠、时间倒挂、时间空洞; 时间格式 "分:秒" 或 "时:分:秒";\n'
    '4. 修正类型误判: 夹在唱歌中间的感谢口播归入歌曲段; 对面主播/连麦对象唱的歌 type 改为 "other" '
    "(不进歌单), 其闲聊不记入主播的话题;\n"
    '5. 保持口语简短风格(每条 summary 不超过 15 个字, 不以"聊/聊聊"开头);\n'
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
        if not fixed:
            return segs
        return _reattach_evidence(fixed, segs)  # 捡回 engine/rec_times/conf
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


def start_analysis(video: Path, mode: str = "full") -> str:
    # 幂等: 同一视频同一模式已有在跑/排队任务时直接复用。
    # 双击/重试产生的第二个任务会在 _analysis_lock 后排队, 跑完照常写结果——
    # 用户在中途保存的手改会被它整个覆盖, 且识别/LLM 花两份钱(实跑日志实锤)
    with _jobs_lock:
        for jid, job in _jobs.items():
            if (
                job.get("status") == "running"
                and job.get("path") == str(video)
                and job.get("mode") == mode
            ):
                _log(f"已有在跑任务 {jid}({video.name}, {mode}), 复用而不重复启动")
                return jid
    job_id = uuid.uuid4().hex[:12]
    _set_job(
        job_id,
        status="running",
        pct=0,
        msg="排队中(歌单模式)…" if mode == "songs" else "排队中…",
        path=str(video),
        mode=mode,
    )

    def _run_guarded() -> None:
        with _analysis_lock:  # 有任务在跑就排队等待, 绝不并发
            _run_analysis(job_id, video, mode)

    threading.Thread(
        target=_run_guarded, name=f"analysis-{job_id}", daemon=True
    ).start()
    return job_id


def _full_pipeline(job_id: str, segs: list, hits: list, wav: Path, video: Path) -> list:
    """完整模式: 逐 chunk 内容总结 → 代码合并 → 全局把关 → 二轮识别。
    切片(chunk总结+合并+把关)结果落盘缓存: 这一步是LLM现场生成、temp>0,
    每跑一遍切法都不同(同一代码两次跑出天差地别的歌单), 且占全程大半耗时。
    锁定一次好切片, 重跑才能稳定复现并秒回; 切片差时删 .tiles.json 重新掷骰"""
    tp = _tiles_path(video)
    if tp.exists():
        try:
            data = json.loads(tp.read_text(encoding="utf-8"))
            if data.get("ver") == TILES_VERSION and isinstance(
                data.get("segments"), list
            ):
                _log(
                    f"复用段落切片缓存({len(data['segments'])}段), "
                    "跳过逐chunk总结与全局校对(想重新掷骰请删除对应 .tiles.json)"
                )
                return _recognize_llm_songs(job_id, wav, data["segments"], video)
        except Exception:
            pass
    chunk_target = _env_int("LLM_CHUNK_SECONDS", 600)
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
    merged = _bridge_split_songs(
        _llm_reconcile(_apply_song_hits(_merge_segments(collected), hits))
    )
    try:
        tp.write_text(
            json.dumps({"ver": TILES_VERSION, "segments": merged}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass
    # 切片质量自检: >=2个巨型唱歌段说明本次掷骰很差, 提示用户可重掷
    giants = [
        sg
        for sg in merged
        if sg.get("type") == "song" and sg["end"] - sg["start"] > 420
    ]
    if len(giants) >= 2:
        _log(
            f"警告: 本次切片含 {len(giants)} 个巨型唱歌段(>7min), 质量较差; "
            "如结果不理想, 删除 .tiles.json 后重新生成可重掷"
        )
    return _recognize_llm_songs(job_id, wav, merged, video)


def _drop_short_songs(segs: list, min_s: int = 45) -> list:
    """过短 song 段原则上不丢弃(宁多列勿吞歌), 仅丢掉 <20s 且无任何证据的噪声段;
    其余短段保留并由 _conf_tier 压低置信度, 交给人工待审队列决定"""
    out = []
    for sg in segs:
        if sg["type"] == "song" and sg["end"] - sg["start"] < min_s:
            at_edge = sg["start"] < 90
            # 弱命中(假投票)不构成豁免证据: 《时光》类 13s 幻听段由此被拦
            has_evidence = (
                sg.get("src") == "reco"
                and _sane_title(sg["title"])
                and not sg.get("weak")
            )
            if at_edge or has_evidence:
                out.append(sg)
                continue
            if sg["end"] - sg["start"] < 20:
                _log(
                    f"丢弃过短song段({sg['end'] - sg['start']:.0f}s): 《{sg['title']}》"
                )
                continue
            # 20-45s 无证据短段(清唱片段/点歌哼两句): 保留但强制待审
            sg["conf"] = "low"
            _log(
                f"保留无证据短song段({sg['end'] - sg['start']:.0f}s)并标存疑: 《{sg['title']}》"
            )
        out.append(sg)
    return out


def _run_analysis(job_id: str, video: Path, mode: str = "full") -> None:
    try:
        _log(f"任务开始: {video.name} mode={mode}")
        _set_job(job_id, pct=5, msg="提取音频…")
        wav = _wav_path(video)
        _extract_audio(video, wav)

        _set_job(job_id, pct=15, msg="语音转写: SenseVoice 快转中…")
        segs = _transcribe(wav, _txt_path(video), video)
        _set_job(job_id, pct=50, msg="转写完成, 进入歌曲识别…")

        # 歌曲识别(可选, SHAZAM_ENABLE=1): 规则圈候选 → 只对候选送 Shazam
        hits = []
        if _song_provider():
            try:
                candidates = _find_song_candidates(segs)
                # 音频级音乐段检测(可选, 免费): 补上文本规则抓不到的盲区
                if _env_int("MUSIC_SEG_ENABLE", 1):
                    try:
                        music_segs = _detect_music_segments(
                            wav, _music_cache_path(video)
                        )
                        for ms in music_segs:
                            candidates.append((ms["start"], ms["end"]))
                        if music_segs:
                            _log(f"音频级音乐段: {len(music_segs)} 段")
                    except Exception as e:
                        _log(f"inaSpeechSegmenter 不可用({e}), 仅文本规则候选")
                candidates = _merge_windows(candidates)
                # 全量粗扫(可选): 按固定间隔全程取样, 与候选重叠处自动跳过
                # RECOG_SCAN_INTERVAL=60 → 2小时约120次请求(原唱档约¥2.4/场), 基本不漏歌
                interval = _env_int("RECOG_SCAN_INTERVAL", 0)
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
                        _log(
                            f"粗扫模式: 候选 {len(candidates) - added} 段 + 补扫 {added} 点"
                        )
                if candidates:
                    # 预算耗尽时会截断后面的候选: 让 12s 粗扫点排最后,
                    # 规则/音乐段候选优先, 防止直播后半场被系统性漏扫
                    candidates.sort(key=lambda w: (w[1] - w[0] <= 15, w[0]))
                    _set_job(
                        job_id, pct=55, msg=f"歌曲识别中(候选 {len(candidates)} 段)…"
                    )
                    hits = _shazam_scan(job_id, wav, candidates, _songs_path(video))
                else:
                    _set_job(job_id, pct=55, msg="未检测到唱歌候选, 跳过歌曲识别")
            except Exception as e:
                # Shazam 只是增强: 任何失败都降级为纯 LLM 模式, 不拖垮任务
                _log(f"Shazam 识别不可用({e}), 降级为纯 LLM 模式")
                _set_job(job_id, pct=55, msg=f"Shazam 不可用({e}), 改用纯 LLM 识别")

        if mode == "songs":
            # 歌单模式: 跳过逐 chunk 内容总结, 歌单直接由识别结果组装
            _set_job(job_id, pct=75, msg="歌单模式: 组装识别结果…")
            merged = [
                {
                    "start": int(h["start"]),
                    "end": int(h["end"]),
                    "type": "song",
                    "title": h["title"],
                    "summary": "",
                    "src": "reco",
                    # engine/rec_times/weak 必须带上: 丢了置信分层会把指纹命中误压成存疑
                    "engine": h.get("engine", ""),
                    "rec_times": h.get("rec_times", 0),
                    "weak": bool(h.get("weak")),
                }
                for h in hits
            ]
            merged.sort(key=lambda x: (x["start"], x["end"]))
            merged = _merge_adjacent_songs(merged)
        else:
            merged = _full_pipeline(job_id, segs, hits, wav, video)
        try:
            _music_segs = _detect_music_segments(wav, _music_cache_path(video))
        except Exception:
            _music_segs = []
        merged = _recognize_uncovered_music(job_id, wav, merged, _music_segs, video)
        # 巨型唱歌段拆分(LLM切片掷出跨多歌大段时拆回单曲粒度),
        # 用全量命中缓存(含二轮/补漏追加)给子段取名
        try:
            _all_hits = json.loads(_songs_path(video).read_text(encoding="utf-8"))
        except Exception:
            _all_hits = hits
        merged = _split_giant_song_segments(merged, _music_segs, _all_hits)
        merged = _apply_recog_alias(merged)  # 顽固性误配的别名表, 在仲裁前修正标题
        _set_job(job_id, pct=95, msg="歌词辅助识别中…")
        _lyric_out, _cands_map = _identify_songs_by_lyrics(job_id, merged, segs)
        merged = _arbitrate_titles(merged, _lyric_out, _cands_map)
        # 版本标注括号代码侧确定性清除(全模式生效, 不再依赖LLM记得删)
        for _sg in merged:
            if _sg.get("type") == "song":
                _ct = _clean_title(_sg.get("title") or "")
                if _ct != (_sg.get("title") or ""):
                    _log(f"歌名版本标注清除: 《{_sg['title']}》→《{_ct}》")
                    _sg["title"] = _ct
        # 末尾再桥接合并一次: 二轮/补漏识别后追加的同歌碎片(中间夹短闲聊段)并回,
        # 例如补漏命中的两个《晴》夹着一个10s闲聊——早期那次桥接管不到晚到的段
        merged = _bridge_split_songs(_drop_short_songs(_merge_adjacent_songs(merged)))
        merged = _drop_short_songs(_merge_adjacent_songs(merged))
        # 开始时间戳对齐音频级音乐段(提前+口播区推后), 先吸收对齐撞车的
        # 双检测未知行, 再整形重叠边界
        merged = _align_song_starts_to_music(merged, _music_segs)
        merged = _absorb_shadowed_unknowns(merged)
        merged = _resolve_overlaps(merged)
        # 人工补丁重放(最高优先级证据), 补丁改同名后可能有新可合并碎片, 再收尾一次
        merged = _apply_manual_patches(merged, video)
        merged = _resolve_overlaps(_merge_adjacent_songs(merged))
        # 同名去重放在合并之后: 同歌碎片先并回, 剩下的跨时段重名才按幻影降级
        merged = _dedup_same_title_weak(merged)
        for _sg in merged:
            if not _sg.get("patched"):
                _sg["conf"] = _conf_tier(_sg)
            if _sg["type"] == "song":
                _log(
                    f"conf={_sg['conf'] or '-'} [{fmt_ts(_sg['start'])}] 《{_sg['title']}》"
                    f" src={_sg.get('src')} engine={_sg.get('engine') or '-'}"
                    f" rec={_sg.get('rec_times') or 0} weak={bool(_sg.get('weak'))}"
                )
        if mode == "songs":
            _set_job(job_id, pct=96, msg="歌单去重排序…")
            merged = _dedup_songs_llm(job_id, merged)
            overall = ""
        else:
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
            "mode": mode,
            "video": video.name,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "asr_model": os.environ.get("ASR_MODEL", "small"),
            "llm_model": os.environ.get("LLM_MODEL", "gpt-4o-mini"),
            "overall": overall,
            "segments": merged,
        }
        out = _json_path(video)
        out.parent.mkdir(parents=True, exist_ok=True)
        # 覆盖保护: 旧结果含人工手改(edited)时先备份再写,
        # 防止重新生成/排队任务把用户校准过的时间戳无声吞掉
        if out.exists():
            try:
                _old = json.loads(out.read_text(encoding="utf-8"))
                if _old.get("edited"):
                    _bak = out.with_name(out.stem + ".edited-backup.json")
                    _bak.write_text(
                        json.dumps(_old, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    _log(f"检测到人工修改过的旧结果, 已备份到 {_bak.name}")
            except Exception:
                pass
        out.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _log(f"分析完成: {len(merged)} 段, 已保存 {out.name}")
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
    _learn_alias_from_edits(old, segs)  # 人工纠正识别错名 → 自动学进别名表
    _learn_patches_from_edits(old, segs, video)  # 人工修正补丁层: 重跑自动重放
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
