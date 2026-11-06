import { create } from 'zustand'

// The page lives in the URL hash, so each result has a link: #/result/<id>, #/history
function fromHash() {
  const [, page, id] = (window.location.hash || '').replace(/^#/, '').split('/')
  if (page === 'result' && id) return { page: 'result', resultId: id }
  if (page === 'history') return { page: 'history', resultId: null }
  return { page: 'overview', resultId: null }
}

export const useView = create((set) => {
  window.addEventListener('hashchange', () => set(fromHash()))
  return {
    ...fromHash(),
    open: (resultId) => { window.location.hash = `/result/${resultId}`; window.scrollTo(0, 0) },
    go: (page) => { window.location.hash = page === 'overview' ? '/' : `/${page}`; window.scrollTo(0, 0) },
  }
})
