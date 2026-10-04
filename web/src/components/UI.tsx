import { AlertCircle, Check, ChevronDown, FileSearch, LoaderCircle, X } from 'lucide-react';
import type { ReactNode } from 'react';
import { humanize } from '../utils';

export function Spinner({ label = 'Loading' }: { label?: string }) { return <span className="loading" role="status"><LoaderCircle className="spin" size={18} />{label}</span>; }
export function Badge({ value }: { value: string }) { return <span className={`badge badge-${value.replaceAll(' ', '_')}`}>{humanize(value)}</span>; }
export function Empty({ icon, title, children, action }: { icon?: ReactNode; title: string; children: ReactNode; action?: ReactNode }) {
  return <div className="empty"><div className="empty-icon">{icon ?? <FileSearch size={29} />}</div><h3>{title}</h3><div className="empty-copy">{children}</div>{action}</div>;
}
export function PageHeader({ eyebrow, title, children, action }: { eyebrow: string; title: string; children?: ReactNode; action?: ReactNode }) {
  return <div className="page-header"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1>{children && <p>{children}</p>}</div>{action && <div className="page-actions">{action}</div>}</div>;
}
export function Notice({ children, kind = 'info' }: { children: ReactNode; kind?: 'info' | 'warning' | 'success' }) {
  return <div className={`notice notice-${kind}`}><span>{kind === 'success' ? <Check size={17} /> : <AlertCircle size={17} />}</span><div>{children}</div></div>;
}
export function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  return <div className="modal-backdrop" onClick={onClose}><section className="modal" role="dialog" aria-modal="true" aria-label={title} onClick={e => e.stopPropagation()} onKeyDown={e => { if (e.key === 'Escape') onClose(); }}><div className="modal-heading"><h2>{title}</h2><button className="icon-button" aria-label="Close dialog" onClick={onClose}><X size={21} /></button></div>{children}</section></div>;
}
export function JsonDetails({ value, label = 'Inspect returned details' }: { value: unknown; label?: string }) {
  if (value == null) return null;
  return <details className="json-details"><summary>{label}<ChevronDown size={15} /></summary><pre>{JSON.stringify(value, null, 2)}</pre></details>;
}
export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return <label className="field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}
