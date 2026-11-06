import { create } from 'zustand'

// The page lives in the URL hash, so every screen has a link:
// #/agent  #/result/<id>  #/connect  #/recorded/<run_id>  #/workflows/<name>  #/history
const PAGES = new Set(['result', 'connect', 'recorded', 'workflows', 'history', 'agent', 'live', 'pay'])

function fromHash() {
  const [, page, id] = (window.location.hash || '').replace(/^#/, '').split('/')
  if (PAGES.has(page)) return { page, resultId: id ? decodeURIComponent(id) : null }
  return { page: 'overview', resultId: null }
}

export const useView = create((set) => {
  window.addEventListener('hashchange', () => set(fromHash()))
  return {
    ...fromHash(),
    open: (resultId) => { window.location.hash = `/result/${resultId}`; window.scrollTo(0, 0) },
    go: (page, id) => { window.location.hash = page === 'overview' ? '/' : `/${page}${id ? `/${encodeURIComponent(id)}` : ''}`; window.scrollTo(0, 0) },
  }
})
