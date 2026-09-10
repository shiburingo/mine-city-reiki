import {
  ViewTransition, addTransitionType, startTransition, useCallback, useState,
  type Dispatch, type ReactNode, type SetStateAction,
} from 'react';
import './viewTransitions.css';

const navigationAnimation = { 'mt-navigation': 'mt-screen', default: 'none' };

export function canAnimateViewChange(): boolean {
  return typeof document !== 'undefined'
    && typeof document.startViewTransition === 'function'
    && document.visibilityState === 'visible'
    && typeof CSS !== 'undefined' && CSS.supports('view-transition-class', 'mt-screen')
    && !window.matchMedia('(prefers-reduced-motion: reduce)').matches
    && !window.matchMedia('print').matches;
}

/** Navigation only: never wrap network requests or controlled text input updates. */
export function changeView(update: () => void): void {
  if (!canAnimateViewChange()) {
    update();
    return;
  }
  startTransition(() => {
    addTransitionType('mt-navigation');
    update();
  });
}

/** Keep the same subtree identity; adding a page key here would reset drafts. */
export function ViewRegion({ children }: { children: ReactNode }) {
  return (
    <ViewTransition default="none" update={navigationAnimation} enter={navigationAnimation} exit={navigationAnimation}>
      {children}
    </ViewTransition>
  );
}

/** For page/tab identifiers only, not business data, form fields or loading flags. */
export function useViewState<T>(initial: T | (() => T)): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState(initial);
  // A stable dispatch preserves the useState contract for existing effect dependencies.
  const setView = useCallback((next: SetStateAction<T>) => changeView(() => setValue(next)), []);
  return [value, setView];
}
