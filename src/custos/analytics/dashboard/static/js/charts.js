/**
 * Custos uPlot grafik factory — Alpine.js component.
 *
 * Multi-axis: Her tag'in birimi (units[i]) bir scale key'e çevrilir.
 * İlk 2 benzersiz birim görünür Y ekseni alır (sol + sağ); 3. ve sonrası
 * görünmez scale'a bağlanır (tooltip'te okunur, eksen çizilmez).
 *
 * Etkileşim — X ekseni (plot alanında):
 *   - Sürükle (sol tuş)   → yatay zoom (box-select)
 *   - Ctrl + Tekerlek     → yatay zoom in/out (Ctrl olmadan sayfa scroll)
 *   - Shift + sürükle     → yatay pan
 *   - Çift tıkla          → X ekseni zoom sıfırla
 *
 * Etkileşim — Y ekseni (sol/sağ eksen üzerinde):
 *   - Ctrl + Tekerlek     → o ekseni zoom (Y)
 *   - Shift + sürükle     → o ekseni pan (Y)
 *   - Çift tıkla          → o eksenin auto-range'ini geri al
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
          // Ctrl yoksa sayfa scroll akışına karışma (kullanıcı chart
          // üzerinden geçerken yanlışlıkla zoom tetiklenmesin)
          if (!e.ctrlKey) return;
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
        }, { passive: false });
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

/**
 * Per-axis Y pan/zoom — fare görünür bir Y ekseni üzerindeyken:
 *   - Wheel          → o scale'i cursor etrafında zoom
 *   - Shift+sürükle  → o scale'i dikey pan
 *   - Çift tıkla      → o scale'i auto-range'e döndür
 * X ekseni (sürükle, tekerlek, çift tık plot alanında) olduğu gibi korunur.
 */
function perAxisZoomPanPlugin() {
  const zoomFactor = 0.8;
  return {
    hooks: {
      ready(u) {
        const root = u.root;
        // uPlot axis DOM'u: `.u-axis` elementleri. u.axes ile sırayla eşleşir.
        const axisEls = Array.from(root.querySelectorAll('.u-axis'));

        // Her axis element'i için hangi scale'e ait olduğunu bul
        const scaleByEl = new Map();
        axisEls.forEach((el, idx) => {
          const ax = u.axes[idx];
          if (ax && ax.scale && ax.scale !== 'x') {
            scaleByEl.set(el, ax.scale);
            el.style.cursor = 'ns-resize';
          }
        });

        const zoomScale = (scaleKey, cursorVal, zoomIn) => {
          const s = u.scales[scaleKey];
          if (s.min == null || s.max == null) return;
          const range = s.max - s.min;
          const newRange = zoomIn ? range * zoomFactor : range / zoomFactor;
          const ratio = cursorVal == null ? 0.5 : (cursorVal - s.min) / range;
          u.setScale(scaleKey, {
            min: cursorVal - newRange * ratio,
            max: cursorVal + newRange * (1 - ratio),
          });
        };

        // --- Wheel zoom on axis (Ctrl+Wheel) ---
        for (const [el, scaleKey] of scaleByEl) {
          el.addEventListener('wheel', (e) => {
            // Ctrl olmadan wheel sayfa scroll'una dokunmaz
            if (!e.ctrlKey) return;
            e.preventDefault();
            e.stopPropagation();
            const s = u.scales[scaleKey];
            if (s.min == null || s.max == null) return;
            const plotRect = u.over.getBoundingClientRect();
            const relY = e.clientY - plotRect.top;
            const clampedY = Math.max(0, Math.min(plotRect.height, relY));
            const cursorVal = u.posToVal(clampedY, scaleKey);
            zoomScale(scaleKey, cursorVal, e.deltaY < 0);
          }, { passive: false });
        }

        // --- Shift+drag pan on axis ---
        let panState = null;
        for (const [el, scaleKey] of scaleByEl) {
          el.addEventListener('mousedown', (e) => {
            if (!e.shiftKey) return;
            e.preventDefault();
            const s = u.scales[scaleKey];
            if (s.min == null || s.max == null) return;
            panState = {
              scaleKey,
              startY: e.clientY,
              startMin: s.min,
              startMax: s.max,
              plotHeight: u.over.getBoundingClientRect().height,
            };
          });
        }

        const onMove = (e) => {
          if (!panState) return;
          const dy = e.clientY - panState.startY;
          const range = panState.startMax - panState.startMin;
          // Aşağı fare → min artsın (değer düşer görünsün)
          const shift = (dy / panState.plotHeight) * range;
          u.setScale(panState.scaleKey, {
            min: panState.startMin + shift,
            max: panState.startMax + shift,
          });
        };
        const onUp = () => { panState = null; };
        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseup', onUp);

        // --- Double-click auto-range ---
        for (const [el, scaleKey] of scaleByEl) {
          el.addEventListener('dblclick', (e) => {
            e.stopPropagation();
            // uPlot auto-range: scale.auto=true + setScale(null,null) range fonksiyonu yeniden çalışır
            u.setScale(scaleKey, { min: null, max: null });
          });
        }
      }
    }
  };
}

function chartPanel(chartId) {
  return {
    chart: null,

    init() {
      // Guard: aynı component için init() iki kez çağrılırsa ya da
      // container'da eski bir chart DOM'u kaldıysa temizle — böylece
      // aynı yerde iki uPlot overlay'i oluşmaz.
      if (this.chart) return;
      const el = document.getElementById(chartId);
      if (!el) return;
      if (el.firstChild) {
        el.innerHTML = '';
      }

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
          perAxisZoomPanPlugin(),
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
