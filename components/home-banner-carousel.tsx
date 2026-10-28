"use client";

import { ChevronDown, ChevronUp, Pause, Play } from "lucide-react";
import {
  type FocusEvent,
  type TouchEvent,
  useEffect,
  useRef,
  useState,
} from "react";

const SLIDES = [
  {
    desktopSrc:
      "/homepage-banners/polaris-home-banner-01-v12-fresh-1920x240.png",
    mobileSrc: "/homepage-banners/polaris-mobile-banner-01-v1-750x300.png",
    alt: "海宇绘品，懂电商的全品类出图工作台",
  },
  {
    desktopSrc:
      "/homepage-banners/polaris-home-banner-02-v12-fresh-1920x240.png",
    mobileSrc: "/homepage-banners/polaris-mobile-banner-02-v1-750x300.png",
    alt: "姿态任选，场景随心换，有效降低出图与换景成本",
  },
] as const;

const AUTOPLAY_INTERVAL_MS = 5_000;
const TRANSITION_DURATION_MS = 550;
const SWIPE_THRESHOLD_PX = 40;

export function HomeBannerCarousel() {
  const [activeIndex, setActiveIndex] = useState(0);
  const [interactionPaused, setInteractionPaused] = useState(false);
  const [userPaused, setUserPaused] = useState(false);
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false);
  const touchStart = useRef<{ x: number; y: number } | null>(null);

  const autoplayPaused =
    interactionPaused || userPaused || prefersReducedMotion;

  useEffect(() => {
    const mediaQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    const updatePreference = () => setPrefersReducedMotion(mediaQuery.matches);

    updatePreference();
    mediaQuery.addEventListener?.("change", updatePreference);
    return () => mediaQuery.removeEventListener?.("change", updatePreference);
  }, []);

  useEffect(() => {
    if (autoplayPaused || SLIDES.length < 2) return;

    const interval = window.setInterval(() => {
      setActiveIndex((current) => (current + 1) % SLIDES.length);
    }, AUTOPLAY_INTERVAL_MS);

    return () => window.clearInterval(interval);
  }, [autoplayPaused]);

  const showPrevious = () => {
    setActiveIndex((current) =>
      current === 0 ? SLIDES.length - 1 : current - 1,
    );
  };

  const showNext = () => {
    setActiveIndex((current) => (current + 1) % SLIDES.length);
  };

  const handleBlur = (event: FocusEvent<HTMLElement>) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
      setInteractionPaused(false);
    }
  };

  const handleTouchStart = (event: TouchEvent<HTMLElement>) => {
    const touch = event.touches[0];
    if (!touch) return;
    touchStart.current = { x: touch.clientX, y: touch.clientY };
    setInteractionPaused(true);
  };

  const handleTouchEnd = (event: TouchEvent<HTMLElement>) => {
    const start = touchStart.current;
    const touch = event.changedTouches[0];
    touchStart.current = null;
    setInteractionPaused(false);
    if (!start || !touch) return;

    const deltaX = touch.clientX - start.x;
    const deltaY = touch.clientY - start.y;
    const primaryDelta = Math.abs(deltaY) >= Math.abs(deltaX) ? deltaY : deltaX;

    if (Math.abs(primaryDelta) < SWIPE_THRESHOLD_PX) return;
    if (primaryDelta > 0) showPrevious();
    else showNext();
  };

  return (
    <section
      className={`home-banner-carousel${
        prefersReducedMotion ? " is-reduced-motion" : ""
      }`}
      aria-label="首页推荐内容"
      aria-roledescription="轮播图"
      data-active-index={activeIndex}
      data-autoplay-interval={AUTOPLAY_INTERVAL_MS}
      data-transition-duration={TRANSITION_DURATION_MS}
      onMouseEnter={() => setInteractionPaused(true)}
      onMouseLeave={() => setInteractionPaused(false)}
      onFocusCapture={() => setInteractionPaused(true)}
      onBlurCapture={handleBlur}
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
    >
      <div className="home-banner-carousel__viewport">
        <div className="home-banner-carousel__track" aria-live="off">
          {SLIDES.map((slide, index) => {
            const active = index === activeIndex;
            return (
              <div
                className={`home-banner-carousel__slide${
                  active ? " is-active" : ""
                }`}
                role="group"
                aria-roledescription="幻灯片"
                aria-label={`第 ${index + 1} 张，共 ${SLIDES.length} 张`}
                aria-hidden={!active}
                data-slide-index={index}
                key={slide.desktopSrc}
              >
                <picture>
                  <source
                    media="(max-width: 720px)"
                    srcSet={slide.mobileSrc}
                    width="750"
                    height="300"
                  />
                  <source
                    media="(min-width: 721px)"
                    srcSet={slide.desktopSrc}
                    width="1920"
                    height="240"
                  />
                  <img
                    src={slide.desktopSrc}
                    alt={slide.alt}
                    width="1920"
                    height="240"
                    loading={index === 0 ? "eager" : "lazy"}
                    fetchPriority={index === 0 ? "high" : "auto"}
                    decoding="async"
                  />
                </picture>
              </div>
            );
          })}
        </div>
      </div>

      <div className="home-banner-carousel__controls">
        <button
          type="button"
          className="home-banner-carousel__arrow home-banner-carousel__arrow--previous"
          onClick={showPrevious}
          aria-label="查看上一张横幅"
        >
          <ChevronUp size={18} aria-hidden="true" />
        </button>
        <div className="home-banner-carousel__indicators" aria-label="选择横幅">
          {SLIDES.map((slide, index) => (
            <button
              type="button"
              className={`home-banner-carousel__indicator${
                index === activeIndex ? " is-active" : ""
              }`}
              onClick={() => setActiveIndex(index)}
              aria-label={`查看第 ${index + 1} 张横幅`}
              aria-current={index === activeIndex ? "true" : undefined}
              key={slide.desktopSrc}
            />
          ))}
        </div>
        <button
          type="button"
          className="home-banner-carousel__arrow home-banner-carousel__arrow--next"
          onClick={showNext}
          aria-label="查看下一张横幅"
        >
          <ChevronDown size={18} aria-hidden="true" />
        </button>
        <button
          type="button"
          className="home-banner-carousel__pause"
          onClick={() => setUserPaused((paused) => !paused)}
          aria-label={userPaused ? "继续自动轮播" : "暂停自动轮播"}
          aria-pressed={userPaused}
        >
          {userPaused ? (
            <Play size={16} aria-hidden="true" />
          ) : (
            <Pause size={16} aria-hidden="true" />
          )}
        </button>
      </div>
    </section>
  );
}
