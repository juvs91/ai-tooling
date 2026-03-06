# DX + Observability Roadmap for Claude Code Proxy

## Context

Based on comparative analysis with `claude-code-router`, LiteLLM, Portkey, and OpenRouter, our proxy has:
- **Strengths**: Quality scoring, intent routing, guardrails, comprehensive in-memory metrics
- **Gaps in DX**: No CLI, no UI, requires manual env file editing, no in-session model switching
- **Gaps in Observability**: No external integrations (Prometheus, Langfuse, Datadog), metrics lost on restart, no visualization

**Goal**: Leverage LiteLLM integrations + add minimal code to close DX/observability gaps without breaking existing functionality.

---

## Phase 1: Observability Integration (Week 1)

### 1.1 Prometheus Metrics Exporter

**New Files:**
- `vendor/claude-code-proxy/utils/observability.py` - Observability integration layer
- `tests/test_observability.py`

**Modified Files:**
- `vendor/claude-code-proxy/server.py` - Add `/metrics` endpoint
- `vendor/claude-code-proxy/config.py` - Add `ObservabilityConfig` dataclass
- `vendor/claude-code-proxy/.env.example` - Add OBSERVABILITY_* vars

**Implementation:**

```python
# utils/observability.py (new)
from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry, generate_latest
from typing import Optional
from utils.metrics import metrics as proxy_metrics

class ObservabilityManager:
    def __init__(self):
        self.registry = CollectorRegistry()
        self.request_count = Counter(
            'proxy_requests_total',
            'Total proxy requests',
            ['provider', 'model', 'intent', 'is_stream'],
            registry=self.registry
        )
        self.request_duration = Histogram(
            'proxy_request_duration_seconds',
            'Request duration',
            ['provider', 'intent'],
            registry=self.registry
        )
        self.token_count = Counter(
            'proxy_tokens_total',
            'Total tokens processed',
            ['direction', 'model'],
            registry=self.registry
        )
        self.cache_hits = Gauge(
            'proxy_cache_hits',
            'Response cache hits',
            registry=self.registry
        )
        # Hook into existing metrics
        self._sync_with_proxy_metrics()

    def _sync_with_proxy_metrics(self):
        """Sync Prometheus metrics with in-memory proxy_metrics on each request."""
        # Called via hook in metrics.record()
        pass

    def generate_latest(self) -> str:
        """Generate Prometheus scrape format."""
        return generate_latest(self.registry)
```

**Environment Variables:**
```bash
OBSERVABILITY_PROMETHEUS_ENABLED=1          # default: 0
OBSERVABILITY_PROMETHEUS_PORT=9090           # default: 9090
```

**Endpoints to Add:**
- `GET /metrics` - Prometheus scrape endpoint (standard format)
- `GET /api/health-detailed` - Extended health with per-provider status

### 1.2 LiteLLM Callback Integration

LiteLLM supports built-in callbacks for Langfuse, Datadog, Helicone. Hook them up via env config.

**Modified Files:**
- `vendor/claude-code-proxy/server.py` - Initialize LiteLLM callbacks at startup

**Environment Variables:**
```bash
# Langfuse (LLM observability platform)
LANGFUSE_PUBLIC_KEY=pf-xxx...
LANGFUSE_SECRET_KEY=sk-xxx...
LANGFUSE_HOST=https://cloud.langfuse.com       # optional, default is cloud

# Datadog
DATADOG_SITE=datadoghq.com                # optional
DATADOG_API_KEY=...

# Helicone (OpenAI proxy observability)
HELICONE_API_KEY=...
```

**Implementation (in server.py startup):**

```python
# LiteLLM callback integration
if cfg.observability.langfuse_enabled:
    try:
        from litellm.integrations.langfuse import langfuse_handler
        litellm.set_callback("langfuse", langfuse_handler.LangfuseHandler(
            public_key=cfg.observability.langfuse_public_key,
            secret_key=cfg.observability.langfuse_secret_key,
            host=cfg.observability.langfuse_host,
        ))
        logger.info("[observability] Langfuse enabled: %s", cfg.observability.langfuse_host or "cloud")
    except Exception as e:
        logger.warning("[observability] Failed to enable Langfuse: %s", e)

if cfg.observability.datadog_enabled:
    try:
        from litellm.integrations.datadog import datadog_handler
        litellm.set_callback("datadog", datadog_handler.DatadogHandler(
            site=cfg.observability.datadog_site,
            api_key=cfg.observability.datadog_api_key,
        ))
        logger.info("[observability] Datadog enabled")
    except Exception as e:
        logger.warning("[observability] Failed to enable Datadog: %s", e)
```

### 1.3 Persistent Metrics Storage

**New Files:**
- `vendor/claude-code-proxy/utils/persistent_metrics.py`

**Modified Files:**
- `vendor/claude-code-proxy/server.py` - Initialize persistent storage
- `vendor/claude-code-proxy/config.py` - Add `MetricsStorageConfig`

