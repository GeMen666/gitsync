<script setup>
const props = defineProps({
  repos: { type: Array, default: () => [] },
  pairMap: { type: Object, default: () => ({}) },
  selected: { type: String, default: '' },
})
const emit = defineEmits(['select'])

function label(repo) {
  const pair = props.pairMap[repo.full_name]
  return pair ? pair.remoteLabel : '未配置同步'
}
</script>

<template>
  <aside class="list">
    <h2>仓库 <span class="count">{{ repos.length }}</span></h2>
    <p v-if="repos.length === 0" class="muted">这个账号下还没有仓库。</p>
    <button
      v-for="repo in repos"
      :key="repo.full_name"
      :class="['item', { active: repo.full_name === selected }]"
      @click="emit('select', repo.full_name)"
    >
      <span class="item-top">
        <span class="item-name">{{ repo.full_name }}</span>
        <span class="item-tags">
          <span v-if="pairMap[repo.full_name]" class="dot on" title="已配对"></span>
          <span v-if="repo.private" class="mini">私有</span>
          <span v-if="repo.empty" class="mini">空仓</span>
        </span>
      </span>
      <span :class="['item-sub', { off: !pairMap[repo.full_name] }]">{{ label(repo) }}</span>
    </button>
  </aside>
</template>

<style scoped>
.list { border-right: 1px solid #eee; padding-right: 16px; }
h2 { margin: 0 0 10px; font-size: 13px; color: #888; font-weight: 600; }
.count { color: #bbb; font-weight: 400; }
.item {
  display: block; width: 100%; text-align: left; padding: 7px 10px; margin-bottom: 4px;
  border: 0; border-radius: 6px; background: none; cursor: pointer; font-size: 13px; color: #333;
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
</style>
