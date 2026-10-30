import { execSync } from "node:child_process"
import { existsSync, readFileSync } from "node:fs"
import { join } from "node:path"

// Port de los hooks críticos de CC (.claude/hooks/) a OpenCode.
// Cubre: doc-guard, protect-secrets, quality-enforce, scope-gate, adr-gate,
//        intent-bootstrap, ts-quality-gate, edit-drift-detector.
// Bloquear = throw new Error(...) dentro de tool.execute.before.
export const Guards = async ({ directory }: { directory: string }) => {
  let editsSinTests = 0
  let scopeCache: string[] | null = null
  let scopeLoaded = false

  const loadScope = (): string[] | null => {
    if (scopeLoaded) return scopeCache
    scopeLoaded = true
    const p = join(directory, ".claude/task-scope.json")
    if (!existsSync(p)) return null
    try {
      scopeCache = JSON.parse(readFileSync(p, "utf8")).scope ?? null
    } catch {
      scopeCache = null
    }
    return scopeCache
  }

  return {
    // intent-bootstrap: una vez por sesión.
    // OpenCode NO despacha "session.created" como hook de plugin (verificado en
    // runtime 1.18.18); hay que suscribirse al hook genérico `event` y filtrar.
    event: async ({ event }: { event: { type: string } }) => {
      if (event.type !== "session.created") return
      try {
        execSync("bash .claude/hooks/intent-bootstrap.sh", { cwd: directory, stdio: "pipe" })
      } catch {
        // no bloquear la sesión si el bootstrap falla
      }
    },

    "tool.execute.before": async (input: any, output: any) => {
      const args = output?.args ?? {}

      // doc-guard: PDF/imagen grande -> doclens, bloquear con instrucciones.
      // El backend Kimi rechaza PDFs crudos con HTTP 400 y envenena la sesión.
      if (input.tool === "read" && /\.pdf$/i.test(args.filePath ?? "")) {
        // Estructura homologada (ADR-0076): doclens/ + doclens/.venv; sin venv, se prepara solo
        if (!existsSync(join(directory, "doclens/.venv/bin/python")))
          execSync("bash doclens/sync.sh --venv", { cwd: directory, stdio: "pipe" })
        const out = execSync(
          `doclens/.venv/bin/python doclens/doclens.py "${args.filePath}"`,
          { cwd: directory }
        ).toString()
        const dir = out.match(/INSPECT_DIR=(.+)/)?.[1]
        throw new Error(
          `PDF pre-procesado por doclens. NO leas el PDF (backend Kimi lo rechaza con 400). ` +
          `Lee ${dir}/index.md (texto+topología+tablas) y ${dir}/pages/*.png (visual).`
        )
      }

      // protect-secrets: no escribir api keys en archivos trackeados
      if (
        (input.tool === "edit" || input.tool === "write") &&
        /sk-(kimi|ant|or)-[A-Za-z0-9]{20,}/.test(args.content ?? args.newString ?? "")
      ) {
        throw new Error("BLOCKED: secret en archivo trackeado.")
      }

      if (input.tool === "edit" || input.tool === "write") {
        // quality-enforce: errores TS pendientes bloquean nuevos edits
        const qs = join(directory, ".claude/quality-state/errors.json")
        if (existsSync(qs)) {
          try {
            const count = JSON.parse(readFileSync(qs, "utf8")).count ?? 0
            if (count > 0) {
              throw new Error(
                "BLOCKED: hay errores TS pendientes en .claude/quality-state/. Corrígelos primero."
              )
            }
          } catch (e) {
            if (e instanceof SyntaxError) { /* state corrupto: no bloquear */ }
            else throw e
          }
        }

        // scope-gate: edits solo dentro del scope de la sesión
        const scope = loadScope()
        if (scope && args.filePath && !scope.some((s) => args.filePath.startsWith(s))) {
          throw new Error(
            `BLOCKED: ${args.filePath} fuera del scope de la sesión (.claude/task-scope.json).`
          )
        }

        // adr-gate: rutas guarded requieren ADR staged (mandato ADR-first).
        // TOOLBOX: rutas genéricas — cada consumer las personaliza en su copia.
        const guarded = ["frontend/src/lib/", "backend/app/core/"]
        if (args.filePath && guarded.some((g) => args.filePath.includes(g))) {
          const staged = execSync("git diff --cached --name-only", { cwd: directory }).toString()
          if (!staged.includes("ai-notes/adr/")) {
            throw new Error("BLOCKED: ruta guarded requiere ADR staged (mandato ADR-first).")
          }
        }
      }
    },

    "tool.execute.after": async (input: any, output: any) => {
      const args = output?.args ?? {}
      if (input.tool === "edit" || input.tool === "write") {
        editsSinTests++
        // ts-quality-gate: corre tsc tras edits de *.ts(x)
        if (/\.tsx?$/.test(args.filePath ?? "")) {
          try {
            execSync("bash .claude/hooks/ts-quality-gate.sh", { cwd: directory, stdio: "pipe" })
          } catch { /* el gate escribe su state; quality-enforce bloquea el siguiente edit */ }
        }
        // edit-drift-detector
        if ([8, 15, 25].includes(editsSinTests)) {
          console.error(`DRIFT: ${editsSinTests} edits sin correr tests.`)
        }
      }
      if (input.tool === "bash" && /pytest|npm test|vitest/.test(args.command ?? "")) {
        editsSinTests = 0
      }
    },
  }
}

export default Guards
