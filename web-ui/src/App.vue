<template>
  <div class="page" :class="{ wide }">
    <header class="topbar">
      <span class="logo">YuriStream</span>
      <span class="topbar-right">
        <el-tag v-show="!isMobile" effect="plain">监控 {{ status.monitoring }}</el-tag>
        <el-tag v-show="!isMobile" type="danger" effect="dark">录制 {{ status.recording_count }}</el-tag>
        <el-button size="small" @click="mergeVisible = true">合并视频</el-button>
        <el-button size="small" type="primary" plain @click="openUpload()">上传B站</el-button>
        <el-button size="small" @click="manageVisible = true">监控管理</el-button>
      </span>
    </header>

    <div class="player-wrap">
      <div class="player-box" ref="playerBox">
        <div class="player-video">
          <video ref="videoEl" controls autoplay controlslist="nofullscreen"
                 :src="playPath ? `${API_BASE}/api/play?path=${encodeURIComponent(playPath)}` : undefined"></video>
          <canvas v-show="dmOn" ref="dmCanvas" class="dm-canvas" :style="dmLayerStyle"></canvas>
          <span v-if="isFs" class="fs-title">{{ playTitle }}</span>
          <div class="click-layer" @click="onVideoClick" @dblclick="toggleFs"></div>
          <span v-if="playTitle" class="player-title">{{ playTitle }}</span>
          <span v-else class="player-hint">从右侧录像树点播，或点"观看直播"</span>
          <span v-if="liveUrl" class="live-badge"><i></i>LIVE</span>
        </div>
        <div class="player-bar">
          <span class="pb-title">{{ liveUrl ? '实时转播中' : (playPath ? '回放' : '未在播放') }}</span>
          <span>
            <el-button v-if="playPath" size="small" type="primary" plain :icon="MagicStick" class="sum-btn" @click="openAnalysis(playPath)">视频总结</el-button>
            <el-button size="small" class="wide-btn" @click="wide = !wide">{{ wide ? '退出宽屏' : '宽屏' }}</el-button>
<el-button size="small" class="fs-btn" @click="toggleFs">{{ isFs ? '退出全屏' : '全屏' }}</el-button>
            <el-button size="small" :type="dmOn ? 'primary' : 'default'" plain @click="dmOn = !dmOn">{{ dmOn ? '弹幕 开' : '弹幕 关' }}</el-button>
<el-dropdown v-if="dmOn" size="small" trigger="click" @command="v => dmZone = v">
              <el-button size="small" plain>区域 {{ zoneLabel }}</el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="full">全屏</el-dropdown-item>
                  <el-dropdown-item command="top">上半屏</el-dropdown-item>
                  <el-dropdown-item command="third">上 1/3</el-dropdown-item>
                  <el-dropdown-item command="bottom">底部 1/3</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
<el-popover v-if="dmOn" placement="top" :width="250" trigger="click">
  <template #reference><el-button size="small" plain>弹幕设置</el-button></template>
  <div class="dm-set">
    <div class="dm-set-row"><span class="dm-set-label">字号</span><el-slider v-model="dmSize" :min="12" :max="28" :show-tooltip="false" /></div>
    <div class="dm-set-row"><span class="dm-set-label">速度</span><el-slider v-model="dmSpeedMul" :min="0.5" :max="2" :step="0.1" :show-tooltip="false" /></div>
    <div class="dm-set-row"><span class="dm-set-label">间距</span><el-slider v-model="dmSpacing" :min="4" :max="40" :show-tooltip="false" /></div>
    <div class="dm-set-row"><span class="dm-set-label">颜色</span>
      <el-radio-group v-model="dmColorMode" size="small">
        <el-radio-button value="white">白</el-radio-button>
        <el-radio-button value="colorful">彩</el-radio-button>
        <el-radio-button value="yellow">黄</el-radio-button>
        <el-radio-button value="red">红</el-radio-button>
        <el-radio-button value="blue">蓝</el-radio-button>
      </el-radio-group>
    </div>
  </div>
