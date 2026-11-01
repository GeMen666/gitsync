<script setup>
const props = defineProps({
  pair: { type: Object, required: true },
})
const emit = defineEmits(['push', 'pull', 'resolve'])
</script>

<template>
  <div class="table-wrap">
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
        <tr v-for="item in pair.branches" :key="item.branch">
          <td class="mono branch">
            <template v-if="item.hasLocal">
              <div class="b-name">{{ item.branch }}</div>
              <div class="b-sha">{{ item.localSha.slice(0, 7) }}</div>
            </template>
          </td>
          <td class="mono branch">
            <template v-if="item.hasRemote">
              <div class="b-name">{{ item.branch }}</div>
              <div class="b-sha">{{ item.remoteSha.slice(0, 7) }}</div>
            </template>
          </td>
          <td class="status-cell"><span :class="['badge', item.statusTone]">{{ item.statusLabel }}</span></td>
          <td class="note"><span class="muted">{{ item.detail }}</span></td>
          <td class="ops">
            <span v-if="item.showResolve" class="arb">
              <button class="small" :disabled="pair.isBusy || item.isBusy || !item.hasBackup"
                @click="emit('resolve', item, 'local')">
                使用本地<span v-if="item.busy === 'resolve'" class="spin"></span>
              </button>
              <button class="small" :disabled="pair.isBusy || item.isBusy || !item.hasBackup"
                @click="emit('resolve', item, 'remote')">
                使用远程<span v-if="item.busy === 'resolve'" class="spin"></span>
              </button>
            </span>
            <span v-else-if="item.showActions" class="arb">
              <button v-if="item.hasLocal" class="small" :disabled="pair.isBusy || item.isBusy"
                @click="emit('push', item)">
                推送<span v-if="item.busy === 'push'" class="spin"></span>
              </button>
              <button v-if="item.hasRemote" class="small" :disabled="pair.isBusy || item.isBusy"
                @click="emit('pull', item)">
                拉取<span v-if="item.busy === 'pull'" class="spin"></span>
              </button>
            </span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.table-wrap { overflow-x: auto; margin-top: 16px; }
.status { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; font-weight: 600; font-size: 12px; color: #999; padding: 0 8px 6px 0; border-bottom: 1px solid #eee; }
td { height: 50px; padding: 4px 8px 4px 0; border-bottom: 1px solid #f2f2f2; vertical-align: middle; }
.b-name { font-size: 12.5px; line-height: 1.3; max-width: 180px; overflow: hidden; text-overflow: ellipsis; }
.b-sha { font-size: 10.5px; color: #aaa; line-height: 1.3; }
td.branch { white-space: nowrap; min-width: 110px; max-width: 200px; }
td.status-cell { white-space: nowrap; min-width: 96px; }
.status th:nth-child(3) { white-space: nowrap; }
td.note { width: 100%; min-width: 150px; }
th.ops, td.ops { width: 150px; text-align: right; padding-right: 0; white-space: nowrap; }
.arb { display: inline-flex; gap: 6px; justify-content: flex-end; }
.mono { font-family: ui-monospace, monospace; }
.badge { font-size: 11px; border-radius: 4px; padding: 1px 6px; background: #e8f0ea; color: #2f6f4e; }
.badge.todo { background: #e6f2fb; color: #2b6c9e; }
.badge.warn { background: #fff3d6; color: #8a6d1f; }
.badge.err { background: #fbeaea; color: #b5483a; }
.muted { color: #999; font-size: 12px; }
.spin {
  display: inline-block; width: 11px; height: 11px; margin-left: 6px;
  border: 2px solid rgba(255, 255, 255, .45); border-top-color: #fff;
  border-radius: 50%; vertical-align: -1px; animation: spin .7s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
</style>
