#!/bin/bash
# AI-Tooling Proxy - Initialization Script
# Instala todas las dependencias y configura el proxy para ser usado en otros proyectos

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"

# Colores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Función para verificar dependencias
check_dependency() {
    if ! command -v "$1" >/dev/null 2>&1; then
        log_error "Falta dependencia: $1"
        log_warn "Instala con: $2"
        exit 1
    else
        log_success "$1 encontrado"
    fi
}

# Función para crear symlinks
create_symlink() {
    local script="$1"
    local target="$2"

    if [ -f "$PROJECT_DIR/scripts/$script" ]; then
        chmod +x "$PROJECT_DIR/scripts/$script"
        ln -sf "$PROJECT_DIR/scripts/$script" "$target"
        log_success "Symlink creado: $script -> $target"
    else
        log_warn "Script no encontrado: $script"
    fi
}

# Función para copiar templates
copy_template() {
    local template="$1"
    local dest="$2"

    if [ -f "$PROJECT_DIR/templates/$template" ] && [ ! -f "$dest" ]; then
        cp "$PROJECT_DIR/templates/$template" "$dest"
        log_success "Template copiado: $template -> $dest"
    fi
}

# Verificar que estamos en el directorio correcto
if [ ! -f "$PROJECT_DIR/CLAUDE.md" ]; then
    log_error "No se encontró CLAUDE.md. Ejecuta desde la raíz del proyecto ai-tooling."
    exit 1
fi

log_info "=== AI-Tooling Proxy Initialization ==="

# 1. Verificar dependencias del sistema
log_info "1. Verificando dependencias del sistema..."

check_dependency "docker" "https://docs.docker.com/get-docker/"
check_dependency "docker-compose" "pip install docker-compose"
check_dependency "python3" "https://www.python.org/downloads/"
check_dependency "pip" "curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py && python3 get-pip.py"
check_dependency "git" "https://git-scm.com/downloads"

# 2. Instalar dependencias Python del proxy
log_info "2. Instalando dependencias Python del proxy..."

if [ -f "$PROJECT_DIR/vendor/claude-code-proxy/requirements.txt" ]; then
    cd "$PROJECT_DIR/vendor/claude-code-proxy"
    pip install -r requirements.txt
    if [ $? -eq 0 ]; then
        log_success "Dependencias Python instaladas"
    else
        log_error "Error instalando dependencias Python"
        exit 1
    fi
    cd "$PROJECT_DIR"
else
    log_error "No se encontró requirements.txt en vendor/claude-code-proxy/"
    exit 1
fi

# 3. Crear symlinks de los scripts CLI
log_info "3. Creando symlinks de scripts CLI..."

# Crear directorio ~/.local/bin si no existe
mkdir -p "$HOME/.local/bin"

# Symlinks esenciales
create_symlink "cc-proxy-up" "$HOME/.local/bin/cc-proxy-up"
create_symlink "cc-switch" "$HOME/.local/bin/cc-switch"
create_symlink "cc-health" "$HOME/.local/bin/cc-health"
create_symlink "cc-chat" "$HOME/.local/bin/cc-chat"
create_symlink "cc-proxy-init.sh" "$HOME/.local/bin/cc-proxy-init"
create_symlink "ralph-init" "$HOME/.local/bin/ralph-init"

# Verificar que ~/.local/bin está en PATH
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    log_warn "\n~/.local/bin no está en tu PATH. Agrega esto a ~/.bashrc o ~/.zshrc:"
    echo "export PATH=\"\$HOME/.local/bin:\$PATH\""

    # Preguntar si agregar automáticamente
    read -p "¿Agregar al PATH automáticamente? (y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        if [ -f "$HOME/.zshrc" ]; then
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.zshrc"
            source "$HOME/.zshrc"
            log_success "Agregado a ~/.zshrc"
        elif [ -f "$HOME/.bashrc" ]; then
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
            source "$HOME/.bashrc"
            log_success "Agregado a ~/.bashrc"
        else
            log_warn "No se encontró .bashrc ni .zshrc. Agrega manualmente."
        fi
    fi
fi

# 4. Scaffold de directorios esenciales
log_info "4. Creando estructura de directorios..."

mkdir -p "$PROJECT_DIR/profile-envs"
mkdir -p "$PROJECT_DIR/cloud-provider-ymls"
mkdir -p "$PROJECT_DIR/ai-notes"
mkdir -p "$PROJECT_DIR/docs"

log_success "Directorios creados"

# 5. Copiar templates si no existen
log_info "5. Configurando templates..."

copy_template "AI_LEARNING.template.md" "$PROJECT_DIR/ai-notes/AI_LEARNING.md"
copy_template "GUARDRAILS.template.md" "$PROJECT_DIR/ai-notes/GUARDRAILS.md"

# 6. Configurar profile-envs con ejemplos
log_info "6. Configurando profile-envs..."

if [ ! -f "$PROJECT_DIR/profile-envs/cloud.zai.env.example" ]; then
    cat > "$PROJECT_DIR/profile-envs/cloud.zai.env.example" << 'EOF'
# Z.AI Configuration (OpenAI-compatible)
PREFERRED_PROVIDER=openai
OPENAI_BASE_URL=https://api.z.ai/api/paas/v4/
OPENAI_API_KEY=your-api-key-here

# Models
BIG_MODEL=glm-4.7
SMALL_MODEL=glm-4.7-flash
BUILDING_MODEL=glm-4.7

# Optional: LLM Intent Classifier
#CLASSIFIER_MODEL=glm-4.7-flash
#CLASSIFIER_API_KEY=${OPENAI_API_KEY}
#CLASSIFIER_BASE_URL=${OPENAI_BASE_URL}
EOF
    log_success "Ejemplo Z.AI creado"
fi

# 7. Mostrar resumen
log_info "7. Resumen de instalación:\n"

echo "✅ Dependencias instaladas"
echo "✅ Scripts CLI disponibles en ~/.local/bin/"
echo "✅ Estructura de directorios creada"
echo "✅ Templates configurados"
echo "✅ Ejemplos de profile-envs creados"
echo ""

echo "📋 Pasos siguientes:"
echo "1. Configura un proveedor LLM:"
echo "   cp profile-envs/cloud.zai.env.example profile-envs/cloud.zai.env"
echo "   # Edita profile-envs/cloud.zai.env con tu API key"
echo ""
echo "2. Levanta el proxy:"
echo "   cc-proxy-up cloud-provider-ymls/docker-compose.zai.override.yml"
echo ""
echo "3. Verifica que funcione:"
echo "   cc-health"
echo ""
echo "4. Configura Claude Code:"
echo "   cc-switch proxy"
echo ""
echo "5. Para debuggear:"
echo "   cc-health --json | jq"
echo "   docker-compose logs -f"
echo ""

echo "🛠️  Scripts disponibles:"
echo "  cc-proxy-up     - Levanta proxy con hot-reload"
echo "  cc-switch       - Cambia configuración de Claude Code"
echo "  cc-health       - Verifica salud del proxy"
echo "  cc-chat         - CLI chat con modelos"
echo "  cc-proxy-init   - Inicializa nuevo proveedor"
echo ""

echo "📚 Documentación completa en README.md"

log_success "=== Initialization completada ==="