</el-popover>
<el-button size="small" text :disabled="!playPath && !liveUrl" @click="closePlayer">关闭</el-button>
          </span>
        </div>
      </div>
    </div>

    <div class="right-col">
      <div class="m-tabs">
        <span :class="{ on: mTab === 'rec' }" @click="mTab = 'rec'">正在录制</span>
        <span :class="{ on: mTab === 'files' }" @click="mTab = 'files'">录像</span>
      </div>

      <el-card v-show="!isMobile || mTab === 'rec'" shadow="never" class="card rec-card">
        <template #header>正在录制</template>
        <div v-for="r in status.recording" :key="r.name" class="rec-item" @click="watchLive(r.name)">
          <div class="rec-info">
            <span class="rec-dot"></span>
            <div>
              <div class="rec-name">{{ prettyName(r.name) }}</div>
              <div class="rec-sub">{{ fmtDuration(displayDur(r)) }} · {{ r.quality }}</div>
            </div>
          </div>
          <el-button size="small" plain round>观看直播</el-button>
        </div>
        <el-empty v-if="!status.recording?.length" description="暂无录制" :image-size="50" />
      </el-card>

      <el-card v-show="!isMobile || mTab === 'files'" shadow="never" class="card tree-card">
        <template #header>录像（{{ recordings.count }}）</template>
        <el-tree ref="treeRef" :data="treeData" node-key="key" :props="{ label: 'label' }"
                 @node-click="onNodeClick" @node-expand="d => expandedKeys.add(d.key)"
                 @node-collapse="d => expandedKeys.delete(d.key)" class="file-tree">
          <template #default="{ data }">
            <span class="tree-node">
              <span class="tree-label" :title="data.full || data.label">{{ data.label }}</span>
              <span v-if="data.file" class="tree-ops" @click.stop>
                <el-button :icon="CaretRight" circle size="small" title="播放" @click="play(data.file.path)" />
                <el-link :href="`/api/download?path=${encodeURIComponent(data.file.path)}`" download title="下载">
                  <el-icon><Download /></el-icon>
                </el-link>
                <el-button :icon="Delete" circle size="small" type="danger" plain title="删除(回收站)" @click="removeFile(data.file.path)" />
              </span>
            </span>
          </template>
        </el-tree>
        <el-empty v-if="!treeData.length" description="暂无录像" :image-size="50" />
      </el-card>
    </div>

    <el-dialog v-model="manageVisible" title="监控管理" :width="isMobile ? '94%' : '560px'" class="ma-dialog">
      <div class="row">
        <el-input v-model="newUrl" placeholder="粘贴直播间 URL，回车解析主播信息" clearable @keyup.enter="parseUrl" />
        <el-button :loading="parsing" @click="parseUrl">解析</el-button>
      </div>
      <div v-if="preview" class="preview">
        <el-tag size="small" effect="plain">{{ preview.platform }}</el-tag>
        <span class="pv-name">{{ preview.name }}</span>
        <el-tag v-if="preview.title" size="small" type="info" effect="plain" class="pv-title">{{ preview.title }}</el-tag>
        <el-tag size="small" :type="preview.is_live ? 'danger' : 'info'">{{ preview.is_live ? '直播中' : '未在播' }}</el-tag>
        <span class="pv-gap"></span>
        <el-button type="primary" size="small" @click="startRecord">开始录制</el-button>
        <el-button size="small" @click="confirmAdd">添加到列表</el-button>
        <el-button size="small" @click="watchOnly">仅转播</el-button>
        <el-button size="small" text @click="preview = null">取消</el-button>
      </div>
      <div class="ma-sec">
        监控列表 <span>启用 {{ anchors.active.length }} · 停用 {{ anchors.paused.length }}</span>
      </div>
      <div class="ma-list">
        <div v-for="u in anchors.active" :key="u.url" class="ma-item">
          <span class="dot" :class="{ on: isRecording(u) }"></span>
          <div class="ma-info">
            <div class="ma-name">{{ u.name || u.url }}</div>
            <div class="ma-url" :title="u.url">{{ u.url }}</div>
          </div>
          <el-tag v-if="isRecording(u)" type="danger" size="small" effect="light">录制中</el-tag>
          <el-button size="small" text type="primary" @click="rebroadcast(u.url)">转播</el-button>
          <el-switch :model-value="u.danmaku" size="small" inline-prompt active-text="弹幕" inactive-text="弹幕" @change="v => toggleDanmaku(u, v)" />
          <el-button size="small" text @click="disable(u.url)">停用</el-button>
          <el-button size="small" text type="danger" @click="removeAnchor(u.url)">删除</el-button>
        </div>
        <div v-for="u in anchors.paused" :key="u.url" class="ma-item">
          <span class="dot"></span>
          <div class="ma-info">
            <div class="ma-name off">{{ u.name || u.url }}</div>
            <div v-if="u.name" class="ma-url">{{ u.url }}</div>
          </div>
          <el-tag size="small" type="info" effect="light">已停用</el-tag>
          <el-button size="small" text type="primary" @click="rebroadcast(u.url)">转播</el-button>
          <el-switch :model-value="u.danmaku" size="small" inline-prompt active-text="弹幕" inactive-text="弹幕" @change="v => toggleDanmaku(u, v)" />
          <el-button size="small" text type="success" @click="enable(u.url)">启用</el-button>
          <el-button size="small" text type="danger" @click="removeAnchor(u.url)">删除</el-button>
        </div>
        <el-empty v-if="!anchors.active?.length && !anchors.paused?.length" description="暂无监控" :image-size="50" />
      </div>
    </el-dialog>

    <el-dialog v-model="uploadVisible" title="上传到 B站" :width="isMobile ? '96%' : '780px'" class="up-dialog" top="4vh">
      <div class="up-body">
        <div class="up-left">
          <div class="up-sec-title">视频文件（P1 为主视频，其余为分P）</div>
          <div class="p-list">
            <div v-for="(p, i) in uploadForm.paths" :key="p" class="p-item">
              <el-tag size="small" effect="plain">P{{ i + 1 }}</el-tag>
              <span class="p-name" :title="p">{{ nameOf(p) }}</span>
              <el-button size="small" text type="danger" :disabled="uploading" @click="uploadForm.paths.splice(i, 1)">移除</el-button>
            </div>
            <el-empty v-if="!uploadForm.paths.length" description="从下方点击视频加入" :image-size="40" />
          </div>
          <el-tree :data="treeData" node-key="key" default-expand-all :props="{ label: 'label' }"
                   class="pick-tree-inner up-tree" @node-click="pickForUpload">
            <template #default="{ data }">
              <span class="pick-node" :class="{ added: data.file && uploadSet.has(data.file.path) }">
                <span>{{ data.label }}</span>
                <el-tag v-if="data.file && uploadSet.has(data.file.path)" size="small" type="success" effect="plain">已加入</el-tag>
              </span>
            </template>
          </el-tree>
          <div class="pick-tip">点击文件即按顺序加入</div>
        </div>
        <div class="up-right">
          <el-form label-position="top">
            <el-form-item label="标题">
              <el-input v-model="uploadForm.title" maxlength="80" show-word-limit placeholder="标题" />
            </el-form-item>
            <el-form-item label="分区 ID (tid)">
              <el-input v-model="uploadForm.tid" placeholder="如 146" />
              <div class="field-tip">投稿页打开分区下拉 → F12 审查元素 → 选项上有 data-tid。常用：音乐综合146 / 音乐现场1011 / 翻唱31 / 游戏4 / 生活160</div>
            </el-form-item>
            <el-form-item label="标签（逗号分隔）">
              <el-input v-model="uploadForm.tag" placeholder="直播回放,虚拟主播" />
            </el-form-item>
            <el-form-item label="简介">
              <el-input v-model="uploadForm.desc" type="textarea" :rows="3" placeholder="可后补章节时间戳" />
            </el-form-item>
            <el-form-item label="转载来源（原直播间地址）">
              <el-input v-model="uploadForm.source" placeholder="https://live.douyin.com/..." />
            </el-form-item>
            <el-form-item label="B站 Cookie（三段分别粘贴，自动合并保存）">
              <div class="cookie-three">
                <el-input v-model="uploadForm.cookieSess" type="password" show-password placeholder="SESSDATA" />
                <el-input v-model="uploadForm.cookieJct" type="password" show-password placeholder="bili_jct" />
                <el-input v-model="uploadForm.cookieUid" type="password" show-password placeholder="DedeUserID（可选）" />
                <el-tag size="small" :type="cookieStatus.set ? 'success' : 'info'" effect="light">{{ cookieStatus.set ? '已保存' : '未保存' }}</el-tag>
              </div>
            </el-form-item>
          </el-form>
        </div>
      </div>
      <template #footer>
        <el-button @click="uploadVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" :disabled="!uploadForm.paths.length" @click="doUpload">{{ uploading ? uploadJobText : '投稿' }}</el-button>
      </template>
    </el-dialog>
    <el-dialog v-model="mergeVisible" title="合并分片（有序拼接，不重编码）" :width="isMobile ? '94%' : '520px'">
      <div class="merge-list">
        <div v-for="(p, i) in mergeList" :key="p" class="p-item">
          <el-tag size="small" type="warning" effect="plain">第{{ i + 1 }}段</el-tag>
          <span class="p-name" :title="p">{{ nameOf(p) }}</span>
          <el-button size="small" text :disabled="i === 0" @click="moveMerge(i, -1)">上移</el-button>
          <el-button size="small" text :disabled="i === mergeList.length - 1" @click="moveMerge(i, 1)">下移</el-button>
          <el-button size="small" text type="danger" @click="mergeList.splice(i, 1)">移除</el-button>
        </div>
        <el-empty v-if="!mergeList.length" description="从下方勾选分片后加入" :image-size="40" />
      </div>
      <div class="pick-tree">
        <el-tree :data="treeData" node-key="key" default-expand-all :props="{ label: 'label' }"
                 class="pick-tree-inner" @node-click="pickForMerge">
          <template #default="{ data }">
            <span class="pick-node" :class="{ added: data.file && mergeSet.has(data.file.path) }">
              <span>{{ data.label }}</span>
              <el-tag v-if="data.file && mergeSet.has(data.file.path)" size="small" type="success" effect="plain">已加入</el-tag>
            </span>
          </template>
        </el-tree>
        <div class="pick-tip">点击文件即按顺序加入，再点一次会提示已存在</div>
      </div>
      <template #footer>
        <el-button @click="mergeVisible = false">取消</el-button>
        <el-button type="primary" :disabled="mergeList.length < 2" :loading="merging" @click="doMerge">{{ merging ? mergeJobText : '开始合并' }}</el-button>
      </template>
    </el-dialog>
    <el-dialog v-model="analysisVisible" title="AI 内容总结" :width="isMobile ? '94%' : '920px'" top="5vh" class="ana-dialog">
      <div class="ana-head">
        <span class="ana-path" :title="analysisPath">{{ nameOf(analysisPath) }}</span>
        <span class="pv-gap"></span>
        <template v-if="!analyzing && analysisAnalyzed">
          <el-button size="small" :type="analysisView === 'edit' ? 'primary' : 'default'" @click="analysisView = 'edit'">编辑</el-button>
          <el-button size="small" :type="analysisView === 'text' ? 'primary' : 'default'" @click="analysisView = 'text'">纯文本</el-button>
        </template>
        <el-button v-if="!analyzing && analysisAnalyzed" size="small" type="primary" plain :icon="MagicStick" @click="regenAnalysis">重新生成</el-button>
        <el-button v-if="!analyzing && analysisAnalyzed" size="small" plain @click="startAnalysis('songs')">仅提取歌单</el-button>
          <el-button v-if="!analyzing && analysisAnalyzed" size="small" type="primary" plain @click="copyIntro">复制投稿简介</el-button>
        <template v-if="!analyzing && !analysisAnalyzed">
          <el-button size="small" type="primary" :icon="MagicStick" @click="startAnalysis('full')">开始生成</el-button>
          <el-button size="small" plain @click="startAnalysis('songs')">仅提取歌单</el-button>
        </template>
      </div>
      <div v-if="analyzing" class="ana-progress">
        <el-progress :percentage="analysisPct" :stroke-width="8" />
        <span class="ana-msg">{{ analysisMsg }}</span>
      </div>
      <template v-else-if="analysisAnalyzed">
        <template v-if="analysisView === 'edit'">
        <div class="ana-ov-row">
          <span class="ana-ov-label">整体总结</span>
          <el-input v-model="analysisOverall" type="textarea" :rows="2" size="small" placeholder="2-3 句话概括整场直播" @input="markDirty" />
        </div>
        <div class="ana-sec-title">🎵 内容时间轴</div>
        <div class="ana-list">
          <div v-for="(seg, i) in analysisSegments" :key="i"
             v-show="(!songOnly || seg.type === 'song') && (!reviewOnly || seg.conf !== 'high')"
             class="ana-seg" :class="{ 'ana-seg-warn': seg.conf && seg.conf !== 'high' }">
            <el-button link size="small" class="ana-seek" title="从头播放" @click="playSegAt(seg, 1)">头</el-button>
            <el-button link size="small" class="ana-seek" title="从中点播放(查段内换歌)" @click="playSegAt(seg, 0.5)">中</el-button>
            <el-button link size="small" class="ana-seek" title="播放结尾前10秒(查段尾吞歌)" @click="playSegAt(seg, -10)">尾</el-button>
            <el-input v-model="seg.startText" size="small" class="ana-time" placeholder="00:00" @change="markDirty" />
            <el-button link size="small" class="ana-nudge" title="开始 -10 秒" @click="nudgeSeg(seg, 'startText', -10)">‹</el-button>
            <el-button link size="small" class="ana-nudge" title="开始 +10 秒" @click="nudgeSeg(seg, 'startText', 10)">›</el-button>
            <span class="ana-dash">–</span>
            <el-input v-model="seg.endText" size="small" class="ana-time" placeholder="03:15" @change="markDirty" />
            <el-button link size="small" class="ana-nudge" title="结束 -10 秒" @click="nudgeSeg(seg, 'endText', -10)">‹</el-button>
            <el-button link size="small" class="ana-nudge" title="结束 +10 秒" @click="nudgeSeg(seg, 'endText', 10)">›</el-button>
            <el-select v-model="seg.type" size="small" class="ana-type" @change="markDirty">
              <el-option label="🎵 唱歌" value="song" />
              <el-option label="💬 闲聊" value="chat" />
              <el-option label="🎮 游戏" value="game" />
              <el-option label="🎁 感谢/SC" value="thanks" />
              <el-option label="📌 其他" value="other" />
            </el-select>
            <el-input v-if="seg.type === 'song'" v-model="seg.title" size="small" class="ana-title" placeholder="歌曲名（识别不出填 未知曲目）" @change="markDirty" />
            <el-tag v-if="seg.type === 'song'" size="small" effect="plain" class="ana-src-tag"
                    :type="seg.conf === 'high' ? 'success' : seg.conf === 'low' ? 'danger' : 'warning'">
              {{ seg.src === 'reco' ? '识别' : '猜测' }}{{ seg.conf === 'low' ? '·存疑' : seg.conf === 'mid' ? '·待审' : '' }}
            </el-tag>
            <el-tag v-if="seg.type === 'song' && (seg.musicBreaks || 0) >= 2" size="small" type="warning" effect="dark"
                    class="ana-src-tag" :title="`段内检测到 ${seg.musicBreaks} 个音乐段, 疑似跨歌, 建议点「尾」听结尾前10秒`">
              ⚠{{ seg.musicBreaks }}段音乐
            </el-tag>
            <el-input v-model="seg.summary" size="small" class="ana-summary" placeholder="一句话总结（可选）" @change="markDirty" />
            <el-button :icon="Delete" circle size="small" text type="danger" title="删除该段" @click="delSeg(i)" />
          </div>
        </div>
        <el-button size="small" text type="primary" @click="addSeg">+ 添加段落</el-button>
        <el-checkbox v-model="songOnly" size="small" class="ana-song-only">只看唱歌</el-checkbox>
        <el-checkbox v-model="reviewOnly" size="small">只看待审</el-checkbox>
        </template>
        <template v-else>
          <el-input type="textarea" :rows="14" :model-value="plainText" readonly class="ana-plain" />
          <div class="ana-copy-row">
            <el-checkbox v-model="textSongsOnly" size="small">仅歌单</el-checkbox>
            <span class="pv-gap"></span>
            <el-button size="small" type="primary" plain @click="copyAnalysisText">复制全部</el-button>
          </div>
        </template>
      </template>
      <el-empty v-else description="还没有内容总结" :image-size="60" />
      <template #footer>
        <span class="ana-foot">
          <span v-if="analysisDirty" class="ana-dirty">有未保存的修改</span>
          <span class="pv-gap"></span>
          <el-button @click="analysisVisible = false">关闭</el-button>
          <el-button type="primary" :disabled="!analysisDirty" :loading="analysisSaving" @click="saveAnalysisEdits">保存修改</el-button>
        </span>
      </template>
    </el-dialog>
    <el-dialog v-model="introVisible" title="投稿简介（剪贴板受限，请手动复制）" :width="isMobile ? '94%' : '560px'">
      <el-input type="textarea" :rows="16" :model-value="introText" readonly class="ana-plain" />
      <template #footer>
        <el-button type="primary" @click="introVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { nextTick, onMounted, onUnmounted, reactive, ref, watch, computed } from 'vue'
