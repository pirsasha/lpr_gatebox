# CloudPub API contract (RU)

Документ фиксирует рабочий контракт CloudPub API для UI/оператора.

## GET `/api/v1/cloudpub/status`

### Назначение
Вернуть текущее состояние удалённого доступа CloudPub, ссылки и короткий audit trail.

### Основные поля ответа
- `ok: boolean` — ответ endpoint.
- `enabled: boolean` — включён ли CloudPub в settings.
- `configured: boolean` — заполнены ли обязательные поля (`server_ip`, `access_key`).
- `connected: boolean` — внутренний флаг активного подключения.
- `connection_state: "online" | "offline" | "sdk_pending" | "disabled"` — нормализованное состояние для UI.
- `state_reason: string` — причина/контекст состояния:
  - `connected`
  - `disconnected`
  - `simulation_mode`
  - `cloudpub_disabled`
  - `cloudpub_not_configured`
- `server_ip: string`
- `target: string`
- `management_url: string`
- `public_url: string`
- `mode: "simulation" | "sdk"`
- `simulation: boolean`
- `last_ok_ts: number` — unix ts последнего успешного подключения.
- `last_error: string`
- `audit: Array<{ ts:number, action:string, ok:boolean, note:string, target:string }>` — последние события.

### Примеры

#### 1) CloudPub выключен
```json
{
  "ok": true,
  "enabled": false,
  "configured": false,
  "connected": false,
  "connection_state": "disabled",
  "state_reason": "cloudpub_disabled",
  "server_ip": "",
  "target": "",
  "management_url": "",
  "public_url": "",
  "mode": "simulation",
  "simulation": true,
  "last_ok_ts": 0,
  "last_error": "",
  "audit": []
}
```

#### 2) Включен, но не настроен
```json
{
  "ok": true,
  "enabled": true,
  "configured": false,
  "connected": false,
  "connection_state": "offline",
  "state_reason": "cloudpub_not_configured",
  "server_ip": "",
  "target": "",
  "management_url": "",
  "public_url": "",
  "mode": "simulation",
  "simulation": true,
  "last_ok_ts": 0,
  "last_error": "",
  "audit": []
}
```

#### 3) Подключен в simulation
```json
{
  "ok": true,
  "enabled": true,
  "configured": true,
  "connected": true,
  "connection_state": "sdk_pending",
  "state_reason": "simulation_mode",
  "server_ip": "10.0.0.5",
  "target": "10.0.0.5",
  "management_url": "http://10.0.0.5",
  "public_url": "http://10.0.0.5",
  "mode": "simulation",
  "simulation": true,
  "last_ok_ts": 1760000000,
  "last_error": "",
  "audit": [
    {"ts": 1760000000, "action": "connect", "ok": true, "note": "simulation", "target": "10.0.0.5"}
  ]
}
```

## POST `/api/v1/cloudpub/connect`

- При успехе возвращает `ok=true`, `connected=true`, а также нормализованные `connection_state` + `state_reason`.
- При ошибках конфигурации/флага возвращает `ok=false` и `error` (`cloudpub_disabled` или `cloudpub_not_configured`).

## POST `/api/v1/cloudpub/disconnect`

- Возвращает `ok=true`, `connected=false`, `connection_state="offline"`, `state_reason="disconnected"`.
