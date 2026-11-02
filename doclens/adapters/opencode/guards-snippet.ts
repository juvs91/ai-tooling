// guards-snippet.ts — bloque defensivo doclens para el plugin de guards de OpenCode.
// Pegar dentro de "tool.execute.before" en .opencode/plugins/guards.ts.
// Red defensiva: aunque exista el tool nativo doclens, esto intercepta un
// `read` directo de PDF (p. ej. si el modelo ignora el tool).

import { execFileSync } from "node:child_process"
import { existsSync } from "node:fs"
import { join } from "node:path"

// --- dentro de "tool.execute.before": async (input, output) => { ... } ---
//
//   const args = (output as any).args ?? {}
//
//   if (input.tool === "read" && /\.pdf$/i.test(args.filePath ?? "")) {
//     // Estructura homologada (ADR-0076): doclens/ + doclens/.venv; sin venv, se prepara solo
//     if (!existsSync(join(directory, "doclens/.venv/bin/python")))
//       execFileSync("bash", ["doclens/sync.sh", "--venv"], { cwd: directory, stdio: "pipe" })
//     // argumentos en lista, NUNCA un string de shell: "x$(cmd).pdf" ejecutaría cmd (ADR-0077)
//     const out = execFileSync(
//       "doclens/.venv/bin/python",
//       ["doclens/doclens.py", String(args.filePath)],
//       { cwd: directory }
//     ).toString()
//     const dir = out.match(/INSPECT_DIR=(.+)/)?.[1]
//     throw new Error(
//       `PDF pre-procesado por doclens. NO leas el PDF (backend Kimi lo rechaza con 400). ` +
//       `Lee ${dir}/index.md (texto+topología+tablas) y ${dir}/pages/*.png (visual).`
//     )
//   }

export const doclensGuardSnippet = true // marcador; el código real va en guards.ts
