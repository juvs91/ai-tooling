# ADR-0050: Fix — `ProxyMetrics.record()` llamado con la API equivocada en `cleanup_expired_sessions()`

- **Estado:** Aceptado
- **Fecha:** 2026-08-12
- **Autor:** jeguzman

---

## Contexto

El log del proxy reportaba, cada hora:
```
WARNING:server:[session] Hourly cache cleanup failed: ProxyMetrics.record() takes 2 positional arguments but 3 were given
```

**Causa raíz confirmada leyendo el código:**
- `utils/metrics.py:90` — `def record(self, log: RequestLog):` acepta un único argumento
  posicional (`log`), pensado exclusivamente para instancias de `RequestLog` (un request
  completo: tokens, latencia, provider, etc.).
- `llm/session/lifecycle.py:91` — `metrics.record("sessions_cleaned", len(expired_sessions))`
  llama a `record()` con un `str` y un `int`. No es un error de aridad menor: es un uso de la
  API completamente distinto al que `record()` está diseñado para aceptar.
- El propio docstring de `lifecycle.py` (líneas 4-5) ya documentaba esto como bug conocido:
  *"Split out of llm/compressor.py — ADR-0032. Pure code motion; see the ADR-0032 plan/summary
  for a pre-existing bug noted (not fixed) during the move."* — el bug existía antes de
  ADR-0032 y sobrevivió intacto a la migración de código.
- `sessions_cleaned` no existe como atributo/contador en `ProxyMetrics`, ni se referencia en
  ningún otro archivo del proyecto — nunca estuvo cableado a `get_stats()` ni a ningún endpoint.
- **Sin cobertura de tests**: `tests/test_metrics.py` no cubre `sessions_cleaned` ni
  `cleanup_expired_sessions()` — por eso el bug llegó a producción y sobrevivió sin que ningún
  test lo detectara.

**Patrón existente en el propio código** para contadores simples de `ProxyMetrics`: se declaran
en `__init__` como `int = 0` y se incrementan directamente desde el punto que los dispara —
ejemplo, `compression_cache_hits`/`compression_cache_misses`, incrementados en
`llm/session/lifecycle.py` (`metrics.compression_cache_hits += 1`) sin pasar por `.record()`.

## Decisión

Agregar un contador dedicado `sessions_cleaned` a `ProxyMetrics`, siguiendo el mismo patrón que
`compression_cache_hits`, en vez de forzar el uso incorrecto de `record()`:

```python
# utils/metrics.py — en __init__, junto a los demás contadores de sesión
self.sessions_cleaned: int = 0
```

```python
# llm/session/lifecycle.py:91 — antes:
metrics.record("sessions_cleaned", len(expired_sessions))
# después:
metrics.sessions_cleaned += len(expired_sessions)
```

Se actualiza además el docstring de `lifecycle.py` para quitar la mención al bug ya resuelto.

### Lo que NO cambia

- No se modifica la firma de `record()` — sigue siendo específica para `RequestLog`. Generalizarla
  para aceptar pares `(nombre, valor)` mezclaría dos responsabilidades distintas (logging de
  requests vs. contadores agregados) sin necesidad real.
- No se expone `sessions_cleaned` en `get_stats()`/endpoint de stats en este fix — es un
  contador interno; exponerlo es una decisión de producto aparte, no parte de este bugfix.
- No se agrega infraestructura de test de integración para `cleanup_expired_sessions()` en sí
  (requeriría mockear `_session_cache`/`_state_lock`) — el test agregado cubre el contador de
  `ProxyMetrics`, que es donde vivía el bug real.

## Consecuencias

### Positivas
- Elimina el warning recurrente cada hora en los logs del proxy.
- El conteo de sesiones limpiadas por ciclo ahora se acumula correctamente en memoria (aunque
  no esté expuesto en un endpoint todavía).
- Test de regresión agregado en `tests/test_metrics.py`.

### Negativas / Costos
- Ninguna — cambio aditivo (nuevo atributo con default `0`), no rompe ningún consumidor
  existente de `ProxyMetrics`.

## Implementación

Archivos modificados: `utils/metrics.py` (nuevo contador `sessions_cleaned`),
`llm/session/lifecycle.py` (call site corregido + docstring actualizado),
`tests/test_metrics.py` (test de regresión).
