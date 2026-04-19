/**
 * Custos uPlot grafik factory — Alpine.js component.
 *
 * Multi-axis: Her tag'in birimi (units[i]) bir scale key'e çevrilir.
 * İlk 2 benzersiz birim görünür Y ekseni alır (sol + sağ); 3. ve sonrası
 * görünmez scale'a bağlanır (tooltip'te okunur, eksen çizilmez).
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

const BASE_AXIS = {
  stroke: '#9DA0A5',
  font: '11px Inter, system-ui, sans-serif',
  grid: { stroke: '#2C3235', width: 1 },
  ticks: { stroke: '#3D4147', width: 1 },
};

window.custos = window.custos || {};
window.custos.charts = window.custos.charts || {};

/**
 * Birim string'ini uPlot scale key'ine çevirir. Boş birim → "default".
 */
function unitToScale(unit) {
  if (!unit) return 'default';
  return `u_${unit.replace(/[^a-zA-Z0-9]/g, '_')}`;
}

/**
 * Serileri birimlerine göre scale gruplarına ayırır.
 * İlk 2 birim görünür axis alır (sol + sağ), gerisi invisible.
 */
function buildScaleLayout(units) {
  const uniqueUnits = [];
  for (const u of units) {
    const key = unitToScale(u);
    if (!uniqueUnits.find((x) => x.key === key)) {
      uniqueUnits.push({ key, label: u || '' });
    }
  }
  return uniqueUnits.map((u, idx) => ({
    key: u.key,
    label: u.label,
    visible: idx < 2,
    side: idx === 0 ? 3 : (idx === 1 ? 1 : null),  // uPlot: 3=left, 1=right
  }));
}

/**
 * Verilen scale için Y range hesaplar (sadece o scale'deki seriler).
 */
function calcYRangeForScale(seriesArrays, units, scaleKey) {
  let min = Infinity;
  let max = -Infinity;
  for (let i = 0; i < seriesArrays.length; i++) {
    if (unitToScale(units[i]) !== scaleKey) continue;
    for (const v of seriesArrays[i]) {
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

      const { timestamps, series, labels, units = [] } = chartData;
      if (!timestamps || timestamps.length === 0) return;

      // Her serinin unit bilgisini dizi olarak elde et (eski data için fallback)
      const effectiveUnits = labels.map((_, i) => units[i] || '');
      const layout = buildScaleLayout(effectiveUnits);

      // uPlot series: ilki x ekseni
      const uplotSeries = [{}];
      labels.forEach((label, i) => {
        uplotSeries.push({
          label: label,
          stroke: CHART_COLORS[i % CHART_COLORS.length],
          width: 1,
          points: { show: false },
          scale: unitToScale(effectiveUnits[i]),
        });
      });

      const uplotData = [timestamps, ...series];

      // Scales: her benzersiz birim için ayrı Y scale
      const scales = { x: { time: true } };
      for (const l of layout) {
        scales[l.key] = {
          range: () => calcYRangeForScale(series, effectiveUnits, l.key),
        };
      }

      // Axes: x ekseni + görünür Y eksenleri
      const axes = [{ ...BASE_AXIS, gap: 8 }];
      for (const l of layout) {
        if (!l.visible) continue;
        axes.push({
          ...BASE_AXIS,
          scale: l.key,
          side: l.side,
          gap: 5,
          size: 55,
          label: l.label || undefined,
          labelSize: l.label ? 18 : 0,
        });
      }

      const chartHeight = parseInt(el.dataset.chartHeight) || 200;

      const opts = {
        width: el.clientWidth,
        height: chartHeight,
        pxAlign: false,
        series: uplotSeries,
        axes: axes,
        scales: scales,
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
        legend: { show: true },
        plugins: [
          wheelZoomPlugin(),
          dblClickResetPlugin(uplotData),
        ],
      };

      this.chart = new uPlot(opts, uplotData, el);
      window.custos.charts[chartId] = this.chart;

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
