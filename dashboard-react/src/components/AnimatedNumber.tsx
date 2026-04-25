import { useEffect, useRef, useState } from "react";

/** Eased animation between number values; uses cubic ease-out over `duration` ms. */
export function AnimatedNumber({
  value,
  format,
  duration = 700,
  className,
}: {
  value: number;
  format: (n: number) => string;
  duration?: number;
  className?: string;
}) {
  const [display, setDisplay] = useState<number>(value);
  const fromRef = useRef<number>(value);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    const start = fromRef.current;
    const delta = value - start;
    if (Math.abs(delta) < 0.005) {
      setDisplay(value);
      fromRef.current = value;
      return;
    }
    const t0 = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - t0) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      const v = start + delta * eased;
      setDisplay(v);
      if (t < 1) rafRef.current = requestAnimationFrame(tick);
      else { fromRef.current = value; }
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, [value, duration]);

  return <span className={className}>{format(display)}</span>;
}
