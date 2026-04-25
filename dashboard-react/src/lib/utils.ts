import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fmtEur(v: number | string | null | undefined): string {
  if (v === null || v === undefined || v === "") return "€--";
  const n = Number(v);
  if (Number.isNaN(n)) return "€--";
  return "€" + n.toLocaleString("en-EU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function fmtTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}