**Implementation:**

```python
# utils/persistent_metrics.py (new)
import json
import os
from pathlib import Path
from typing import Optional
import fcntl

class PersistentMetrics:
    def __init__(self, storage_path: str = "/data/metrics.jsonl"):
        self.storage_path = Path(storage_path)
        self.lock_file = Path(storage_path + ".lock")
        self._ensure_dir()

    def _ensure_dir(self):
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

    def _acquire_lock(self) -> bool:
        """Acquire file lock for atomic writes."""
        try:
            self.lock_fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX)
            return True
        except (OSError, IOError):
            return False

    def _release_lock(self):
        if hasattr(self, 'lock_fd'):
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            os.close(self.lock_fd)

    def save_log(self, log: dict) -> None:
        """Append log entry to JSONL file with file locking."""
        if not self._acquire_lock():
            return  # Skip if lock held by another process

        try:
            with open(self.storage_path, 'a') as f:
                f.write(json.dumps(log) + '\n')
        finally:
            self._release_lock()

    def load_recent(self, n: int = 1000) -> list[dict]:
        """Load recent logs from JSONL."""
        if not self.storage_path.exists():
            return []

        logs = []
        with open(self.storage_path, 'r') as f:
            for line in f:
                if len(logs) >= n:
                    break
                try:
                    logs.append(json.loads(line.strip()))
                except json.JSONDecodeError:
                    continue
        return logs

    def rotate(self, retention_days: int = 30) -> None:
        """Remove logs older than retention_days."""
        import time
        cutoff = time.time() - (retention_days * 86400)

        # Read all, filter, rewrite
        all_logs = []
        with open(self.storage_path, 'r') as f:
            for line in f:
                try:
                    log = json.loads(line.strip())
                    if log.get('timestamp', '').startswith('20'):
                        # Parse timestamp and check age
                        from datetime import datetime, timezone
                        ts = datetime.fromisoformat(log['timestamp'].replace('Z', '+00:00'))
                        ts_age = ts.timestamp()
                        if ts_age >= cutoff:
                            continue
                    all_logs.append(log)
                except:
                    pass

        # Rewrite filtered logs
        with open(self.storage_path, 'w') as f:
            for log in all_logs:
                f.write(json.dumps(log) + '\n')
```

**Environment Variables:**
```bash
METRICS_PERSISTENCE_ENABLED=1          # default: 0
METRICS_STORAGE_PATH=/data/metrics.jsonl  # default
METRICS_RETENTION_DAYS=30               # default
```

**Integration in server.py:**

```python
# After metrics.record() calls, also save to persistent storage
if cfg.metrics_storage.enabled:
    from utils.persistent_metrics import persistent_metrics
    persistent_metrics.save_log(asdict(log))
```

---

## Phase 2: Developer Experience - CLI Tool (Week 2)

### 2.1 CLI Implementation

**New Files:**
- `vendor/claude-code-proxy/cli/__init__.py`
- `vendor/claude-code-proxy/cli/main.py` - CLI entry point
- `vendor/claude-code-proxy/cli/commands/status.py`
- `vendor/claude-code-proxy/cli/commands/config.py`
- `vendor/claude-code-proxy/cli/commands/logs.py`
- `vendor/claude-code-proxy/cli/commands/health.py`

**Modified Files:**
- `vendor/claude-code-proxy/pyproject.toml` - Add CLI entry point and dependencies

**Implementation:**

