import { useFeed } from './api.js'
import { useView } from './store.js'
import Overview from './components/Overview.jsx'
import Result from './components/Result.jsx'
import History from './components/History.jsx'

function Nav() {
  const { page, go } = useView()
  const tab = (id, label) => (
    <button
      onClick={() => go(id)}
      className={`px-3 py-1.5 rounded-md text-sm ${page === id || (id === 'overview' && page === 'result') ? 'bg-surface-2 text-ink' : 'text-ink-3 hover:text-ink-2'}`}
    >
      {label}
    </button>
  )
  return (
    <header className="border-b border-line bg-surface-1">
      <div className="max-w-7xl mx-auto px-6 h-14 flex items-center gap-6">
        <button onClick={() => go('overview')} className="font-semibold tracking-tight">
          synapse<span className="text-after">0g</span>
        </button>
        <nav className="flex gap-1">
          {tab('overview', 'Results')}
          {tab('history', 'Run history')}
        </nav>
        <span className="ml-auto text-xs text-ink-3">Every number on this page was measured on real provider calls.</span>
      </div>
    </header>
  )
}

export default function App() {
  const { data, error, isLoading } = useFeed()
  const { page, resultId } = useView()
  let body
  if (isLoading) body = <p className="text-ink-3">Loading measured results…</p>
  else if (error)
    body = (
      <div className="rounded-lg border border-critical/50 bg-critical/10 p-5">
        <p className="font-medium">The results could not be loaded.</p>
        <p className="text-sm text-ink-2 mt-1">{error.message}. Start the API with <code className="font-mono">synapse0g ui</code>. Nothing is shown in its place.</p>
      </div>
    )
  else if (page === 'result') body = <Result r={data.featured.find((r) => r.id === resultId)} />
  else if (page === 'history') body = <History rows={data.history} />
  else body = <Overview featured={data.featured} />
  return (
    <div className="min-h-full">
      <Nav />
      <main className="max-w-7xl mx-auto px-6 py-8">{body}</main>
    </div>
  )
}
