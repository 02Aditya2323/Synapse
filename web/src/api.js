import { useMutation, useQuery } from '@tanstack/react-query'

async function call(url, init) {
  const r = await fetch(url, init)
  const body = await r.json().catch(() => null)
  if (!r.ok) throw new Error(body?.error || `the API answered ${r.status}`)
  return body
}

export const useFeed = () => useQuery({ queryKey: ['feed'], queryFn: () => call('/api/feed'), retry: 1, staleTime: 60_000 })
export const useHealth = () => useQuery({ queryKey: ['health'], queryFn: () => call('/api/health'), retry: 0, refetchInterval: 15_000 })
export const useSamples = () => useQuery({ queryKey: ['samples'], queryFn: () => call('/api/samples'), staleTime: Infinity })
export const useRecorded = () => useQuery({ queryKey: ['observed'], queryFn: () => call('/api/observed'), refetchInterval: 10_000 })
export const useRecordedRun = (id) =>
  useQuery({ queryKey: ['observed', id], queryFn: () => call(`/api/observed/${encodeURIComponent(id)}`), enabled: !!id })
export const useMap = () =>
  useMutation({ mutationFn: (body) => call('/api/map', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) })