import Hls from 'hls.js'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CaretRight, Download, Delete, MagicStick } from '@element-plus/icons-vue'

const status = reactive({ monitoring: 0, recording_count: 0, recording: [] })
const anchors = reactive({ active: [], paused: [] })
const recordings = reactive({ count: 0, files: [] })
const newUrl = ref('')
const playPath = ref('')
const liveUrl = ref('')
const liveName = ref('')
const videoEl = ref(null)
const playerBox = ref(null)
const isFs = ref(false)
function onVideoClick() {
  const v = videoEl.value
  if (!v) return
  if (v.paused) v.play().catch(() => {})
  else v.pause()
}
function toggleFs() {
  if (!document.fullscreenElement) playerBox.value?.requestFullscreen?.()
  else document.exitFullscreen()
}
let fsHideTimer = null
function fsUiWake() {
  if (!isFs.value || !playerBox.value) return
  playerBox.value.classList.add('show-ui')
  clearTimeout(fsHideTimer)
  fsHideTimer = setTimeout(() => playerBox.value?.classList.remove('show-ui'), 3000)
}
function onFsChange() {
  isFs.value = !!document.fullscreenElement
  if (isFs.value) fsUiWake()
  else playerBox.value?.classList.remove('show-ui')
}
const treeRef = ref(null)
const parsing = ref(false)
const preview = ref(null)
const wide = ref(false)
const manageVisible = ref(false)
const isMobile = ref(window.innerWidth <= 768)
const mTab = ref('rec')
const expandedKeys = new Set()
let hls = null
let timer = null
let lastRefresh = Date.now()
const nowTs = ref(Date.now())

// 仅本机开发(localhost:5173)直连 8000; 手机/生产走同源代理
const API_BASE = (location.hostname === 'localhost' || location.hostname === '127.0.0.1') && location.port === '5173'
  ? 'http://127.0.0.1:8000'
  : ''

const onResize = () => { isMobile.value = window.innerWidth <= 768 }

// ---------- 录像树: 平台 → 主播 → 视频 ----------
const treeData = computed(() => {
  const plats = new Map()
  for (const f of recordings.files) {
    const segs = f.path.split(/[\\/]/)
    const plat = segs[0] || '其他'
    const anchor = segs.length > 2 ? segs[1] : '默认'
    if (!plats.has(plat)) plats.set(plat, new Map())
    const am = plats.get(plat)
    if (!am.has(anchor)) am.set(anchor, [])
    am.get(anchor).push({ ...f, label: shortLabel(f.name, f.size), full: f.name })
  }
  return [...plats].map(([plat, am]) => ({
    key: 'p:' + plat,
    label: `${plat}（${[...am.values()].flat().length}）`,
    children: [...am].map(([anchor, files]) => ({
      key: 'a:' + plat + '/' + anchor,
      label: `${anchor}（${files.length}）`,
      // 关键修复: 按分片序号升序(_003→_009), 不再跟随修改时间倒序(会把分片排反)
      children: [...files]
        .sort((a, b) => a.name.localeCompare(b.name))  // 文件名含日期时间+序号, 字典序即正确顺序
        .map(f => ({ key: 'f:' + f.path, label: f.label, full: f.full, file: f })),
    })),
  }))
})

function partNum(name) {
  // 分片文件名结尾的 _003(恰好3位数字, 不会误匹配时间里的 _59)
  const m = name.match(/_(\d{3})\.[^.]+$/)
  return m ? parseInt(m[1]) : 0
}

function shortLabel(fname, size) {
  const m = fname.match(/(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})/)
  const t = m ? `${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}` : fname
  const p = fname.match(/_(\d{3})\.[^.]+$/)
  return `${t}${p ? ' #' + p[1] : ''} · ${fmtSize(size)}`
}

function onNodeClick(data) {
  if (data.file) play(data.file.path)
}

// ---------- 播放 ----------
const playTitle = computed(() => {
  if (liveUrl.value) return liveName.value || '直播'
  if (playPath.value) return playPath.value.split(/[\\/]/).pop()
  return ''
})

function prettyName(n) {
  return (n || '').replace(/^序号\d+\s*/, '')
}

watch(playPath, async p => {
  if (p && hls) { hls.destroy(); hls = null; liveUrl.value = '' }
  dmDisconnectLive()
  dmClear()
  if (p && dmOn.value) await dmLoadPlayback(p)
})

watch(liveUrl, async url => {
  if (hls) { hls.destroy(); hls = null }
  if (!url) return
  await nextTick()
  if (!videoEl.value || !liveUrl.value) return
  if (Hls.isSupported()) {
    hls = new Hls({ liveSyncDurationCount: 2, maxBufferLength: 8 })
    hls.loadSource(encodeURI(url))
    hls.attachMedia(videoEl.value)
    hls.on(Hls.Events.ERROR, (_e, data) => {
      if (!data.fatal || !hls) return
      if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
        setTimeout(() => { if (hls && liveUrl.value) hls.startLoad() }, 2000)
      } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
        hls.recoverMediaError()
      } else {
        ElMessage.error('直播流无法播放（主播可能已下播）')
        closePlayer()
      }
    })
  } else {
    videoEl.value.src = url
  }
  syncLiveDanmaku()
})

function play(p) { playPath.value = p }

function closePlayer() {
  dmDisconnectLive()
  dmClear()
  playPath.value = ''
  liveUrl.value = ''
  liveName.value = ''
}

async function watchLive(name) {
  playPath.value = ''
  const res = await fetch(`/api/live/start?name=${encodeURIComponent(name)}`, { method: 'POST' })
  const data = await res.json()
  if (!res.ok) { ElMessage.error(`失败：${data.error}`); return }
  liveUrl.value = data.hls
  liveName.value = '直播 · ' + prettyName(name)
  dmLiveRecordName = name
}

// ---------- 监控管理 ----------
function isRecording(u) {
  return !!u.name && status.recording?.some(r => r.name.split(' ').pop() === u.name)
}

