import { useFeed } from './api.js'
import { useView } from './store.js'
import Shell from './components/Shell.jsx'
import Overview from './components/Overview.jsx'
import Result from './components/Result.jsx'
import History from './components/History.jsx'
import Workflows from './components/Workflows.jsx'
import Connect from './pages/Connect.jsx'
import Recorded from './pages/Recorded.jsx'
import PaidAgent from './pages/PaidAgent.jsx'
import LiveRun from './pages/LiveRun.jsx'
import Pay from './pages/Pay.jsx'

function FeedPage() {
  const { data, error, isLoading } = useFeed()
  const { page, resultId } = useView()
  if (isLoading) return <p className="text-ink-3">Loading measured results…</p>
  if (error)
    return (
      <div className="panel p-5 border-l-2 !border-l-[var(--bad)]">
        <p className="font-medium text-[var(--text-strong)]">The results could not be loaded.</p>
        <p className="text-sm text-ink-2 mt-1">{error.message}. Start the API with <code className="font-mono">synapse0g ui</code>. Nothing is shown in its place.</p>
      </div>
    )
  if (page === 'result') return <Result r={data.featured.find((r) => r.id === resultId)} />
  if (page === 'history') return <History rows={data.history} />
  if (page === 'workflows') return <Workflows workflows={data.workflows || []} />
  if (page === 'connect') return <Connect hero={data.featured.find((r) => r.claim === 'app')} />
  return <Overview featured={data.featured} />
}

export default function App() {
  const page = useView((v) => v.page)
  return (
    <Shell>
      {page === 'recorded' ? <Recorded /> : page === 'agent' ? <PaidAgent /> : page === 'live' ? <LiveRun /> : page === 'pay' ? <Pay /> : <FeedPage />}
    </Shell>
  )
}