```python
# cli/main.py (new)
import typer
import httpx
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from typing import Optional

app = typer.Typer(
    name="cc-proxy",
    help="Claude Code Proxy CLI - manage configuration, view metrics, check health"
)

@app.command()
def status(
    url: str = typer.Option("http://localhost:8083", "--url", "-u", help="Proxy URL"),
    format: str = typer.Option("table", "--format", "-f", help="Output format: table, json"),
):
    """Show proxy status and metrics."""
    console = Console()

    try:
        resp = httpx.get(f"{url}/api/stats", timeout=5)
        resp.raise_for_status()
        stats = resp.json()
    except Exception as e:
        console.print(f"[red]Error connecting to proxy: {e}[/red]")
        raise typer.Exit(1)

    if format == "json":
        console.print_json(stats)
        return

    # Rich table formatting
    console.print(Panel("[bold blue]Claude Code Proxy Status[/bold blue]"))

    # Summary table
    summary = Table(title="Summary", show_header=False)
    summary.add_column("", style="cyan")
    summary.add_row(f"Total Requests: [green]{stats['total_requests']}[/green]")
    summary.add_row(f"Errors: [red]{stats['total_errors']}[/red]")
    summary.add_row(f"Average Latency: [yellow]{stats['providers'].get('primary', {}).get('avg_latency_ms', 0):.0f}ms[/yellow]")
    console.print(summary)

    # Providers table
    providers_table = Table(title="Providers", show_edge=False)
    providers_table.add_column("Provider", style="cyan")
    providers_table.add_column("Requests", style="green")
    providers_table.add_column("Errors", style="red")
    providers_table.add_column("Avg Latency", style="yellow")
    providers_table.add_column("Fallback Rate", style="magenta")

    for provider, data in stats.get('providers', {}).items():
        providers_table.add_row(
            provider,
            str(data.get('requests', 0)),
            str(data.get('errors', 0)),
            f"{data.get('avg_latency_ms', 0):.0f}ms",
            f"{stats.get('fallback_rate_pct', 0):.1f}%"
        )
    console.print(providers_table)

    # Quality metrics
    if stats.get('analysis_avg_quality', 0) > 0:
        quality_panel = Panel(
            f"Analysis Quality: [green]{stats['analysis_avg_quality']:.0%}[/green]\n"
            f"Refinements: {stats['analysis_refinements']}",
            title="Quality Metrics"
        )
        console.print(quality_panel)

@app.command()
def config(
    list_vars: bool = typer.Option(False, "--list", "-l", help="List current configuration"),
    set_key: Optional[str] = typer.Option(None, "--set", "-s", help="Set KEY=VALUE"),
    show: Optional[str] = typer.Option(None, "--show", help="Show specific key"),
):
    """Manage proxy configuration."""
    console = Console()

    if list_vars:
        # Show from /api/health
        resp = httpx.get("http://localhost:8083/health")
        health_data = resp.json()

        console.print(Panel("[bold blue]Current Configuration[/bold blue]"))
        config_table = Table(show_header=True)
        config_table.add_column("Setting", style="cyan")
        config_table.add_column("Value", style="green")

        models = health_data.get('models', {})
        config_table.add_row("Big Model", models.get('big', {}).get('model', 'N/A'))
        config_table.add_row("Small Model", models.get('small', {}).get('model', 'N/A'))
        config_table.add_row("Building Model", models.get('building', {}).get('model', 'N/A'))
        config_table.add_row("Preferred Provider", health_data.get('provider', 'N/A'))
        config_table.add_row("Classifier", health_data.get('classifier', {}).get('mode', 'N/A'))
        console.print(config_table)
        return

    if set_key:
        # Parse KEY=VALUE
        if '=' not in set_key:
            console.print("[red]Error: SET must be in KEY=VALUE format[/red]")
            raise typer.Exit(1)

        key, value = set_key.split('=', 1)
        console.print(f"[yellow]Would set {key}={value}[/yellow]")
        console.print("[yellow]Edit .env file directly for now. Runtime config coming in Phase 4.[/yellow]")
        return

    if show:
        console.print(f"[cyan]{show}: {key}[/cyan]")
        # Could fetch from health endpoint or read .env
        return

@app.command()
def logs(
    tail: int = typer.Option(50, "--tail", "-n", help="Number of recent logs"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow logs in real-time"),
    level: str = typer.Option(None, "--level", "-l", help="Filter by log level"),
):
    """View proxy logs."""
    console = Console()

    try:
        resp = httpx.get(f"http://localhost:8083/api/logs?n={tail}", timeout=5)
        resp.raise_for_status()
        logs_data = resp.json()
    except Exception as e:
        console.print("[red]Error fetching logs: {e}[/red]")
        raise typer.Exit(1)

    if not logs_data:
        console.print("[yellow]No logs available[/yellow]")
        return

    console.print(Panel(f"[bold blue]Recent {len(logs_data)} Logs[/bold blue]"))

    for log in logs_data:
        # Format timestamp
        ts = log.get('timestamp', '')[:19]  # ISO format
        provider = log.get('provider', 'unknown')
        intent = log.get('intent', 'unknown')
        model = log.get('model_used', 'unknown')

        # Status indicators
        status_color = "green"
        if log.get('error'):
            status_color = "red"
        elif log.get('is_fallback'):
            status_color = "yellow"

        console.print(f"[dim]{ts}[/dim] [{status_color}]{provider}[/{status_color}] [cyan]{intent}[/cyan] -> [magenta]{model}[/magenta]")

        if log.get('quality_score', 1.0) < 1.0:
            q_score = log.get('quality_score', 1.0)
            console.print(f"    [yellow]Quality: {q_score:.0%}[/yellow] {', '.join(log.get('quality_issues', []))}")

@app.command()
def health(
    detailed: bool = typer.Option(False, "--detailed", "-d", help="Show detailed health info"),
):
    """Quick health check."""
    console = Console()

    try:
        resp = httpx.get("http://localhost:8083/health", timeout=5)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        console.print("[red][bold]✗ Proxy Unhealthy[/bold red]")
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    status = data.get('status', 'unknown')
    if status == 'healthy':
        console.print("[green][bold]✓ Proxy Healthy[/bold green]")
    else:
        console.print(f"[red][bold]✗ Proxy {status.title()}[/bold red]")

    if detailed:
        # Show full health data as JSON
        console.print_json(data)
```

