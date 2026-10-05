import { useRef, useState } from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Modal, Notice } from '../components/UI';
import { useFocusBoundary } from '../components/useFocusBoundary';
import { EvidenceDrawer } from '../views/Evidence';
import type { Measurement } from '../types';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function DialogHarness({ autoFocus = false }: { autoFocus?: boolean }) {
  const [open, setOpen] = useState(false);
  return <main><button onClick={() => setOpen(true)}>Open dialog</button><button>Background action</button>
    {open && <Modal title="Synthetic dialog" onClose={() => setOpen(false)}>
      <label>Review name<input autoFocus={autoFocus} /></label>
      <button disabled>Unavailable action</button>
      <button hidden>Hidden action</button>
      <button>Last dialog action</button>
    </Modal>}
  </main>;
}

function openDialog() {
  const opener = screen.getByRole('button', { name: 'Open dialog' });
  opener.focus();
  fireEvent.click(opener);
  return opener;
}

describe('dialog keyboard access', () => {
  it('moves focus inside, wraps Tab in both directions, and blocks background focus', () => {
    render(<DialogHarness />);
    const background = screen.getByRole('button', { name: 'Background action' });
    const opener = openDialog();
    const first = screen.getByRole('button', { name: 'Close dialog' });
    const last = screen.getByRole('button', { name: 'Last dialog action' });
    expect(first).toHaveFocus();
    expect(opener).toHaveAttribute('inert');
    expect(background).toHaveAttribute('inert');
    fireEvent.keyDown(first, { key: 'Tab', shiftKey: true });
    expect(last).toHaveFocus();
    fireEvent.keyDown(last, { key: 'Tab' });
    expect(first).toHaveFocus();
    background.focus();
    expect(first).toHaveFocus();
    expect(document.body.style.overflow).toBe('hidden');
  });

  it('closes with Escape and restores the activating control and background access', () => {
    render(<DialogHarness />);
    const opener = openDialog();
    fireEvent.keyDown(screen.getByRole('button', { name: 'Close dialog' }), { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
    expect(opener).not.toHaveAttribute('inert');
    expect(document.body.style.overflow).toBe('');
  });

  it('restores the opener even when a dialog child uses autoFocus', () => {
    render(<DialogHarness autoFocus />);
    const opener = openDialog();
    expect(screen.getByRole('textbox', { name: 'Review name' })).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }));
    expect(opener).toHaveFocus();
  });

  it('keeps clicks within the dialog open and closes only a backdrop click', () => {
    const { container } = render(<DialogHarness />);
    const opener = openDialog();
    fireEvent.click(screen.getByRole('textbox', { name: 'Review name' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    fireEvent.click(container.querySelector('.modal-backdrop')!);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it('retains existing background restrictions after the dialog closes', () => {
    const restricted = document.createElement('div');
    restricted.setAttribute('inert', 'existing');
    document.body.append(restricted);
    try {
      render(<DialogHarness />);
      openDialog();
      fireEvent.keyDown(screen.getByRole('button', { name: 'Close dialog' }), { key: 'Escape' });
      expect(restricted.getAttribute('inert')).toBe('existing');
    } finally { restricted.remove(); }
  });

  it('handles only the top dialog and restores focus to its parent dialog', () => {
    function Nested() {
      const [outer, setOuter] = useState(false); const [inner, setInner] = useState(false);
      return <><button onClick={() => setOuter(true)}>Open outer</button>{outer &&
        <Modal title="Outer dialog" onClose={() => setOuter(false)}>
          <button onClick={() => setInner(true)}>Open inner</button>{inner &&
            <Modal title="Inner dialog" onClose={() => setInner(false)}><button>Inner action</button></Modal>}
        </Modal>}</>;
    }
    render(<Nested />);
    const opener = screen.getByRole('button', { name: 'Open outer' }); opener.focus(); fireEvent.click(opener);
    const innerOpener = screen.getByRole('button', { name: 'Open inner' }); innerOpener.focus(); fireEvent.click(innerOpener);
    const inner = screen.getByRole('dialog', { name: 'Inner dialog' });
    expect(within(inner).getByRole('button', { name: 'Close dialog' })).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Inner dialog' })).not.toBeInTheDocument();
    expect(screen.getByRole('dialog', { name: 'Outer dialog' })).toBeInTheDocument();
    expect(innerOpener).toHaveFocus();
    fireEvent.keyDown(innerOpener, { key: 'Escape' });
    expect(opener).toHaveFocus();
  });
});

describe('persistent navigation focus boundary', () => {
  it('activates only while open, handles Escape, and restores the navigation button', () => {
    function Navigation() {
      const [open, setOpen] = useState(false);
      const ref = useRef<HTMLElement>(null);
      useFocusBoundary(ref, { active: open, onClose: () => setOpen(false) });
      return <><button onClick={() => setOpen(true)}>Open navigation</button>
        <aside ref={ref} tabIndex={-1} inert={!open}>
          <button onClick={() => setOpen(false)}>Close navigation</button><a href="#science">Science</a>
        </aside><button>Workspace action</button></>;
    }
    render(<Navigation />);
    const opener = screen.getByRole('button', { name: 'Open navigation' }); opener.focus(); fireEvent.click(opener);
    const first = screen.getByRole('button', { name: 'Close navigation' });
    const last = screen.getByRole('link', { name: 'Science' });
    expect(first).toHaveFocus();
    fireEvent.keyDown(first, { key: 'Tab', shiftKey: true }); expect(last).toHaveFocus();
    fireEvent.keyDown(last, { key: 'Escape' });
    expect(opener).toHaveFocus();
    expect(first.closest('aside')).toHaveAttribute('inert');
    expect(opener).not.toHaveAttribute('inert');
  });
});

describe('evidence drawer and asynchronous messages', () => {
  it('supports keyboard closure and restores the measurement source control', () => {
    const measurement: Measurement = { id: 'synthetic', experiment_id: 'experiment', outcome: 'Droplet_Size_um',
      raw_value: '7', value: 7, unit: 'um', qualifier: 'exact', uncertainty_type: 'none', origin: 'reported',
      status: 'uncertain', validation_notes: [], evidence: [] };
    function Evidence() {
      const [open, setOpen] = useState(false);
      return <><button onClick={() => setOpen(true)}>View source</button>{open &&
        <EvidenceDrawer projectId="project" measurement={measurement} experiments={[]} documents={[]}
          canEdit={false} busy={false} onReview={vi.fn()} onClose={() => setOpen(false)} />}</>;
    }
    render(<Evidence />);
    const opener = screen.getByRole('button', { name: 'View source' }); opener.focus(); fireEvent.click(opener);
    const close = screen.getByRole('button', { name: 'Close evidence' }); expect(close).toHaveFocus();
    fireEvent.keyDown(close, { key: 'Tab' }); expect(close).toHaveFocus();
    fireEvent.keyDown(close, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it('exposes errors and success responses to assistive technology', () => {
    render(<><Notice kind="warning">The request failed.</Notice><Notice kind="success">Saved.</Notice></>);
    expect(screen.getByRole('alert')).toHaveTextContent('The request failed.');
    expect(screen.getByRole('status')).toHaveTextContent('Saved.');
  });
});
