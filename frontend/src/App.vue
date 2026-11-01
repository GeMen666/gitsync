<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'

const me = ref(null)
const repos = ref([])
const pairs = ref([])
const selected = ref('')
const error = ref('')
const loading = ref(false)
const busy = ref('')
const busyAction = ref('')
const busyBranch = ref('')

const username = ref('')
const password = ref('')

const remoteInputs = reactive({})
const remoteUsers = reactive({})
const remoteTokens = reactive({})
const credUsers = reactive({})
const credTokens = reactive({})

const pairMap = computed(() => {
  const map = {}
  for (const pair of pairs.value) map[pair.local_repo] = pair
  return map
})

const current = computed(
  () => repos.value.find((repo) => repo.full_name === selected.value) || null
)
const currentPair = computed(() =>
  current.value ? pairMap.value[current.value.full_name] : null
)

// 表格只读后端持久化的状态，前端不再拼临时结果
const rows = computed(() => {
  const list = currentPair.value ? currentPair.value.status : []
  return [...list].sort((a, b) => a.branch.localeCompare(b.branch))
})

const lastUpdated = computed(() => {
  const list = currentPair.value ? currentPair.value.status : []
  if (!list.length) return ''
  const latest = list.map((item) => item.updated_at).sort().at(-1)
  return new Date(latest).toLocaleString('zh-CN', { hour12: false })
})

const busyText = computed(() => {
  if (!busy.value) return ''
  switch (busyAction.value) {
    case 'live': return '正在比对…'
    case 'sync': return '正在同步…'
    case 'push': return `正在推送 ${busyBranch.value}…`
    case 'pull': return `正在拉取 ${busyBranch.value}…`
    case 'add': return '正在配对…'
    case 'remove': return '正在取消配对…'
    case 'cred': return '正在保存凭据…'
    default: return '处理中…'
  }
})

function showActions(st) {
  if (st.status === 'synced' || st.status === 'pending') return false
  return !!(st.local_sha || st.remote_sha)
}

function remoteLabel(repo) {
  const pair = pairMap.value[repo.full_name]
  if (!pair) return '未配置同步'
  return pair.remote_url.replace(/^https?:\/\//, '')
}

function selectRepo(name) {
  selected.value = name
  error.value = ''
}

async function json(res) {
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
  return data
}

async function loadMe() {
  const res = await fetch('/api/me', { cache: 'no-store' })
  if (!res.ok) {
    me.value = null
    return
  }
  me.value = await res.json()
  await refresh()
}

async function refresh() {
  const [repoRes, pairRes] = await Promise.all([
    fetch('/api/repos', { cache: 'no-store' }),
    fetch('/api/pairs', { cache: 'no-store' }),
  ])
  if (repoRes.ok) repos.value = await repoRes.json()
  if (pairRes.ok) pairs.value = await pairRes.json()
}

watch(
  repos,
  (list) => {
    if (!list.length) {
      selected.value = ''
      return
    }
    if (!list.some((repo) => repo.full_name === selected.value)) {
      selected.value = list[0].full_name
    }
  },
  { immediate: true }
)

async function login() {
  error.value = ''
  loading.value = true
  try {
    await json(
      await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.value, password: password.value }),
      })
    )
    password.value = ''
    await loadMe()
  } catch (exc) {
    error.value = exc.message
  } finally {
    loading.value = false
  }
}

async function logout() {
  await fetch('/api/logout', { method: 'POST' })
  me.value = null
  repos.value = []
  pairs.value = []
  selected.value = ''
}

async function addPair(repo) {
  const remote = (remoteInputs[repo.full_name] || '').trim()
  if (!remote) {
    error.value = '先填远程地址'
    return
  }
  error.value = ''
  busy.value = repo.full_name
  busyAction.value = 'add'
  try {
    await json(
      await fetch('/api/pairs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          local_repo: repo.full_name,
          remote_url: remote,
          remote_username: (remoteUsers[repo.full_name] || '').trim(),
          remote_token: (remoteTokens[repo.full_name] || '').trim(),
        }),
      })
    )
    delete remoteInputs[repo.full_name]
    delete remoteUsers[repo.full_name]
    delete remoteTokens[repo.full_name]
    await refresh()
  } catch (exc) {
    error.value = exc.message
  } finally {
    busy.value = ''
    busyAction.value = ''
  }
}