async function parseUrl() {
  if (!newUrl.value.trim()) return
  parsing.value = true
  try {
    const res = await fetch('/api/parse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: newUrl.value.trim() }),
    })
    const data = await res.json()
    if (!data.ok) { preview.value = null; ElMessage.error(`解析失败：${data.error}`) }
    else preview.value = data
  } finally { parsing.value = false }
}

async function addAnchor(url, name = '', paused = false) {
  const res = await fetch('/api/anchors', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, name, paused }),
  })
  const data = await res.json()
  if (res.ok) { ElMessage.success(data.note || '已添加'); newUrl.value = ''; refresh() }
  else ElMessage.error(`失败：${data.error}`)
}

function confirmAdd() {
  addAnchor(newUrl.value.trim(), preview.value?.name || '', true)
  preview.value = null
}

function startRecord() {
  addAnchor(newUrl.value.trim(), preview.value?.name || '', false)
  preview.value = null
}

const enable = url => addAnchor(url)

const opBusy = ref(false)

async function disable(url) {
  if (opBusy.value) return
  opBusy.value = true
  try {
    await fetch(`/api/anchors?url=${encodeURIComponent(url)}`, { method: 'DELETE' })
    refresh()
  } finally { opBusy.value = false }
}

async function removeAnchor(url) {
  if (opBusy.value) return
  opBusy.value = true
  try {
    await fetch(`/api/anchors?url=${encodeURIComponent(url)}&hard=true`, { method: 'DELETE' })
    refresh()
  } finally { opBusy.value = false }
}

async function rebroadcast(url) {
  const res = await fetch('/api/live/watch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  })
  const data = await res.json()
  if (!res.ok) { ElMessage.error(data.error || '转播失败'); return }
  liveUrl.value = data.hls
  liveName.value = '直播 · ' + (data.name || '转播')
  dmLiveRecordName = ''  // 转播无录制会话, 无弹幕
  manageVisible.value = false
}

async function watchOnly() {
  const url = newUrl.value.trim()
  if (!url) return
  await addAnchor(url, preview.value?.name || '', true)
  preview.value = null
  newUrl.value = ''
  await rebroadcast(url)
}

// ---------- 录播删除(回收站)与 B站投稿 ----------
const uploadVisible = ref(false)

// 关闭弹窗即保存参数(即使未投稿), 防止误关后重填
watch(uploadVisible, v => {
  if (!v) {
    localStorage.setItem(BILI_PREFS_KEY, JSON.stringify({
      desc: uploadForm.desc, tid: uploadForm.tid, tag: uploadForm.tag, source: uploadForm.source,
    }))
  }
})
const uploading = ref(false)
const uploadForm = reactive({ paths: [], title: '', desc: '', tid: '', tag: '', source: '', cookieSess: '', cookieJct: '', cookieUid: '' })
const uploadJobText = ref('')
const cookieStatus = ref({ set: false })
const mergeList = ref([])
const mergeVisible = ref(false)
const merging = ref(false)
const mergeJobText = ref('')

const uploadSet = computed(() => new Set(uploadForm.paths))
const mergeSet = computed(() => new Set(mergeList.value))
function nameOf(p) {
  const f = recordings.files.find(x => x.path === p)
  return f ? f.name : p.split('/').pop()
}
const BILI_PREFS_KEY = 'ys-bili-prefs'

function defaultTitle(fname) {
  return (fname.replace(/\.[^.]+$/, '').replace(/_/g, ' ') + ' 直播回放').slice(0, 80)
}

function openUpload() {
  let prefs = {}
  try { prefs = JSON.parse(localStorage.getItem(BILI_PREFS_KEY) || '{}') } catch {}
  uploadForm.paths = []
  uploadForm.title = '' 
  uploadForm.desc = prefs.desc || ''
  uploadForm.tid = prefs.tid || ''
  uploadForm.tag = prefs.tag || ''
  uploadForm.source = prefs.source || ''
  uploadForm.cookieSess = ''
  uploadForm.cookieJct = ''
  uploadForm.cookieUid = ''
  fetch('/api/bili/cookie').then(r => r.json()).then(d => {
    cookieStatus.value = d
    if (d.values) {
      uploadForm.cookieSess = d.values.SESSDATA || ''
      uploadForm.cookieJct = d.values.bili_jct || ''
      uploadForm.cookieUid = d.values.DedeUserID || ''
    }
  }).catch(() => {})
  uploadVisible.value = true
}
function pickForUpload(data) {
  if (!data.file) return  // 点目录只展开/折叠
  const p = data.file.path
  if (uploadForm.paths.includes(p)) { ElMessage.info(`${data.file.name} 已在列表中`); return }
  const isFirst = uploadForm.paths.length === 0
  uploadForm.paths.push(p)
  if (isFirst) uploadForm.title = defaultTitle(data.file.name)
}

async function doUpload() {
  if (!uploadForm.title.trim()) { ElMessage.warning('标题不能为空'); return }
  if (!uploadForm.paths.length) { ElMessage.warning('请选择视频文件'); return }
  uploading.value = true
  uploadJobText.value = '提交中…'
  try {
    const res = await fetch('/api/bilibili/upload', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        paths: uploadForm.paths,
        title: uploadForm.title.trim(),
        desc: uploadForm.desc,
        tid: parseInt(uploadForm.tid) || 0,
        tag: uploadForm.tag,
        copyright: 2,
        source: uploadForm.source,
        cookie: [uploadForm.cookieSess && `SESSDATA=${uploadForm.cookieSess}`,
                 uploadForm.cookieJct && `bili_jct=${uploadForm.cookieJct}`,
                 uploadForm.cookieUid && `DedeUserID=${uploadForm.cookieUid}`]
          .filter(Boolean).join('; '),
      }),
    })
    const data = await res.json()
    if (!res.ok) { ElMessage.error(`投稿失败：${data.error || data.detail || res.status}`); return }
    const job = data.job
    const done = await new Promise((resolve, reject) => {
      const timer = setInterval(async () => {
        try {
          const r = await fetch(`/api/bilibili/upload/${job}`)
          const d = await r.json()
          uploadJobText.value = d.msg || d.status
          if (d.status === 'done') { clearInterval(timer); resolve(d) }
          else if (d.status === 'failed') { clearInterval(timer); reject(new Error(d.msg)) }
        } catch (e) { /* 下轮重试 */ }
      }, 3000)
    })
    localStorage.setItem(BILI_PREFS_KEY, JSON.stringify({
      desc: uploadForm.desc, tid: uploadForm.tid, tag: uploadForm.tag, source: uploadForm.source,
    }))
    fetch('/api/bili/cookie').then(r => r.json()).then(d => { cookieStatus.value = d }).catch(() => {})
    ElMessage.success(`已投稿：${done.bvid || '成功'}`)
    uploadVisible.value = false
  } catch (e) {
    ElMessage.error(`投稿失败：${e.message}`)
  } finally { uploading.value = false }
}

async function removeFile(p) {
  try {
    await ElMessageBox.confirm('将移入回收站，7 天后彻底清除。确定删除？', '删除录播', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
  } catch { return }
  const res = await fetch(`/api/recordings?path=${encodeURIComponent(p)}&confirm=true`, { method: 'DELETE' })
  const data = await res.json().catch(() => ({}))
  if (res.ok) {
    ElMessage.success('已移入回收站')
    if (playPath.value === p) closePlayer()
    refresh()
  } else {
    ElMessage.error(`删除失败：${data.error || data.detail || res.status}`)
  }
}

// ---------- 分片合并 ----------
function pickForMerge(data) {
  if (!data.file) return
  const p = data.file.path
  if (mergeList.value.includes(p)) { ElMessage.info(`${data.file.name} 已在列表中`); return }
  mergeList.value.push(p)
}
function moveMerge(i, d) {
  const j = i + d
  if (j < 0 || j >= mergeList.value.length) return
  const arr = mergeList.value
  ;[arr[i], arr[j]] = [arr[j], arr[i]]
}
async function doMerge() {
  if (mergeList.value.length < 2) return
  merging.value = true
  mergeJobText.value = '合并中…'
  try {
    const res = await fetch('/api/merge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths: mergeList.value }),
    })
    const data = await res.json()
    if (!res.ok) { ElMessage.error(`合并失败：${data.error || data.detail || res.status}`); return }
    const job = data.job
    const done = await new Promise((resolve, reject) => {
      const timer = setInterval(async () => {
        try {
          const r = await fetch(`/api/merge/${job}`)
          const d = await r.json()
          mergeJobText.value = d.pct > 0 ? `合并中 ${d.pct}%` : '合并中…'
          if (d.status === 'done') { clearInterval(timer); resolve(d) }
          else if (d.status === 'failed') { clearInterval(timer); reject(new Error(d.msg)) }
        } catch (e) { /* 下轮重试 */ }
      }, 1500)
    })
    if (done.danmaku?.gaps?.length) {
      const lines = done.danmaku.gaps.map(g => `${g.after} → ${g.before}：缺口 ${g.seconds}s`)
      ElMessageBox.alert(
        `画面缺失区间已检测并跳过（弹幕衔接对齐）：\n\n${lines.join('\n')}\n\n缺口区间丢弃弹幕 ${done.danmaku.dropped_in_gap ?? 0} 条（源档案未删）`,
        '合并完成 · 有断连缺口',
        { type: 'warning' },
      )
    } else {
      ElMessage.success(`已合并：${done.path}`)
    }
    mergeList.value = []
    mergeVisible.value = false
    refresh()
  } catch (e) {
    ElMessage.error(`合并失败：${e.message}`)
  } finally { merging.value = false }
}

