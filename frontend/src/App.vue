<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { api } from './api'
import { Pair } from './models/pair'
import BranchTable from './components/BranchTable.vue'
import LoginPanel from './components/LoginPanel.vue'
import RepoList from './components/RepoList.vue'

const me = ref(null)
const repos = ref([])
const pairs = ref([])          // Pair 对象列表，运行状态挂在对象上
const selected = ref('')
const error = ref('')
const adding = ref(false)

const remoteInputs = reactive({})
const remoteUsers = reactive({})
const remoteTokens = reactive({})
const credUsers = reactive({})
const credTokens = reactive({})

const pairMap = computed(() => {
  const map = {}
  for (const pair of pairs.value) map[pair.localRepo] = pair
  return map
})

const anyBusy = computed(() => pairs.value.some((pair) => pair.isLocked) || adding.value)
const current = computed(
  () => repos.value.find((repo) => repo.full_name === selected.value) || null
)
const currentPair = computed(() =>
  current.value ? pairMap.value[current.value.full_name] : null
)

async function refresh() {
  const [repoList, pairList] = await Promise.all([api.repos(), api.pairs()])
  repos.value = repoList
  // 复用已有对象，保留正在进行的运行状态
  const existing = new Map(pairs.value.map((pair) => [pair.id, pair]))
  pairs.value = pairList.map((data) => {
    const pair = existing.get(data.id)
    if (pair) {
      pair.update(data)
      return pair
    }
    return new Pair(data)
  })
}

async function loadMe() {
  try {
    me.value = await api.me()
  } catch {
    me.value = null
    return
  }
  try {
    await refresh()
  } catch (exc) {
    error.value = exc.message
  }
}

async function onLogin() {
  error.value = ''
  try {
    me.value = await api.me()
  } catch (exc) {
    me.value = null
    error.value = `登录后会话校验失败：${exc.message}`
    return
  }
  try {
    await refresh()
  } catch (exc) {
    error.value = exc.message
  }
}

async function logout() {
  await api.logout().catch(() => {})
  me.value = null
  repos.value = []
  pairs.value = []
  selected.value = ''
}

function select(name) {
  selected.value = name
  error.value = ''
}

async function run(pair, action, fn) {
  pair.begin(action)
  try {
    await fn()
    await refresh()
  } catch (exc) {
    pair.fail(exc.message)
  } finally {
    pair.end()
  }
}

async function addPair(repo) {
  const remote = (remoteInputs[repo.full_name] || '').trim()
  if (!remote) {
    error.value = '先填远程地址'
    return
  }
  error.value = ''
  adding.value = true
  try {
    await api.addPair({
      local_repo: repo.full_name,
      remote_url: remote,
      remote_username: (remoteUsers[repo.full_name] || '').trim(),
      remote_token: (remoteTokens[repo.full_name] || '').trim(),
    })
    delete remoteInputs[repo.full_name]
    delete remoteUsers[repo.full_name]
    delete remoteTokens[repo.full_name]
    await refresh()
  } catch (exc) {
    error.value = exc.message
  } finally {
    adding.value = false
  }
}

function removePair(pair) {
  const ok = window.confirm(
    `取消配对「${pair.name}」？\n\n只会删除本工具里的同步配置，不会动 Gitea 或 GitHub 上的仓库。`
  )
  if (!ok) return
  run(pair, 'remove', () => api.deletePair(pair.id))
}

function saveCred(pair) {
  const token = (credTokens[pair.name] || '').trim()
  if (!token) return
  run(pair, 'cred', async () => {
    await api.updatePair(pair.id, {
      remote_username: (credUsers[pair.name] || '').trim(),
      remote_token: token,
    })
    delete credTokens[pair.name]
    delete credUsers[pair.name]
  })
}

function compare(pair) {
  run(pair, 'compare', () => api.compare(pair.id))
}

function sync(pair) {
  run(pair, 'sync', () => api.sync(pair.id))
}

