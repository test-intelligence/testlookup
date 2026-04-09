import { useState, useEffect, useCallback } from 'react'
import { Database, Loader2, Plus, RefreshCw, Trash2, AlertTriangle, CheckCircle2, XCircle } from 'lucide-react'
import toast from 'react-hot-toast'
import PageHeader from '@/components/ui/PageHeader'
import { api } from '@/services/api'

type SeedAction = 'load' | 'reset' | 'delete' | null

export default function SeedDataPage() {
  const [seeded, setSeeded] = useState<boolean | null>(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState<SeedAction>(null)
  const [lastOutput, setLastOutput] = useState<string | null>(null)

  const fetchStatus = useCallback(async () => {
    try {
      const res = await api.get('/api/v1/dev/seed/status')
      setSeeded(res.data.seeded)
    } catch {
      setSeeded(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchStatus()
  }, [fetchStatus])

  const handleAction = async (action: SeedAction) => {
    if (!action) return
    setRunning(action)
    setLastOutput(null)
    try {
      let res
      if (action === 'load') {
        res = await api.post('/api/v1/dev/seed')
      } else if (action === 'reset') {
        res = await api.post('/api/v1/dev/seed/reset')
      } else {
        res = await api.delete('/api/v1/dev/seed')
      }
      toast.success(res.data.message)
      setLastOutput(res.data.output || null)
      await fetchStatus()
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(detail ?? 'Operation failed')
    } finally {
      setRunning(null)
    }
  }

  const actionLabel: Record<string, string> = {
    load: 'Loading seed data...',
    reset: 'Resetting seed data...',
    delete: 'Deleting seed data...',
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <PageHeader title="Seed Data" subtitle="Manage demo data for the development environment" />

      {/* Dev environment warning */}
      <div className="flex items-start gap-3 rounded-lg border px-4 py-3"
        style={{ borderColor: 'var(--color-border)', background: 'rgba(217, 119, 6, 0.08)' }}>
        <AlertTriangle className="h-4 w-4 text-amber-400 mt-0.5 flex-shrink-0" />
        <div className="text-sm text-amber-300">
          <p className="font-medium">Development environment only</p>
          <p className="text-amber-400/70 mt-0.5">
            These controls are only available in the dev environment. Seed data includes demo users,
            projects, test runs, AI analysis, defects, and coverage snapshots.
          </p>
        </div>
      </div>

      {/* Status card */}
      <section className="card space-y-4">
        <h2 className="text-sm font-semibold text-[var(--color-text)] uppercase tracking-wider flex items-center gap-2">
          <Database className="h-4 w-4 text-[var(--color-text-muted)]" />
          Seed Data Status
        </h2>

        {loading ? (
          <div className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
            <Loader2 className="h-4 w-4 animate-spin" />
            Checking status...
          </div>
        ) : seeded === null ? (
          <div className="flex items-center gap-2 text-sm text-red-400">
            <XCircle className="h-4 w-4" />
            Unable to check seed status — API may be unavailable
          </div>
        ) : seeded ? (
          <div className="flex items-center gap-2 text-sm text-emerald-400">
            <CheckCircle2 className="h-4 w-4" />
            Seed data is loaded
          </div>
        ) : (
          <div className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
            <XCircle className="h-4 w-4" />
            No seed data present
          </div>
        )}
      </section>

      {/* Actions card */}
      <section className="card space-y-4">
        <h2 className="text-sm font-semibold text-[var(--color-text)] uppercase tracking-wider">
          Actions
        </h2>

        {running && (
          <div className="flex items-center gap-2 text-sm text-amber-300 bg-amber-900/20 border border-amber-700/30 rounded-lg px-4 py-3">
            <Loader2 className="h-4 w-4 animate-spin" />
            {actionLabel[running]}
            <span className="text-amber-400/60 ml-1">This may take a minute.</span>
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {/* Load */}
          <button
            className="btn-primary flex items-center justify-center gap-2 py-2.5"
            onClick={() => handleAction('load')}
            disabled={running !== null}
          >
            {running === 'load'
              ? <Loader2 className="h-4 w-4 animate-spin" />
              : <Plus className="h-4 w-4" />}
            Load Seed Data
          </button>

          {/* Reset */}
          <button
            className="flex items-center justify-center gap-2 py-2.5 px-4 rounded-lg text-sm font-medium transition-colors border"
            style={{
              borderColor: 'var(--color-border)',
              color: 'var(--color-text)',
              background: 'var(--color-bg-secondary)',
            }}
            onClick={() => handleAction('reset')}
            disabled={running !== null}
          >
            {running === 'reset'
              ? <Loader2 className="h-4 w-4 animate-spin" />
              : <RefreshCw className="h-4 w-4" />}
            Reset Seed Data
          </button>

          {/* Delete */}
          <button
            className="flex items-center justify-center gap-2 py-2.5 px-4 rounded-lg text-sm font-medium transition-colors border border-red-700/50 text-red-400 hover:bg-red-900/20"
            onClick={() => handleAction('delete')}
            disabled={running !== null || seeded === false}
          >
            {running === 'delete'
              ? <Loader2 className="h-4 w-4 animate-spin" />
              : <Trash2 className="h-4 w-4" />}
            Delete Seed Data
          </button>
        </div>

        <div className="text-xs text-[var(--color-text-faint)] space-y-1">
          <p><strong>Load</strong> — Idempotent: skips if data already exists.</p>
          <p><strong>Reset</strong> — Wipes and regenerates all seed data from scratch.</p>
          <p><strong>Delete</strong> — Removes all seed data without re-seeding.</p>
        </div>
      </section>

      {/* Output log */}
      {lastOutput && (
        <section className="card space-y-3">
          <h2 className="text-sm font-semibold text-[var(--color-text)] uppercase tracking-wider">
            Output
          </h2>
          <pre className="text-xs text-[var(--color-text-muted)] bg-[var(--color-bg-secondary)] rounded-lg p-4 overflow-x-auto max-h-64 whitespace-pre-wrap">
            {lastOutput}
          </pre>
        </section>
      )}
    </div>
  )
}
