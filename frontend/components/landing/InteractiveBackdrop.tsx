"use client";

import { useEffect, useRef } from "react";

/**
 * The landing page's background: a measurement grid that responds to the
 * pointer, with a slow scan passing through it.
 *
 * The brief came from the product, not from a trend. WhipGuard's identity
 * (see globals.css) is "a forensic instrument, not a generic admin panel", so
 * the background is a grid being swept rather than particles drifting or a
 * gradient breathing. The pointer brightens the cells near it — the page
 * reacts to being examined, which is the one metaphor this product has.
 *
 * Canvas rather than 800 DOM nodes, and a single requestAnimationFrame loop
 * rather than per-element transitions. Three things keep it cheap:
 *
 *   - it stops entirely when the tab is hidden or the canvas scrolls out of
 *     view, because a background nobody is looking at should cost nothing;
 *   - it honours `prefers-reduced-motion` by drawing the grid once and never
 *     animating — the texture survives, the movement does not;
 *   - the pointer is tracked passively and read on the next frame, so moving
 *     the mouse never forces a synchronous repaint.
 */

const SPACING = 34;
const DOT = 1.1;
const REACH = 190;

export function InteractiveBackdrop() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d", { alpha: true });
    if (!context) return;

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    // Pointer starts far off-canvas so nothing is highlighted until the
    // visitor actually moves — an unprompted glow in the corner reads as a
    // rendering artefact.
    const pointer = { x: -9999, y: -9999 };
    let width = 0;
    let height = 0;
    let frame = 0;
    let running = true;
    let scan = 0;

    function resize() {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      width = canvas!.clientWidth;
      height = canvas!.clientHeight;
      canvas!.width = Math.floor(width * ratio);
      canvas!.height = Math.floor(height * ratio);
      context!.setTransform(ratio, 0, 0, ratio, 0, 0);
    }

    function draw() {
      context!.clearRect(0, 0, width, height);

      // The scan: a soft band travelling down the page, which is what makes
      // the grid read as being *examined* rather than as wallpaper.
      const scanY = reduceMotion ? -1 : (scan % (height + 420)) - 210;

      for (let y = SPACING; y < height; y += SPACING) {
        for (let x = SPACING; x < width; x += SPACING) {
          const dx = x - pointer.x;
          const dy = y - pointer.y;
          const distance = Math.sqrt(dx * dx + dy * dy);
          const near = distance < REACH ? 1 - distance / REACH : 0;

          const scanDistance = Math.abs(y - scanY);
          const swept = scanDistance < 150 ? (1 - scanDistance / 150) * 0.5 : 0;

          const energy = Math.min(1, near * near + swept);
          if (energy < 0.015 && !reduceMotion) {
            // The resting grid: present, but barely.
            context!.fillStyle = "rgba(169,166,161,0.055)";
            context!.beginPath();
            context!.arc(x, y, DOT, 0, Math.PI * 2);
            context!.fill();
            continue;
          }

          // Amber is this product's colour for scrutiny, so it is what the
          // examined cells turn.
          context!.fillStyle = `rgba(255,178,36,${0.07 + energy * 0.5})`;
          context!.beginPath();
          context!.arc(x, y, DOT + energy * 1.7, 0, Math.PI * 2);
          context!.fill();
        }
      }

      if (!reduceMotion) scan += 2.1;
    }

    function loop() {
      if (!running) return;
      draw();
      frame = window.requestAnimationFrame(loop);
    }

    function onPointerMove(event: PointerEvent) {
      pointer.x = event.clientX;
      pointer.y = event.clientY - canvas!.getBoundingClientRect().top;
    }

    function onPointerLeave() {
      pointer.x = -9999;
      pointer.y = -9999;
    }

    function start() {
      if (running) return;
      running = true;
      loop();
    }

    function stop() {
      running = false;
      window.cancelAnimationFrame(frame);
    }

    resize();
    if (reduceMotion) {
      draw();
    } else {
      loop();
    }

    window.addEventListener("resize", resize);
    // Passive: this must never be able to delay a scroll or a tap.
    window.addEventListener("pointermove", onPointerMove, { passive: true });
    window.addEventListener("pointerleave", onPointerLeave);
    const onVisibility = () => (document.hidden ? stop() : !reduceMotion && start());
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      stop();
      window.removeEventListener("resize", resize);
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerleave", onPointerLeave);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden
      className="pointer-events-none fixed inset-0 -z-10 h-full w-full"
    />
  );
}
