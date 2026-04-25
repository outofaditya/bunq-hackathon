import { useEffect, useState } from "react";
import type { BusEvent } from "@/lib/types";

type Status = "connecting" | "live" | "reconnecting";

export function useEventBus(onEvent: (ev: BusEvent) => void) {
  const [status, setStatus] = useState<Status>("connecting");

  useEffect(() => {
    const es = new EventSource("/events");
    es.onopen = () => setStatus("live");
    es.onerror = () => setStatus("reconnecting");
    es.onmessage = (e) => {
      try {
        const ev: BusEvent = JSON.parse(e.data);
        onEvent(ev);
      } catch {
        /* malformed */
      }
    };
    return () => es.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return status;
}
