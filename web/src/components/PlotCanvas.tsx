import { useEffect, useRef, useState } from 'react';
import type { Data, Layout } from 'plotly.js';
import { Notice, Spinner } from './UI';

export default function PlotCanvas({ data, layout, onPoint }: { data: Data[]; layout?: Partial<Layout>; onPoint?: (index: number) => void }) {
  const element = useRef<HTMLDivElement>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let disposed = false;
    let cleanup: (() => void) | undefined;
    setError(''); setLoading(true);
    void import('plotly.js-dist-min').then(async module => {
      if (disposed || !element.current) return;
      const Plotly = module.default;
      const target = element.current;
      const chart = await Plotly.newPlot(target, data, {
        paper_bgcolor: '#ffffff', plot_bgcolor: '#ffffff',
        colorway: ['#0969da', '#bf5b04', '#8250df', '#1a7f37'],
        margin: { l: 65, r: 28, t: 26, b: 100 }, height: 420,
        ...layout,
        font: { family: '-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif', color: '#57606a', size: 13, ...layout?.font },
        xaxis: { gridcolor: '#d8dee4', zerolinecolor: '#8c959f', automargin: true, ...layout?.xaxis },
        yaxis: { gridcolor: '#d8dee4', zerolinecolor: '#8c959f', automargin: true, ...layout?.yaxis },
      }, { responsive: true, displaylogo: false, modeBarButtonsToRemove: ['lasso2d', 'select2d'], toImageButtonOptions: { format: 'png', filename: 'living-meta-analysis', scale: 2 } });
      if (disposed) { Plotly.purge(target); return; }
      if (onPoint) chart.on('plotly_click', event => { const point = event.points[0]; if (typeof point?.customdata === 'number') onPoint(point.customdata); });
      const observer = new ResizeObserver(() => { void Plotly.Plots.resize(target); });
      observer.observe(target);
      cleanup = () => { observer.disconnect(); Plotly.purge(target); };
      setLoading(false);
    }).catch(reason => { if (!disposed) { setError(reason instanceof Error ? reason.message : 'Chart rendering failed.'); setLoading(false); } });
    return () => { disposed = true; cleanup?.(); };
  }, [data, layout, onPoint]);
  return <div className="chart-shell">{loading && <div className="chart-loading"><Spinner label="Preparing interactive chart" /></div>}{error && <Notice kind="warning">{error}</Notice>}<div ref={element} className="plot-canvas" aria-label="Interactive chart of recorded measurements" /></div>;
}