async function branchAction(pair, item, action) {
  const call = action === 'push' ? api.push : api.pull
  item.begin(action)
  try {
    await call(pair.id, item.branch)
    await refresh()
  } catch (exc) {
    pair.fail(exc.message)
  } finally {
    item.end()
  }
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
        <button class="link" :disabled="anyBusy" @click="refresh">刷新</button>
        <button class="link" :disabled="anyBusy" @click="logout">退出</button>
      </div>
    </header>

    <LoginPanel v-if="!me" @success="onLogin" />

    <main v-else class="layout">
      <RepoList :repos="repos" :pair-map="pairMap" :selected="selected" @select="select" />

      <section class="detail" :class="{ busy: currentPair && currentPair.isBusy }">
        <p v-if="!current" class="muted">从左侧选一个仓库。</p>

        <template v-else>
          <div class="detail-head">
            <h2>{{ current.full_name }}</h2>
            <span v-if="current.private" class="tag">私有</span>
            <span v-if="current.empty" class="tag">空仓</span>
            <span v-if="!current.push" class="tag">只读</span>
          </div>

          <p v-if="error || (currentPair && currentPair.error)" class="error">
            {{ (currentPair && currentPair.error) || error }}
          </p>

          <template v-if="currentPair">
            <div class="line">
              <span class="label">远程</span>
              <code class="url">{{ currentPair.remoteUrl }}</code>
            </div>
            <div class="line">
              <span class="label">凭据</span>
              <span v-if="currentPair.hasRemoteSecret" class="tag ok">已保存</span>
              <span v-else class="tag warn">未保存</span>
            </div>
            <div class="line">
              <button :disabled="currentPair.isLocked" @click="compare(currentPair)">比对</button>
              <button :disabled="currentPair.isLocked" @click="sync(currentPair)">同步</button>
              <button class="danger" :disabled="currentPair.isLocked" @click="removePair(currentPair)">取消配对</button>
              <span v-if="currentPair.lastUpdated" class="updated">
                最后更新 {{ currentPair.lastUpdated }}
              </span>
            </div>

            <div v-if="!currentPair.hasRemoteSecret" class="form">
              <input v-model="credUsers[current.full_name]" placeholder="远程用户名" />
              <input v-model="credTokens[current.full_name]" type="password" placeholder="远程令牌" />
              <button :disabled="currentPair.isLocked" @click="saveCred(currentPair)">保存凭据</button>
            </div>

            <BranchTable
              v-if="currentPair.branches.length"
              :pair="currentPair"
              @push="(item) => branchAction(currentPair, item, 'push')"
              @pull="(item) => branchAction(currentPair, item, 'pull')"
            />
            <p v-else class="muted">还没有状态记录，点「比对」生成。</p>
          </template>

          <div v-else class="form">
            <input v-model="remoteInputs[current.full_name]" placeholder="远程地址，例如 https://github.com/you/repo.git" />
            <input v-model="remoteUsers[current.full_name]" placeholder="远程用户名（可选）" />
            <input v-model="remoteTokens[current.full_name]" type="password" placeholder="远程令牌（私有仓库必填）" />
            <button :disabled="adding" @click="addPair(current)">配对</button>
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

.layout { display: grid; grid-template-columns: 260px 1fr; gap: 24px; margin-top: 24px; }

.detail { min-height: 200px; }
.detail.busy button { opacity: .5; filter: grayscale(.4); }
.detail-head { display: flex; align-items: center; gap: 8px; }
.detail-head h2 { margin: 0; font-size: 16px; font-family: ui-monospace, monospace; }

.line { display: flex; align-items: center; gap: 10px; margin-top: 12px; flex-wrap: wrap; }
.label { color: #999; font-size: 12px; width: 34px; flex: none; }
.url { font-size: 12px; background: #f4f4f4; padding: 3px 7px; border-radius: 4px; color: #555; word-break: break-all; }
.updated { color: #aaa; font-size: 12px; margin-left: auto; }

.form { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
.form input { flex: 1; min-width: 190px; }

.tag { font-size: 11px; border: 1px solid #ddd; border-radius: 4px; padding: 1px 6px; color: #777; }
.tag.ok { border-color: #2f6f4e; color: #2f6f4e; }
.tag.warn { border-color: #b58b3a; color: #8a6d1f; }

.muted { color: #999; font-size: 12px; }
.error { color: #c0392b; font-size: 13px; }

@media (max-width: 720px) {
  .layout { grid-template-columns: 1fr; }
}
</style>
