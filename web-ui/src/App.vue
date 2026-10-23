<template>
  <div class="page" :class="{ wide }">
    <header class="topbar">
      <span class="logo">YuriStream</span>
      <span class="topbar-right">
        <el-tag v-show="!isMobile" effect="plain">监控 {{ status.monitoring }}</el-tag>
        <el-tag v-show="!isMobile" type="danger" effect="dark">录制 {{ status.recording_count }}</el-tag>
        <el-button size="small" @click="manageVisible = true">监控管理</el-button>
      </span>
    </header>

    <div class="player-wrap">
      <div class="player-box">
        <div class="player-video">
          <video ref="videoEl" controls autoplay
                 :src="playPath ? `${API_BASE}/api/play?path=${encodeURIComponent(playPath)}` : undefined"></video>
          <span v-if="playTitle" class="player-title">{{ playTitle }}</span>
          <span v-else class="player-hint">从右侧录像树点播，或点"观看直播"</span>
          <span v-if="liveUrl" class="live-badge"><i></i>LIVE</span>
        </div>
        <div class="player-bar">
          <span class="pb-title">{{ liveUrl ? '实时转播中' : (playPath ? '回放' : '未在播放') }}</span>
          <span>
            <el-button size="small" class="wide-btn" @click="wide = !wide">{{ wide ? '退出宽屏' : '宽屏' }}</el-button>
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
            <div v-if="u.name" class="ma-url">{{ u.url }}</div>
          </div>
          <el-tag v-if="isRecording(u)" type="danger" size="small" effect="light">录制中</el-tag>
          <el-button size="small" text type="primary" @click="rebroadcast(u.url)">转播</el-button>
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
          <el-button size="small" text type="success" @click="enable(u.url)">启用</el-button>
          <el-button size="small" text type="danger" @click="removeAnchor(u.url)">删除</el-button>
        </div>
        <el-empty v-if="!anchors.active?.length && !anchors.paused?.length" description="暂无监控" :image-size="50" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { nextTick, onMounted, onUnmounted, reactive, ref, watch, computed } from 'vue'
import Hls from 'hls.js'
import { ElMessage } from 'element-plus'
import { CaretRight, Download } from '@element-plus/icons-vue'

const status = reactive({ monitoring: 0, recording_count: 0, recording: [] })
const anchors = reactive({ active: [], paused: [] })
const recordings = reactive({ count: 0, files: [] })
const newUrl = ref('')
const playPath = ref('')
const liveUrl = ref('')
const liveName = ref('')
const videoEl = ref(null)
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
      children: files.map(f => ({ key: 'f:' + f.path, label: f.label, full: f.full, file: f })),
    })),
  }))
})

function shortLabel(fname, size) {
  const m = fname.match(/(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})/)
  const t = m ? `${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}` : fname
  return `${t} · ${fmtSize(size)}`
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

watch(playPath, p => {
  if (p && hls) { hls.destroy(); hls = null; liveUrl.value = '' }
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
})

function play(p) { playPath.value = p }

function closePlayer() {
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

onMounted(() => {
  refresh()
  timer = setInterval(() => { nowTs.value = Date.now(); refresh() }, 5000)
  window.addEventListener('resize', onResize)
})

onUnmounted(() => {
  clearInterval(timer)
  window.removeEventListener('resize', onResize)
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
.topbar-right { display: flex; align-items: center; gap: 8px; }

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
.player-bar { display: flex; justify-content: space-between; align-items: center; background: #fff; padding: 7px 12px; }
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
</style>
