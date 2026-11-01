<script setup>
import { ref } from 'vue'
import { api } from '../api'

const emit = defineEmits(['success'])

const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')

async function submit() {
  error.value = ''
  loading.value = true
  try {
    await api.login(username.value, password.value)
    password.value = ''
    emit('success')
  } catch (exc) {
    error.value = exc.message
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <section class="login">
    <form @submit.prevent="submit">
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
</template>

<style scoped>
.login { max-width: 420px; margin-top: 40px; }
form { display: flex; gap: 8px; flex-wrap: wrap; }
input { flex: 1; min-width: 150px; }
.hint { color: #999; font-size: 12px; margin-top: 8px; }
.error { color: #c0392b; font-size: 13px; }
</style>
