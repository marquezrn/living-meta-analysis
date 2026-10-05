import { useEffect, useRef } from 'react';
import type { RefObject } from 'react';

interface FocusBoundaryOptions {
  active?: boolean;
  onClose: () => void;
  restoreFocus?: boolean;
  lockScroll?: boolean;
}
interface Boundary { token: symbol; container: HTMLElement }
const boundaries: Boundary[] = [];
let restoreBackground: (() => void)[] = [];
let scrollLocks = 0;
let previousOverflow = '';

function updateBackground() {
  restoreBackground.forEach(restore => restore());
  restoreBackground = [];
  const top = boundaries.at(-1);
  if (!top) return;
  let branch: HTMLElement = top.container;
  let parent = branch.parentElement;
  while (parent && parent !== document.documentElement) {
    for (const sibling of parent.children) {
      if (sibling === branch || !(sibling instanceof HTMLElement)) continue;
      const previous = sibling.getAttribute('inert');
      sibling.setAttribute('inert', '');
      restoreBackground.push(() => {
        if (previous === null) sibling.removeAttribute('inert');
        else sibling.setAttribute('inert', previous);
      });
    }
    branch = parent;
    parent = branch.parentElement;
  }
}

function focusable(container: HTMLElement): HTMLElement[] {
  const candidates = container.querySelectorAll<HTMLElement>(
    'a[href],button,input,select,textarea,iframe,[tabindex],[contenteditable="true"]',
  );
  return [...candidates].filter(element => {
    if (element.tabIndex < 0 || element.matches(':disabled,input[type="hidden"]') ||
        element.closest('[hidden],[inert]')) return false;
    let ancestor: HTMLElement | null = element;
    while (ancestor) {
      const style = window.getComputedStyle(ancestor);
      if (style.display === 'none' || style.visibility === 'hidden') return false;
      if (ancestor === container) break;
      ancestor = ancestor.parentElement;
    }
    return true;
  });
}

/**
 * Keep keyboard access inside an open dialog or mobile navigation panel.
 * Give the container ref an element with tabIndex={-1}; the hook moves focus
 * inside, traps Tab, makes the surrounding UI inert, handles Escape, and restores
 * the activating control on close. onClose may refuse closure while work is busy.
 * For a persistent mobile sidebar use active: mobileNav && isMobile, and mark
 * the closed mobile sidebar inert separately so its off-screen controls cannot
 * receive focus. Desktop navigation should leave this boundary inactive.
 */
export function useFocusBoundary(
  containerRef: RefObject<HTMLElement | null>,
  { active = true, onClose, restoreFocus = true, lockScroll = true }: FocusBoundaryOptions,
) {
  const close = useRef(onClose);
  close.current = onClose;
  const activation = useRef<{ active: boolean; previous: HTMLElement | null }>({ active: false, previous: null });
  // Capture before a child autoFocus runs during the DOM commit.
  if (active && !activation.current.active) {
    activation.current.previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  }
  activation.current.active = active;

  useEffect(() => {
    const container = containerRef.current;
    if (!active || !container) return;
    const previous = activation.current.previous;
    const token = Symbol('focus-boundary');
    boundaries.push({ token, container });
    updateBackground();
    if (lockScroll) {
      if (scrollLocks === 0) previousOverflow = document.body.style.overflow;
      scrollLocks += 1;
      document.body.style.overflow = 'hidden';
    }
    const isTop = () => boundaries.at(-1)?.token === token;
    const focusInside = () => (focusable(container)[0] ?? container).focus({ preventScroll: true });
    const keydown = (event: KeyboardEvent) => {
      if (!isTop()) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        close.current();
      } else if (event.key === 'Tab') {
        const controls = focusable(container);
        const index = controls.indexOf(document.activeElement as HTMLElement);
        if (!controls.length) {
          event.preventDefault();
          container.focus({ preventScroll: true });
        } else if (event.shiftKey ? index <= 0 : index < 0 || index === controls.length - 1) {
          event.preventDefault();
          controls[event.shiftKey ? controls.length - 1 : 0].focus({ preventScroll: true });
        }
      }
    };
    const focusin = (event: FocusEvent) => {
      if (isTop() && event.target instanceof Node && !container.contains(event.target)) focusInside();
    };
    document.addEventListener('keydown', keydown, true);
    document.addEventListener('focusin', focusin, true);
    if (!container.contains(document.activeElement)) focusInside();
    return () => {
      document.removeEventListener('keydown', keydown, true);
      document.removeEventListener('focusin', focusin, true);
      const index = boundaries.findIndex(boundary => boundary.token === token);
      if (index >= 0) boundaries.splice(index, 1);
      updateBackground();
      if (lockScroll) {
        scrollLocks -= 1;
        if (scrollLocks === 0) document.body.style.overflow = previousOverflow;
      }
      if (restoreFocus && previous?.isConnected && !previous.closest('[inert]')) {
        previous.focus({ preventScroll: true });
      }
    };
  }, [active, containerRef, restoreFocus, lockScroll]);
}
