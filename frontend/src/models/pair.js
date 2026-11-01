// 仓库对象：一个 Pair 承载自己的分支、选中状态和运行状态。
// 所有和“某个仓库”相关的显示都由它派生，页面不再维护全局 busy。

const STATUS = {
  need_pull: { label: '需拉取', tone: 'todo' },
  need_push: { label: '需推送', tone: 'todo' },
  synced: { label: '已同步', tone: 'ok' },
  pending: { label: '有冲突（已选最新）', tone: 'warn' },
  error: { label: '出错了', tone: 'err' },
}

export class Branch {
  constructor(data = {}) {
    this.busy = ''      // '' | 'push' | 'pull'
    this.error = ''
    this.update(data)
  }

  update(data = {}) {
    this.branch = data.branch || ''
    this.localSha = data.local_sha || ''
    this.remoteSha = data.remote_sha || ''
    this.status = data.status || ''
    this.detail = data.detail || ''
    this.backupBranch = data.backup_branch || ''
    this.backupSide = data.backup_side || ''
    this.updatedAt = data.updated_at || ''
  }

  get statusLabel() { return (STATUS[this.status] || {}).label || this.status }
  get statusTone() { return (STATUS[this.status] || {}).tone || '' }
  get hasLocal() { return !!this.localSha }
  get hasRemote() { return !!this.remoteSha }
  get isBusy() { return !!this.busy }
  get showActions() {
    if (!['need_push', 'need_pull', 'error'].includes(this.status)) return false
    return this.hasLocal || this.hasRemote
  }

  begin(action) {
    this.busy = action
    this.error = ''
  }

  end() {
    this.busy = ''
  }
}

export class Pair {
  constructor(data) {
    this.busy = ''        // '' | 'compare' | 'sync' | 'cred' | 'remove'
    this.error = ''
    this.branches = []    // 先初始化，update 里会按键复用
    this.update(data)
  }

  update(data) {
    this.id = data.id
    this.name = data.name
    this.owner = data.owner || ''
    this.localRepo = data.local_repo
    this.remoteUrl = data.remote_url
    this.remoteUsername = data.remote_username || ''
    this.hasRemoteSecret = !!data.has_remote_secret
    this.retentionDays = data.retention_days
    this.lastUpdatedRaw = data.last_updated || ''
    // 按分支名复用对象，保住进行中的分支状态
    const existing = new Map(this.branches.map((item) => [item.branch, item]))
    this.branches = (data.branches || []).map((item) => {
      const branch = existing.get(item.branch)
      if (branch) {
        branch.update(item)
        return branch
      }
      return new Branch(item)
    })
  }

  get isBusy() { return !!this.busy }
  get hasBranchBusy() { return this.branches.some((item) => item.isBusy) }
  get isLocked() { return this.isBusy || this.hasBranchBusy }
  get remoteLabel() { return this.remoteUrl.replace(/^https?:\/\//, '') }
  get lastUpdated() {
    return this.lastUpdatedRaw
      ? new Date(this.lastUpdatedRaw).toLocaleString('zh-CN', { hour12: false })
      : ''
  }
  get comparing() { return this.busy === 'compare' }

  begin(action) {
    this.busy = action
    this.error = ''
  }

  end() {
    this.busy = ''
  }

  fail(message) {
    this.error = message
  }
}
