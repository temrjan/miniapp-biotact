import { useCallback, useEffect, useRef, useState, type MouseEvent } from 'react';
import { motion } from 'framer-motion';
import { Pause, Play } from 'lucide-react';
import WebApp from '@twa-dev/sdk';
import { safeMedia, type CatalogueBanner } from '@/api/catalogue';
import { useTranslation } from '@/i18n';
import type { Language } from '@/stores/languageStore';
import { cn } from '@/utils/cn';

/** Auto-start playback budget in milliseconds before falling back to still-only (D-14, CD-27). */
const START_BUDGET_MS = 4000;
const WIDE_MEDIA_QUERY = '(min-width: 768px)';
const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)';

type LayoutMode = 'wide' | 'phone' | 'fallback';
type StillState = 'loaded' | 'error';
type ClipStatus = 'idle' | 'playing' | 'paused' | 'ended' | 'refused' | 'still-only';

interface ClipState {
  key: string;
  status: ClipStatus;
  frameReady: boolean;
  visitorPaused: boolean;
  visibilityPaused: boolean;
  budgetExpiredAt: number | null;
  manual: boolean;
}

interface NetworkConnection extends EventTarget {
  saveData?: boolean;
}

interface BannerCarouselProps {
  banners: CatalogueBanner[];
  language: Language;
  intervalMs: number;
  onBannerClick: (linkUrl: string | null) => void;
}

function createClipState(key: string): ClipState {
  return {
    key,
    status: 'idle',
    frameReady: false,
    visitorPaused: false,
    visibilityPaused: false,
    budgetExpiredAt: null,
    manual: false,
  };
}

function readMediaMatches(query: string): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false;
  }
  return window.matchMedia(query).matches;
}

function readSaveData(): boolean {
  const conn = (navigator as Navigator & { connection?: NetworkConnection }).connection;
  return conn?.saveData === true;
}

/** A banner is a video banner only when both wide clip and wide still pass D-03. */
function isVideoBanner(banner: CatalogueBanner): boolean {
  return banner.video_url !== null && safeMedia(banner.image_url, false) !== null;
}

function bannerText(
  banner: CatalogueBanner,
  language: Language
): { title: string; subtitle: string | null } {
  if (!isVideoBanner(banner)) {
    return { title: banner.title.trim(), subtitle: null };
  }
  if (language === 'uz') {
    return {
      title: banner.title_uz?.trim() || '',
      subtitle: banner.subtitle_uz?.trim() || null,
    };
  }
  return {
    title: banner.title.trim(),
    subtitle: banner.subtitle?.trim() || null,
  };
}

function stillUrlFor(banner: CatalogueBanner, layoutMode: LayoutMode): string | null {
  if (layoutMode === 'phone') {
    return banner.image_mobile_url ?? (banner.image_url || null);
  }
  return banner.image_url || null;
}

function clipUrlFor(banner: CatalogueBanner, layoutMode: LayoutMode): string | null {
  if (!isVideoBanner(banner)) {
    return null;
  }
  return layoutMode === 'phone' ? banner.video_mobile_url : banner.video_url;
}

