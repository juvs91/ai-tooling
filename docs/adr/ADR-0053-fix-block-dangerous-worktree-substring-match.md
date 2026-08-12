# ADR-0053: Fix — regla 5 de `block-dangerous.sh` bloqueaba/evadía por substring en vez de por path real

- **Estado:** Aceptado
- **Fecha:** 2026-08-12
- **Autor:** jeguzman

---

## Contexto

Durante un fix de limpieza de `.venv*` en `vendor/claude-code-proxy/` (7 directorios, 1.9GB,
aprobado explícitamente por el usuario), un `rm -rf` con **ruta absoluta** sobre un subdirectorio
inocuo (`.venv-proxy_local`, un venv de Python) fue bloqueado por `block-dangerous.sh` con:
```
BLOCKED: '/Users/jeguzman/ai-tooling' es un git worktree activo. Usa: git worktree remove /Users/jeguzman/ai-tooling
```

**Causa raíz confirmada leyendo el código (regla 5, agregada por ADR-0044):**
```bash
if echo "$segment" | grep -qE 'rm\s+-[a-zA-Z]*r[a-zA-Z]*f' && echo "$segment" | grep -qF "$wt_path"; then
```
El chequeo hacía **match de substring** (`grep -qF`) entre el path del worktree y el segmento de
comando completo — no comparaba el path *objetivo real* del `rm -rf` contra el worktree. Dos
fallas simétricas de la misma causa:

- **Falso positivo:** cualquier `rm -rf <ruta absoluta dentro del worktree>` contiene el string
  del worktree root como prefijo → bloqueado, aunque el target sea un subdirectorio sin relación
  con borrar el worktree en sí (el caso real que disparó este ADR).
- **Falso negativo (bypass trivial):** una ruta relativa nunca contiene el string absoluto del
  worktree, así que `cd <worktree> && rm -rf .` — que sí borraría el worktree completo — pasa el
  chequeo sin problema. Confirmado empíricamente: fue exactamente así como se completó el borrado
  de los 6 primeros `.venv*` en esta misma sesión (con paths relativos), mientras que el 7mo,
  invocado con ruta absoluta, fue bloqueado.

La intención original de ADR-0044 era correcta ("bloquea `rm -rf` sobre paths que son worktrees
activos registrados en git", para evitar refs huérfanas) — el bug está en la implementación del
matching, no en el propósito de la regla.

## Decisión

Reemplazar el match de substring por comparación de **rutas resueltas**:

1. Extraer el/los argumento(s) objetivo del `rm -rf` dentro del segmento (ignorando flags
   adicionales tipo `--no-preserve-root`).
2. Resolver cada argumento a ruta absoluta con `python3 -c "import os,sys;
   print(os.path.abspath(sys.argv[1]))"` — portable entre macOS (BSD `realpath` no soporta `-m`
   y falla si el path no existe) y Linux, y no requiere que el path exista.
3. Comparar el path resuelto contra cada worktree registrado (`git worktree list --porcelain`,
   que ya reporta paths absolutos): bloquear si son **iguales**, o si el worktree es un
   **descendiente** del path resuelto (o sea, borrar el resuelto también se llevaría el
   worktree). Un path resuelto que es descendiente del worktree (subdirectorio) ya NO bloquea.

La resolución usa el cwd real del proceso del hook — Claude Code lo fija igual al cwd de la
sesión (confirmado en el incidente de la sesión `92941296-...` documentado en
`ai-notes/analysis/2026-08-11-session-92941296-hook-failures.md`, donde un `cd` persistente
rompió los hooks con ruta relativa por la misma razón: el cwd del hook sí sigue al cwd real).

### Lo que NO cambia

- El propósito y alcance de la regla (ADR-0044) no cambian: sigue bloqueando intentos de borrar
  un worktree activo vía `rm -rf` en vez de `git worktree remove`.
- No se agrega manejo de rutas con espacios entre comillas ni expansión de globs (`rm -rf
  .venv*`) — limitaciones preexistentes del enfoque basado en `grep`/word-splitting de bash,
  consistentes con el resto de los hooks del proyecto (ninguno maneja quoting exótico).
- No se tocan las reglas 1-4 del mismo archivo.

## Consecuencias

### Positivas
- Elimina el falso positivo: subdirectorios dentro de un worktree vuelven a ser borrables
  normalmente con `rm -rf`.
- Elimina el bypass trivial: `cd <worktree> && rm -rf .` (o cualquier ruta relativa que resuelva
  al worktree o a un ancestro) ahora sí se bloquea, cerrando el hueco de seguridad real.

### Negativas / Costos
- Ninguna funcional — el chequeo es más preciso en ambas direcciones. Costo marginal: invoca
  `python3` por cada target de `rm -rf` detectado (solo cuando el comando ya matchea el patrón
  `rm -rf`, no en cada tool call).

## Implementación

Archivo modificado: `.claude/hooks/block-dangerous.sh` (regla 5).
