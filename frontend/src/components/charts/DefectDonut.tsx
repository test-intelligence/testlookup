import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from 'recharts'

const COLORS = ['var(--status-failed)', 'var(--status-broken)', 'var(--color-accent)', '#6b7280']
const LABELS = ['P1 Critical', 'P2 High', 'P3 Medium', 'P4 Low']

interface Props {
  data: number[]   // [p1, p2, p3, p4]
  /** Forwarded to Recharts' `isAnimationActive`; `undefined` keeps Recharts' default. */
  animate?: boolean
}

export default function DefectDonut({ data, animate }: Props) {
  const chartData = LABELS.map((name, i) => ({ name, value: data[i] ?? 0 })).filter(d => d.value > 0)
  if (!chartData.length) return <p className="text-[var(--color-text-muted)] text-sm text-center py-8">No defect data</p>

  return (
    <ResponsiveContainer width="100%" height={200}>
      <PieChart>
        <Pie data={chartData} cx="50%" cy="50%" innerRadius={50} outerRadius={80} paddingAngle={3} dataKey="value" isAnimationActive={animate}>
          {chartData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
        </Pie>
        <Tooltip
          contentStyle={{ backgroundColor: '#1e293b', border: '1px solid #334155', borderRadius: '8px', fontSize: '12px' }}
          formatter={(val, name) => [val, name]}
        />
        <Legend iconType="circle" wrapperStyle={{ fontSize: 11 }} />
      </PieChart>
    </ResponsiveContainer>
  )
}
