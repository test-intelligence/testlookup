import { RadialBarChart, RadialBar, ResponsiveContainer } from 'recharts'

interface Props { value: number; size?: number }

export default function PassRateGauge({ value, size = 120 }: Props) {
  const color = value >= 95 ? '#10b981' : value >= 80 ? '#f59e0b' : '#ef4444'
  const data = [{ value: 100, fill: '#1e293b' }, { value, fill: color }]

  return (
    <div className="relative flex items-center justify-center" style={{ width: size, height: size }}>
      <ResponsiveContainer width="100%" height="100%">
        <RadialBarChart
          cx="50%" cy="50%"
          innerRadius="65%" outerRadius="100%"
          startAngle={210} endAngle={-30}
          data={data} barSize={10}
        >
          <RadialBar dataKey="value" cornerRadius={5} background={false} />
        </RadialBarChart>
      </ResponsiveContainer>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-xl font-bold text-[var(--color-text)]">{value.toFixed(1)}%</span>
        <span className="text-[10px] text-[var(--color-text-muted)]">Pass Rate</span>
      </div>
    </div>
  )
}