/** Mini App banner carousel implementing shared display, playback, phone-format and text rules. */
export function BannerCarousel({
  banners,
  language,
  intervalMs,
  onBannerClick,
}: BannerCarouselProps) {
  const { t } = useTranslation();

  const containerRef = useRef<HTMLDivElement>(null);
  const firstImgRef = useRef<HTMLImageElement | null>(null);
  const activeImgRef = useRef<HTMLImageElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const budgetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const currentKeyRef = useRef('');
  const activatedAtRef = useRef(0);
  const autoStartedRef = useRef(false);
  const playPendingRef = useRef(false);
  const onScreenRef = useRef(true);
  const tgActiveRef = useRef(true);
  const clipSnapshotRef = useRef<ClipState>(createClipState(''));

  const [currentBanner, setCurrentBanner] = useState(0);
  const [isWide, setIsWide] = useState(() => readMediaMatches(WIDE_MEDIA_QUERY));
  const [reduced, setReduced] = useState(() => readMediaMatches(REDUCED_MOTION_QUERY));
  const [saveData, setSaveData] = useState(readSaveData);
  const [hidden, setHidden] = useState(() =>
    typeof document !== 'undefined' ? document.hidden : false
  );
  const [onScreen, setOnScreen] = useState(true);
  const [tgActive, setTgActive] = useState(true);
  const [imageStates, setImageStates] = useState<Record<string, StillState>>({});
  const [clipState, setClipState] = useState<ClipState>(() => createClipState(''));

  const count = banners.length;
  const rotating = count > 1;
  const safeIndex = count > 0 ? currentBanner % count : 0;
  const hasPhonePictures = count > 0 && banners.every((b) => b.image_mobile_url !== null);
  const layoutMode: LayoutMode = !isWide ? (hasPhonePictures ? 'phone' : 'fallback') : 'wide';
  const hasVideoBanner = banners.some(isVideoBanner);

  const firstBanner = banners[0];
  const firstStillUrl = firstBanner ? stillUrlFor(firstBanner, layoutMode) : null;
  const firstStillSettled = firstStillUrl === null || imageStates[firstStillUrl] !== undefined;

  const activeBanner = banners[safeIndex];
  const activeStillUrl = activeBanner ? stillUrlFor(activeBanner, layoutMode) : null;
  const activeClipUrl = activeBanner ? clipUrlFor(activeBanner, layoutMode) : null;
  const activeStillState = activeStillUrl ? imageStates[activeStillUrl] : undefined;
  const activeStillLoaded =
    firstStillSettled && (activeStillUrl === null || activeStillState === 'loaded');
  const activeStillFailed = activeStillState === 'error';
  const stillOnlyByEnv = reduced || saveData || activeStillFailed;

  const activationKey = activeBanner ? `${safeIndex}:${activeBanner.id}:${activeClipUrl ?? ''}` : '';
  const effectiveClip = clipState.key === activationKey ? clipState : createClipState(activationKey);
  const isVideoSlide = activeClipUrl !== null;
  const isEffectiveStillOnly = stillOnlyByEnv || effectiveClip.status === 'still-only';
  const shouldMountVideo = activeClipUrl !== null && activeStillLoaded && !isEffectiveStillOnly;

  const patchClip = useCallback((targetKey: string, updater: (prev: ClipState) => ClipState) => {
    setClipState((prev) => updater(prev.key === targetKey ? prev : createClipState(targetKey)));
  }, []);

  const clearBudgetTimer = useCallback(() => {
    if (budgetTimerRef.current !== null) {
      clearTimeout(budgetTimerRef.current);
      budgetTimerRef.current = null;
    }
  }, []);

  const markImage = useCallback((url: string, state: StillState) => {
    setImageStates((prev) => (prev[url] === state ? prev : { ...prev, [url]: state }));
  }, []);

  const startBudgetFor = useCallback(
    (targetKey: string) => {
      clearBudgetTimer();
      budgetTimerRef.current = setTimeout(() => {
        budgetTimerRef.current = null;
        if (currentKeyRef.current !== targetKey) return;
        patchClip(targetKey, (prev) => {
          if (
            prev.manual ||
            prev.status === 'playing' ||
            prev.status === 'ended' ||
            prev.status === 'refused' ||
            prev.status === 'still-only'
          ) {
            return prev;
          }
          return {
            ...prev,
            status: 'still-only',
            frameReady: false,
            budgetExpiredAt: Date.now(),
          };
        });
      }, START_BUDGET_MS);
    },
    [clearBudgetTimer, patchClip]
  );

  const attemptPlay = useCallback(
    (video: HTMLVideoElement, targetKey: string, manual: boolean, withBudget: boolean) => {
      video.muted = true;
      if (withBudget && !manual) startBudgetFor(targetKey);
      else clearBudgetTimer();
      playPendingRef.current = true;
      const rejectPlay = (err?: unknown) => {
        playPendingRef.current = false;
        if (currentKeyRef.current !== targetKey) return;
        const errName =
          err && typeof err === 'object' && 'name' in err
            ? String((err as { name?: unknown }).name)
            : '';
        // Client/visibility pause interrupts pending play() with AbortError; never mark as platform refusal.
        if (errName === 'AbortError') return;
        clearBudgetTimer();
        patchClip(targetKey, (prev) =>
          prev.status !== 'still-only' &&
          prev.status !== 'playing' &&
          !prev.visibilityPaused &&
          !prev.visitorPaused
            ? { ...prev, status: 'refused' }
            : prev
        );
      };
      let playPromise: unknown;
      try {
        playPromise = video.play();
      } catch (err) {
        rejectPlay(err);
        return;
      }
      if (playPromise && typeof (playPromise as Promise<unknown>).then === 'function') {
        void (playPromise as Promise<unknown>).then(() => {
          if (currentKeyRef.current === targetKey) playPendingRef.current = false;
        }, rejectPlay);
      } else {
        playPendingRef.current = false;
      }
    },
    [clearBudgetTimer, patchClip, startBudgetFor]
  );

  const bindVideoRef = useCallback((el: HTMLVideoElement | null) => {
    videoRef.current = el;
    if (el) el.muted = true;
  }, []);

  useEffect(() => {
    clipSnapshotRef.current = effectiveClip;
  });

  useEffect(() => {
    currentKeyRef.current = activationKey;
    activatedAtRef.current = Date.now();
    autoStartedRef.current = false;
    playPendingRef.current = false;
    clipSnapshotRef.current = createClipState(activationKey);
    return clearBudgetTimer;
  }, [activationKey, clearBudgetTimer]);

  // Catch synchronously cached/already-complete still images for D-13 sequencing.
  useEffect(() => {
    const pending: [string, StillState][] = [];
    const check = (el: HTMLImageElement | null, url: string | null) => {
      if (
        el &&
        url &&
        el.getAttribute('src') === url &&
        el.complete &&
        el.naturalWidth > 0 &&
        imageStates[url] === undefined
      ) {
        pending.push([url, 'loaded']);
      }
    };
    check(firstImgRef.current, firstStillUrl);
    if (firstStillSettled) check(activeImgRef.current, activeStillUrl);
    if (pending.length === 0) return undefined;
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      for (const [url, state] of pending) {
        markImage(url, state);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [firstStillUrl, firstStillSettled, activeStillUrl, imageStates, markImage]);

  // Auto-start active video clip once still image is loaded and view is visible (D-02, D-13, D-14).
  useEffect(() => {
    if (!shouldMountVideo || autoStartedRef.current || hidden || !onScreen || !tgActive) {
      return;
    }
    const video = videoRef.current;
    if (!video) return;
    autoStartedRef.current = true;
    attemptPlay(video, activationKey, false, true);
  }, [shouldMountVideo, activationKey, hidden, onScreen, tgActive, attemptPlay]);

  const handleVisibilityPause = useCallback(
    (isVisible: boolean) => {
      const key = currentKeyRef.current;
      const snap = clipSnapshotRef.current;
      const video = videoRef.current;
      if (!isVisible) {
        if (
          snap.key === key &&
          (snap.status === 'playing' ||
            playPendingRef.current ||
            (snap.status === 'idle' && autoStartedRef.current && budgetTimerRef.current !== null))
        ) {
          clearBudgetTimer();
          playPendingRef.current = false;
          video?.pause();
          if (snap.status !== 'still-only') {
            clipSnapshotRef.current = { ...snap, status: 'paused', visibilityPaused: true };
          }
          patchClip(key, (prev) =>
            prev.status !== 'still-only' ? { ...prev, status: 'paused', visibilityPaused: true } : prev
          );
        }
        return;
      }
      if (
        snap.key === key &&
        snap.status === 'paused' &&
        snap.visibilityPaused &&
        !snap.visitorPaused &&
        video
      ) {
        clipSnapshotRef.current = { ...snap, visibilityPaused: false };
        patchClip(key, (prev) => ({ ...prev, visibilityPaused: false }));
        attemptPlay(video, key, snap.manual, !snap.frameReady && !snap.manual);
      }
    },
    [attemptPlay, clearBudgetTimer, patchClip]
  );

  // Observe container viewport intersection for D-10 off-screen pause/resume.
  useEffect(() => {
    const el = containerRef.current;
    if (!el || typeof IntersectionObserver === 'undefined') return undefined;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.some((entry) => entry.isIntersecting);
        onScreenRef.current = visible;
        setOnScreen(visible);
        handleVisibilityPause(visible && !document.hidden && tgActiveRef.current);
      },
      { threshold: 0 }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [handleVisibilityPause]);

  // Observe breakpoint, reduced motion, saveData, document visibility, and Telegram visibility.
  useEffect(() => {
    const hasMatchMedia = typeof window !== 'undefined' && typeof window.matchMedia === 'function';
    const wideMedia = hasMatchMedia ? window.matchMedia(WIDE_MEDIA_QUERY) : null;
    const motionMedia = hasMatchMedia ? window.matchMedia(REDUCED_MOTION_QUERY) : null;
    const conn = (navigator as Navigator & { connection?: NetworkConnection }).connection;

    const onWideChange = () => {
      if (wideMedia) setIsWide(wideMedia.matches);
    };
    const onMotionChange = () => {
      if (motionMedia) setReduced(motionMedia.matches);
    };
    const onConnectionChange = () => setSaveData(readSaveData());
    const onVisibilityChange = () => {
      setHidden(document.hidden);
      handleVisibilityPause(!document.hidden && onScreenRef.current && tgActiveRef.current);
    };
    const onTgDeactivated = () => {
      tgActiveRef.current = false;
      setTgActive(false);
      handleVisibilityPause(false);
    };
    const onTgActivated = () => {
      tgActiveRef.current = true;
      setTgActive(true);
      handleVisibilityPause(!document.hidden && onScreenRef.current);
    };

    wideMedia?.addEventListener?.('change', onWideChange);
    motionMedia?.addEventListener?.('change', onMotionChange);
    conn?.addEventListener?.('change', onConnectionChange);
    document.addEventListener('visibilitychange', onVisibilityChange);
    WebApp.onEvent('deactivated', onTgDeactivated);
    WebApp.onEvent('activated', onTgActivated);

    return () => {
      wideMedia?.removeEventListener?.('change', onWideChange);
      motionMedia?.removeEventListener?.('change', onMotionChange);
      conn?.removeEventListener?.('change', onConnectionChange);
      document.removeEventListener('visibilitychange', onVisibilityChange);
      WebApp.offEvent('deactivated', onTgDeactivated);
      WebApp.offEvent('activated', onTgActivated);
    };
  }, [handleVisibilityPause]);

  // Auto-advance timer (D-01, D-05, D-07, D-10, D-14, M-01).
  useEffect(() => {
    if (!rotating || hidden || !tgActive || (isVideoSlide && !onScreen)) {
      return undefined;
    }

    const advanceToNext = () => {
      setCurrentBanner((prev) => (prev + 1) % count);
    };

    const readyToAdvance =
      !isVideoSlide ||
      isEffectiveStillOnly ||
      effectiveClip.status === 'refused' ||
      effectiveClip.status === 'ended';

    if (readyToAdvance) {
      const readyAt = Math.max(
        activatedAtRef.current + intervalMs,
        isEffectiveStillOnly ? (effectiveClip.budgetExpiredAt ?? 0) : 0
      );
      const timer = setTimeout(advanceToNext, Math.max(0, readyAt - Date.now()));
      return () => clearTimeout(timer);
    }

    // While playing, visitor-paused, visibility-paused, or idle awaiting start: do not advance.
    return undefined;
  }, [
    rotating,
    hidden,
    tgActive,
    onScreen,
    safeIndex,
    count,
    intervalMs,
    isVideoSlide,
    isEffectiveStillOnly,
    effectiveClip.status,
    effectiveClip.budgetExpiredAt,
  ]);

  const handleVideoPlaying = () => {
    clearBudgetTimer();
    playPendingRef.current = false;
    const key = currentKeyRef.current;
    patchClip(key, (prev) =>
      prev.status !== 'still-only'
        ? {
            ...prev,
            status: 'playing',
            frameReady: true,
            visitorPaused: false,
            visibilityPaused: false,
          }
        : prev
    );
  };

  const handleVideoEnded = () => {
    clearBudgetTimer();
    playPendingRef.current = false;
    const key = currentKeyRef.current;
    if (count === 1) {
      const video = videoRef.current;
      if (video && !isEffectiveStillOnly) {
        try {
          video.currentTime = 0;
        } catch {
          // Ignore seek errors on unbuffered test media.
        }
        attemptPlay(video, key, true, false);
      }
      return;
    }
    patchClip(key, (prev) =>
      prev.status !== 'still-only'
        ? { ...prev, status: 'ended', frameReady: true, visitorPaused: false }
        : prev
    );
  };

  const handleVideoError = () => {
    clearBudgetTimer();
    playPendingRef.current = false;
    const key = currentKeyRef.current;
    patchClip(key, (prev) => ({ ...prev, status: 'still-only', frameReady: false }));
  };

  const handleVideoControlClick = (e: MouseEvent<HTMLButtonElement>) => {
    e.stopPropagation();
    const video = videoRef.current;
    const key = activationKey;
    if (effectiveClip.status === 'playing') {
      clearBudgetTimer();
      playPendingRef.current = false;
      video?.pause();
      clipSnapshotRef.current = {
        ...effectiveClip,
        status: 'paused',
        visitorPaused: true,
        manual: true,
      };
      patchClip(key, (prev) => ({ ...prev, status: 'paused', visitorPaused: true, manual: true }));
      return;
    }
    if (!video || isEffectiveStillOnly) return;
    if (effectiveClip.status === 'ended') {
      try {
        video.currentTime = 0;
      } catch {
        // Ignore seek errors on unbuffered test media.
      }
    }
    autoStartedRef.current = true;
    clipSnapshotRef.current = {
      ...effectiveClip,
      status: 'idle',
      visitorPaused: false,
      visibilityPaused: false,
      manual: true,
    };
    patchClip(key, (prev) => ({
      ...prev,
      status: 'idle',
      visitorPaused: false,
      visibilityPaused: false,
      manual: true,
    }));
    attemptPlay(video, key, true, false);
  };

  const handleBannerDotClick = (e: MouseEvent<HTMLButtonElement>, index: number) => {
    e.stopPropagation();
    if (index !== safeIndex) {
      setCurrentBanner(index);
    }
  };

  const showVideoControl = isVideoSlide && !isEffectiveStillOnly;
  const videoControlIsPause = effectiveClip.status === 'playing';
  const controlLabel = videoControlIsPause ? t('banners.pauseVideo') : t('banners.playVideo');
  const ctaLabel = t('banners.cta');
  const mediaHeightClass = layoutMode === 'phone' ? 'aspect-[3/2]' : 'h-32 md:h-72 lg:h-72';

  return (
    <div ref={containerRef} data-testid="banner-carousel" data-format={layoutMode} className="relative">
      <div className="grid grid-cols-1">
        {banners.map((banner, i) => {
          const active = i === safeIndex;
          const videoBanner = isVideoBanner(banner);
          const stillSrc = stillUrlFor(banner, layoutMode);
          const clipSrc = clipUrlFor(banner, layoutMode);
          const canLoadStill = i === 0 || firstStillSettled;
          const previousLoadedStill =
            banner.image_mobile_url && imageStates[banner.image_mobile_url] === 'loaded'
              ? banner.image_mobile_url
              : banner.image_url && imageStates[banner.image_url] === 'loaded'
                ? banner.image_url
                : undefined;
          const renderedStillSrc = canLoadStill ? (stillSrc ?? undefined) : previousLoadedStill;
          const stillState = renderedStillSrc ? imageStates[renderedStillSrc] : undefined;
          const copyText = bannerText(banner, language);
          const fallbackTitle = t('banners.defaultTitle', { index: i + 1 });
          const imageAltTitle =
            language === 'uz' && banner.title_uz?.trim()
              ? banner.title_uz.trim()
              : banner.title.trim();
          const accessibleName = videoBanner
            ? copyText.title || fallbackTitle
            : imageAltTitle || fallbackTitle;

          const imgRef =
            i === 0 && active
              ? (el: HTMLImageElement | null) => {
                  firstImgRef.current = el;
                  activeImgRef.current = el;
                }
              : i === 0
                ? (el: HTMLImageElement | null) => {
                    firstImgRef.current = el;
                  }
                : active
                  ? (el: HTMLImageElement | null) => {
                      activeImgRef.current = el;
                    }
                  : undefined;

          return (
            <motion.div
              key={banner.id}
              data-banner-id={banner.id}
              data-active={active ? 'true' : 'false'}
              role="link"
              tabIndex={active ? 0 : -1}
              aria-label={accessibleName}
              aria-hidden={!active || undefined}
              initial={false}
              animate={{
                opacity: active ? 1 : 0,
                zIndex: active ? 1 : 0,
              }}
              transition={{ duration: reduced ? 0 : 0.5 }}
              className={cn(
                'col-start-1 row-start-1 cursor-pointer select-none',
                !active && 'pointer-events-none'
              )}
              onClick={() => onBannerClick(banner.link_url)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onBannerClick(banner.link_url);
                }
              }}
            >
              <div className={cn('relative w-full overflow-hidden rounded-md', mediaHeightClass)}>
                {/* Skeleton while image loads */}
                {stillSrc && stillState !== 'loaded' && (
                  <div className="absolute inset-0 bg-[var(--bg-tertiary)] animate-pulse" />
                )}

                {/* Gradient fallback when still image is absent or not yet assigned */}
                {!renderedStillSrc && (
                  <div
                    className="absolute inset-0 bg-center md:bg-bottom bg-cover"
                    style={{ backgroundImage: banner.gradient || undefined }}
                  />
                )}

                {/* Still image */}
                {stillSrc && (
                  <img
                    ref={imgRef}
                    src={renderedStillSrc}
                    alt={videoBanner ? '' : accessibleName}
                    draggable={false}
                    fetchPriority={i === 0 ? 'high' : 'auto'}
                    className={cn(
                      'absolute inset-0 w-full h-full object-cover',
                      layoutMode === 'phone' ? 'object-center' : 'object-center md:object-bottom',
                      !renderedStillSrc && 'hidden'
                    )}
                    onLoad={() => renderedStillSrc && markImage(renderedStillSrc, 'loaded')}
                    onError={() => renderedStillSrc && markImage(renderedStillSrc, 'error')}
                  />
                )}

                {/* Active slide muted inline clip (D-02, D-04) */}
                {active && shouldMountVideo && clipSrc && (
                  <video
                    key={activationKey}
                    ref={bindVideoRef}
                    src={clipSrc}
                    poster={stillSrc ?? undefined}
                    muted
                    playsInline
                    loop={count === 1}
                    preload="auto"
                    aria-hidden="true"
                    tabIndex={-1}
                    className={cn(
                      'absolute inset-0 w-full h-full object-cover transition-opacity duration-150',
                      effectiveClip.frameReady ? 'opacity-100' : 'opacity-0 pointer-events-none'
                    )}
                    onPlaying={handleVideoPlaying}
                    onEnded={handleVideoEnded}
                    onError={handleVideoError}
                  />
                )}

                {/* Scrim and overlay text on video banners in wide and phone format (D-08, D-15, CD-27) */}
                {videoBanner && layoutMode !== 'fallback' && (
                  <>
                    <div
                      aria-hidden="true"
                      className={cn(
                        'pointer-events-none absolute inset-0 z-[1]',
                        layoutMode === 'phone'
                          ? 'bg-[linear-gradient(to_top,rgb(0_0_0/0.6)_0%,transparent_55%)]'
                          : 'bg-[linear-gradient(to_right,rgb(0_0_0/0.55)_0%,transparent_55%)]'
                      )}
                    />
                    <div
                      className={cn(
                        'pointer-events-none absolute z-[2] text-white flex',
                        layoutMode === 'phone'
                          ? 'inset-x-4 bottom-7 top-auto flex-row items-end justify-between gap-3'
                          : 'inset-0 flex-col justify-center items-start pl-[6%] pr-[55%] gap-2'
                      )}
                    >
                      <div
                        className={cn(
                          'flex flex-col gap-1',
                          layoutMode === 'phone' ? 'flex-1 min-w-0 max-w-[70%]' : 'max-w-full'
                        )}
                      >
                        {copyText.title && (
                          <span
                            className={cn(
                              'font-bold line-clamp-2 [overflow-wrap:anywhere] [text-shadow:0_1px_2px_rgb(0_0_0/0.3)]',
                              layoutMode === 'phone'
                                ? 'text-[18px] leading-[22px]'
                                : 'text-[28px] leading-[34px]'
                            )}
                          >
                            {copyText.title}
                          </span>
                        )}
                        {copyText.subtitle && (
                          <span
                            className={cn(
                              'font-normal line-clamp-2 [overflow-wrap:anywhere] [text-shadow:0_1px_2px_rgb(0_0_0/0.25)]',
                              layoutMode === 'phone'
                                ? 'text-[13px] leading-[17px]'
                                : 'text-[16px] leading-[22px]'
                            )}
                          >
                            {copyText.subtitle}
                          </span>
                        )}
                      </div>
                      <span
                        aria-hidden="true"
                        className={cn(
                          'inline-flex items-center justify-center shrink-0 rounded-full bg-white text-[var(--color-primary)] leading-none font-bold shadow-[0_1px_4px_rgb(0_0_0/0.2)]',
                          layoutMode === 'phone'
                            ? 'h-7 px-3 text-[13px]'
                            : 'h-9 px-4 mt-1.5 text-[14px]'
                        )}
                      >
                        {ctaLabel}
                      </span>
                    </div>
                  </>
                )}
              </div>

              {/* Equal-height reserved below-media text area in 3:1 fallback (D-08, CD-27) */}
              {layoutMode === 'fallback' && hasVideoBanner && (
                <div
                  data-testid="banner-fallback-below"
                  aria-hidden={!videoBanner || undefined}
                  className="box-border h-[92px] pt-[10px] px-[2px] pb-[4px] flex flex-col gap-1 overflow-hidden text-[var(--text-primary)]"
                >
                  {videoBanner && (
                    <>
                      {copyText.title && (
                        <span className="text-[16px] leading-[20px] font-bold line-clamp-2 [overflow-wrap:anywhere]">
                          {copyText.title}
                        </span>
                      )}
                      {copyText.subtitle && (
                        <span className="text-[13px] leading-[17px] font-normal line-clamp-2 [overflow-wrap:anywhere] text-[var(--text-muted)]">
                          {copyText.subtitle}
                        </span>
                      )}
                    </>
                  )}
                </div>
              )}
            </motion.div>
          );
        })}
      </div>

      {/* Sibling controls overlay sized only to the media frame so buttons never trigger banner click */}
      {(showVideoControl || rotating) && (
        <div
          className={cn(
            'pointer-events-none absolute inset-x-0 top-0 z-20',
            mediaHeightClass
          )}
        >
          {showVideoControl && (
            <button
              type="button"
              aria-label={controlLabel}
              title={controlLabel}
              onClick={handleVideoControlClick}
              className="pointer-events-auto absolute top-1.5 right-1.5 md:top-3 md:right-3 w-8 h-7 rounded-lg border border-white/80 bg-[var(--color-primary)] text-white shadow-[0_1px_4px_rgb(0_0_0/0.25)] flex items-center justify-center"
            >
              {videoControlIsPause ? (
                <Pause size={14} strokeWidth={2} fill="currentColor" aria-hidden="true" />
              ) : (
                <Play size={14} strokeWidth={2} fill="currentColor" aria-hidden="true" />
              )}
            </button>
          )}

          {rotating && (
            <div className="pointer-events-auto absolute bottom-3 left-1/2 -translate-x-1/2 flex gap-2">
              {banners.map((banner, i) => (
                <button
                  key={banner.id}
                  type="button"
                  aria-label={t('banners.slideLabel', { index: i + 1, total: count })}
                  aria-current={i === safeIndex ? 'true' : undefined}
                  onClick={(e) => handleBannerDotClick(e, i)}
                  className={cn(
                    'w-2 h-2 rounded-full transition-colors',
                    i === safeIndex ? 'bg-white' : 'bg-white/40'
                  )}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
