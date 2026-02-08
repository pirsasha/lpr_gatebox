// ui/src/hooks/useEventsStream.ts
// Подписка на SSE события ("как у взрослых")

import { useEffect, useMemo, useRef, useState } from "react";
import { eventsStreamUrl, getEvents } from "../api";

export type UiEvent = {
  ts: number;
  plate: string;
  raw?: string;
  conf?: number;
  status?: string;
  message?: string;
  level?: string;
  meta?: any;
};

function keyOf(e: UiEvent) {
  // устойчивый ключ, чтобы не было дублей при реконнекте
  return `${e.ts}|${e.plate || ""}|${e.raw || ""}|${e.status || ""}`;
}

export function useEventsStream(opts: {
  includeDebug: boolean;
  limit?: number;
}) {
  const limit = Math.max(10, Math.min(500, opts.limit || 100));
  const includeDebug = !!opts.includeDebug;

  const [items, setItems] = useState<UiEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const seen = useRef<Set<string>>(new Set());
  const lastTs = useRef<number>(0);

  const streamUrl = useMemo(() => eventsStreamUrl({ include_debug: includeDebug }), [includeDebug]);

  // 1) первичная загрузка, чтобы UI был не пустой при открытии
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await getEvents(Math.min(200, limit), { include_debug: includeDebug });
        if (!alive) return;
        const list = Array.isArray(r?.items) ? r.items : [];
        // newest-first -> сделаем старые -> новые
        const ordered = [...list].sort((a: any, b: any) => (a.ts || 0) - (b.ts || 0));
        const next: UiEvent[] = [];
        seen.current.clear();
        lastTs.current = 0;
        for (const e of ordered) {
          const ev = e as UiEvent;
          const k = keyOf(ev);
          if (seen.current.has(k)) continue;
          seen.current.add(k);
          next.push(ev);
          lastTs.current = Math.max(lastTs.current, Number(ev.ts || 0));
        }
        // храним newest-first
        setItems(next.slice(-limit).reverse());
        setErr(null);
      } catch (e: any) {
        if (!alive) return;
        setErr(e?.message || String(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, [includeDebug, limit]);

  // 2) SSE: новые события "прилетает само"
  useEffect(() => {
    const es = new EventSource(streamUrl);
    setConnected(false);
    setErr(null);

    es.onopen = () => {
      setConnected(true);
      setErr(null);
    };

    es.onerror = () => {
      // браузер сам переподключается; мы просто показываем статус
      setConnected(false);
    };

    const onEvent = (ev: MessageEvent) => {
      try {
        const obj = JSON.parse(ev.data) as UiEvent;
        const k = keyOf(obj);
        if (seen.current.has(k)) return;
        seen.current.add(k);
        lastTs.current = Math.max(lastTs.current, Number(obj.ts || 0));
        setItems((prev) => {
          const next = [obj, ...prev];
          return next.slice(0, limit);
        });
      } catch {
        // ignore bad payload
      }
    };

    es.addEventListener("event", onEvent as any);

    return () => {
      es.removeEventListener("event", onEvent as any);
      es.close();
    };
  }, [streamUrl, limit]);

  return { items, connected, error: err };
}
