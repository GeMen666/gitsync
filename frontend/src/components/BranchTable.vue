<script setup>
const props = defineProps({
  pair: { type: Object, required: true },
})
const emit = defineEmits(['push', 'pull'])
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
          <td class="mono branch">{{ item.hasLocal ? item.branch : '' }}</td>
          <td class="mono branch">{{ item.hasRemote ? item.branch : '' }}</td>
          <td><span :class="['badge', item.status]">{{ item.status }}</span></td>
          <td class="note"><span class="muted">{{ item.detail }}</span></td>
          <td class="ops">
            <span v-if="item.showActions" class="arb">
              <button v-if="item.hasLocal" class="small" :disabled="pair.isBusy || item.isBusy"
                @click="emit('push', item)">
                <span v-if="item.busy === 'push'" class="spin"></span>推送
              </button>
              <button v-if="item.hasRemote" class="small" :disabled="pair.isBusy || item.isBusy"
                @click="emit('pull', item)">
                <span v-if="item.busy === 'pull'" class="spin"></span>拉取
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
td { height: 40px; padding: 6px 8px 6px 0; border-bottom: 1px solid #f2f2f2; vertical-align: middle; }
td.branch { white-space: nowrap; min-width: 160px; }
td.note { width: 100%; min-width: 240px; }
th.ops, td.ops { width: 150px; text-align: right; padding-right: 0; white-space: nowrap; }
.arb { display: inline-flex; gap: 6px; justify-content: flex-end; }
.mono { font-family: ui-monospace, monospace; }
.badge { font-size: 11px; border-radius: 4px; padding: 1px 6px; background: #e8f0ea; color: #2f6f4e; }
.badge.error, .badge.skipped { background: #fbeaea; color: #b5483a; }
.badge.pending { background: #fff3d6; color: #8a6d1f; }
.muted { color: #999; font-size: 12px; }
.spin {
  display: inline-block; width: 11px; height: 11px; margin-right: 6px;
  border: 2px solid rgba(255, 255, 255, .45); border-top-color: #fff;
  border-radius: 50%; vertical-align: -1px; animation: spin .7s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
</style>
