// guards-snippet.ts — bloque defensivo doclens para el plugin de guards de OpenCode.
// Pegar dentro de "tool.execute.before" en .opencode/plugins/guards.ts.
// Red defensiva: aunque exista el tool nativo doclens, esto intercepta un
// `read` directo de PDF (p. ej. si el modelo ignora el tool).

import { execSync } from "node:child_process"

// --- dentro de "tool.execute.before": async (input, output) => { ... } ---
//
//   const args = (output as any).args ?? {}
//
//   if (input.tool === "read" && /\.pdf$/i.test(args.filePath ?? "")) {
//     const out = execSync(
//       `scripts/.venv-doc-tools/bin/python scripts/doclens.py "${args.filePath}"`,
//       { cwd: directory }
//     ).toString()
//     const dir = out.match(/INSPECT_DIR=(.+)/)?.[1]
//     throw new Error(
//       `PDF pre-procesado por doclens. NO leas el PDF (backend Kimi lo rechaza con 400). ` +
//       `Lee ${dir}/index.md (texto+topología+tablas) y ${dir}/pages/*.png (visual).`
//     )
//   }

export const doclensGuardSnippet = true // marcador; el código real va en guards.ts
