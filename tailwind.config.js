/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './src/custos/analytics/dashboard/templates/**/*.html',
  ],
  theme: {
    extend: {
      colors: {
        'bg-base':     '#0B0C0E',
        'bg-surface':  '#141619',
        'bg-elevated': '#1F2125',
        'bg-input':    '#181B1F',
        'border-weak':   '#2C3235',
        'border-medium': '#3D4147',
        'border-strong': '#575B60',
        'text-primary':   '#E6E9EC',
        'text-secondary': '#9DA0A5',
        'text-disabled':  '#5A5D62',
        'accent-primary': '#3274D9',
        'accent-hover':   '#5794F2',
        'status-ok':   '#56A64B',
        'status-warn': '#F2CC0C',
        'status-crit': '#E02F44',
        'status-info': '#3274D9',
        'chart-1': '#73BF69',
        'chart-2': '#F2CC0C',
        'chart-3': '#E02F44',
        'chart-4': '#5794F2',
        'chart-5': '#B877D9',
        'chart-6': '#FF9830',
        'chart-7': '#FADE2A',
        'chart-8': '#37872D',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'Courier New', 'monospace'],
      },
      fontSize: {
        'kpi':   ['2.5rem', { lineHeight: '1', fontWeight: '600' }],
        'kpi-label': ['0.75rem', { lineHeight: '1.2', fontWeight: '500' }],
        'panel-title': ['0.875rem', { lineHeight: '1.3', fontWeight: '600' }],
        'body':  ['0.875rem', { lineHeight: '1.5', fontWeight: '400' }],
        'cell':  ['0.8125rem', { lineHeight: '1.4', fontWeight: '400' }],
        'small': ['0.75rem', { lineHeight: '1.3', fontWeight: '400' }],
      },
      borderRadius: {
        'sm': '2px',
        'md': '4px',
        'lg': '6px',
      },
    },
  },
  plugins: [],
}