// ---------- AI 内容总结 ----------
const analysisVisible = ref(false)
const analysisPath = ref('')
const analysisSegments = ref([])
const analysisAnalyzed = ref(false)
const analyzing = ref(false)
const analysisPct = ref(0)
const analysisMsg = ref('')
const analysisSaving = ref(false)
const analysisDirty = ref(false)
const analysisView = ref('edit')
const analysisOverall = ref('')
const songOnly = ref(false)
const textSongsOnly = ref(false)
const reviewOnly = ref(false)

function fmtTs(s) {
  s = Math.max(0, parseInt(s) || 0)
  const pad = x => String(x).padStart(2, '0')
  const h = Math.floor(s / 3600)
  const m = Math.floor(s / 60) % 60
  return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${pad(m)}:${pad(s % 60)}`
}

function parseTs(t) {
  const parts = String(t || '').split(':').map(x => parseInt(x))
  if (parts.some(isNaN)) return 0
  return parts.reduce((a, b) => a * 60 + b, 0)
}

function toEditable(segments) {
  return (segments || []).map(s => ({
    startText: fmtTs(s.start),
    endText: fmtTs(s.end),
    type: s.type || 'other',
    title: s.title || '',
    summary: s.summary || '',
    src: s.src || 'llm',
    conf: s.conf || '',
    weak: s.weak || false,
    musicBreaks: s.music_breaks || 0,
  }))
}

function fromEditable() {
  return analysisSegments.value
    .map(s => ({ start: parseTs(s.startText), end: parseTs(s.endText), type: s.type, title: s.title.trim(), summary: s.summary.trim(), src: s.src || 'llm', conf: s.conf || '', weak: s.weak || false, music_breaks: s.musicBreaks || 0 }))
    .sort((a, b) => a.start - b.start)
}

function markDirty() { analysisDirty.value = true }

const TYPE_LABELS = { song: '', chat: '', game: '', thanks: '感谢', other: '' }

function fmtChapterTs(s) {
  const pad = x => String(x).padStart(2, '0')
  const h = Math.floor(s / 3600)
  const m = Math.floor(s / 60) % 60
  return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${m}:${pad(s % 60)}`
}

function chapterLabel(s) {
  if (s.type === 'song') {
    return `《${s.title || '未知曲目'}》${s.summary ? ' ' + s.summary : ''}`
  }
  const body = s.summary || s.title
  if (body) return `${TYPE_LABELS[s.type] || ''}${body}`
  return TYPE_LABELS[s.type] || '日常'
}

function buildPlainText() {
  const segs = fromEditable()
  if (textSongsOnly.value) {
    // 仅歌单模式: 一行一首歌, 发 B站评论区/置顶歌单专用
    return segs
      .filter(s => s.type === 'song')
      .map(s => `${fmtChapterTs(s.start)} \u300a${s.title || '未知曲目'}\u300b${s.summary ? ' ' + s.summary : ''}`)
      .join('\n')
  }
  const lines = []
  const overall = analysisOverall.value.trim()
  if (overall) lines.push(overall, '')
  for (const s of segs) {
    if (s.type === 'thanks') continue  // 旧数据兼容: 感谢环节不进总结
    lines.push(`${fmtChapterTs(s.start)} ${chapterLabel(s)}`)
  }
  return lines.join('\n')
}

const plainText = computed(() => buildPlainText())

async function copyAnalysisText() {
  const text = plainText.value
  if (!text) { ElMessage.warning('没有可复制的内容'); return }
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.warning('浏览器限制了自动复制，请在文本框里手动 Ctrl+A / Ctrl+C')
  }
}

function addSeg() {
  const last = analysisSegments.value[analysisSegments.value.length - 1]
  const start = last ? parseTs(last.endText) : 0
  analysisSegments.value.push({ startText: fmtTs(start), endText: fmtTs(start + 180), type: 'song', title: '', summary: '' })
  markDirty()
}

function delSeg(i) { analysisSegments.value.splice(i, 1); markDirty() }

function nudgeSeg(seg, field, delta) {
  seg[field] = fmtTs(parseTs(seg[field]) + delta)
  markDirty()
}

async function openAnalysis(p) {
  analysisPath.value = p
  analysisVisible.value = true
  analyzing.value = false
  analysisDirty.value = false
  analysisView.value = 'edit'
  await loadAnalysis()
}

async function loadAnalysis() {
  const res = await fetch(`/api/analysis?path=${encodeURIComponent(analysisPath.value)}`)
  const data = await res.json()
  if (data.analyzed) {
    analysisAnalyzed.value = true
    analysisOverall.value = data.result.overall || ''
    analysisSegments.value = toEditable(data.result.segments)
  } else {
    analysisAnalyzed.value = false
    analysisSegments.value = []
  }
}

async function startAnalysis(mode = 'full') {
  if (mode === 'songs' && analysisAnalyzed.value) {
    try {
      await ElMessageBox.confirm('歌单模式只保留歌曲时间轴(无闲聊总结)，会覆盖当前内容，确定？', '仅提取歌单', { type: 'warning' })
    } catch { return }
  }
  analyzing.value = true
  analysisPct.value = 0
  analysisMsg.value = '启动中…'
  try {
    const res = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: analysisPath.value, mode }),
    })
    const data = await res.json()
    if (!res.ok) { ElMessage.error(`启动失败：${data.error}`); return }
    await pollAnalysisJob(data.job)
  } finally { analyzing.value = false }
}

function pollAnalysisJob(job) {
  return new Promise((resolve) => {
    const timer = setInterval(async () => {
      try {
        const r = await fetch(`/api/analyze/${job}`)
        const d = await r.json()
        analysisPct.value = d.pct || 0
        analysisMsg.value = d.msg || d.status
        if (d.status === 'done') {
          clearInterval(timer)
          analysisAnalyzed.value = true
          analysisOverall.value = d.result?.overall || ''
          analysisSegments.value = toEditable(d.result?.segments || [])
          analysisDirty.value = false
          ElMessage.success('总结生成完成')
          resolve()
        } else if (d.status === 'failed') {
          clearInterval(timer)
          ElMessage.error(`分析失败：${d.msg}`)
          resolve()
        }
      } catch { /* 下轮重试 */ }
    }, 3000)
  })
}

async function regenAnalysis() {
  try {
    await ElMessageBox.confirm('将重新生成总结（语音转写走缓存，只重跑大模型）。当前内容会被覆盖，确定继续？', '重新生成', { type: 'warning' })
  } catch { return }
  analysisDirty.value = false
  await startAnalysis()
}

async function saveAnalysisEdits() {
  analysisSaving.value = true
  try {
    const res = await fetch('/api/analysis', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: analysisPath.value, segments: fromEditable(), overall: analysisOverall.value }),
    })
    const data = await res.json()
    if (!res.ok) { ElMessage.error(`保存失败：${data.error}`); return }
    analysisDirty.value = false
    ElMessage.success('已保存')
  } finally { analysisSaving.value = false }
}

function playSegAt(seg, where) {
  // where: 1=段开头, 0.5=段中点, -10=结尾前10秒; 短段自动退化到开头
  if (playPath.value !== analysisPath.value) play(analysisPath.value)
  const st = parseTs(seg.startText)
  const en = parseTs(seg.endText)
  let t
  if (where === 1) t = st + 0.3
  else if (where === 0.5) t = (st + en) / 2
  else t = Math.max(st + 0.3, en + where)
  nextTick(() => { if (videoEl.value) videoEl.value.currentTime = t })
}

// ---------- 弹幕: Canvas 引擎(B站/DPlayer 同款方案) / 区域 / 设置 / 直播 SSE ----------
// 核心: 单 canvas 每帧清屏重绘, 零 DOM 布局/重绘, 流畅度由绘制管线保证
const dmOn = ref(localStorage.getItem('ys-danmaku') !== '0')
const dmZone = ref(localStorage.getItem('ys-dm-zone') || 'full')
const dmSize = ref(parseInt(localStorage.getItem('ys-dm-size') || '16'))
const dmSpeedMul = ref(parseFloat(localStorage.getItem('ys-dm-speed') || '1'))
const dmColorMode = ref(localStorage.getItem('ys-dm-color') || 'white')
const dmSpacing = ref(parseInt(localStorage.getItem('ys-dm-spacing') || '14'))
const dmCanvas = ref(null)
let dmQueue = []            // 回放: 待投放事件(文件本地 ts 排序)
let dmActive = []           // {text, lane, speed, width, x, color, alpha, size}
let dmSource = null         // EventSource(直播模式)
let dmLiveRecordName = ''   // 直播 SSE 用的 record_name(仅"正在录制"入口有会话)
let dmLiveHold = []         // 直播暂停时暂存(恢复后补投)
let dmTimer = null          // 投放调度器(200ms 轮询)
let dmRaf = null            // 绘制循环
let dmLastDrawTs = 0
const laneState = new Array(24).fill(null)
const dmRecentTexts = new Map()
const DM_MAX_SPAWN_PER_SEC = 8
let dmBudget = 0
let dmBudgetTs = 0

