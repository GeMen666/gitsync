// 前端接口层：所有请求都在这里，组件不直接 fetch。

async function request(path, options = {}) {
  const res = await fetch(path, { cache: 'no-store', ...options })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
  return data
}

function json(body) {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

export const api = {
  me: () => request('/api/me'),
  login: (username, password) => request('/api/login', json({ username, password })),
  logout: () => request('/api/logout', { method: 'POST' }),

  repos: () => request('/api/repos'),
  pairs: () => request('/api/pairs'),

  addPair: (body) => request('/api/pairs', json(body)),
  updatePair: (id, body) =>
    request(`/api/pairs/${id}`, { ...json(body), method: 'PATCH' }),
  deletePair: (id) => request(`/api/pairs/${id}`, { method: 'DELETE' }),

  compare: (id) => request(`/api/pairs/${id}/compare`, { method: 'POST' }),
  sync: (id) => request(`/api/pairs/${id}/sync`, { method: 'POST' }),
  push: (id, branch) =>
    request(`/api/pairs/${id}/branches/${branch}/push`, { method: 'POST' }),
  pull: (id, branch) =>
    request(`/api/pairs/${id}/branches/${branch}/pull`, { method: 'POST' }),
  resolve: (id, branch, prefer) =>
    request(`/api/pairs/${id}/branches/${branch}/resolve`, json({ prefer })),

}
