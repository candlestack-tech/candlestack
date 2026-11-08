// @vitest-environment jsdom
import { cleanup, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ADA, dashboardAnswer, modelsAnswer, session, sessionWithPreferences, stubApi } from '@/test/api';
import { renderApp } from '@/test/app';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const inset = (px: number) => [
  `max-sm:pl-[max(${px}px,env(safe-area-inset-left,0px))]`,
  `max-sm:pr-[max(${px}px,env(safe-area-inset-right,0px))]`,
];
const classesOf = (element: Element | null | undefined) => element?.className.split(' ') ?? [];
const header = () => document.querySelector('header');
const footer = () => document.querySelector('footer')?.firstElementChild;
const main = () => document.querySelector('main');

// On a phone each group of pages keeps its own design's sides: mockups/home for the public pages,
// mockups/app for the signed-in ones.
describe('the frame of a page on a phone', () => {
  it('on the public pages: the header 12 px in, the footer 24 px, the page 28 px under the header', async () => {
    stubApi({ ...session(null), 'GET /api/v1/instruments/search': { status: 503 } });
    await renderApp('/');
    expect(classesOf(header())).toEqual(expect.arrayContaining(inset(12)));
    expect(classesOf(footer()).join(' ')).not.toContain('max-sm:p');
    expect(classesOf(main())).toEqual(expect.arrayContaining(['pt-7', 'pb-10', ...inset(16)]));
    expect(classesOf(main()).join(' ')).not.toMatch(/max-sm:p[tb]-/);
  });

  it('in the app: the header and the footer 16 px in, the page 20 px under the header', async () => {
    stubApi({ ...sessionWithPreferences(ADA), ...dashboardAnswer(), ...modelsAnswer([]) });
    await renderApp('/dashboard');
    await screen.findByRole('navigation', { name: 'Workspace' });
    expect(classesOf(header())).toEqual(expect.arrayContaining(inset(16)));
    expect(classesOf(footer())).toEqual(expect.arrayContaining(inset(16)));
    expect(classesOf(main())).toEqual(expect.arrayContaining(['max-sm:pt-5', 'max-sm:pb-7', ...inset(16)]));
  });
});
