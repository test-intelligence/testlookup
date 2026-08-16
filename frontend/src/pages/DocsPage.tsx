/**
 * In-app user documentation (backlog B-2).
 *
 * Written against what the code ACTUALLY does. Every number, threshold and
 * weight below was read out of the implementation, and each section names the
 * module it describes so a reader can check the claim:
 *
 *   flaky scoring        services/flaky_score_service.py
 *   GO / NO-GO           services/criticality_service.py, release_council_service.py
 *   analysis routing     services/analysis_router.py
 *   agents               agents/*.py  (stage_name on each)
 *
 * Deliberately also documents what the product does NOT do — "insufficient
 * data" states, advisory-not-authoritative AI output, and the conditions under
 * which a summary is deterministic rather than model-written. Documentation
 * that overstates the product is the same defect class as a field that reports
 * a value nothing produces.
 */
import { useState } from 'react'
import {
  Rocket, Sparkles, Bot, Repeat, GaugeCircle, ChevronRight,
} from 'lucide-react'

import PageHeader from '@/components/ui/PageHeader'

type SectionId = 'start' | 'reports' | 'agents' | 'flaky' | 'gate'

const SECTIONS: { id: SectionId; label: string; icon: typeof Rocket }[] = [
  { id: 'start',   label: 'Start a new project', icon: Rocket },
  { id: 'reports', label: 'Using AI reports',    icon: Sparkles },
  { id: 'agents',  label: 'How the AI agents work', icon: Bot },
  { id: 'flaky',   label: 'How flaky tests are determined', icon: Repeat },
  { id: 'gate',    label: 'How GO / NO-GO is decided', icon: GaugeCircle },
]

function H({ children }: { children: React.ReactNode }) {
  return <h2 className="text-base font-semibold text-[var(--color-text)] mt-1 mb-2">{children}</h2>
}

function P({ children }: { children: React.ReactNode }) {
  return <p className="text-[13.5px] leading-relaxed text-[var(--color-text-muted)] mb-3">{children}</p>
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3 mb-3">
      <div
        className="flex-none w-6 h-6 rounded-full grid place-items-center text-[11px] font-semibold"
        style={{
          background: 'color-mix(in srgb, var(--color-accent) 14%, transparent)',
          color: 'var(--color-accent)',
        }}
      >
        {n}
      </div>
      <div className="min-w-0">
        <div className="text-[13.5px] font-medium text-[var(--color-text)]">{title}</div>
        <div className="text-[13px] leading-relaxed text-[var(--color-text-muted)]">{children}</div>
      </div>
    </div>
  )
}

