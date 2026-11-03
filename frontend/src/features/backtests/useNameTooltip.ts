import { useEffect, useRef, useState } from 'react';

// Where the full name of a cut name shows (NameTooltip): `ref` goes on the element that may cut
// it, and `at` is its box while it is hovered, or while the link or button it sits in has the
// focus (a row's link is stretched over the row, so the name is what the keyboard reaches), and
// only while the name is cut with an ellipsis or after its second line. A scroll hides it.

const isCut = (element: HTMLElement) =>
  element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1;

export function useNameTooltip<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [at, setAt] = useState<DOMRect | null>(null);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const host = element.closest<HTMLElement>('a, button');
    const show = () => {
      setAt(isCut(element) ? element.getBoundingClientRect() : null);
    };
    const hide = () => {
      setAt(null);
    };
    element.addEventListener('pointerenter', show);
    element.addEventListener('pointerleave', hide);
    host?.addEventListener('focus', show);
    host?.addEventListener('blur', hide);
    return () => {
      element.removeEventListener('pointerenter', show);
      element.removeEventListener('pointerleave', hide);
      host?.removeEventListener('focus', show);
      host?.removeEventListener('blur', hide);
    };
  }, []);
  useEffect(() => {
    if (!at) return;
    const hide = () => {
      setAt(null);
    };
    window.addEventListener('scroll', hide, { capture: true, passive: true });
    window.addEventListener('wheel', hide, { passive: true });
    return () => {
      window.removeEventListener('scroll', hide, { capture: true });
      window.removeEventListener('wheel', hide);
    };
  }, [at]);
  return { ref, at };
}
