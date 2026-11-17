import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useLanguageStore } from '@/stores/languageStore';
import { HomePage } from './HomePage';

interface MediaCall {
  type: 'play' | 'pause';
  src: string;
}

const VIDEO_BANNER = {
  id: 21,
  title: 'Иммунитет',
  title_uz: 'Immunitet',
  subtitle: 'Комплексная поддержка каждый день',
  subtitle_uz: "Har kunlik kompleks qo'llab-quvvatlash",
  image_url: 'https://cdn.example.test/v1-wide.jpg',
  image_mobile_url: 'https://cdn.example.test/v1-phone.jpg',
  video_url: 'https://cdn.example.test/v1-wide.mp4',
  video_mobile_url: 'https://cdn.example.test/v1-phone.mp4',
  gradient: null,
  link_url: '/product/4004',
  sort_order: 0,
  is_active: true,
};

const IMAGE_BANNER = {
  id: 22,
  title: 'Каталог',
  title_uz: 'Katalog',
  subtitle: null,
  subtitle_uz: null,
  image_url: 'https://cdn.example.test/img2-wide.jpg',
  image_mobile_url: 'https://cdn.example.test/img2-phone.jpg',
  video_url: null,
  video_mobile_url: null,
  gradient: null,
  link_url: '/catalog',
  sort_order: 1,
  is_active: true,
};

let currentBanners: Record<string, unknown>[] = [];
let currentInterval = '4000';
const mediaCalls: MediaCall[] = [];
let playBehavior: ((video: HTMLMediaElement) => Promise<void>) | null = null;
let viewportWidth = 390;
let reducedMotion = false;
const wideListeners = new Set<() => void>();

function stubMatchMedia() {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => {
      const isWideQuery = query === '(min-width: 768px)';
      const isReducedQuery = query === '(prefers-reduced-motion: reduce)';
      const listeners = new Set<() => void>();
      return {
        get matches() {
          if (isWideQuery) return viewportWidth >= 768;
          if (isReducedQuery) return reducedMotion;
          return false;
        },
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn((event: string, listener: () => void) => {
          if (event === 'change') {
            listeners.add(listener);
            if (isWideQuery) wideListeners.add(listener);
          }
        }),
        removeEventListener: vi.fn((event: string, listener: () => void) => {
          if (event === 'change') {
            listeners.delete(listener);
            if (isWideQuery) wideListeners.delete(listener);
          }
        }),
        dispatchEvent: vi.fn(),
      };
    }),
  });
}

function setViewportWidth(width: number) {
  viewportWidth = width;
  for (const listener of wideListeners) {
    listener();
  }
}

function LocationReadout() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}

function mountHome() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <LocationReadout />
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/product/:productId" element={<div>product-4004</div>} />
        <Route path="/catalog" element={<div>catalog</div>} />
      </Routes>
    </MemoryRouter>
  );
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false });
  vi.setSystemTime(new Date('2026-11-17T10:00:00.000Z'));
  currentBanners = [VIDEO_BANNER, IMAGE_BANNER];
  currentInterval = '4000';
  mediaCalls.length = 0;
  playBehavior = null;
  viewportWidth = 390;
  reducedMotion = false;
  wideListeners.clear();
  useLanguageStore.setState({ language: 'ru' });
  Object.defineProperty(document, 'hidden', { value: false, configurable: true });
  stubMatchMedia();

  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (this: HTMLMediaElement) {
    mediaCalls.push({ type: 'play', src: this.getAttribute('src') ?? '' });
    if (playBehavior) {
      return playBehavior(this);
    }
    return Promise.resolve();
  });
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function (
    this: HTMLMediaElement
  ) {
    mediaCalls.push({ type: 'pause', src: this.getAttribute('src') ?? '' });
  });

  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string | URL | Request) => {
      const url = new URL(String(input), 'http://miniapp.test');
      if (url.pathname.endsWith('/categories')) {
        return new Response(
          JSON.stringify({ items: [], total: 0, page: 1, size: 100, pages: 1 })
        );
      }
      if (url.pathname.endsWith('/products')) {
        return new Response(
          JSON.stringify({ items: [], total: 0, page: 1, size: 100, pages: 1 })
        );
      }
      if (url.pathname.endsWith('/banners')) {
        return new Response(
          JSON.stringify({ items: currentBanners, total: currentBanners.length })
        );
      }
      if (url.pathname.endsWith('/settings/banner_interval')) {
        return new Response(JSON.stringify({ value: currentInterval }));
      }
      throw new Error(`Unexpected URL ${url}`);
    })
  );
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  Object.defineProperty(document, 'hidden', { value: false, configurable: true });
});