**pyproject.toml Addition:**
```toml
[project.scripts]
cc-proxy = "cli.main:app"

[project]
dependencies = [
    # ... existing dependencies ...
    "typer>=0.12.0",
    "rich>=13.0.0",
]
```

### 2.2 Profile Management Enhancement

**New Files:**
- `profile-envs/presets/quick-start.env`
- `profile-envs/presets/low-cost.env`
- `profile-envs/presets/high-performance.env`

**Modified Files:**
- `scripts/cc-switch` - Wrap with CLI hints

---

## Phase 3: Web Dashboard (Week 3-4)

### 3.1 Static Web Assets

**New Directory Structure:**
```
vendor/claude-code-proxy/web/
├── static/
│   ├── index.html
│   ├── app.js
│   └── styles.css
└── api/
    └── dashboard.py
```

**New Files:**
- `vendor/claude-code-proxy/web/static/index.html` - Dashboard UI
- `vendor/claude-code-proxy/web/static/app.js` - Dashboard logic (fetch API, render charts)
- `vendor/claude-code-proxy/web/static/styles.css` - Styling
- `vendor/claude-code-proxy/web/api/dashboard.py` - Dashboard-specific API endpoints

**Modified Files:**
- `vendor/claude-code-proxy/server.py` - Serve static files, add dashboard API

**Implementation (server.py additions):**

```python
# Static file serving
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app.mount("/static", StaticFiles(directory="web/static"), name="static")

# Dashboard API endpoints
@app.get("/api/dashboard/metrics")
async def dashboard_metrics():
    """Metrics formatted for dashboard consumption."""
    stats = metrics.get_stats()
    return {
        "summary": {
            "total_requests": stats["total_requests"],
            "total_errors": stats["total_errors"],
            "total_fallbacks": stats["total_fallbacks"],
            "avg_latency_ms": stats["providers"].get("primary", {}).get("avg_latency_ms", 0),
            "fallback_rate_pct": stats["fallback_rate_pct"],
            "analysis_avg_quality": stats["analysis_avg_quality"],
        },
        "providers": stats["providers"],
        "intents": stats["intents"],
        "tool_quality": stats["tool_quality"],
        "cost": stats["cost"],
        "recent_logs": metrics.get_recent(100),
    }

@app.get("/api/dashboard/providers")
async def dashboard_providers():
    """Provider status for dashboard."""
    resp = httpx.get("http://localhost:8083/health")
    health_data = resp.json()
    return {
        "primary": health_data.get("provider"),
        "models": health_data.get("models", {}),
        "fallbacks": [
            {"name": f.get("name")}
            for f in health_data.get("fallbacks", [])
        ]
    }

@app.get("/")
async def dashboard():
    """Serve dashboard."""
    return FileResponse("web/static/index.html")

# Add to requirements
"jinja2>=3.1.0",
```

**Dashboard UI (index.html - simplified):**

```html
<!DOCTYPE html>
<html>
<head>
    <title>Claude Code Proxy Dashboard</title>
    <link rel="stylesheet" href="/static/styles.css">
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
</head>
<body>
    <div class="container">
        <header>
            <h1>Claude Code Proxy</h1>
            <div id="status-indicator" class="status unknown">Checking...</div>
        </header>

        <div class="metrics-grid">
            <div class="metric-card">
                <h3>Total Requests</h3>
                <div id="total-requests" class="value">-</div>
            </div>
            <div class="metric-card">
                <h3>Errors</h3>
                <div id="total-errors" class="value error">-</div>
            </div>
            <div class="metric-card">
                <h3>Avg Latency</h3>
                <div id="avg-latency" class="value">-</div>
            </div>
            <div class="metric-card">
                <h3>Fallback Rate</h3>
                <div id="fallback-rate" class="value">-</div>
            </div>
            <div class="metric-card">
                <h3>Total Cost</h3>
                <div id="total-cost" class="value">-</div>
            </div>
            <div class="metric-card">
                <h3>Analysis Quality</h3>
                <div id="analysis-quality" class="value">-</div>
            </div>
        </div>

        <div class="charts-grid">
            <div class="chart-container">
                <canvas id="requests-chart"></canvas>
            </div>
            <div class="chart-container">
                <canvas id="latency-chart"></canvas>
            </div>
            <div class="chart-container">
                <canvas id="cost-chart"></canvas>
            </div>
        </div>

        <div class="logs-panel">
            <h3>Recent Requests</h3>
            <div id="logs-container" class="logs"></div>
        </div>
    </div>

    <script src="/static/app.js"></script>
</body>
</html>
```

**Dashboard JS (app.js):**

