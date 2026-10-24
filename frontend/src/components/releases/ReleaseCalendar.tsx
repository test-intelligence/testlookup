/**
 * The release calendar (W11 REL-06): a month grid in the list column, from
 * the release list the page already holds (no request of its own).
 *
 * Each release with a date is a chip on its day, each phase with a planned
 * window a bar across the days it covers in the week row (clipped at the
 * row's edges and continued on the next), three lanes a week row and then
 * "+N more" on the day. A chip or a bar opens the release in the pane, as a
 * card does. Names are React text, truncated in their bar with the full name
 * in its title. Dates are UTC days. Every position is a static class from
 * the model's lookup tables (the production CSP: no inline style from data).
 */
import { clsx } from 'clsx'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useNow } from '@/hooks/useNow'
import type { Release } from '@/types/releases'
import {
  COL_SPAN,
  COL_START,
  itemTone,
  LANE_ROW,
  layoutWeek,
  monthGrid,
  monthLabel,
  monthParam,
  moreLabel,
  shiftMonth,
  WEEKDAYS,
} from './releaseCalendarModel'

export const CALENDAR_LEGEND = 'Dates are shown as UTC days.'

export interface ReleaseCalendarProps {
  releases: readonly Release[]
  /** `YYYY-MM`. */
  month: string
  onMonth: (month: string) => void
  onOpen: (releaseId: string) => void
}

export default function ReleaseCalendar({ releases, month, onMonth, onOpen }: ReleaseCalendarProps) {
  const now = useNow()
  const today = new Date(now)
  const weeks = monthGrid(month, today)

  return (
    <section aria-label={`Release calendar, ${monthLabel(month)}`} data-release-calendar={month} className="card !p-0 overflow-hidden">
      <div className="flex items-center gap-2 border-b border-[var(--color-border)] px-3 py-1.5">
        <button type="button" onClick={() => onMonth(shiftMonth(month, -1))} className="btn-secondary btn-sm inline-flex items-center gap-1">
          <ChevronLeft aria-hidden="true" className="h-3.5 w-3.5" /> Prev
        </button>
        <h2 className="m-0 min-w-0 flex-1 truncate text-center text-sm font-semibold text-[var(--color-text)]">{monthLabel(month)}</h2>
        <button type="button" onClick={() => onMonth(shiftMonth(month, 1))} className="btn-secondary btn-sm inline-flex items-center gap-1">
          Next <ChevronRight aria-hidden="true" className="h-3.5 w-3.5" />
        </button>
        <button type="button" onClick={() => onMonth(monthParam(today))} className="btn-secondary btn-sm">Today</button>
      </div>
      <div aria-hidden="true" className="grid grid-cols-7 border-b border-[var(--color-border)] text-xs text-[var(--color-text-muted)]">
        {WEEKDAYS.map(d => <div key={d} className="px-1.5 py-1">{d}</div>)}
      </div>
      <div>
        {weeks.map(week => {
          const layout = layoutWeek(week, releases)
          return (
            <div
              key={week[0].date}
              data-calendar-week={week[0].date}
              className="grid h-[88px] grid-cols-7 grid-rows-[22px_18px_18px_18px] gap-y-0.5 border-b border-[var(--color-border)] last:border-b-0"
            >
              {week.map((day, i) => {
                const more = moreLabel(layout.hidden[i])
                return (
                  <div
                    key={day.date}
                    data-calendar-day={day.date}
                    className={clsx(
                      'row-start-1 row-span-4 min-w-0 border-[var(--color-border)]',
                      i < 6 && 'border-r',
                      COL_START[i],
                      !day.inMonth && 'bg-[var(--color-bg-secondary)]',
                      day.isToday && 'ring-1 ring-inset ring-[var(--color-ring)]',
                    )}
                  >
                    <div className="flex items-center justify-between gap-1 px-1.5 pt-0.5 text-xs">
                      <span className={clsx('tabular-nums', day.inMonth ? 'text-[var(--color-text-secondary)]' : 'text-[var(--color-text-faint)]')}>
                        {day.day}
                      </span>
                      {more && (
                        <span data-calendar-more="" title={more.title} className="truncate text-[var(--color-text-muted)]">{more.text}</span>
                      )}
                    </div>
                  </div>
                )
              })}
              {layout.items.map(item => (
                <button
                  key={item.key}
                  type="button"
                  data-calendar-item={item.kind}
                  title={item.title}
                  onClick={() => onOpen(item.releaseId)}
                  className={clsx(
                    'z-[1] mx-0.5 min-w-0 truncate rounded-sm px-1 text-left text-xs leading-[18px] hover:brightness-95',
                    COL_START[item.colStart - 1],
                    COL_SPAN[item.span - 1],
                    LANE_ROW[item.lane],
                    item.continuesBefore && 'rounded-l-none',
                    item.continuesAfter && 'rounded-r-none',
                    itemTone(item),
                  )}
                >
                  {item.label}
                </button>
              ))}
            </div>
          )
        })}
      </div>
      <p className="m-0 border-t border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-text-muted)]">{CALENDAR_LEGEND}</p>
    </section>
  )
}