async function removePair(pair) {
  const ok = window.confirm(
    `取消配对「${pair.name}」？\n\n只会删除本工具里的同步配置，不会动 Gitea 或 GitHub 上的仓库。`
  )
  if (!ok) return
  busy.value = pair.name
  busyAction.value = 'remove'
  try {
    await json(await fetch(`/api/pairs/${pair.name}`, { method: 'DELETE' }))
    await refresh()
  } catch (exc) {
    error.value = exc.message
  } finally {
    busy.value = ''
    busyAction.value = ''
  }
}

async function patch(pair, body, action = 'patch') {
  busy.value = pair.name
  busyAction.value = action
  try {
    await json(
      await fetch(`/api/pairs/${pair.name}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
    )
    await refresh()
  } finally {
    busy.value = ''
    busyAction.value = ''
  }
}

async function saveCred(pair) {
  const token = (credTokens[pair.name] || '').trim()
  if (!token) return
  error.value = ''
  try {
    await patch(
      pair,
      {
        remote_username: (credUsers[pair.name] || '').trim(),
        remote_token: token,
      },
      'cred'
    )
    delete credTokens[pair.name]
    delete credUsers[pair.name]
  } catch (exc) {
    error.value = exc.message
  }
}

async function run(pair, mode) {
  const target = pair.name
  busy.value = target
  busyAction.value = mode
  error.value = ''
  try {
    const url = `/api/pairs/${target}/${mode === 'live' ? 'live' : 'sync'}`
    await json(await fetch(url, { method: mode === 'live' ? 'GET' : 'POST' }))
    await refresh()
  } catch (exc) {
    error.value = exc.message
  } finally {
    busy.value = ''
    busyAction.value = ''
    busyBranch.value = ''
  }
}

async function branchAction(st, action) {
  const pair = currentPair.value
  if (!pair) return
  error.value = ''
  busy.value = pair.name
  busyAction.value = action
  busyBranch.value = st.branch
  try {
    await json(
      await fetch(`/api/branch/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: pair.name, branch: st.branch }),
      })
    )
    await refresh()
  } catch (exc) {
    error.value = exc.message
  } finally {
    busy.value = ''
    busyAction.value = ''
    busyBranch.value = ''
  }
}

onMounted(loadMe)
</script>