```javascript
const API_BASE = '/api/dashboard';
const UPDATE_INTERVAL = 2000; // 2 seconds

let requestsChart, latencyChart, costChart;

async function fetchMetrics() {
    try {
        const resp = await fetch(`${API_BASE}/metrics`);
        const data = await resp.json();
        updateDashboard(data);
        updateCharts(data);
        updateStatus();
    } catch (e) {
        console.error('Failed to fetch metrics:', e);
    }
}

function updateDashboard(data) {
    const summary = data.summary;

    document.getElementById('total-requests').textContent = summary.total_requests || 0;
    document.getElementById('total-errors').textContent = summary.total_errors || 0;
    document.getElementById('avg-latency').textContent = `${summary.avg_latency_ms.toFixed(0)}ms`;
    document.getElementById('fallback-rate').textContent = `${summary.fallback_rate_pct}%`;
    document.getElementById('total-cost').textContent = `$${summary.cost?.total_usd || '0.00'}`;
    document.getElementById('analysis-quality').textContent = `${(summary.analysis_avg_quality * 100).toFixed(0)}%`;

    // Logs
    const logsContainer = document.getElementById('logs-container');
    logsContainer.innerHTML = data.recent_logs.map(log => {
        const statusClass = log.error ? 'error' : (log.is_fallback ? 'warning' : 'success');
        const qualityInfo = log.quality_score < 1.0 ? `<span class="quality-warning">Quality: ${(log.quality_score * 100).toFixed(0)}%</span>` : '';

        return `
            <div class="log-entry ${statusClass}">
                <div class="log-time">${log.timestamp.substring(0, 19)}</div>
                <div class="log-provider">${log.provider}</div>
                <div class="log-intent">${log.intent}</div>
                <div class="log-latency">${log.latency_ms}ms</div>
                ${qualityInfo}
            </div>
        `;
    }).join('');
}

function initCharts() {
    const chartOptions = {
        responsive: true,
        maintainAspectRatio: false,
        scales: { y: { beginAtZero: true } }
    };

    requestsChart = new Chart(document.getElementById('requests-chart'), {
        type: 'line',
        data: { labels: [], datasets: [{ label: 'Requests/min', data: [], borderColor: 'rgb(75, 192, 192)', tension: 0.1 }] },
        options: chartOptions
    });

    latencyChart = new Chart(document.getElementById('latency-chart'), {
        type: 'line',
        data: { labels: [], datasets: [{ label: 'Latency (ms)', data: [], borderColor: 'rgb(255, 99, 132)', tension: 0.1 }] },
        options: chartOptions
    });

    costChart = new Chart(document.getElementById('cost-chart'), {
        type: 'bar',
        data: { labels: [], datasets: [{ label: 'Cost ($)', data: [], backgroundColor: 'rgb(54, 162, 235)' }] },
        options: chartOptions
    });
}

function updateCharts(data) {
    // Update with recent metrics (simplified - would need time-series data)
    // For now, use static values
    const summary = data.summary;
    const now = new Date().toLocaleTimeString();

    // Add new data point
    requestsChart.data.labels.push(now);
    requestsChart.data.datasets[0].data.push(summary.total_requests);
    if (requestsChart.data.labels.length > 20) {
        requestsChart.data.labels.shift();
        requestsChart.data.datasets[0].data.shift();
    }
    requestsChart.update();

    latencyChart.data.labels.push(now);
    latencyChart.data.datasets[0].data.push(summary.avg_latency_ms);
    if (latencyChart.data.labels.length > 20) {
        latencyChart.data.labels.shift();
        latencyChart.data.datasets[0].data.shift();
    }
    latencyChart.update();
}

function updateStatus() {
    const indicator = document.getElementById('status-indicator');
    indicator.className = 'status healthy';
    indicator.textContent = '● Healthy';
}

document.addEventListener('DOMContentLoaded', () => {
    initCharts();
    fetchMetrics();
    setInterval(fetchMetrics, UPDATE_INTERVAL);
});
```

**Dashboard CSS (styles.css):**

```css
:root {
    --bg-primary: #0f172a;
    --bg-card: #1e293b;
    --text-primary: #e2e8f0;
    --text-secondary: #94a3b8;
    --green: #22c55e;
    --red: #ef4444;
    --yellow: #eab308;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg-primary);
    color: var(--text-primary);
}

.container { max-width: 1400px; margin: 0 auto; padding: 20px; }

header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 20px 0;
    border-bottom: 1px solid #334155;
}

header h1 { font-size: 24px; font-weight: 600; }

.status {
    padding: 8px 16px;
    border-radius: 20px;
    font-weight: 500;
}

.status.healthy { background: var(--green); color: white; }
.status.unhealthy { background: var(--red); color: white; }

.metrics-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px;
    margin: 24px 0;
}

.metric-card {
    background: var(--bg-card);
    padding: 20px;
    border-radius: 8px;
}

.metric-card h3 {
    font-size: 14px;
    color: var(--text-secondary);
    margin-bottom: 8px;
}

.metric-card .value {
    font-size: 32px;
    font-weight: 700;
}

.metric-card .value.error { color: var(--red); }

.charts-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
    gap: 16px;
    margin: 24px 0;
}

.chart-container {
    background: var(--bg-card);
    padding: 20px;
    border-radius: 8px;
    height: 300px;
}

.logs-panel { margin-top: 24px; background: var(--bg-card); padding: 20px; border-radius: 8px; }

.logs-panel h3 { margin-bottom: 16px; }

.logs {
    max-height: 400px;
    overflow-y: auto;
}

.log-entry {
    display: grid;
    grid-template-columns: 160px 120px 100px 80px auto;
    gap: 8px;
    padding: 12px;
    border-bottom: 1px solid #334155;
    font-size: 13px;
}

.log-entry.success { border-left: 3px solid var(--green); }
.log-entry.warning { border-left: 3px solid var(--yellow); }
.log-entry.error { border-left: 3px solid var(--red); }

.quality-warning { color: var(--yellow); font-size: 12px; }
```