function Table({ head, rows }: { head: string[]; rows: (string | number)[][] }) {
  return (
    <div className="overflow-x-auto mb-3">
      <table className="w-full text-[13px] border border-[var(--color-border)] rounded-lg overflow-hidden">
        <thead className="bg-[var(--color-bg-secondary)]">
          <tr>
            {head.map(h => (
              <th key={h} className="text-left px-3 py-2 font-medium text-[var(--color-text)]">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-[var(--color-border)]">
              {r.map((c, j) => (
                <td key={j} className="px-3 py-2 text-[var(--color-text-muted)] align-top">{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Something the product deliberately does not claim. */
function Honest({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="rounded-lg border px-3 py-2.5 mb-3 text-[13px] leading-relaxed"
      style={{
        borderColor: 'color-mix(in srgb, var(--status-broken) 35%, transparent)',
        background: 'color-mix(in srgb, var(--status-broken) 8%, transparent)',
        color: 'var(--color-text-muted)',
      }}
    >
      {children}
    </div>
  )
}

export default function DocsPage() {
  const [active, setActive] = useState<SectionId>('start')

  return (
    <>
      <PageHeader
        title="Documentation"
        subtitle="How TestLookup works — the actual rules behind the numbers on every page."
      />

      <div className="grid gap-4" style={{ gridTemplateColumns: 'minmax(0, 220px) minmax(0, 1fr)' }}>
        {/* Nav */}
        <nav className="flex flex-col gap-0.5" aria-label="Documentation sections">
          {SECTIONS.map(s => {
            const Icon = s.icon
            const on = active === s.id
            return (
              <button
                key={s.id}
                type="button"
                onClick={() => setActive(s.id)}
                aria-current={on ? 'page' : undefined}
                className="flex items-center gap-2 px-3 py-2 rounded-lg text-[13px] text-left transition-colors"
                style={{
                  background: on ? 'color-mix(in srgb, var(--color-accent) 12%, transparent)' : 'transparent',
                  color: on ? 'var(--color-accent)' : 'var(--color-text-muted)',
                }}
              >
                <Icon className="h-4 w-4 flex-none" />
                <span className="min-w-0">{s.label}</span>
                {on && <ChevronRight className="h-3.5 w-3.5 ml-auto flex-none" />}
              </button>
            )
          })}
        </nav>

        <div className="card p-5 min-w-0">
          {active === 'start' && (
            <section aria-labelledby="doc-start">
              <h2 id="doc-start" className="text-base font-semibold text-[var(--color-text)] mb-3">
                Setting up a new project
              </h2>
              <Step n={1} title="Create the project">
                <strong>Settings → Projects → New</strong>. The name and slug are yours; the
                slug appears in URLs. Whoever creates it becomes a member automatically.
              </Step>
              <Step n={2} title="Send your first test results">
                Three routes, all equivalent once ingested: upload a report file from
                <strong> Runs → Upload Report</strong>; POST to <code>/api/v1/ingest/file</code>
                from CI; or stream live from a test run via the SDK. Ten report formats are
                understood — JUnit, TestNG, pytest, Allure (including a zipped results
                directory), Cypress, Playwright, Robot, Cucumber, NUnit and TRX.
              </Step>
              <Step n={3} title="Let a baseline build up">
                A single run tells you what failed. Trends, flakiness and release risk all
                need history — see the flakiness section for exactly how much.
              </Step>
              <Step n={4} title="Set who owns failures">
                Failures are auto-assigned at ingest to the suite owner, or to the project's
                default QA-lead when no owner is set. If <strong>My Failures</strong> looks
                empty, you are probably looking at the "Mine" scope while the synthetic
                QA-lead owns the rows — the page opens on <strong>Team</strong> for leads for
                that reason.
              </Step>
              <Step n={5} title="Decide how much AI you want">
                <strong>Settings → AI Configuration</strong> selects the analysis engine.
                Rules-based needs nothing external. ML needs a trained model. LLM needs a
                configured provider — local (Ollama) or hosted.
              </Step>
            </section>
          )}

          {active === 'reports' && (
            <section aria-labelledby="doc-reports">
              <h2 id="doc-reports" className="text-base font-semibold text-[var(--color-text)] mb-3">
                Reading the AI reports
              </h2>
              <P>
                Every analysed run has an intelligence view with four layers. They are
                produced independently, so one can be present while another is not.
              </P>
              <Table
                head={['Layer', 'What it answers']}
                rows={[
                  ['Executive summary', 'What happened in this run, in a few sentences'],
                  ['Incident view', 'What failed, the likely cause, criticality, release impact'],
                  ['Evidence pack', 'Stack traces, log anomalies, similar historical failures, citations'],
                  ['Action plan', 'Immediate mitigation, fixes, validation steps, per-role owner hints'],
                ]}
              />
              <H>Where the analysis comes from</H>
              <P>
                Each failing test is routed to one of three engines. <strong>Rules</strong>
                match error signatures — deterministic and always available.{' '}
                <strong>ML</strong> uses a trained classifier when one exists.{' '}
                <strong>LLM</strong> runs a reasoning agent against the configured model. Auto
                picks the first available of ML → LLM → Rules, and falls back rather than
                failing if a tier is unavailable.
              </P>
              <Honest>
                <strong>AI output is advisory, not authoritative.</strong> Every analysis
                carries a confidence score and provenance showing which engine produced it.
                Results below the configured confidence threshold are marked as needing human
                review rather than acted on automatically. Treat a root cause as a lead to
                confirm, not a verdict.
              </Honest>
              <Honest>
                <strong>A summary is not always model-written.</strong> If the configured LLM
                is unavailable — no model pulled, provider unreachable, rate-limited — the
                summary is rebuilt deterministically from stored pipeline evidence and says so
                in its own text. The run's provenance reports{' '}
                <code>fallback_used: true</code> in that case. Empty is never presented as
                "all clear".
              </Honest>
            </section>
          )}

          {active === 'agents' && (
            <section aria-labelledby="doc-agents">
              <h2 id="doc-agents" className="text-base font-semibold text-[var(--color-text)] mb-3">
                How the AI agents work
              </h2>
              <P>
                Analysis is a pipeline of small agents, each owning one stage and recording
                what it decided and why. Stages run in order; the pipeline can end early when
                there is nothing worth passing on.
              </P>
              <Table
                head={['Stage', 'What the agent does']}
                rows={[
                  ['ingestion', 'Normalises the run and gathers its evidence'],
                  ['anomaly_detection', 'Finds what is unusual about this run versus history'],
                  ['root_cause_analysis', 'Classifies each failure and proposes a cause'],
                  ['failure_clustering', 'Groups failures that share a cause into one finding'],
                  ['summary', 'Writes the four-layer report'],
                  ['triage', 'Assigns and prioritises what a human should look at'],
                ]}
              />
              <P>
                Further specialised agents run where relevant — among them{' '}
                <code>flaky_sentinel</code>, <code>regression_watchman</code>,{' '}
                <code>change_ownership</code>, <code>release_risk</code>,{' '}
                <code>defect_commander</code>, <code>log_intelligence</code>, and a{' '}
                <code>decision_report_critic</code> that reviews the decision report before it
                is shown.
              </P>
              <H>Why you can audit them</H>
              <P>
                Each stage writes a decision-trail entry: the decision point, what was chosen,
                and the rationale. The run's pipeline ribbon shows which stages ran, which
                were skipped, and how long each took. When a stage is skipped the reason is
                recorded — for example "no analyses above the confidence threshold".
              </P>
              <Honest>
                <strong>Prompts are redacted before they leave the process.</strong> Text sent
                to a model passes a sanitiser, and the count of redacted strings is recorded
                with the invocation. When offline mode is on, no hosted provider can be called
                at all — that ceiling is set by the environment and cannot be lifted from this
                UI.
              </Honest>
            </section>
          )}

          {active === 'flaky' && (
            <section aria-labelledby="doc-flaky">
              <h2 id="doc-flaky" className="text-base font-semibold text-[var(--color-text)] mb-3">
                How flaky tests are determined
              </h2>
              <P>
                Flakiness is a score in 0–1 fused from four measured signals. It is not a
                guess about intent — each component is observable, and all four are shown
                alongside the score so you can see which one drove it.
              </P>
              <Table
                head={['Signal', 'Weight', 'What it measures']}
                rows={[
                  ['Result volatility', '0.45', 'Pass/fail flips on unchanged code — the closest thing to direct evidence'],
                  ['Retry rate', '0.25', 'How often the test only passes on a retry'],
                  ['Environment instability', '0.20', 'Whether failures track the environment rather than the code'],
                  ['Duration variance', '0.10', 'Spread in runtime; a slow test is not a flaky test, so this counts least'],
                ]}
              />
              <H>How much history it needs</H>
              <P>
                A test needs at least <strong>5 observations</strong> before any score is
                produced. Confidence is reported with the score and follows the observation
                count directly.
              </P>
              <Table
                head={['Observations', 'Confidence']}
                rows={[
                  ['Fewer than 5', 'No score at all — reported as insufficient'],
                  ['5 – 9', 'Low'],
                  ['10 – 19', 'Medium'],
                  ['20 or more', 'High'],
                ]}
              />
              <Honest>
                <strong>Below five observations you get "insufficient", not zero.</strong> A
                new test is not a stable test, and reporting 0.0 would read as evidence of
                stability that nobody has. The components observed so far are still shown, so
                you can see what little is known.
              </Honest>
              <P>
                The weights used are stored with each score. Changing them later affects new
                scores only — it never silently reinterprets history.
              </P>
            </section>
          )}

          {active === 'gate' && (
            <section aria-labelledby="doc-gate">
              <h2 id="doc-gate" className="text-base font-semibold text-[var(--color-text)] mb-3">
                How the GO / NO-GO decision is made
              </h2>
              <P>
                Two independent things decide the release signal: a weighted{' '}
                <strong>composite risk score</strong> built from seven dimensions, and the run's{' '}
                <strong>pass rate</strong> measured against the bar you configure. Either one
                alone can block a release.
              </P>
              <H>The seven risk dimensions</H>
              <Table
                head={['Dimension', 'Reads as']}
                rows={[
                  ['User impact', 'Product-bug share weighted by open defect pressure'],
                  ['Environment sensitivity', 'How much of the failure is environmental'],
                  ['Reproducibility', 'Whether the failure repeats or wanders'],
                  ['Regression likelihood', 'Whether this looks like something that used to pass'],
                  ['Historical recurrence', 'Whether this has happened before'],
                  ['Blast radius', 'How far across suites the failure reaches'],
                  ['Diagnosis confidence', 'How sure the analysis is — low confidence raises risk'],
                ]}
              />
              <P>
                Each dimension scores 0–100 and the weighted sum is clamped to 0–100. Weights
                are configurable per deployment, and a project's release-gate policy can
                override them.
              </P>
              <H>The decision rules, in order</H>
              <P>
                Order matters: the two NO-GO rules are evaluated first, so nothing below can
                soften a NO-GO.
              </P>
              <Table
                head={['#', 'Condition', 'Result']}
                rows={[
                  ['1', 'Pass rate below 70% of your configured bar', 'NO-GO — catastrophic, whatever the composite says'],
                  ['2', 'Composite risk at or above the NO-GO threshold', 'NO-GO'],
                  ['3', 'Composite risk at or above the GO threshold', 'CONDITIONAL GO'],
                  ['4', 'Pass rate below your configured bar', 'CONDITIONAL GO'],
                  ['5', 'Otherwise', 'GO'],
                ]}
              />
              <P>
                So with a 90% bar, a run at 62% or below is a hard NO-GO; between 63% and 89%
                it is CONDITIONAL GO even when the composite is calm; at or above 90% with low
                risk it is GO.
              </P>
              <H>Pass-rate bands can only make it stricter</H>
              <P>
                If the project has a release-gate policy with pass-rate bands, the band verdict
                is layered over the composite one and the <em>stricter</em> of the two wins.
                Bands can block a release; they can never unblock one. That keeps the release
                gate and the overview page in agreement — when the overview shows red, the gate
                cannot say GO.
              </P>
              <Honest>
                <strong>An override is recorded, not hidden.</strong> A human can override the
                decision; the override, who made it and when are kept with the run.
              </Honest>
            </section>
          )}
        </div>
      </div>
    </>
  )
}
