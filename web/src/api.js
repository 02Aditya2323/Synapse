import { useQuery } from '@tanstack/react-query'

export function useFeed() {
  return useQuery({
    queryKey: ['feed'],
    queryFn: async () => {
      const r = await fetch('/api/feed')
      const body = await r.json().catch(() => null)
      if (!r.ok) throw new Error(body?.error || `the API answered ${r.status}`)
      return body
    },
    retry: 1,
    staleTime: 60_000,
  })
}