---

## Phase 4: In-Session Model Switching (Week 5)

### 4.1 Runtime Configuration Manager

**New Files:**
- `vendor/claude-code-proxy/utils/runtime_config.py`
- `vendor/claude-code-proxy/api/model_switching.py`

**Modified Files:**
- `vendor/claude-code-proxy/server.py` - Add model switching detection and endpoint
- `vendor/claude-code-proxy/proxy/proxy.py` - Use runtime config for model selection

**Implementation:**

```python
# utils/runtime_config.py (new)
from dataclasses import dataclass
from typing import Optional, Callable
import threading

@dataclass
class RuntimeConfig:
    """Runtime configuration that can be changed without restart."""
    big_model: Optional[str] = None
    small_model: Optional[str] = None
    preferred_provider: Optional[str] = None

class RuntimeConfigManager:
    def __init__(self, initial_config: dict):
        self.lock = threading.Lock()
        self.runtime = RuntimeConfig(**initial_config)
        self.listeners: list[Callable] = []

    def update(self, **kwargs):
        """Thread-safe config update with notification."""
        with self.lock:
            for key, value in kwargs.items():
                setattr(self.runtime, key, value)
        self._notify_listeners()

    def get(self, key: str):
        """Thread-safe config read."""
        with self.lock:
            return getattr(self.runtime, key, None)

    def register_listener(self, callback: Callable):
        """Register for config change notifications."""
        with self.lock:
            self.listeners.append(callback)

    def _notify_listeners(self):
        """Notify all listeners of config change."""
        for listener in self.listeners:
            try:
                listener(self.runtime)
            except Exception as e:
                logger.error(f"Config listener error: {e}")

# Global instance
runtime_config = RuntimeConfigManager({})
```

```python
# api/model_switching.py (new)
from utils.runtime_config import runtime_config

# Parse model name from /model command
def parse_model_command(content: str, cfg: ProxyConfig) -> Optional[str]:
    """Extract model name from /model command and return target model."""
    model_part = content.split("/model")[-1].strip()

    # Shortcuts
    if model_part == "big":
        return cfg.routing.big_model
    elif model_part == "small":
        return cfg.routing.small_model
    elif model_part == "building":
        return cfg.routing.building_model

    # Direct model name
    return model_part if model_part else None

def apply_model_switch(model: str, cfg: ProxyConfig):
    """Update runtime config with new model."""
    runtime_config.update(big_model=model, small_model=model)
    logger.info(f"[model-switch] Applied: {model}")
    return f"Model switched to: {model}"

# server.py additions

@app.post("/api/config")
async def update_config(request: dict):
    """Update runtime configuration via API."""
    allowed_keys = ["big_model", "small_model", "preferred_provider"]
    updates = {k: v for k, v in request.items() if k in allowed_keys}
    if updates:
        from utils.runtime_config import runtime_config
        runtime_config.update(**updates)
        logger.info(f"[config] Runtime updated: {updates}")
    return {"status": "updated", "config": updates}

@app.get("/api/config")
async def get_config():
    """Get current runtime configuration."""
    from utils.runtime_config import runtime_config
    return {
        "big_model": runtime_config.get("big_model"),
        "small_model": runtime_config.get("small_model"),
        "preferred_provider": runtime_config.get("preferred_provider"),
    }

# In create_message(), detect /model command:
def _is_model_switch_command(request: MessagesRequest) -> bool:
    if not request.messages:
        return False
    last_msg = request.messages[-1]
    if hasattr(last_msg, "content"):
        content = str(last_msg.content)
        return content.strip().startswith("/model ")
    return False

# After processing request, if it was a model switch:
if _is_model_switch_command(request):
    model_name = parse_model_command(str(request.messages[-1].content), cfg)
    response = MessagesResponse(
        id="msg_" + str(int(time.time() * 1000)),
        role="assistant",
        content=[ContentBlock(text=apply_model_switch(model_name, cfg))],
        stop_reason="end_turn",
    )
    return response
```

### 4.2 Model Presets

**New Files:**
- `vendor/claude-code-proxy/presets/registry.json` - Preset definitions
- `vendor/claude-code-proxy/api/presets.py` - Preset management

