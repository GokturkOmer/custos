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
 * Tag label'ını (örn. "T101 (°C)") benzersiz scale key'ine çevirir.
 * Per-tag modda her seri kendi scale'ine sahip olur.
 */
function tagLabelToScale(label, index) {
  const id = (label || '').split(' ')[0] || `s${index}`;
  return `tag_${id.replace(/[^a-zA-Z0-9]/g, '_')}_${index}`;
}

/**
 * Per-tag layout: her seri kendi scale ve renkli ekseni alır, hepsi sol
 * tarafta stacked dizilir. Label yazılmaz; eksen rengi serinin rengiyle
 * aynı olduğu için hangi tag'e ait olduğu görsel olarak net.
 */
function buildPerTagLayout(labels, units) {
  return labels.map((label, idx) => ({
    key: tagLabelToScale(label, idx),
    label: '',  // birim yazmıyoruz, renk yeterli
    visible: true,
    side: 3,     // hepsi sol
    perTag: true,
    color: CHART_COLORS[idx % CHART_COLORS.length],
    unit: units[idx] || '',
    seriesIdx: idx,
  }));
}

/**
 * Serileri birimlerine göre scale gruplarına ayırır.
 * visibleLimit: görünür eksen sayısı üst sınırı.
 *   - Overview için 2 önerilir (kompakt, chart alanı geniş kalır)
 *   - Detay sayfası için Infinity (hepsi görünür, yan yana istiflenir)
 * İlk limitLimit eksen görünür (sol + sağ dengeli), gerisi invisible
 * scale'e bağlı (tooltip'te okunur).
 */
