#!/usr/bin/env python3
"""
Script para ejecutar serper-mcp-server como MCP local.
Multi-plataforma: funciona en macOS, Linux, y Windows.
"""

import os
import sys
import subprocess
import platform
import signal
from pathlib import Path

# Configuración
SCRIPT_DIR = Path(__file__).parent
SERPER_API_KEY = "[REDACTED]"

def print_usage():
    """Muestra el uso del script."""
    print("Uso: ./scripts/serper-mcp.py [opciones]")
    print("Opciones:")
    print("  start    - Iniciar el servidor Serper (bloqueante)")
    print("  stop     - Detener el servidor Serper")
    print("  status    - Mostrar estado del servidor")
    print("  --help    - Mostrar este mensaje")
    sys.exit(0)

def find_npx_command():
    """Busca el comando npx en el sistema."""
    try:
        result = subprocess.run(["which", "npx"], capture_output=True, text=True)
        return result.stdout.strip()
    except Exception:
        return None

def start_server():
    """Inicia el servidor Serper usando npx."""
    npx_cmd = find_npx_command()
    if not npx_cmd:
        print("ERROR: No se encontró 'npx'. Instala Node.js primero.")
        sys.exit(1)

    print("[serper] Iniciando servidor Serper...")
    print(f"[serper] API Key: {SERPER_API_KEY[:20]}...{SERPER_API_KEY[-4:]}")
    print("[serper] Presiona Ctrl+C para detener")

    try:
        # Ejecutar npx con el servidor en modo detached
        process = subprocess.Popen(
            [npx_cmd, "-y", "serper-search-scrape-mcp-server"],
            env={**os.environ, "SERPER_API_KEY": SERPER_API_KEY},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid
        )

        # Capturar el proceso hijo para poder detenerlo
        global serper_process
        serper_process = process

        # Función para manejar SIGINT (Ctrl+C)
        def signal_handler(sig, frame):
            print(f"\n[serper] Deteniendo servidor... (señal {sig})")
            if serper_process.poll() is None:
                # El proceso terminó
                sys.exit(0)
            else:
                # El proceso está corriendo, intentar terminarlo con timeout
                try:
                    serper_process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    serper_process.terminate()
                sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Esperar a que el servidor esté listo
        import time
        time.sleep(2)

        if serper_process.poll() is None:
            print("[serper] ERROR: El servidor se detuvo inesperadamente")
            sys.exit(1)
        else:
            print("[serper] Servidor iniciado correctamente en http://localhost:3356")
            # Mantener el proceso vivo
            try:
                serper_process.wait()
            except KeyboardInterrupt:
                print("\n[serper] Deteniendo servidor...")
                signal_handler(signal.SIGINT, None)
            except Exception as e:
                print(f"[serper] ERROR: {e}")
                sys.exit(1)

    except Exception as e:
        print(f"[serper] ERROR al iniciar servidor: {e}")
        sys.exit(1)

def stop_server():
    """Intenta detener el servidor Serper."""
    if serper_process and serper_process.poll() is None:
        print("[serper] Deteniendo servidor...")
        serper_process.terminate()
        try:
            serper_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            print("[serper] El servidor no respondió en tiempo razonable, terminando...")
        except Exception as e:
            print(f"[serper] ERROR al detener: {e}")
        serper_process = None
    else:
        print("[serper] Servidor no está corriendo o no se puede detener")

def show_status():
    """Muestra el estado del servidor Serper."""
    if serper_process and serper_process.poll() is None:
        print("[serper] ✅ Servidor está corriendo (PID: {serper_process.pid})")
        print("[serper] URL: http://localhost:3356")
    elif serper_process.poll() is not None:
        exit_code = serper_process.returncode
        print(f"[serper] Servidor detenido (exit code: {exit_code})")
    else:
        print("[serper] Servidor no se ha iniciado")

def main():
    if len(sys.argv) == 1:
        command = sys.argv[1].lower()

        if command == "start":
            start_server()
        elif command == "stop":
            stop_server()
        elif command == "status":
            show_status()
        elif command in ("--help", "-h"):
            print_usage()
        else:
            print(f"ERROR: Comando desconocido: {command}")
            print_usage()
            sys.exit(1)

if __name__ == "__main__":
    main()
