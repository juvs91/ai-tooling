import { tool } from "@opencode-ai/plugin"
import path from "node:path"

// doclens — custom tool nativo para OpenCode.
// Instalación: copiar a .opencode/tools/doclens.ts del proyecto.
// Envuelve scripts/doclens.py (copia sincronizada desde ai-tooling/doclens
// via scripts/sync-doclens.sh). La red defensiva vive en el plugin de guards
// (ver guards-snippet.ts).
export default tool({
  description:
    "Pre-procesa un PDF o imagen ANTES de leerla: genera <archivo>.inspect/ con " +
    "index.md (metadata + texto/OCR spa + tablas en markdown + topología con coordenadas), " +
    "pages/page-N.png (renders para lectura visual) y figures/ (figuras embebidas " +
    "extraídas + OCR del texto dentro de figuras). NUNCA leas el PDF crudo — el backend " +
    "Kimi lo rechaza con HTTP 400 y envenena la sesión. Idempotente: no reprocesa si " +
    "el sidecar está fresco. Después de correrlo, lee <sidecar>/index.md.",
  args: {
    file: tool.schema.string().describe("Ruta al PDF o imagen a inspeccionar"),
    dpi: tool.schema
      .number()
      .optional()
      .describe("DPI de los renders de página (default 150)"),
  },
  async execute(args, context) {
    const script = path.join(context.worktree, "scripts/doclens.py")
    const py = path.join(context.worktree, "scripts/.venv-doc-tools/bin/python")
    const dpi = args.dpi ?? 150
    const result = await Bun.$`${py} ${script} ${args.file} --dpi ${dpi}`.text()
    return result.trim()
  },
})