const zoneLabel = computed(() => ({ full: '全屏', top: '上半', third: '上⅓', bottom: '底⅓' }[dmZone.value] || '全屏'))
const dmLayerStyle = computed(() => ({
  full: { top: '0', height: '100%' },
  top: { top: '0', height: '50%' },
  third: { top: '0', height: '33%' },
  bottom: { top: '64%', height: '36%' },
}[dmZone.value]))
const DM_FONT = '"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif'

// 抖音表情码 → emoji(未命中保留原码; 表可自由扩充)
const DM_EMOJI = {
  '捂脸': '🤦', '流泪': '😭', '笑哭': '😂', '大笑': '😄', '呲牙': '😁', '嘻嘻': '😁',
  '送心': '❤️', '比心': '❤️', '爱心': '❤️', '心碎': '💔', '赞': '👍', '顶呱呱': '👍',
  '强': '💪', '加油': '💪', '加油加油': '💪', '鼓掌': '👏', '感谢': '🙏', '保佑': '🙏',
  '看': '👀', '思考': '🤔', '机智': '😏', '吃瓜群众': '🍉', '玫瑰': '🌹', '害羞': '😊',
  '可爱': '😊', '呆无辜': '🥺', '可怜': '🥺', '委屈': '😢', '哭泣': '😭', '吐血': '🤢',
  '发怒': '😡', '哼': '😤', '震惊': '😱', '晕': '😵‍💫', '宕机': '😵', '疑问': '❓',
  '彩虹屁': '🌈', '击掌': '🙌', '炸弹': '💣', '抠鼻': '🤏', '舔屏': '😋', '暖心': '🥰',
  '抱抱': '🤗', '哈欠': '🥱', '睡着': '💤', '钱袋': '💰', '灵机一动': '💡', '翻白眼': '🙄',
  '火火': '🔥', '庆祝': '🎉', '礼花': '🎉', '烟花': '🎆', '啤酒': '🍺', '奶茶': '🧋',
  '蛋糕': '🎂', '礼物': '🎁', '红包': '🧧', '来了来了': '🏃', '不失礼貌的微笑': '🙂',
  '握手': '🤝', 'OK': '👌',
}
const DM_PALETTE = ['#ffffff', '#ffd93d', '#ff6b6b', '#6bcb77', '#7cc7ff', '#f473b9', '#c084fc']
const DM_COLORS = { white: '#ffffff', yellow: '#ffd93d', red: '#ff6b6b', blue: '#7cc7ff' }

function dmFormatText(text) {
  return String(text).replace(/\[([^\[\]{}\s]{1,10})\]/g, (m, k) => DM_EMOJI[k] || m)
}

watch(dmZone, v => localStorage.setItem('ys-dm-zone', v))
watch(dmSize, v => localStorage.setItem('ys-dm-size', String(v)))
watch(dmSpeedMul, v => localStorage.setItem('ys-dm-speed', String(v)))
watch(dmColorMode, v => localStorage.setItem('ys-dm-color', v))
watch(dmSpacing, v => localStorage.setItem('ys-dm-spacing', String(v)))
watch(dmOn, v => {
  localStorage.setItem('ys-danmaku', v ? '1' : '0')
  if (!v) { dmStop(); dmClear(); dmDisconnectLive() }
  else if (playPath.value) dmLoadPlayback(playPath.value)
  else if (liveUrl.value) syncLiveDanmaku()
})

async function toggleDanmaku(u, val) {
  const res = await fetch('/api/danmaku/toggle', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url: u.url, enabled: val }),
  })
  if (!res.ok) { ElMessage.error('弹幕开关设置失败'); return }
  u.danmaku = val
  ElMessage.success(val ? `已开启「${u.name || u.url}」弹幕转存` : `已关闭「${u.name || u.url}」弹幕转存（进行中的会话下一场生效）`)
}

async function dmLoadPlayback(p) {
  dmQueue = []
  try {
    const res = await fetch(`/api/danmaku?path=${encodeURIComponent(p)}`)
    const data = await res.json()
    if (data.count > 0) {
      dmQueue = (data.events || []).slice().sort((x, y) => x.ts - y.ts)
      dmStart()
    }
  } catch { /* 无弹幕档案则忽略 */ }
}

function syncLiveDanmaku() {
  dmDisconnectLive()
  dmClear()
  if (!dmOn.value || !liveUrl.value || !dmLiveRecordName) return
  dmSource = new EventSource(`/api/danmaku/live?record_name=${encodeURIComponent(dmLiveRecordName)}`)
  dmSource.addEventListener('danmaku', e => {
    try {
      const ev = JSON.parse(e.data)
      if (videoEl.value && videoEl.value.paused) {
        if (dmLiveHold.length < 40) dmLiveHold.push(ev)
      } else dmSpawn(ev.text, ev.user)
    } catch { /* 结束帧忽略 */ }
  })
  dmSource.addEventListener('end', () => dmDisconnectLive())
  dmStart()
}

function dmDisconnectLive() {
  if (dmSource) { dmSource.close(); dmSource = null }
}

// 生成一条弹幕对象(不入屏): 测宽/选轨/限速/去重都在这里
function dmSpawn(text, user) {
  const cvs = dmCanvas.value
  if (!cvs || !text) return false
  const now = performance.now()
  if (now - dmBudgetTs > 1000) { dmBudgetTs = now; dmBudget = 0 }
  if (dmBudget >= DM_MAX_SPAWN_PER_SEC) return false
  if (dmActive.length >= 30) return false  // 同屏上限
  text = dmFormatText(text)
  // 复读限流: 45s 内同内容丢弃(该生态是几十用户复制粘贴同一句, 必须纯文本去重)
  const lastSeen = dmRecentTexts.get(text)
  if (lastSeen && now - lastSeen < 45000) return false
  if (dmRecentTexts.size > 600) dmRecentTexts.clear()
  dmRecentTexts.set(text, now)

  const W = cvs.clientWidth || 640
  const H = cvs.clientHeight || 360
  const size = dmSize.value
  const laneH = size + dmSpacing.value
  const ctx = cvs.getContext('2d')
  const full = user ? `${user}：${text}` : text
  ctx.font = `500 ${size}px ${DM_FONT}`
  const width = ctx.measureText(full).width
  // 速度: 下限 120px/s 且任何弹幕 9s 内穿出屏幕(防长弹幕爬墙成文字墙)
  let speed = Math.max(120, 220 - full.length * 3) * dmSpeedMul.value * (0.9 + Math.random() * 0.25)
  speed = Math.max(speed, (W + width + 30) / 9)
  // 防追尾选轨: 队首尾巴过 45% 屏 + 间隔 500ms + 新速不高于前速
  const lanes = Math.min(24, Math.max(4, Math.floor(H / laneH)))
  let lane = -1
  for (let i = 0; i < lanes; i++) {
    const st = laneState[i]
    if (!st) { lane = i; break }
    const tailNow = st.x0 + st.width - st.speed * (now - st.spawnTs) / 1000
    if (tailNow < W * 0.45 && now - st.spawnTs > 500 && speed <= st.speed * 1.05) { lane = i; break }
  }
  if (lane < 0) return false
  const color = dmColorMode.value === 'colorful'
    ? DM_PALETTE[Math.floor(Math.random() * DM_PALETTE.length)]
    : (DM_COLORS[dmColorMode.value] || '#ffffff')
  const item = {
    text: full, lane, speed, width, size,
    x0: W, x: W,
    spawnTs: now,
    color, alpha: 0.7 + Math.random() * 0.3,
  }
  laneState[lane] = item
  dmActive.push(item)
  dmBudget += 1
  if (!dmRaf) dmArmDraw()
  return true
}

// 绘制循环: 每帧清屏 + 按真实时间推进 + 描边文本(单管线, GPU 友好)
function dmDraw(tsNow) {
  dmRaf = null
  const cvs = dmCanvas.value
  if (!cvs) return
  const ctx = cvs.getContext('2d')
  const dpr = window.devicePixelRatio || 1
  const W = cvs.clientWidth || 640
  const H = cvs.clientHeight || 360
  if (cvs.width !== Math.round(W * dpr) || cvs.height !== Math.round(H * dpr)) {
    cvs.width = Math.round(W * dpr)
    cvs.height = Math.round(H * dpr)
  }
  // 视频暂停 → 冻结最后一帧不清屏(B站行为), play 事件会重新启动绘制
  if (videoEl.value && videoEl.value.paused && liveUrl.value === '') return
  const dt = Math.min(0.1, Math.max(0.001, (tsNow - dmLastDrawTs) / 1000))
  dmLastDrawTs = tsNow
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, W, H)
  const laneHBase = dmSize.value + dmSpacing.value
  for (let i = dmActive.length - 1; i >= 0; i--) {
    const d = dmActive[i]
    d.x -= d.speed * dt
    if (d.x < -d.width - 30) {
      if (laneState[d.lane] === d) laneState[d.lane] = null
      dmActive.splice(i, 1)
      continue
    }
    ctx.font = `500 ${d.size}px ${DM_FONT}`
    ctx.globalAlpha = d.alpha
    ctx.lineWidth = Math.max(2, d.size / 8)
    ctx.lineJoin = 'round'
    ctx.strokeStyle = 'rgba(0,0,0,0.55)'
    ctx.strokeText(d.text, d.x, d.lane * laneHBase + d.size)
    ctx.fillStyle = d.color
    ctx.fillText(d.text, d.x, d.lane * laneHBase + d.size)
  }
  ctx.globalAlpha = 1
  dmRaf = requestAnimationFrame(dmDraw)
}

