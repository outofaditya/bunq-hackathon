import { useEffect, useRef } from "react";

interface Props {
  analyser: AnalyserNode | null;
  active: boolean;
}

/** Mirror-rendered frequency meter — flares from the center out. */
export function MicMeter({ analyser, active }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (!active || !analyser) {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
      return;
    }
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const bins = analyser.frequencyBinCount;
    const data = new Uint8Array(bins);

    const draw = () => {
      analyser.getByteFrequencyData(data);
      const W = canvas.width;
      const H = canvas.height;
      ctx.clearRect(0, 0, W, H);
      const slice = Math.floor(bins / 2);
      const barW = (W / slice) * 0.7;
      const gap = (W / slice) * 0.3;
      ctx.fillStyle = "#5B8F6E";
      for (let i = 0; i < slice; i++) {
        const v = data[i] / 255;
        const h = Math.max(2, v * H * 0.95);
        const y = (H - h) / 2;
        const x  = W / 2 + i * (barW + gap);
        const x2 = W / 2 - (i + 1) * (barW + gap);
        ctx.fillRect(x, y, barW, h);
        ctx.fillRect(x2, y, barW, h);
      }
      rafRef.current = requestAnimationFrame(draw);
    };
    draw();
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [analyser, active]);

  return (
    <canvas
      ref={canvasRef}
      width={640}
      height={60}
      className="w-full h-[60px] rounded-md bg-paper-900 border border-border"
    />
  );
}
