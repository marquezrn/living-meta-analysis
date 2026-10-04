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
        paper_bgcolor: '#111e30', plot_bgcolor: '#111e30',
        font: { family: 'Inter, -apple-system, BlinkMacSystemFont, sans-serif', color: '#adbed2', size: 12 },
        margin: { l: 65, r: 28, t: 26, b: 100 }, height: 420,
        xaxis: { gridcolor: '#223148', zerolinecolor: '#31445a', automargin: true },
        yaxis: { gridcolor: '#223148', zerolinecolor: '#31445a', automargin: true },
        ...layout,
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