<template>
  <div class="page">
    <header class="top">
      <div class="brand">
        <h1>gitsync</h1>
        <p class="sub">本地 Gitea ⇄ 远程仓库的分支级同步</p>
      </div>
      <div v-if="me" class="actions">
        <span class="who">{{ me.login }}</span>
        <button class="link" :disabled="!!busy" @click="refresh">刷新</button>
        <button class="link" :disabled="!!busy" @click="logout">退出</button>
      </div>
    </header>

    <section v-if="!me" class="login">
      <form @submit.prevent="login">
        <input v-model="username" placeholder="Gitea 用户名" autocomplete="username" />
        <input
          v-model="password"
          type="password"
          placeholder="Gitea 密码"
          autocomplete="current-password"
        />
        <button type="submit" :disabled="loading">{{ loading ? '登录中…' : '登录' }}</button>
      </form>
      <p class="hint">用你的 Gitea 账号登录，密码不会保存。</p>
      <p v-if="error" class="error">{{ error }}</p>
    </section>

    <main v-else class="layout">
      <aside class="list">
        <h2>仓库 <span class="count">{{ repos.length }}</span></h2>
        <p v-if="repos.length === 0" class="muted">这个账号下还没有仓库。</p>
        <button
          v-for="repo in repos"
          :key="repo.full_name"
          :class="['item', { active: repo.full_name === selected }]"
          @click="selectRepo(repo.full_name)"
        >
          <span class="item-top">
            <span class="item-name">{{ repo.full_name }}</span>
            <span class="item-tags">
              <span v-if="pairMap[repo.full_name]" class="dot on" title="已配对"></span>
              <span v-if="repo.private" class="mini">私有</span>
              <span v-if="repo.empty" class="mini">空仓</span>
            </span>
          </span>
          <span :class="['item-sub', { off: !pairMap[repo.full_name] }]">{{ remoteLabel(repo) }}</span>
        </button>
      </aside>

      <section class="detail">
        <p v-if="!current" class="muted">从左侧选一个仓库。</p>

        <template v-else>
          <div class="detail-head">
            <h2>{{ current.full_name }}</h2>
            <span v-if="current.private" class="tag">私有</span>
            <span v-if="current.empty" class="tag">空仓</span>
            <span v-if="!current.push" class="tag">只读</span>
          </div>

          <p v-if="error" class="error">{{ error }}</p>

          <template v-if="currentPair">
            <div class="line">
              <span class="label">远程</span>
              <code class="url">{{ currentPair.remote_url }}</code>
            </div>
            <div class="line">
              <span class="label">凭据</span>
              <span v-if="currentPair.has_remote_secret" class="tag ok">已保存</span>
              <span v-else class="tag warn">未保存</span>
            </div>
            <div class="line">
              <button :disabled="!!busy" @click="run(currentPair, 'live')">比对</button>
              <button :disabled="!!busy" @click="run(currentPair, 'sync')">同步</button>
              <button class="danger" :disabled="!!busy" @click="removePair(currentPair)">取消配对</button>
              <span v-if="lastUpdated" class="updated">最后更新 {{ lastUpdated }}</span>
            </div>

            <div v-if="!currentPair.has_remote_secret" class="form">
              <input v-model="credUsers[current.full_name]" placeholder="远程用户名" />
              <input v-model="credTokens[current.full_name]" type="password" placeholder="远程令牌" />
              <button :disabled="!!busy" @click="saveCred(currentPair)">保存凭据</button>
            </div>

            <p v-if="busyText" class="busy-line">{{ busyText }}</p>

            <div v-if="rows.length" class="table-wrap" :class="{ dim: busyAction === 'live' }">
              <table class="status">
                <thead>
                  <tr>
                    <th>本地分支</th>
                    <th>远程分支</th>
                    <th>状态</th>
                    <th>备注</th>
                    <th class="ops">操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="st in rows" :key="st.branch">
                    <td class="mono branch">{{ st.local_sha ? st.branch : '' }}</td>
                    <td class="mono branch">{{ st.remote_sha ? st.branch : '' }}</td>
                    <td><span :class="['badge', st.status]">{{ st.status }}</span></td>
                    <td class="note"><span class="muted">{{ st.detail }}</span></td>
                    <td class="ops">
                      <span v-if="showActions(st)" class="arb">
                        <button v-if="st.local_sha" class="small" :disabled="!!busy"
                          @click="branchAction(st, 'push')">
                          <span v-if="busyBranch === st.branch && busyAction === 'push'" class="spin"></span>推送
                        </button>
                        <button v-if="st.remote_sha" class="small" :disabled="!!busy"
                          @click="branchAction(st, 'pull')">
                          <span v-if="busyBranch === st.branch && busyAction === 'pull'" class="spin"></span>拉取
                        </button>
                      </span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-else class="muted">还没有状态记录，点「比对」生成。</p>
          </template>

          <div v-else class="form">
            <input v-model="remoteInputs[current.full_name]" placeholder="远程地址，例如 https://github.com/you/repo.git" />
            <input v-model="remoteUsers[current.full_name]" placeholder="远程用户名（可选）" />
            <input v-model="remoteTokens[current.full_name]" type="password" placeholder="远程令牌（私有仓库必填）" />
            <button :disabled="!!busy" @click="addPair(current)">配对</button>
          </div>
        </template>
      </section>
    </main>
  </div>
</template>

<style scoped>
.page { max-width: 1000px; margin: 0 auto; padding: 28px 20px 60px; }