**Implementation:**

```json
// presets/registry.json
{
  "presets": [
    {
      "id": "quick-start",
      "name": "Quick Start",
      "description": "Balanced performance and cost",
      "big_model": "gpt-4.1",
      "small_model": "gpt-4.1-mini",
      "preferred_provider": "openai"
    },
    {
      "id": "low-cost",
      "name": "Low Cost",
      "description": "Maximum cost optimization",
      "big_model": "gpt-4o-mini",
      "small_model": "gpt-4o-mini",
      "preferred_provider": "openai"
    },
    {
      "id": "high-performance",
      "name": "High Performance",
      "description": "Best quality, higher cost",
      "big_model": "gpt-4.5-preview",
      "small_model": "gpt-4o",
      "preferred_provider": "openai"
    },
    {
      "id": "google-gemini",
      "name": "Google Gemini",
      "description": "Use Gemini models",
      "big_model": "gemini-2.5-pro",
      "small_model": "gemini-2.5-flash",
      "preferred_provider": "google"
    }
  ]
}
```

```python
# api/presets.py (new)
import json
from pathlib import Path
from typing import List, Dict

class PresetManager:
    def __init__(self, registry_path: str = "presets/registry.json"):
        self.registry_path = Path(registry_path)
        self.presets = self._load_registry()

    def _load_registry(self) -> List[Dict]:
        if not self.registry_path.exists():
            return []
        with open(self.registry_path) as f:
            return json.load(f).get("presets", [])

    def list_presets(self) -> List[Dict]:
        return self.presets

    def apply_preset(self, preset_id: str) -> Dict:
        """Apply preset to runtime config."""
        from utils.runtime_config import runtime_config

        preset = next((p for p in self.presets if p["id"] == preset_id), None)
        if not preset:
            raise ValueError(f"Preset not found: {preset_id}")

        runtime_config.update(
            big_model=preset.get("big_model"),
            small_model=preset.get("small_model"),
            preferred_provider=preset.get("preferred_provider")
        )
        logger.info(f"[preset] Applied: {preset['name']} ({preset_id})")
        return preset

# Global instance
preset_manager = PresetManager()

# server.py additions
@app.get("/api/presets")
async def list_presets():
    return preset_manager.list_presets()

@app.post("/api/presets/{preset_id}/apply")
async def apply_preset(preset_id: str):
    try:
        preset = preset_manager.apply_preset(preset_id)
        return {"status": "applied", "preset": preset}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
```

---

## Phase 5: Docker & Deployment (Week 6)

### 5.1 Enhanced Docker Configuration

**New Files:**
- `cloud-provider-ymls/docker-compose.observability.override.yml`
- `cloud-provider-ymls/docker-compose.dashboard.override.yml`

**Modified Files:**
- `docker-compose.yml` - Add observability/dashboad service mounts

**Implementation:**

```yaml
# cloud-provider-ymls/docker-compose.observability.override.yml
services:
  proxy_cloud:
    environment:
      - OBSERVABILITY_PROMETHEUS_ENABLED=1
      - OBSERVABILITY_PROMETHEUS_PORT=9090
      - METRICS_PERSISTENCE_ENABLED=1
    ports:
      - "8083:8083"
      - "9090:9090"  # Prometheus scrape port
    volumes:
      - ./data/metrics.jsonl:/data/metrics.jsonl

  prometheus:
    image: prom/prometheus:v2.48.0
    volumes:
      - ./observability/prometheus.yml:/etc/prometheus/prometheus.yml
    ports:
      - "9091:9090"
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--web.console.libraries=/etc/prometheus/console_libraries'
      - '--web.console.templates=/etc/prometheus/consoles'

  grafana:
    image: grafana/grafana:10.3.1
    volumes:
      - ./observability/grafana/dashboards:/var/lib/grafana/dashboards
      - ./observability/grafana/provisioning:/etc/grafana/provisioning
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD:-admin}
      - GF_USERS_ALLOW_SIGN_UP=false
      - GF_INSTALL_PLUGINS=grafana-piechart-panel
    depends_on:
      - prometheus

# observability/prometheus.yml (new file)
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'claude-code-proxy'
    scrape_interval: 5s
    static_configs:
      - targets: ['proxy_cloud:9090']
```

### 5.2 Installation Script

**New Files:**
- `scripts/cc-proxy-install.sh`

**Implementation:**

