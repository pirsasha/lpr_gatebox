// ui/src/hooks/useEventsStream.ts
import { useEffect, useMemo, useRef, useState } from "react";
import { getEvents, eventsStreamUrl } from "../api";

export type EventItem = {
  ts: number;
  plate: string;
  raw?: string;
  conf?: number;
  status?: string;
  message?: string;
  level?: "info" | "debug";
  meta?: any;
};

type Opts = {
  includeDebug?: boolean;
  limit?: number;
  /** fallback polling interval (ms) */
  pollMs?: number;
};

export function useEventsStream(opts?: Opts) {
  const includeDebug = !!opts?.includeDebug;
  const limit = opts?.limit ?? 40;
  const pollMs = opts?.pollMs ?? 1000;

  const [items, setItems] = useState<EventItem[]>([]);
  const [connected, setConnected] = useState(false); // это именно SSE connected
  const [error, setError] = useState<string | null>(null);

  // чтобы не дублировать элементы при реконнектах/поллинге
  const seen = useRef<Set<string>>(new Set());

  // чтобы фетчить только новые записи
  const lastTsRef = useRef<number>(0);

  const url = useMemo(() => eventsStreamUrl({ include_debug: includeDebug }), [includeDebug]);

  function keyOf(it: EventItem) {
    // ts может совпасть, поэтому добавляем plate/status/conf для устойчивости
    return `${it.ts}|${it.plate || ""}|${it.status || ""}|${it.conf ?? ""}|${it.message || ""}`;
  }

  function ingest(list: EventItem[]) {
    if (!Array.isArray(list) || list.length === 0) return;

    setItems((prev) => {
      let out = prev.slice();
      for (const it of list) {
        if (!it || typeof it.ts !== "number") continue;
        const k = keyOf(it);
        if (seen.current.has(k)) continue;
        seen.current.add(k);
        out.unshift(it);
        if (it.ts > lastTsRef.current) lastTsRef.current = it.ts;
      }
      if (out.length > limit) out = out.slice(0, limit);
      return out;
    });
  }

  // 1) первичная загрузка (как было)
  useEffect(() => {
    let dead = false;

    (async () => {
      try {
        const res = await getEvents({ limit, include_debug: includeDebug });
        if (dead) return;
        const list: EventItem[] = (res?.items || []).slice().reverse(); // чтобы ingest добавлял "сверху" корректно
        // сброс состояния при смене includeDebug/limit
        seen.current = new Set();
        lastTsRef.current = 0;
        setItems([]);
        ingest(list);
        setError(null);
      } catch (e: any) {
        if (dead) return;
        setError(e?.message || String(e));
      }
    })();

    return () => {
      dead = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [includeDebug, limit]);

  // 2) SSE (как было)
  useEffect(() => {
    let es: EventSource | null = null;
    let dead = false;

    try {
      es = new EventSource(url, { withCredentials: true });

      es.onopen = () => {
        if (dead) return;
        setConnected(true);
        setError(null);
      };

      es.onerror = () => {
        // ВАЖНО: EventSource сам будет реконнектиться.
        // Мы просто показываем статус "нет связи", но не закрываем вручную,
        // иначе теряется авто-reconnect в некоторых браузерах/прокси.
        if (dead) return;
        setConnected(false);
      };

      es.addEventListener("event", (e: MessageEvent) => {
        if (dead) return;
        try {
          const it = JSON.parse(e.data) as EventItem;
          ingest([it]);
        } catch (err) {
          // не валим поток из-за одного битого сообщения
          // eslint-disable-next-line no-console
          console.warn("bad event payload", err);
        }
      });
    } catch (e: any) {
      if (!dead) setError(e?.message || String(e));
      setConnected(false);
    }

    return () => {
      dead = true;
      try {
        es?.close();
      } catch {}
      es = null;
      setConnected(false);
    };
  }, [url]);

  // 3) Fallback polling: если SSE умирает/режется прокси — UI всё равно живёт.
  useEffect(() => {
    let dead = false;

    const tick = async () => {
      if (dead) return;

      // не спамим сеть, когда вкладка свернута
      if (typeof document !== "undefined" && document.hidden) return;

      const after_ts = lastTsRef.current || undefined;

      try {
        const res = await getEvents({ limit: 200, after_ts, include_debug: includeDebug });
        if (dead) return;
        const list: EventItem[] = (res?.items || []);
        // api может вернуть уже в нужном порядке — нам не важно, ingest всё нормализует
        ingest(list);
        // polling не должен затирать SSE-ошибки, но если сеть ок — уберём текст ошибки
        setError((prev) => prev);
      } catch (e: any) {
        // поллинг тихо терпим — UI не должен превращаться в красную панель
        // но один текст ошибки оставить можно, если хочешь:
        // setError(e?.message || String(e));
      }
    };

    // старт сразу + интервал
    tick();
    const t = window.setInterval(tick, Math.max(500, pollMs));

    return () => {
      dead = true;
      window.clearInterval(t);
    };
  }, [includeDebug, pollMs]);

  return { items, connected, error };
}