.top { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
.brand h1 { margin: 0; font-size: 22px; letter-spacing: .5px; }
.brand .sub { margin: 2px 0 0; color: #999; font-size: 12px; }
.actions { display: flex; align-items: center; gap: 10px; }
.who { color: #666; font-size: 13px; }

.login { max-width: 420px; margin-top: 40px; }
.login form { display: flex; gap: 8px; flex-wrap: wrap; }
.login input { flex: 1; min-width: 150px; }
.hint { color: #999; font-size: 12px; margin-top: 8px; }

.layout { display: grid; grid-template-columns: 260px 1fr; gap: 24px; margin-top: 24px; }

.list { border-right: 1px solid #eee; padding-right: 16px; }
.list h2 { margin: 0 0 10px; font-size: 13px; color: #888; font-weight: 600; }
.count { color: #bbb; font-weight: 400; }
.item {
  display: block; width: 100%; text-align: left; padding: 7px 10px; margin-bottom: 4px;
  border: 0; border-radius: 6px; background: none; cursor: pointer;
  font-size: 13px; color: #333;
}
.item:hover { background: #f2f2f2; }
.item.active { background: #e8f1ec; color: #21543c; font-weight: 600; }
.item-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.item-name { font-family: ui-monospace, monospace; overflow: hidden; text-overflow: ellipsis; }
.item-tags { display: flex; align-items: center; gap: 6px; flex: none; }
.item-sub {
  display: block; margin-top: 1px; font-size: 11px; color: #a6a6a6;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.item-sub.off { color: #c0a06a; }
.item.active .item-sub { color: #5d8a72; }
.mini { font-size: 11px; color: #999; }
.dot { width: 6px; height: 6px; border-radius: 50%; background: #ddd; display: inline-block; }
.dot.on { background: #2f6f4e; }

.detail { min-height: 200px; }
.detail-head { display: flex; align-items: center; gap: 8px; }
.detail-head h2 { margin: 0; font-size: 16px; font-family: ui-monospace, monospace; }

.line { display: flex; align-items: center; gap: 10px; margin-top: 12px; flex-wrap: wrap; }
.label { color: #999; font-size: 12px; width: 34px; flex: none; }
.url { font-size: 12px; background: #f4f4f4; padding: 3px 7px; border-radius: 4px; color: #555; word-break: break-all; }
.updated { color: #aaa; font-size: 12px; margin-left: auto; }

.form { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
.form input { flex: 1; min-width: 190px; }

input:not([type=checkbox]) {
  padding: 7px 10px; border: 1px solid #ddd; border-radius: 6px; font-size: 13px;
}
button {
  padding: 7px 14px; border: 0; border-radius: 6px; background: #2f6f4e; color: #fff;
  font-size: 13px; cursor: pointer;
}
button:disabled { opacity: .5; cursor: default; filter: grayscale(.4); }
.detail .busy, .detail.busy button { opacity: .5; filter: grayscale(.4); }
button.link { background: none; color: #2f6f4e; padding: 0 6px; text-decoration: underline; font-size: 13px; }
button.danger { background: #b5483a; }
.spin {
  display: inline-block; width: 11px; height: 11px; margin-right: 6px;
  border: 2px solid rgba(255, 255, 255, .45); border-top-color: #fff;
  border-radius: 50%; vertical-align: -1px;
  animation: spin .7s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.tag { font-size: 11px; border: 1px solid #ddd; border-radius: 4px; padding: 1px 6px; color: #777; }
.tag.ok { border-color: #2f6f4e; color: #2f6f4e; }
.tag.warn { border-color: #b58b3a; color: #8a6d1f; }

.busy-line {
  margin: 14px 0 0; padding: 6px 10px; border-radius: 6px;
  background: #fff6e0; color: #8a6d1f; font-size: 12px;
}

.table-wrap { overflow-x: auto; margin-top: 16px; }
.table-wrap.dim { opacity: .45; transition: opacity .15s; }
.status { width: 100%; border-collapse: collapse; font-size: 13px; }
.status th {
  text-align: left; font-weight: 600; font-size: 12px; color: #999;
  padding: 0 8px 6px 0; border-bottom: 1px solid #eee;
}
.status td {
  height: 40px; padding: 6px 8px 6px 0;
  border-bottom: 1px solid #f2f2f2; vertical-align: middle;
}
.status td.branch { white-space: nowrap; min-width: 160px; }
.status td.note { width: 100%; min-width: 240px; }
.status th.ops, .status td.ops { width: 150px; text-align: right; padding-right: 0; white-space: nowrap; }
.arb { display: inline-flex; gap: 6px; justify-content: flex-end; }
button.small { padding: 4px 10px; font-size: 12px; line-height: 1.2; }

.mono { font-family: ui-monospace, monospace; }
.badge { font-size: 11px; border-radius: 4px; padding: 1px 6px; background: #e8f0ea; color: #2f6f4e; }
.badge.error, .badge.skipped { background: #fbeaea; color: #b5483a; }
.badge.pending { background: #fff3d6; color: #8a6d1f; }
.muted { color: #999; font-size: 12px; }
.error { color: #c0392b; font-size: 13px; }

@media (max-width: 720px) {
  .layout { grid-template-columns: 1fr; }
  .list { border-right: 0; border-bottom: 1px solid #eee; padding: 0 0 12px; }
}
</style>