function buildScaleLayout(units, visibleLimit) {
  const uniqueUnits = [];
  for (const u of units) {
    const key = unitToScale(u);
    if (!uniqueUnits.find((x) => x.key === key)) {
      uniqueUnits.push({ key, label: u || '' });
    }
  }
  const total = uniqueUnits.length;
  const limit = Math.min(total, visibleLimit);
  const leftCount = Math.ceil(limit / 2);
  return uniqueUnits.map((u, idx) => ({
    key: u.key,
    label: u.label,
    visible: idx < limit,
    side: idx < leftCount ? 3 : 1,
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

/**
 * Tek bir seri için Y range hesaplar (per-tag modda kullanılır).
 */
function calcYRangeForSeries(arr) {
  let min = Infinity;
  let max = -Infinity;
  for (const v of arr) {
    if (v != null) {
      if (v < min) min = v;
      if (v > max) max = v;
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
        // Listener'ı u.root (uPlot'un tüm wrap'i) üzerine koyuyoruz ki
        // axis'in üstünde de Ctrl+Wheel engellensin ve X zoom çalışsın.
        const handler = (e) => {
          if (!e.ctrlKey) return;
          // Axis elementi üzerindeyse per-axis Y zoom plugin'ine bırak
          if (e.target.closest && e.target.closest('.u-axis')) {
            // preventDefault yine gerekli: browser Ctrl+wheel iptal
            // stopImmediatePropagation YOK ki axis listener çalışsın
            e.preventDefault();
            return;
          }
          // Plot alanında: X zoom
          e.preventDefault();
          e.stopImmediatePropagation();
          const { left } = u.cursor;
          const xMin = u.scales.x.min;
          const xMax = u.scales.x.max;
          if (xMin == null || xMax == null) return;

          const range = xMax - xMin;
          const cursorX = (left != null && left >= 0)
            ? u.posToVal(left, 'x')
            : (xMin + xMax) / 2;
          const zoomIn = e.deltaY < 0;
          const newRange = zoomIn ? range * factor : range / factor;
          const ratio = (cursorX - xMin) / range;

          u.setScale('x', {
            min: cursorX - newRange * ratio,
            max: cursorX + newRange * (1 - ratio),
          });
        };
        u.root.addEventListener('wheel', handler, {
          passive: false, capture: true,
        });
      }
    }
  };
}

function dblClickResetPlugin(windowRange) {
  return {
    hooks: {
      ready(u) {
        u.root.addEventListener('dblclick', (e) => {
          // Axis üzerindeki çift tık per-axis auto-range'e ait
          if (e.target.closest && e.target.closest('.u-axis')) return;
          e.stopImmediatePropagation();
          u.setScale('x', { min: windowRange[0], max: windowRange[1] });
        }, { capture: true });
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

/**
 * Plot alanı etkileşimleri — uPlot'un cursor.drag'i tamamen kapalı:
 *   - Sol tık + sürükle (shift yok)  → yatay box-zoom (select)
 *   - Shift + sol tık + sürükle       → yatay pan
 * İki mod da aynı plugin'de, mousedown'da shift kontrolüyle ayrılır.
 */
function xAxisInteractionPlugin() {
  return {
    hooks: {
      ready(u) {
        let mode = null;  // 'pan' | 'box' | null
        let state = null;

        // Box-select için overlay div. u.over zaten position:absolute
        // ile doğru yerde konumlanmış — dokunmuyoruz. Overlay'i u.over'ın
        // içine absolute olarak ekliyoruz; u.over positioned ancestor
        // olduğu için overlay onun sınırlarına göre yerleşir.
        const overlay = document.createElement('div');
        overlay.style.cssText = (
          'position:absolute;top:0;bottom:0;pointer-events:none;' +
          'background:rgba(50,116,217,0.15);' +
          'border-left:1px solid rgba(50,116,217,0.5);' +
          'border-right:1px solid rgba(50,116,217,0.5);' +
          'display:none;'
        );
        u.over.appendChild(overlay);

        const onDown = (e) => {
          if (e.button !== 0 || e.ctrlKey) return;
          // Axis elementi üzerindeki tıklamalar perAxisZoomPanPlugin'e ait;
          // plot alanı dışında ise bu plugin müdahale etmesin
          if (e.target.closest && e.target.closest('.u-axis')) return;
          const rect = u.over.getBoundingClientRect();
          const localX = e.clientX - rect.left;
          // Plot alanı dışında (ör. legend) mousedown'ı göz ardı et
          if (localX < 0 || localX > rect.width
              || e.clientY < rect.top || e.clientY > rect.bottom) return;
          if (e.shiftKey) {
            const xMin = u.scales.x.min;
            const xMax = u.scales.x.max;
            if (xMin == null || xMax == null) return;
            mode = 'pan';
            state = { startX: e.clientX, xMin, xMax, width: rect.width };
            u.over.style.cursor = 'grabbing';
          } else {
            mode = 'box';
            state = { startX: localX, rect };
            overlay.style.left = localX + 'px';
            overlay.style.width = '0';
            overlay.style.display = 'block';
          }
          // uPlot cursor'un pointerdown/mousedown handler'ini bastır
          e.preventDefault();
          e.stopImmediatePropagation();
        };
        // u.root — chart'ın tüm DOM'u. u.over'da bazı uPlot konfigleriyle
        // event flow sorunlu olabiliyor; u.root daima güvenilir.
        u.root.addEventListener('pointerdown', onDown, { capture: true });
        u.root.addEventListener('mousedown', onDown, { capture: true });

        const onMove = (e) => {
          if (!mode) return;
          if (mode === 'pan') {
            const dx = e.clientX - state.startX;
            const range = state.xMax - state.xMin;
            const shift = (dx / state.width) * range * -1;
            u.setScale('x', {
              min: state.xMin + shift,
              max: state.xMax + shift,
            });
          } else if (mode === 'box') {
            const curX = Math.max(
              0, Math.min(state.rect.width, e.clientX - state.rect.left),
            );
            const left = Math.min(state.startX, curX);
            const width = Math.abs(curX - state.startX);
            overlay.style.left = left + 'px';
            overlay.style.width = width + 'px';
          }
        };
        const finish = (e) => {
          if (mode === 'box' && state && e) {
            const curX = Math.max(
              0, Math.min(state.rect.width, e.clientX - state.rect.left),
            );
            const start = Math.min(state.startX, curX);
            const end = Math.max(state.startX, curX);
            overlay.style.display = 'none';
            if (end - start > 5) {
              u.setScale('x', {
                min: u.posToVal(start, 'x'),
                max: u.posToVal(end, 'x'),
              });
            }
          } else if (mode === 'box') {
            overlay.style.display = 'none';
          } else if (mode === 'pan') {
            u.over.style.cursor = '';
          }
          mode = null;
          state = null;
        };
        // Mouse, pointer, pencere odak değişimi — hepsinde state'i bırak
        window.addEventListener('mousemove', onMove);
        window.addEventListener('pointermove', onMove);
        window.addEventListener('mouseup', finish);
        window.addEventListener('pointerup', finish);
        window.addEventListener('pointercancel', () => finish(null));
        window.addEventListener('blur', () => finish(null));
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

      const {
        timestamps = [],
        series = [],
        labels = [],
        units = [],
        window_start,
        window_end,
      } = chartData;

      // X ekseni aralığı: backend'in verdiği window (örn. 24h seçildiyse
      // 24 saat geri). Veri yoksa bile chart doğru span ile render edilir.
      // Eski data için fallback: timestamp min/max.
      const windowRange = (window_start != null && window_end != null)
        ? [window_start, window_end]
        : (timestamps.length > 0
            ? [timestamps[0], timestamps[timestamps.length - 1]]
            : null);
      if (!windowRange) return;

      // Görünür eksen sayısı data-visible-axes ile geçilir (default 2).
      // Overview kompakt: 2 eksen. Detay: tüm birimler için ayrı eksen.
      const visibleLimitRaw = el.dataset.visibleAxes;
      const visibleLimit = visibleLimitRaw === 'all'
        ? Infinity
        : (parseInt(visibleLimitRaw) || 2);

      // Axis mode: 'unit' (birim bazlı gruplama, overview default)
      // veya 'per-tag' (her tag için ayrı renkli eksen, detay sayfası)
      const axisMode = el.dataset.axisMode || 'unit';

      // Compact mode (overview kompakt): Y ekseni ve tick'leri çizilmez —
      // chart alanı genişler ve render maliyeti düşer. Kullanıcı detay
      // sayfasına (data-compact='false') girdiğinde tam axis görünür.
      // X ekseni (zaman) her zaman gösterilir; scale'ler build edilir
      // (çizgi yerleşimi için), sadece visual axis gizlenir.
      const compact = el.dataset.compact === 'true';

      const effectiveUnits = labels.map((_, i) => units[i] || '');
      const layout = axisMode === 'per-tag'
        ? buildPerTagLayout(labels, effectiveUnits)
        : buildScaleLayout(effectiveUnits, visibleLimit);

      // Her seri için scale key: per-tag modda her tag kendi scale'i,
      // unit modda aynı birimli tag'ler tek scale paylaşır.
      const seriesScale = (idx) => axisMode === 'per-tag'
        ? tagLabelToScale(labels[idx], idx)
        : unitToScale(effectiveUnits[idx]);

      // uPlot series: ilki x ekseni
      const uplotSeries = [{}];
      labels.forEach((label, i) => {
        uplotSeries.push({
          label: label,
          stroke: CHART_COLORS[i % CHART_COLORS.length],
          width: 1,
          points: { show: false },
          scale: seriesScale(i),
        });
      });

      // Veri yoksa da chart render edilsin diye minimum 2 timestamp gerek
      // (uPlot'un iç boşluk hesabı için). Window aralığını x olarak kullan.
      const effectiveTimestamps = timestamps.length > 0
        ? timestamps
        : [windowRange[0], windowRange[1]];
      const effectiveSeries = series.length > 0
        ? series
        : labels.map(() => [null, null]);
      const uplotData = [effectiveTimestamps, ...effectiveSeries];

      // Scales: x backend penceresine sabit; Y scale'leri layout'a göre.
      // auto:false ile uPlot redraw'da x range'i otomatik min/max'a
      // dönmez — setScale ile verilen değer kalıcı olur.
      const scales = {
        x: {
          time: true,
          auto: false,
        },
      };
      if (axisMode === 'per-tag') {
        // Her tag kendi scale'inde → sadece kendi verisinin min/max'i
        layout.forEach((l) => {
          const sIdx = l.seriesIdx;
          scales[l.key] = {
            range: () => calcYRangeForSeries(effectiveSeries[sIdx]),
          };
        });
      } else {
        for (const l of layout) {
          scales[l.key] = {
            range: () => calcYRangeForScale(
              effectiveSeries, effectiveUnits, l.key,
            ),
          };
        }
      }

      // Axes: X ekseni her durumda çizilir; Y eksenleri compact modda
      // gizli. Compact'te axes array'i sadece X içerir → uPlot ekstra
      // label/tick render etmez, perAxisZoomPanPlugin de Y axis DOM'u
      // bulamadığı için listener kurmaz (otomatik devre dışı).
      const axes = [{ ...BASE_AXIS, gap: 8 }];
      if (!compact) {
        if (axisMode === 'per-tag') {
          // Her tag için sol tarafta renkli eksen; grid çakışmasın diye
          // sadece ilk eksende grid görünür, diğerlerinde kapalı.
          layout.forEach((l, i) => {
            axes.push({
              ...BASE_AXIS,
              scale: l.key,
              side: 3,  // hepsi sol
              stroke: l.color,       // tick etiketi bu renkte
              ticks: { stroke: l.color, width: 1 },
              grid: { show: i === 0, stroke: '#2C3235', width: 1 },
              gap: 3,
              size: 50,
            });
          });
        } else {
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
        }
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
          // uPlot'un kendi drag mekanizmasi tamamen kapali — tum yatay
          // etkilesimler xAxisInteractionPlugin tarafindan yonetiliyor.
          drag: { x: false, y: false, setScale: false },
          points: {
            size: 6 * DPR,
            stroke: '#E6E9EC',
            fill: '#141619',
            width: 1.5 * DPR,
          },
        },
        legend: { show: true },
        plugins: [
          wheelZoomPlugin(),
          xAxisInteractionPlugin(),
          dblClickResetPlugin(windowRange),
          perAxisZoomPanPlugin(),
        ],
      };

      this.chart = new uPlot(opts, uplotData, el);
      window.custos.charts[chartId] = this.chart;
      // Init sonrası x ekseni zaman penceresine oturt (auto:false olduğu için
      // uPlot kendi başına set etmiyor). setScale sonrası user zoom/pan yapar,
      // çift tıkla yine windowRange'e döner.
      this.chart.setScale('x', { min: windowRange[0], max: windowRange[1] });

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
