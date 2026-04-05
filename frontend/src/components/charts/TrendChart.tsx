import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'

interface DataPoint {
  date: string
  passed: number
  failed: number
  skipped: number
  broken?: number
  total: number
  pass_rate: number
}

interface Props {
  data: DataPoint[]
  type?: 'line' | 'area' | 'bar'
  height?: number
}

const TOOLTIP_STYLE = {
  backgroundColor: '#1e293b', border: '1px solid #334155',
  borderRadius: '8px', color: '#e2e8f0', fontSize: '12px',
}

const AXIS_TICK = { fill: '#64748b', fontSize: 11 }

export default function TrendChart({ data, type = 'line', height = 280 }: Props) {
  const common = {
    data,
    margin: { top: 4, right: 4, left: -16, bottom: 0 },
  }

  if (type === 'area') {
    return (
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart {...common}>
          <defs>
            <linearGradient id="totalGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
          <XAxis dataKey="date" axisLine={false} tickLine={false} tick={AXIS_TICK} dy={8} />
          <YAxis axisLine={false} tickLine={false} tick={AXIS_TICK} />
          <Tooltip contentStyle={TOOLTIP_STYLE} />
          <Area type="monotone" dataKey="total" stroke="#3b82f6" fill="url(#totalGrad)" strokeWidth={2} name="Total Tests" />
          <Area type="monotone" dataKey="passed" stroke="#10b981" fill="transparent" strokeWidth={1.5} name="Passed" />
        </AreaChart>
      </ResponsiveContainer>
    )
  }

  if (type === 'bar') {
    return (
      <ResponsiveContainer width="100%" height={height}>
        <BarChart {...common} barSize={18}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
          <XAxis dataKey="date" axisLine={false} tickLine={false} tick={AXIS_TICK} dy={8} />
          <YAxis axisLine={false} tickLine={false} tick={AXIS_TICK} />
          <Tooltip contentStyle={TOOLTIP_STYLE} />
          <Legend iconType="circle" wrapperStyle={{ paddingTop: 12, fontSize: 12 }} />
          <Bar dataKey="passed"  stackId="a" fill="#10b981" name="Passed"  radius={[0, 0, 0, 0]} />
          <Bar dataKey="failed"  stackId="a" fill="#ef4444" name="Failed"  />
          <Bar dataKey="skipped" stackId="a" fill="#f59e0b" name="Skipped" />
          <Bar dataKey="broken"  stackId="a" fill="#f97316" name="Broken"  radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    )
  }

  // Default: line chart
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart {...common}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
        <XAxis dataKey="date" axisLine={false} tickLine={false} tick={AXIS_TICK} dy={8} />
        <YAxis axisLine={false} tickLine={false} tick={AXIS_TICK} />
        <Tooltip contentStyle={TOOLTIP_STYLE} />
        <Legend iconType="circle" wrapperStyle={{ paddingTop: 16, fontSize: 12 }} />
        <Line type="monotone" dataKey="passed"  stroke="#10b981" strokeWidth={2} dot={false} activeDot={{ r: 4 }} name="Passed" />
        <Line type="monotone" dataKey="failed"  stroke="#ef4444" strokeWidth={2} dot={false} activeDot={{ r: 4 }} name="Failed" />
        <Line type="monotone" dataKey="skipped" stroke="#f59e0b" strokeWidth={1.5} dot={false} activeDot={{ r: 4 }} name="Skipped" strokeDasharray="4 2" />
        <Line type="monotone" dataKey="pass_rate" stroke="#3b82f6" strokeWidth={2} dot={false} activeDot={{ r: 4 }} name="Pass Rate %" hide />
      </LineChart>
    </ResponsiveContainer>
  )
}