function dmArmDraw() {
  if (dmRaf || !dmOn.value) return
  dmLastDrawTs = performance.now()
  dmRaf = requestAnimationFrame(dmDraw)
}

// 投放调度: 200ms 轮询到点事件(轻量; 绘制独立于此)
function dmScheduler() {
  try {
    if (!dmOn.value || !videoEl.value) return
    if (videoEl.value.paused) return  // 冻结: 不投放不补投
    const t = videoEl.value.currentTime
    while (liveUrl.value === '' && dmQueue.length && dmQueue[0].ts <= t + 0.15) {
      const ev = dmQueue.shift()
      if (ev.ts >= t - 2.0) dmSpawn(ev.text, ev.user)
    }
    while (dmLiveHold.length) {
      if (!dmSpawn(dmLiveHold[0].text, dmLiveHold[0].user)) break
      dmLiveHold.shift()
    }
  } catch (e) { /* 调度器异常不扩散 */ }
}

function dmSyncPause() {
  if (!videoEl.value) return
  if (videoEl.value.paused) {
    // 冻结: 停掉绘制循环, canvas 保留最后一帧不清屏
    if (dmRaf) { cancelAnimationFrame(dmRaf); dmRaf = null }
  } else {
    dmArmDraw()  // 恢复播放 → 绘制循环接着走
  }
}

function dmStart() { if (!dmTimer && dmOn.value) dmTimer = setInterval(dmScheduler, 200) }
function dmStop() { if (dmTimer) { clearInterval(dmTimer); dmTimer = null } }

function dmClear() {
  dmQueue = []
  dmLiveHold = []
  dmActive = []
  laneState.fill(null)
  dmRecentTexts.clear()
  if (dmRaf) { cancelAnimationFrame(dmRaf); dmRaf = null }
  const cvs = dmCanvas.value
  if (cvs) {
    const ctx = cvs.getContext('2d')
    ctx && ctx.clearRect(0, 0, cvs.width, cvs.height)
  }
}

// ---------- 投稿简介导出 (B站转载格式, 复制即用) ----------
const introVisible = ref(false)
const introText = ref('')
async function copyIntro() {
  const res = await fetch(`/api/bili/intro?path=${encodeURIComponent(analysisPath.value)}`)
  const data = await res.json()
  if (!res.ok) { ElMessage.error(`生成失败：${data.error}`); return }
  introText.value = `${data.title}\n\n${data.desc}`
  try {
    await navigator.clipboard.writeText(introText.value)
    ElMessage.success('投稿简介已复制到剪贴板')
  } catch {
    introVisible.value = true  // 剪贴板受限时弹窗手动复制
  }
}

// ---------- 数据轮询 ----------
async function refresh() {
  const [s, a, r] = await Promise.all([
    fetch('/api/status').then(x => x.json()),
    fetch('/api/anchors').then(x => x.json()),
    fetch('/api/recordings').then(x => x.json()),
  ])
  Object.assign(status, s)
  Object.assign(anchors, a)
  Object.assign(recordings, r)
  lastRefresh = Date.now()
  const keep = new Set(expandedKeys)
  await nextTick()
  if (treeRef.value) {
    for (const k of keep) {
      const n = treeRef.value.getNode(k)
      if (n) n.expanded = true
    }
  }
}

function displayDur(r) {
  return r.duration_seconds + Math.floor((nowTs.value - lastRefresh) / 1000)
}

function fmtSize(n) {
  return n > 1 << 30 ? (n / (1 << 30)).toFixed(2) + ' GB'
    : n > 1 << 20 ? (n / (1 << 20)).toFixed(1) + ' MB'
    : (n / 1024).toFixed(0) + ' KB'
}

function fmtDuration(s) {
  const pad = x => String(x).padStart(2, '0')
  return `${pad(Math.floor(s / 3600))}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}`
}

function onSpaceToggle(e) {
  // 全局空格暂停/播放: 输入框/文本域内不劫持(打字时空格照常),
  // 其余页面任意位置(含按钮聚焦时)空格都作用于视频
  if (e.code !== 'Space' || e.repeat) return
  const t = e.target
  const tag = (t && t.tagName) || ''
  if (tag === 'INPUT' || tag === 'TEXTAREA' || (t && t.isContentEditable)) return
  if (!videoEl.value) return
  e.preventDefault()
  if (videoEl.value.paused) videoEl.value.play().catch(() => {})
  else videoEl.value.pause()
}

onMounted(() => {
  refresh()
  nextTick(() => {
    if (videoEl.value) {
      videoEl.value.addEventListener('pause', dmSyncPause)
      videoEl.value.addEventListener('play', dmSyncPause)
    }
  })
  timer = setInterval(() => { nowTs.value = Date.now(); refresh() }, 5000)
  window.addEventListener('resize', onResize)
  window.addEventListener('keydown', onSpaceToggle)
  document.addEventListener('fullscreenchange', onFsChange)
  playerBox.value?.addEventListener('mousemove', fsUiWake)
  playerBox.value?.addEventListener('touchstart', fsUiWake)
})

onUnmounted(() => {
  clearInterval(timer)
  if (videoEl.value) {
    videoEl.value.removeEventListener('pause', dmSyncPause)
    videoEl.value.removeEventListener('play', dmSyncPause)
  }
  dmStop()
  dmClear()
  dmDisconnectLive()
  window.removeEventListener('resize', onResize)
  window.removeEventListener('keydown', onSpaceToggle)
  document.removeEventListener('fullscreenchange', onFsChange)
  playerBox.value?.removeEventListener('mousemove', fsUiWake)
  playerBox.value?.removeEventListener('touchstart', fsUiWake)
  clearTimeout(fsHideTimer)
  if (hls) hls.destroy()
})
</script>

