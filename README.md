# YuriStream-Webui

基于 [DouyinLiveRecorder](https://github.com/ihmily/DouyinLiveRecorder) 深度改造的**直播录制 + Web 管理 + AI 内容分析**一体化工具。

在原生多平台循环录制的基础上，新增了：Web 管理界面（Vue3）、边录边播（HLS）、录像在线播放/下载/分片合并、**AI 视频总结与歌单提取**（ASR + 歌曲识别 + LLM 仲裁 + 本地歌词检索 + 人工反馈闭环）、B站投稿简介一键生成。

## ✨ 功能特性

### 录制核心（继承自 DouyinLiveRecorder）
- 50+ 直播平台循环值守录制（抖音/TikTok/快手/虎牙/斗鱼/B站/小红书/淘宝……），基于 FFmpeg 拉流
- 画质可选（原画/蓝光/超清/高清/标清/流畅），分段录制、自动转 mp4、磁盘空间保护
- 直播状态推送（钉钉/微信/TG/Bark/NTFY/邮箱等）
- 主播 URL 自动规范化回写，注释 `#` 即停用

### Web 管理（webapi.py + App.vue）
- **监控管理**：粘贴直播间 URL → 解析主播信息 → 开始录制 / 停用 / 删除（软删除=注释语义）
- **正在录制**：实时时长/画质展示，一键「观看直播」
- **边录边播**：录制线程暴露当前拉流地址，独立 ffmpeg 打包 HLS（约 6 秒延迟），hls.js 播放
- **仅转播**：不录制不落盘，现场解析流地址直接转播（6 小时超时自动收摊）
- **录像树**：平台 → 主播 → 视频，在线播放（ts/flv/mkv 自动转封装 mp4，h265 自动转 h264，结果缓存）、下载、回收站删除（7 天自动清除）
- **分片合并**：勾选分片有序拼接（ffmpeg concat demuxer，不重编码）
- 移动端自适应（页签切换 + 大触控目标）

### AI 内容分析（analysis.py）—— 本项目的核心
对任意录像文件执行：**音频转写 → 唱歌检测 → 歌曲识别 → LLM 时间轴总结 → 歌词仲裁 → 置信分层**，产出可直接用于 B站简介的章节化歌单。

- 多层异构证据链：音频指纹/哼唱识别（ACRCloud/Shazam）+ LLM 语义判断 + 本地歌词库检索（汉字/拼音双通道 TF-IDF），互相印证
- 防吞歌设计：识别窗口指纹冲突自动二分重扫、一段跨多歌自动拆分、补漏识别、巨型段按音乐段拆回单曲粒度
- 置信三层分层（high/mid/low），前端「只看待审」聚焦人工火力
- **人工反馈闭环**：改歌名自动学习进别名表（hum 全局 / fp 位置锁定）；改名/挪起点/删行记成逐视频补丁，重跑自动重放——审核一次，终身有效
- 所有中间产物落盘缓存（转写/识别/音乐段/切片/补丁），重跑接近零成本
- 段落切片缓存锁定 LLM 随机性，同一场录像重跑结果稳定复现

### B站投稿工具链（bili_intro.py）
- 一键生成投稿 payload：标题 / 简介（含可点击跳转的歌单时间戳）/ 分区 / 标签 / 转载来源
- 主播级档案 `config/uploader_profiles.json` 覆盖默认配置
- 抖音用户主页 → 稳定直播间地址解析（未开播也可解析）
- 歌词库自动收割：`fetch_lrc.py` 从分析结果抓取历史歌名建本地歌词库

## 🗂 项目结构

```
YuriStream-Webui/
├── main.py                 # 录制主程序（已重构为可嵌入：import 不启动录制）
├── webapi.py               # FastAPI 服务：REST API + HLS 边录边播 + 播放转封装
├── analysis.py             # AI 内容分析：ASR/识别/LLM/歌词仲裁/补丁重放
├── bili_intro.py           # B站投稿简介生成器（CLI 可独立使用）
├── fetch_lrc.py            # 歌词抓取：收割历史歌名建本地歌词库
├── App.vue                 # Web 前端（Vue3 + Element Plus + hls.js）
├── src/
│   ├── spider.py           # 各平台直播数据抓取（异步）
│   ├── stream.py           # 各平台直播流地址解析
│   ├── room.py             # 抖音房间/用户解析、X-Bogus
│   ├── ab_sign.py          # 抖音 a_bogus 签名（SM3+RC4）
│   ├── utils.py / logger.py / proxy.py / initializer.py
│   ├── http_clients/       # async/sync HTTP 封装
│   └── javascript/         # x-bogus.js / ab_sign / 各平台 sign 脚本
├── config/
│   ├── config.ini          # 录制配置（首次运行自动生成）
│   ├── URL_config.ini      # 监控列表（唯一事实源，# 注释即停用）
│   └── uploader_profiles.json  # B站投稿主播档案（可选）
├── downloads/              # 录像保存目录
├── play_cache/             # 播放转封装缓存 + analysis/ 分析缓存 + live/ HLS 分片
├── lyrics/                 # 本地歌词库 (*.lrc) + recog_alias.json 识别别名表
└── logs/                   # 运行日志
```

## 🚀 快速开始

### 环境要求
- Python ≥ 3.10
- FFmpeg（加入 PATH）
- Node.js（部分平台签名需要 execjs，initializer 可自动安装）
- 可选：GPU（加速 whisper/faster-whisper）

### 安装

```bash
git clone <your-repo> YuriStream-Webui
cd YuriStream-Webui

pip install -U pip
pip install -r requirements.txt
# AI 分析额外依赖（按需）：
pip install faster-whisper funasr inaSpeechSegmenter shazamio pypinyin scikit-learn rapidfuzz
```

### 配置 `.env`（项目根目录，webapi 启动时自动加载）

```ini
# ---- 服务 ----
YS_PORT=8000                     # webapi 监听端口

# ---- ASR ----
ASR_PROVIDER=sensevoice          # sensevoice 快转(默认) / whisper
ASR_MODEL=small                  # faster-whisper 模型尺寸
ASR_DEVICE=cpu                   # cpu / cuda
ASR_COMPUTE=int8
CPU_THREADS=4

# ---- 歌曲识别 ----
SONG_PROVIDER=acrcloud           # acrcloud / shazam，留空则关闭识别走纯 LLM 模式
ACR_HOST=identify-cn.acrcloud.cn
ACR_ACCESS_KEY=你的Key
ACR_ACCESS_SECRET=你的Secret
ACR_HUM_SCORE=0.5                # 哼唱命中均分阈值
RECOG_VOTES=3                    # 每窗口投票样本数
RECOG_MAX_REQUESTS=120           # 识别请求预算上限
RECOG_SCAN_INTERVAL=0            # >0 开启全量粗扫（60 ≈ 2小时120次请求 ≈ ¥2.4/场，基本不漏歌）
MUSIC_SEG_ENABLE=1               # inaSpeechSegmenter 音频级音乐段检测

# ---- LLM ----
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-xxx
LLM_MODEL=gpt-4o-mini

# ---- 分析微调 ----
LYRIC_VOTES=3                    # 歌词判定 LLM 投票次数
GIANT_SONG_SECONDS=420           # 巨型唱歌段拆分阈值
ALIGN_LATER_MAX=120              # 唱歌段起点对齐音乐段(推后)上限
```

### 启动

```bash
# 方式一：Web 模式（推荐）—— 自动拉起录制核心 + Web 服务
python webapi.py
# 打开 http://127.0.0.1:8000 （前端为 App.vue 构建产物 / 开发时 vite 代理）

# 方式二：原生控制台模式
python main.py
```

### 首次添加主播
Web 界面「监控管理」→ 粘贴直播间 URL → 回车解析 → 「开始录制」。
或手动编辑 `config/URL_config.ini`，一行一个地址（支持 `画质,URL,主播: 名字` 格式）。

## 🧠 AI 分析管线详解

点击前端「视频总结」后，mp4 经历以下阶段（每步均有进度反馈）：

```
mp4 ──ffmpeg──► 16k 单声道 wav ──► SenseVoice 快转(+music段 whisper 二遍) ──► 转写文本(JSONL缓存)
                                                                   │
        inaSpeechSegmenter 音频级音乐段检测（缓存）◄───────────────┘
                                                                   │
        识别层：文本规则圈候选(口播提示词/压缩率/副歌重复) + music段 + 可选全量粗扫
              └─ 投票制识别(ACRCloud/Shazam)：指纹直接采信/哼唱多数决/冲突二分重扫/弱命中检测
                                                                   │
        full 模式：边界感知切块 → LLM 逐块时间轴(温度0) → 识别结果回填(跨歌自动拆分)
                    → LLM 全局把关 → 段落切片落盘缓存(.tiles.json)
                                                                   │
        补漏：LLM 标歌未识别的二轮识别 + music段未覆盖的补漏识别（均走缓存）
                                                                   │
        整形：巨型段拆分 → 别名表修正 → 本地歌词检索+LLM三票仲裁 → 版本标注清除
              → 短段策略(宁多列勿吞) → 起点对齐音乐段 → 重叠整形 → 人工补丁重放
                                                                   │
        置信分层 high/mid/low ──► {key}.json（含 edited 备份保护）
```

**人工闭环**：前端改歌名保存时 → 自动学习识别别名（`lyrics/recog_alias.json`）+ 记录逐视频补丁（`{key}.patches.json`），下次重新生成自动重放，人工结论永不丢失。

## 🔌 API 概览

| 接口 | 说明 |
|---|---|
| `GET /api/status` | 监控数 / 录制中列表（含时长、画质） |
| `GET/POST/DELETE /api/anchors` | 主播列表 / 添加（幂等 upsert）/ 停用或硬删除 |
| `POST /api/parse` | 解析直播间 URL → 平台/主播/状态/标题（不入库） |
| `GET /api/live` · `POST /api/live/start` | 边录边播：可观看列表 / 启动 HLS 打包 |
| `POST /api/live/watch` | 仅转播（不录制不落盘） |
| `GET /api/live/{slug}/{file}` | HLS 分片与 playlist |
| `GET/DELETE /api/recordings` | 录像清单 / 回收站删除 |
| `GET /api/play` · `GET /api/download` | 在线播放（自动转封装）/ 原始下载 |
| `POST /api/merge` · `GET /api/merge/{job}` | 分片合并（不重编码）+ 进度 |
| `GET/POST /api/analysis` | 读取 / 保存分析结果（保存触发别名与补丁学习） |
| `POST /api/analyze` · `GET /api/analyze/{job}` | 启动分析 / 进度轮询 |
| `GET /api/bili/intro` | 生成 B站投稿 payload（标题/简介/标签） |
| `GET /api/bili/resolve_room` | 抖音主页 → 稳定直播间地址 |

## ⚙️ 设计要点

- **URL_config.ini 是唯一事实源**：Web 写操作只改配置文件，录制主循环每 3 秒重读，改动秒级生效无需重启；删除采用"注释即停用"软删除语义，与原生行为一致
- **源文件永不修改**：分析缓存、播放转封装、HLS 分片全部落在 `play_cache/`，缓存键含 mtime，重录自动失效
- **全局分析互斥锁**：同一时刻只允许一个分析任务，防并发污染缓存；幂等检查防双击重复花钱
- **缓存即步骤图**：`{key}.wav → .txt → .music.json → .songs.json → .tiles.json → .json`，删任意一层即从该层重跑
- **识别预算保护**：请求预算上限 + 粗扫点排最后，预算耗尽时优先保障高信噪比候选，防后半场系统性漏扫

## 📝 备注

- B站**上传**接口暂未部署（服务器 IP 被风控），投稿简介生成不受影响；接入 biliup-rs 即可补上
- `main.py` 已重构为可嵌入模块（`import main` 不启动录制），SIGTERM 处理在非主线程自动跳过
- 部分平台抓取依赖 `execjs` + Node.js，缺失时 initializer 会自动尝试安装

## 🙏 致谢

- [ihmily/DouyinLiveRecorder](https://github.com/ihmily/DouyinLiveRecorder) — 录制核心与多平台解析
- ACRCloud / Shazamio — 歌曲识别服务
- SenseVoice / faster-whisper / inaSpeechSegmenter — 语音与音频分析
