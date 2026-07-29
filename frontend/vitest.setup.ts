import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { createElement, type ImgHTMLAttributes } from 'react';
import { afterEach, vi } from 'vitest';

type TestImageProps = ImgHTMLAttributes<HTMLImageElement> & {
    fill?: boolean;
    priority?: boolean;
    unoptimized?: boolean;
};

// Happy DOM does not apply Tailwind layout styles, so Next's development-only
// fill geometry checks report false positives for otherwise valid containers.
// Preserve fill identity so component tests can still assert the DOM contract.
vi.mock('next/image', () => ({
    default: ({ fill, priority, unoptimized, ...props }: TestImageProps) => {
        void priority;
        void unoptimized;
        return createElement('img', {
            ...props,
            'data-nimg': fill ? 'fill' : '1',
        });
    },
}));

afterEach(() => {
    cleanup();
});