```bash
#!/usr/bin/env bash
# Claude Code Proxy Installation Script

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

usage() {
  cat <<'USAGE'
Claude Code Proxy Installation Script

Usage:
  cc-proxy-install [options]

Options:
  --with-observability    Install Prometheus + Grafana stack
  --with-dashboard         Install web dashboard (default: enabled)
  --profile PROFILE         Set initial profile (default: openai)
  --help                  Show this message

Examples:
  cc-proxy-install
  cc-proxy-install --with-observability
  cc-proxy-install --profile gemini
'USAGE'
}

WITH_OBSERVABILITY=0
WITH_DASHBOARD=1
PROFILE="openai"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-observability)
      WITH_OBSERVABILITY=1
      ;;
    --with-dashboard)
      WITH_DASHBOARD=1
      ;;
    --profile)
      PROFILE="$2"
      shift 2
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      echo -e "${YELLOW}Unknown option: $1${NC}"
      exit 1
      ;;
  esac
  shift
done

echo -e "${GREEN}Claude Code Proxy Installation${NC}"
echo -e "Observability: $([[ $WITH_OBSERVABILITY -eq 1 ]] && echo -e "${GREEN}enabled${NC}" || echo -e "${YELLOW}disabled${NC}")"
echo -e "Dashboard: $([[ $WITH_DASHBOARD -eq 1 ]] && echo -e "${GREEN}enabled${NC}" || echo -e "${YELLOW}disabled${NC}")"
echo -e "Profile: ${YELLOW}${PROFILE}${NC}"

# Check prerequisites
if ! command -v docker &> /dev/null; then
  echo -e "${YELLOW}Error: Docker not installed${NC}"
  exit 1
fi

if ! command -v docker compose &> /dev/null; then
  echo -e "${YELLOW}Error: docker compose not installed${NC}"
  exit 1
fi

# Create configuration
if [[ ! -f .env ]]; then
  cp vendor/claude-code-proxy/.env.example .env
  echo -e "${GREEN}Created .env from template${NC}"
  echo -e "${YELLOW}Please edit .env and add your API keys${NC}"
fi

# Start proxy
PROFILE_ENV="profile-envs/cloud.${PROFILE}.env"
bash scripts/cc-proxy-up "docker-compose.$(basename $PROFILE .env).override.yml"

if [[ $WITH_OBSERVABILITY -eq 1 ]]; then
  echo -e "${GREEN}Starting observability stack...${NC}"
  docker compose -f docker-compose.yml \
    -f cloud-provider-ymls/docker-compose.observability.override.yml \
    up -d prometheus grafana

  echo -e "${GREEN}Observability stack started:${NC}"
  echo -e "  Prometheus:  http://localhost:9091"
  echo -e "  Grafana:    http://localhost:3000 (admin/admin)"
fi

if [[ $WITH_DASHBOARD -eq 1 ]]; then
  echo -e "${GREEN}Dashboard available at: http://localhost:8083${NC}"
fi

echo -e "${GREEN}Installation complete!${NC}"
echo -e "Run ${YELLOW}cc-proxy status${NC} to check health"
```

---

## Verification

### End-to-End Testing

```bash
# Phase 1: Observability
curl http://localhost:8083/metrics | grep proxy_requests_total
# Verify Prometheus is scraping
curl http://localhost:9091/api/v1/targets | grep proxy_cloud

# Phase 2: CLI
pip install -e . --force-reinstall
cc-proxy status
cc-proxy logs --tail 10

# Phase 3: Dashboard
open http://localhost:8083
# Verify charts load, metrics update

# Phase 4: Model switching
# In Claude Code, send: /model gpt-4.5-preview
# Verify response confirms switch

# Phase 5: Deployment
bash scripts/cc-proxy-install.sh --with-observability --with-dashboard
docker compose ps  # Should show prometheus, grafana services
```

---

## Critical Files Summary

| File | Purpose |
|------|---------|
| `utils/observability.py` | Prometheus metrics exporter |
| `utils/persistent_metrics.py` | JSONL metrics storage |
| `utils/runtime_config.py` | Runtime config manager |
| `cli/main.py` | CLI entry point |
| `cli/commands/*.py` | CLI command modules |
| `web/static/*` | Dashboard assets |
| `web/api/dashboard.py` | Dashboard API |
| `api/model_switching.py` | Model switching logic |
| `api/presets.py` | Preset management |
| `presets/registry.json` | Preset definitions |
| `server.py` | Add endpoints, static serving, callbacks |
| `config.py` | Add observability/storage config |
| `.env.example` | Add new env vars |
| `docker-compose.observability.override.yml` | Observability stack |
| `scripts/cc-proxy-install.sh` | Installation script |
| `profile-envs/presets/*.env` | Preset profiles |

---

## Dependencies to Add

```toml
# pyproject.toml additions
[project]
dependencies = [
    # Existing...
    "typer>=0.12.0",
    "rich>=13.0.0",
    "prometheus-client>=0.19.0",
    "jinja2>=3.1.0",
]
```

---

## Timeline

- **Week 1**: Observability integration (Prometheus, LiteLLM callbacks, persistent storage)
- **Week 2**: CLI tool (status, config, logs, health commands)
- **Week 3-4**: Web dashboard (real-time metrics, charts, live logs)
- **Week 5**: In-session model switching (/model command, runtime config, presets)
- **Week 6**: Deployment (Docker compose, installation script, documentation)