<style>
/* 强调色定调: 直播玫瑰粉, 全局覆盖 Element Plus 主色 */
:root { --el-color-primary: #ef4c7e; }
.el-message { z-index: 9999 !important; }
body { background: #f4f5f7; margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif; }
</style>

<style scoped>
.page { display: grid; grid-template-columns: minmax(700px, 1fr) 380px; grid-template-rows: auto 1fr;
  gap: 14px; height: 100vh; max-width: 1480px; margin: 0 auto; padding: 14px; box-sizing: border-box; }
.topbar { grid-column: 1 / -1; display: flex; align-items: center; justify-content: space-between;
  background: #fff; border: 1px solid #e8eaee; border-radius: 12px; padding: 8px 16px;
  box-shadow: 0 1px 3px rgba(20,20,40,.04); }
.logo { font-weight: 800; font-size: 16px; letter-spacing: .5px; color: #1f2329; }
.topbar-right { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }

.player-wrap { min-height: 0; }
.player-box { width: 100%; max-width: 1080px; background: #000; border-radius: 14px; overflow: hidden;
  box-shadow: 0 8px 28px rgba(20,20,50,.14); }
.player-wrap.big .player-box { max-width: none; }
.page.wide { grid-template-columns: 1fr; grid-template-rows: auto auto auto; height: auto; min-height: 100vh; }
.page.wide .player-wrap { grid-column: 1 / -1; grid-row: 2; }
.page.wide .player-box { max-width: none; }
.page.wide .right-col { grid-column: 1 / -1; grid-row: 3; flex-direction: row; }
.page.wide .right-col > .card { flex: 1; min-width: 0; }
.page.wide .tree-card { max-height: 340px; }
.player-video { position: relative; }
.player-video video { width: 100%; aspect-ratio: 16 / 9; display: block; background: #000; }
.player-hint { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  color: rgba(255,255,255,.55); font-size: 14px; pointer-events: none; }
.player-title { position: absolute; top: 0; left: 0; right: 0; padding: 10px 14px 28px;
  background: linear-gradient(rgba(0,0,0,.62), transparent); color: #fff; font-size: 13px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; pointer-events: none; }
.live-badge { position: absolute; top: 12px; left: 12px; display: flex; align-items: center; gap: 6px;
  background: rgba(0,0,0,.55); color: #fff; font-size: 12px; padding: 3px 10px; border-radius: 999px; }
.live-badge i { width: 7px; height: 7px; border-radius: 50%; background: #ff4d4f; animation: pulse 1.2s infinite; }
@keyframes pulse { 50% { opacity: .35; } }
.player-bar { display: flex; justify-content: space-between; align-items: center; gap: 8px; background: #fff; padding: 7px 12px; }
.player-box:fullscreen { display: flex; flex-direction: column; justify-content: center; background: #000; border-radius: 0; }
/* 全屏影院态: 鼠标静止时全隐藏, 移动时浮出(B站行为) */
.player-box:fullscreen { cursor: none; }
.player-box:fullscreen.show-ui { cursor: default; }
.player-box:fullscreen .player-bar {
  position: absolute; left: 0; right: 0; bottom: 0; z-index: 20;
  background: linear-gradient(transparent, rgba(10,10,14,.92) 40%);
  transform: translateY(102%); opacity: 0;
  transition: transform .28s ease, opacity .28s ease;
}
.player-box:fullscreen.show-ui .player-bar { transform: translateY(0); opacity: 1; }
.fs-title {
  position: absolute; top: 0; left: 0; right: 0; z-index: 20;
  padding: 14px 18px 32px; pointer-events: none;
  background: linear-gradient(rgba(0,0,0,.68), transparent);
  color: #fff; font-size: 15px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap;
  opacity: 0; transition: opacity .28s ease;
}
.player-box:fullscreen.show-ui .fs-title { opacity: 1; }
.player-box:fullscreen .player-video { width: 100%; height: 100%; }
.player-box:fullscreen .player-video video { width: 100%; height: 100%; aspect-ratio: auto; object-fit: contain; }
.player-bar > span:last-child { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
.pb-title { font-size: 12px; color: #606266; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 75%; }

.right-col { display: flex; flex-direction: column; gap: 14px; min-height: 0; }
.rec-card { flex: none; }
.rec-item { display: flex; align-items: center; justify-content: space-between; padding: 9px 8px;
  border-bottom: 1px solid #f2f3f5; cursor: pointer; border-radius: 8px; transition: background .15s; }
.rec-item:hover { background: #f7f8fa; }
.rec-item:last-of-type { border-bottom: none; }
.rec-info { display: flex; align-items: center; gap: 10px; min-width: 0; }
.rec-dot { width: 8px; height: 8px; border-radius: 50%; background: #f56c6c; flex: none;
  box-shadow: 0 0 0 3px rgba(245,108,108,.18); animation: pulse 1.4s infinite; }
.rec-name { font-size: 13px; font-weight: 600; color: #1f2329;
  max-width: 170px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rec-sub { font-size: 12px; color: #8a919f; font-variant-numeric: tabular-nums; margin-top: 2px; }
.tree-card { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.file-tree { flex: 1; overflow: auto; }
.tree-node { display: flex; align-items: center; justify-content: space-between; flex: 1; gap: 6px; padding-right: 4px; }
.tree-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
.tree-ops { display: flex; align-items: center; gap: 6px; flex: none; }

.row { display: flex; gap: 10px; }
.preview { display: flex; align-items: center; gap: 10px; margin-top: 12px; background: #f7f8fa;
  border-radius: 8px; padding: 10px 12px; flex-wrap: wrap; }
.pv-name { font-weight: 600; color: #1f2329; }
.pv-title { max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pv-gap { flex: 1; }
.ma-sec { font-size: 13px; font-weight: 600; color: #303133; margin: 18px 0 2px;
  display: flex; justify-content: space-between; align-items: baseline; }
.ma-sec span { font-size: 12px; font-weight: 400; color: #a8adb8; }
.ma-list { max-height: 320px; overflow: auto; }
.ma-item { display: flex; align-items: center; gap: 10px; padding: 9px 4px; border-bottom: 1px solid #f5f6f7; }
.ma-item:last-child { border-bottom: none; }
.ma-info { flex: 1; min-width: 0; }
.ma-name { font-size: 13px; font-weight: 600; color: #1f2329; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.ma-name.off { color: #a8adb8; font-weight: 400; }
.ma-url { font-size: 12px; color: #a8adb8; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 2px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: #dcdfe6; flex: none; }
.dot.on { background: #f56c6c; box-shadow: 0 0 6px #f56c6c; }

/* ---------- 移动端 (≤768px): 单列, 大触控目标, 页签切换 ---------- */
.m-tabs { display: none; }
@media (max-width: 768px) {
  .page { display: flex; flex-direction: column; gap: 10px; height: 100dvh; padding: 10px; }
  .up-body { flex-direction: column; }
  .up-left { width: 100%; }
  .topbar { flex: none; padding: 8px 12px; border-radius: 10px; }
  .logo { font-size: 15px; }
  .player-wrap { flex: none; }
  .player-box { max-width: none; border-radius: 12px; }
  .wide-btn { display: none; }
  .player-bar { padding: 8px 10px; }
  .right-col { flex: 1; min-height: 0; gap: 10px; }
  .m-tabs { display: flex; gap: 10px; flex: none; }
  .m-tabs span { flex: 1; text-align: center; padding: 11px 0; font-size: 14px; color: #606266;
    background: #fff; border: 1px solid #e4e7ed; border-radius: 10px; }
  .m-tabs span.on { color: #fff; background: var(--el-color-primary); border-color: var(--el-color-primary);
    font-weight: 600; box-shadow: 0 2px 8px rgba(239,76,126,.3); }
  .rec-card, .tree-card { flex: 1; min-height: 0; overflow: auto; }
  .rec-item { padding: 14px 10px; }
  .rec-name { max-width: none; font-size: 14px; }
  .ma-item { padding: 12px 4px; flex-wrap: wrap; }
  .el-tree-node__content { height: 40px; }
}

.up-body { display: flex; gap: 18px; align-items: flex-start; }
.up-left { width: 300px; flex: none; display: flex; flex-direction: column; }
.up-right { flex: 1; min-width: 0; }
.up-sec-title { font-size: 13px; font-weight: 600; color: #303133; margin-bottom: 8px; }
.up-tree { border: 1px solid #e8eaee; border-radius: 8px; padding: 6px; margin-top: 8px; max-height: 300px; }
.field-tip { font-size: 12px; color: #a8adb8; line-height: 1.5; margin-top: 4px; }
.p-list { display: flex; flex-direction: column; gap: 6px; width: 100%; }
.p-item { display: flex; align-items: center; gap: 8px; }
.p-name { flex: 1; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cookie-three { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; width: 100%; align-items: center; }
.cookie-three .el-tag { grid-column: 2; justify-self: end; }
.merge-list { display: flex; flex-direction: column; gap: 6px; min-height: 60px; }
.pick-tree { margin-top: 8px; border: 1px solid #e8eaee; border-radius: 8px; padding: 8px; }
.pick-tree-inner { max-height: 240px; overflow: auto; }
.pick-node { display: flex; align-items: center; gap: 6px; flex: 1; }
.pick-node.added { opacity: .55; }
.pick-tip { font-size: 12px; color: #a8adb8; margin-top: 6px; }

.ana-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.ana-path { font-size: 13px; color: #606266; max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ana-progress { padding: 20px 6px; }
.ana-msg { display: block; text-align: center; font-size: 12px; color: #8a919f; margin-top: 10px; }
.ana-sec-title { font-size: 13px; font-weight: 600; color: #303133; margin: 4px 0 10px; }
.ana-list { display: flex; flex-direction: column; gap: 8px; max-height: 52vh; overflow: auto; padding-right: 4px; }
.ana-seg { display: flex; align-items: center; gap: 6px; }
.ana-time { width: 70px; flex: none; }
.ana-time :deep(.el-input__wrapper) { padding: 0 6px; }
.ana-time :deep(.el-input__inner) { font-variant-numeric: tabular-nums; text-align: center; }
.ana-dash { color: #a8adb8; flex: none; }
.ana-nudge { flex: none; padding: 0 1px !important; font-size: 14px; color: #c0c4cc; }
.ana-nudge:hover { color: var(--el-color-primary); }
.ana-seek { flex: none; padding: 0 2px !important; font-size: 12px; color: #909399; }
.ana-seek:hover { color: var(--el-color-primary); }
.ana-type { width: 104px; flex: none; }
.ana-title { flex: 1.2; min-width: 0; }
.ana-summary { flex: 2; min-width: 0; }
.ana-foot { display: flex; align-items: center; gap: 8px; width: 100%; }
.ana-dirty { font-size: 12px; color: #e6a23c; }
.ana-plain :deep(.el-textarea__inner) { font-family: Consolas, "Courier New", monospace; font-size: 13px; line-height: 1.7; resize: none; }
.ana-copy-row { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.ana-song-only { margin-left: 12px; }
.ana-src-tag { flex: none; }
.ana-seg-warn { background: #fdf6ec; border-radius: 6px; }
.ana-ov-row { display: flex; gap: 8px; align-items: flex-start; margin-bottom: 10px; }
.ana-ov-label { font-size: 12px; color: #8a919f; flex: none; width: 56px; padding-top: 6px; }
@media (max-width: 768px) {
  .ana-seg { flex-wrap: wrap; }
  .ana-path { max-width: 180px; }
}

.dm-canvas { position: absolute; left: 0; right: 0; width: 100%; pointer-events: none; z-index: 5; }
/* 透明点击层: 点击画面切换播放/暂停; 底部 70px 保留给原生控制条, 避免双触发 */
.click-layer { position: absolute; top: 0; left: 0; right: 0; bottom: 70px; z-index: 4; }
.dm-set-row { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
.dm-set-label { flex: none; width: 32px; font-size: 12px; color: #606266; }
.dm-set-row :deep(.el-slider) { flex: 1; }
</style>
