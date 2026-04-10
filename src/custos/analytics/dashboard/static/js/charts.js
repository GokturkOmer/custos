/**
 * Custos uPlot grafik factory — Alpine.js component.
 *
 * Etkileşim:
 *   - Sürükle (sol tuş)  → yatay zoom
 *   - Scroll (tekerlek)   → zoom in/out
 *   - Shift + sürükle     → pan
 *   - Çift tıkla           → zoom sıfırla
 */

const CHART_COLORS = [
  '#73BF69', '#F2CC0C', '#E02F44', '#5794F2',
  '#B877D9', '#FF9830', '#FADE2A', '#37872D'
];

const DPR = window.devicePixelRatio || 1;

const DARK_THEME_AXES = [
  {
    stroke: '#9DA0A5',
    font: '11px Inter, system-ui, sans-serif',
    grid: { stroke: '#2C3235', width: 1 },
    ticks: { stroke: '#3D4147', width: 1 },
    gap: 8,
  },
  {
    stroke: '#9DA0A5',
    font: '11px Inter, system-ui, sans-serif',
    grid: { stroke: '#2C3235', width: 1 },
    ticks: { stroke: '#3D4147', width: 1 },
    gap: 5,
    size: 55,
  }
];

window.custos = window.custos || {};
window.custos.charts = window.custos.charts || {};

/**
 * Tüm serilerden Y min/max hesapla, %15 padding ekle.
 * Sonuç sabit — render döngüsüne girmez.
 */
function calcYRange(seriesArrays) {
  let min = Infinity;
  let max = -Infinity;
  for (const arr of seriesArrays) {
    for (const v of arr) {
      if (v != null) {
        if (v < min) min = v;
        if (v > max) max = v;
      }
    }
  }
  if (min === Infinity) return [0, 1];
  const range = max - min;
  const pad = range > 0 ? range * 0.15 : Math.abs(min) * 0.15 || 1;
  return [min - pad, max + pad];
}

function wheelZoomPlugin() {
  const factor = 0.75;
  return {
    hooks: {
      ready(u) {
        u.over.addEventListener('wheel', (e) => {
          e.preventDefault();
          const { left } = u.cursor;
          if (left == null || left < 0) return;
          const xMin = u.scales.x.min;
          const xMax = u.scales.x.max;
          if (xMin == null || xMax == null) return;

          const range = xMax - xMin;
          const cursorX = u.posToVal(left, 'x');
          const zoomIn = e.deltaY < 0;
          const newRange = zoomIn ? range * factor : range / factor;
          const ratio = (cursorX - xMin) / range;

          u.setScale('x', {
            min: cursorX - newRange * ratio,
            max: cursorX + newRange * (1 - ratio),
          });
        });
      }
    }
  };
}

function dblClickResetPlugin(fullData) {
  return {
    hooks: {
      ready(u) {
        u.over.addEventListener('dblclick', () => {
          const ts = fullData[0];
          u.setScale('x', { min: ts[0], max: ts[ts.length - 1] });
        });
      }
    }
  };
}

function chartPanel(chartId) {
  return {
    chart: null,

    init() {
      const el = document.getElementById(chartId);
      if (!el) return;

      const chartData = window.custos.chartData?.[chartId];
      if (!chartData) return;

      const { timestamps, series, labels } = chartData;

      const uplotSeries = [{}];
      labels.forEach((label, i) => {
        uplotSeries.push({
          label: label,
          stroke: CHART_COLORS[i % CHART_COLORS.length],
          width: 1.5 * DPR,
          points: { show: false },
        });
      });

      const uplotData = [timestamps, ...series];

      // Y range'i bir kez hesapla — sabit, döngüye girmez
      const yRange = calcYRange(series);

      // Yüksekliği data attribute'dan al — container height'a bağımlılık yok
      const chartHeight = parseInt(el.dataset.chartHeight) || 200;

      const opts = {
        width: el.clientWidth,
        height: chartHeight,
        pxAlign: false,
        series: uplotSeries,
        axes: DARK_THEME_AXES,
        cursor: {
          drag: { x: true, y: false, setScale: true },
          points: {
            size: 6 * DPR,
            stroke: '#E6E9EC',
            fill: '#141619',
            width: 1.5 * DPR,
          },
          bind: {
            mousedown: (u, targ, handler) => (e) => {
              if (e.shiftKey) {
                u._panStart = { x: e.clientX, min: u.scales.x.min, max: u.scales.x.max };
                return null;
              }
              return handler(e);
            },
            mousemove: (u, targ, handler) => (e) => {
              if (u._panStart) {
                const dx = e.clientX - u._panStart.x;
                const pxRange = u.bbox.width / DPR;
                const xRange = u._panStart.max - u._panStart.min;
                const shift = (dx / pxRange) * xRange * -1;
                u.setScale('x', {
                  min: u._panStart.min + shift,
                  max: u._panStart.max + shift,
                });
                return null;
              }
              return handler(e);
            },
            mouseup: (u, targ, handler) => (e) => {
              if (u._panStart) {
                u._panStart = null;
                return null;
              }
              return handler(e);
            },
          },
        },
        select: {
          fill: 'rgba(50, 116, 217, 0.15)',
          stroke: 'rgba(50, 116, 217, 0.5)',
        },
        scales: {
          x: { time: true },
          y: { range: () => yRange },
        },
        legend: { show: true },
        plugins: [
          wheelZoomPlugin(),
          dblClickResetPlugin(uplotData),
        ],
      };

      this.chart = new uPlot(opts, uplotData, el);
      window.custos.charts[chartId] = this.chart;

      // Debounced resize
      let resizeTimer = null;
      const ro = new ResizeObserver(() => {
        if (resizeTimer) clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => {
          if (this.chart) {
            this.chart.setSize({ width: el.clientWidth, height: chartHeight });
          }
        }, 200);
      });
      ro.observe(el);
    }
  };
}