async function flushInitialRender() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe('Mini App video banners', () => {
  it('User path D (D-01, D-02, D-04, D-05, D-08, D-09, D-11, D-12, D-16): phone format playback, pause/play control, active-slide isolation, timing and click navigation', async () => {
    const view = mountHome();
    await flushInitialRender();

    const carousel = screen.getByTestId('banner-carousel');
    expect(carousel).toHaveAttribute('data-format', 'phone');
    expect(screen.queryByTestId('banner-fallback-below')).not.toBeInTheDocument();

    const slide21 = view.container.querySelector('[data-banner-id="21"]') as HTMLElement;
    const slide22 = view.container.querySelector('[data-banner-id="22"]') as HTMLElement;
    expect(slide21.firstElementChild?.className).toContain('aspect-[3/2]');
    expect(slide22.firstElementChild?.className).toContain('aspect-[3/2]');
    expect(slide21).toHaveAttribute('data-active', 'true');

    // D-13 & D-04: before still 0 settles, slide 22 has no src and zero <video> elements exist
    const phoneStill0 = slide21.querySelector('img[src="https://cdn.example.test/v1-phone.jpg"]');
    expect(phoneStill0).toBeInTheDocument();
    expect(slide22.querySelector('img')?.hasAttribute('src')).toBe(false);
    expect(view.container.querySelectorAll('video')).toHaveLength(0);

    // Load still 0 -> phone still unlocks phone clip ONLY on active slide 21 (never v1-wide.mp4)
    await act(async () => {
      fireEvent.load(phoneStill0 as HTMLImageElement);
    });
    expect(slide22.querySelector('img')).toHaveAttribute(
      'src',
      'https://cdn.example.test/img2-phone.jpg'
    );
    expect(slide22.querySelectorAll('video')).toHaveLength(0);

    const video = slide21.querySelector('video') as HTMLVideoElement;
    expect(video).toBeInTheDocument();
    expect(video).toHaveAttribute('src', 'https://cdn.example.test/v1-phone.mp4');
    expect(video).toHaveAttribute('poster', 'https://cdn.example.test/v1-phone.jpg');
    expect(video.muted).toBe(true);
    expect(video.playsInline).toBe(true);
    expect(video.controls).toBe(false);
    expect(video.className).toContain('opacity-0');
    expect(mediaCalls.filter((c) => c.type === 'play' && c.src === 'https://cdn.example.test/v1-phone.mp4')).toHaveLength(1);

    // D-02 & D-16: playing event makes video visible and switches control to Pause
    await act(async () => {
      fireEvent(video, new Event('playing'));
    });
    expect(video.className).toContain('opacity-100');
    const pauseButton = screen.getByRole('button', { name: 'Пауза видео' });

    // D-05: carousel MUST NOT advance on interval while clip is playing
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(slide21).toHaveAttribute('data-active', 'true');

    // D-16: visitor pauses -> pause() called, no advance after full interval
    fireEvent.click(pauseButton);
    expect(
      mediaCalls.filter((c) => c.type === 'pause' && c.src === 'https://cdn.example.test/v1-phone.mp4')
    ).toHaveLength(1);
    expect(screen.getByTestId('location')).toHaveTextContent('/');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4500);
    });
    expect(slide21).toHaveAttribute('data-active', 'true');

    // D-16 & F-04 guard: resume MUST invoke play() BEFORE dispatching playing
    const playCallsBeforeTap = mediaCalls.filter(
      (c) => c.type === 'play' && c.src === 'https://cdn.example.test/v1-phone.mp4'
    ).length;
    const playButton = screen.getByRole('button', { name: 'Воспроизвести видео' });
    fireEvent.click(playButton);
    const playCallsAfterTap = mediaCalls.filter(
      (c) => c.type === 'play' && c.src === 'https://cdn.example.test/v1-phone.mp4'
    ).length;
    expect(playCallsAfterTap).toBe(playCallsBeforeTap + 1);

    await act(async () => {
      fireEvent(video, new Event('playing'));
    });
    expect(screen.getByRole('button', { name: 'Пауза видео' })).toBeInTheDocument();

    // D-05: clip ended (after interval has elapsed) advances to image slide 22, which holds full 4000 ms
    await act(async () => {
      fireEvent(video, new Event('ended'));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(slide22).toHaveAttribute('data-active', 'true');
    expect(view.container.querySelectorAll('video')).toHaveLength(0);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3999);
    });
    expect(slide22).toHaveAttribute('data-active', 'true');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(slide21).toHaveAttribute('data-active', 'true');

    // D-09: clicking video banner opens link target
    fireEvent.click(slide21);
    expect(screen.getByTestId('location')).toHaveTextContent('/product/4004');
  });

  it('D-05: clip ending before interval waits on last frame until interval since activation elapses', async () => {
    const view = mountHome();
    await flushInitialRender();

    const slide21 = view.container.querySelector('[data-banner-id="21"]') as HTMLElement;
    const slide22 = view.container.querySelector('[data-banner-id="22"]') as HTMLElement;
    await act(async () => {
      fireEvent.load(slide21.querySelector('img') as HTMLImageElement);
    });
    const video = slide21.querySelector('video') as HTMLVideoElement;
    await act(async () => {
      fireEvent(video, new Event('playing'));
      await vi.advanceTimersByTimeAsync(2000);
      fireEvent(video, new Event('ended'));
    });
    expect(slide21).toHaveAttribute('data-active', 'true');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1999);
    });
    expect(slide21).toHaveAttribute('data-active', 'true');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(slide22).toHaveAttribute('data-active', 'true');
  });

  it('D-14 (3999 / 4000 literal budget): abandons clip on 4000 ms budget expiry, keeps still and advances', async () => {
    const view = mountHome();
    await flushInitialRender();

    const slide21 = view.container.querySelector('[data-banner-id="21"]') as HTMLElement;
    const slide22 = view.container.querySelector('[data-banner-id="22"]') as HTMLElement;
    await act(async () => {
      fireEvent.load(slide21.querySelector('img') as HTMLImageElement);
    });

    expect(slide21.querySelectorAll('video')).toHaveLength(1);
    expect(mediaCalls.filter((c) => c.type === 'play')).toHaveLength(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3999);
    });
    expect(slide21.querySelectorAll('video')).toHaveLength(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(slide21.querySelectorAll('video')).toHaveLength(0);
    expect(screen.queryByRole('button', { name: /видео/i })).not.toBeInTheDocument();
    expect(slide22).toHaveAttribute('data-active', 'true');
  });

  it('D-07b, D-08, D-16: refused autoplay shows Play control and retries play() on tap; Uzbek skips blank subtitle without Russian fallback', async () => {
    useLanguageStore.setState({ language: 'uz' });
    currentBanners = [
      { ...VIDEO_BANNER, subtitle_uz: '   ' },
      IMAGE_BANNER,
    ];
    playBehavior = () =>
      Promise.reject(new DOMException('The request is not allowed by the user agent', 'NotAllowedError'));

    const view = mountHome();
    await flushInitialRender();

    expect(screen.getByText('Immunitet')).toBeInTheDocument();
    expect(screen.queryByText(VIDEO_BANNER.subtitle)).not.toBeInTheDocument();

    const slide21 = view.container.querySelector('[data-banner-id="21"]') as HTMLElement;
    await act(async () => {
      fireEvent.load(slide21.querySelector('img') as HTMLImageElement);
      await Promise.resolve();
    });

    const playButton = screen.getByRole('button', { name: 'Videoni ijro etish' });
    expect(playButton).toBeInTheDocument();

    playBehavior = () => Promise.resolve();
    const playCallsBefore = mediaCalls.filter((c) => c.type === 'play').length;
    fireEvent.click(playButton);
    expect(mediaCalls.filter((c) => c.type === 'play').length).toBe(playCallsBefore + 1);

    const video = slide21.querySelector('video') as HTMLVideoElement;
    await act(async () => {
      fireEvent(video, new Event('playing'));
    });
    expect(screen.getByRole('button', { name: "Videoni to'xtatish" })).toBeInTheDocument();
  });

  it('D-08, D-11, D-12 (3:1 fallback and 768 px boundary): per-carousel fallback keeps equal height with wide media, Uzbek uses title_uz', async () => {
    useLanguageStore.setState({ language: 'uz' });
    currentBanners = [VIDEO_BANNER, { ...IMAGE_BANNER, image_mobile_url: null }];
    viewportWidth = 767;

    const view = mountHome();
    await flushInitialRender();

    const carousel = screen.getByTestId('banner-carousel');
    expect(carousel).toHaveAttribute('data-format', 'fallback');

    const slide21 = view.container.querySelector('[data-banner-id="21"]') as HTMLElement;
    const slide22 = view.container.querySelector('[data-banner-id="22"]') as HTMLElement;
    expect(slide21.firstElementChild?.className).toContain('h-32');
    expect(slide22.firstElementChild?.className).toContain('h-32');
    expect(slide21.querySelectorAll('[data-testid="banner-fallback-below"]')).toHaveLength(1);
    expect(slide22.querySelectorAll('[data-testid="banner-fallback-below"]')).toHaveLength(1);
    expect(slide21.querySelector('[data-testid="banner-fallback-below"]')?.className).toContain('h-[92px]');
    expect(slide22.querySelector('[data-testid="banner-fallback-below"]')?.className).toContain('h-[92px]');
    expect(screen.getByText('Immunitet')).toBeInTheDocument();
    expect(screen.queryByText('Batafsil')).not.toBeInTheDocument();

    const wideStill0 = slide21.querySelector('img[src="https://cdn.example.test/v1-wide.jpg"]');
    expect(wideStill0).toBeInTheDocument();
    await act(async () => {
      fireEvent.load(wideStill0 as HTMLImageElement);
    });
    expect(slide21.querySelector('video')).toHaveAttribute('src', 'https://cdn.example.test/v1-wide.mp4');

    // Cross literal 768 px md boundary -> wide layout renders overlay text + Uzbek pill ("Batafsil")
    await act(async () => {
      setViewportWidth(768);
    });
    expect(carousel).toHaveAttribute('data-format', 'wide');
    expect(screen.queryByTestId('banner-fallback-below')).not.toBeInTheDocument();
    expect(screen.getByText('Batafsil')).toBeInTheDocument();
  });

  it('D-10, D-07b (F-01 guard): client pause aborting pending play() resumes cleanly instead of turning refused', async () => {
    let rejectCurrentPlay: ((reason?: unknown) => void) | null = null;
    playBehavior = () =>
      new Promise<void>((_resolve, reject) => {
        rejectCurrentPlay = reject;
      });

    const view = mountHome();
    await flushInitialRender();
    const slide21 = view.container.querySelector('[data-banner-id="21"]') as HTMLElement;
    await act(async () => {
      fireEvent.load(slide21.querySelector('img') as HTMLImageElement);
    });

    expect(mediaCalls.filter((c) => c.type === 'play')).toHaveLength(1);

    // Hide page while play() is still pending -> pauses and rejects pending promise with AbortError
    await act(async () => {
      Object.defineProperty(document, 'hidden', { value: true, configurable: true });
      document.dispatchEvent(new Event('visibilitychange'));
      rejectCurrentPlay?.(new DOMException('The play() request was interrupted by pause()', 'AbortError'));
      await Promise.resolve();
    });
    expect(mediaCalls.filter((c) => c.type === 'pause')).toHaveLength(1);

    // Return to visible -> auto-resumes via a second play() call rather than staying stuck as refused
    playBehavior = () => Promise.resolve();
    await act(async () => {
      Object.defineProperty(document, 'hidden', { value: false, configurable: true });
      document.dispatchEvent(new Event('visibilitychange'));
      await Promise.resolve();
    });
    expect(mediaCalls.filter((c) => c.type === 'play')).toHaveLength(2);

    const video = slide21.querySelector('video') as HTMLVideoElement;
    await act(async () => {
      fireEvent(video, new Event('playing'));
    });
    expect(screen.getByRole('button', { name: 'Пауза видео' })).toBeInTheDocument();
  });

  it('D-05, D-07a (F-03 guard): mid-play media error advances at interval since activation rather than full interval from error', async () => {
    const view = mountHome();
    await flushInitialRender();

    const slide21 = view.container.querySelector('[data-banner-id="21"]') as HTMLElement;
    const slide22 = view.container.querySelector('[data-banner-id="22"]') as HTMLElement;
    await act(async () => {
      fireEvent.load(slide21.querySelector('img') as HTMLImageElement);
    });

    const video = slide21.querySelector('video') as HTMLVideoElement;
    await act(async () => {
      fireEvent(video, new Event('playing'));
      await vi.advanceTimersByTimeAsync(3000);
      fireEvent(video, new Event('error'));
    });

    expect(slide21.querySelectorAll('video')).toHaveLength(0);
    expect(slide21).toHaveAttribute('data-active', 'true');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(999);
    });
    expect(slide21).toHaveAttribute('data-active', 'true');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(slide22).toHaveAttribute('data-active', 'true');
  });
});
