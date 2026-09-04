"""Small web dashboard for inspecting scripts and completed pipeline runs."""

from __future__ import annotations

import html
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .script_parser import ScriptFormatError, parse_script_file


ROOT = Path(os.environ.get("APP_ROOT", ".")).resolve()
SCRIPTS_DIR = ROOT / "scripts"
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", ROOT / "output")).resolve()


def _script_cards() -> tuple[list[str], int, int]:
    cards: list[str] = []
    total_segments = 0
    total_words = 0
    for path in sorted(SCRIPTS_DIR.glob("*.md")):
        try:
            doc = parse_script_file(str(path))
        except (OSError, ScriptFormatError):
            continue
        total_segments += len(doc.segments)
        total_words += doc.word_count
        description = doc.description or "Ready for voiceover, visuals, and assembly."
        cards.append(
            f"""
            <article class="script-card">
              <div class="card-topline"><span>Ready script</span><code>{html.escape(path.name)}</code></div>
              <h3>{html.escape(doc.title)}</h3>
              <p>{html.escape(description)}</p>
              <div class="card-stats">
                <span><strong>{len(doc.segments)}</strong> scenes</span>
                <span><strong>{doc.word_count:,}</strong> words</span>
                <span><strong>{len(doc.tags)}</strong> tags</span>
              </div>
            </article>
            """
        )
    return cards, total_segments, total_words


def _run_rows() -> list[str]:
    rows: list[str] = []
    if not OUTPUT_DIR.exists():
        return rows
    run_dirs = sorted((p for p in OUTPUT_DIR.iterdir() if p.is_dir()), reverse=True)
    for run_dir in run_dirs[:6]:
        report_path = run_dir / "report.json"
        final_path = run_dir / "final.mp4"
        status = "In progress"
        status_class = "working"
        detail = "Pipeline files are being prepared"
        if report_path.exists():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                passed = bool(report.get("passed"))
                status = "Passed" if passed else "Needs review"
                status_class = "passed" if passed else "review"
                checks = report.get("checks", [])
                detail = f"{sum(bool(c.get('ok')) for c in checks)} of {len(checks)} quality checks passed"
            except (OSError, ValueError, TypeError):
                status = "Report error"
                status_class = "review"
                detail = "The quality report could not be read"
        elif final_path.exists():
            status = "Rendered"
            status_class = "passed"
            detail = "Final video is ready"
        rows.append(
            f"""
            <li>
              <div><strong>{html.escape(run_dir.name)}</strong><span>{html.escape(detail)}</span></div>
              <span class="status {status_class}">{status}</span>
            </li>
            """
        )
    return rows


def render_dashboard() -> str:
    cards, total_segments, total_words = _script_cards()
    runs = _run_rows()
    scripts_count = len(cards)
    scripts_markup = "".join(cards) or '<p class="empty">Add a Markdown script to <code>scripts/</code> to begin.</p>'
    runs_markup = "".join(runs) or """
      <li class="empty-run">
        <span class="empty-icon">01</span>
        <div><strong>No renders yet</strong><span>Your first completed pipeline run will appear here.</span></div>
      </li>
    """
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nostalgia Studio</title>
  <style>
    :root {{ color-scheme: dark; --ink: #f4efe5; --muted: #a9a69f; --panel: #171817; --line: #30312e; --amber: #e4a853; --green: #82b395; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #0e0f0e; color: var(--ink); font: 15px/1.55 Inter, ui-sans-serif, system-ui, sans-serif; }}
    body::before {{ content: ""; display: block; height: 4px; background: var(--amber); }}
    main {{ width: min(1120px, calc(100% - 40px)); margin: 0 auto; padding: 34px 0 64px; }}
    header {{ display: flex; justify-content: space-between; align-items: center; gap: 24px; padding-bottom: 30px; border-bottom: 1px solid var(--line); }}
    .brand {{ display: flex; align-items: center; gap: 13px; font-weight: 750; letter-spacing: .02em; }}
    .brand-mark {{ display: grid; place-items: center; width: 38px; height: 38px; border: 1px solid #6e522d; background: #241d13; color: var(--amber); font: 700 12px/1 ui-monospace, monospace; }}
    .online {{ display: flex; align-items: center; gap: 9px; color: #c8c7c1; font-size: 13px; }}
    .online::before {{ content: ""; width: 8px; height: 8px; border-radius: 50%; background: var(--green); box-shadow: 0 0 0 4px #82b3951f; }}
    .hero {{ display: grid; grid-template-columns: 1.45fr .8fr; gap: 56px; align-items: end; padding: 70px 0 52px; }}
    .eyebrow {{ margin: 0 0 16px; color: var(--amber); font: 700 12px/1.2 ui-monospace, monospace; letter-spacing: .14em; text-transform: uppercase; }}
    h1 {{ max-width: 760px; margin: 0; font: 600 clamp(42px, 6vw, 72px)/1.04 Georgia, serif; letter-spacing: -.035em; }}
    .hero-copy {{ margin: 23px 0 0; max-width: 650px; color: var(--muted); font-size: 17px; }}
    .command {{ padding: 20px; border: 1px solid var(--line); background: var(--panel); }}
    .command span {{ display: block; margin-bottom: 11px; color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .1em; }}
    code {{ color: #edbd78; font: 12px/1.5 ui-monospace, SFMono-Regular, monospace; overflow-wrap: anywhere; }}
    .metrics {{ display: grid; grid-template-columns: repeat(3, 1fr); border: 1px solid var(--line); background: var(--panel); }}
    .metric {{ padding: 25px 28px; border-right: 1px solid var(--line); }}
    .metric:last-child {{ border: 0; }}
    .metric strong {{ display: block; font: 500 30px/1.1 Georgia, serif; }}
    .metric span {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }}
    section {{ margin-top: 54px; }}
    .section-heading {{ display: flex; justify-content: space-between; align-items: baseline; gap: 20px; margin-bottom: 18px; }}
    h2 {{ margin: 0; font: 500 25px/1.2 Georgia, serif; }}
    .section-heading > span {{ color: var(--muted); font-size: 13px; }}
    .scripts {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }}
    .script-card {{ padding: 25px; border: 1px solid var(--line); background: var(--panel); }}
    .card-topline {{ display: flex; justify-content: space-between; gap: 12px; color: var(--green); font-size: 11px; text-transform: uppercase; letter-spacing: .08em; }}
    .script-card h3 {{ margin: 20px 0 10px; font: 500 22px/1.25 Georgia, serif; }}
    .script-card p {{ display: -webkit-box; min-height: 48px; margin: 0; overflow: hidden; color: var(--muted); -webkit-line-clamp: 2; -webkit-box-orient: vertical; }}
    .card-stats {{ display: flex; gap: 22px; margin-top: 24px; padding-top: 18px; border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; }}
    .card-stats strong {{ color: var(--ink); font-size: 14px; }}
    .runs {{ padding: 0; margin: 0; border: 1px solid var(--line); background: var(--panel); list-style: none; }}
    .runs li {{ display: flex; justify-content: space-between; align-items: center; gap: 20px; padding: 18px 22px; border-bottom: 1px solid var(--line); }}
    .runs li:last-child {{ border: 0; }}
    .runs li div span {{ display: block; color: var(--muted); font-size: 12px; }}
    .status {{ padding: 5px 9px; border: 1px solid #4a4b47; color: #d0d0cb; font-size: 11px; text-transform: uppercase; letter-spacing: .06em; }}
    .status.passed {{ border-color: #426251; color: #a8d1b7; }}
    .status.review {{ border-color: #755932; color: #edbd78; }}
    .empty-run {{ justify-content: flex-start !important; }}
    .empty-icon {{ display: grid !important; place-items: center; width: 42px; height: 42px; background: #24211b; color: var(--amber) !important; font: 700 12px/1 ui-monospace, monospace; }}
    footer {{ display: flex; justify-content: space-between; gap: 20px; margin-top: 54px; padding-top: 24px; border-top: 1px solid var(--line); color: #777873; font-size: 12px; }}
    @media (max-width: 760px) {{ main {{ width: min(100% - 28px, 1120px); padding-top: 24px; }} .hero {{ grid-template-columns: 1fr; gap: 30px; padding-top: 48px; }} .metrics, .scripts {{ grid-template-columns: 1fr; }} .metric {{ border-right: 0; border-bottom: 1px solid var(--line); }} header, footer {{ align-items: flex-start; }} .section-heading > span {{ display: none; }} }}
  </style>
</head>
<body>
  <main>
    <header>
      <div class="brand"><span class="brand-mark">NS</span>Nostalgia Studio</div>
      <div class="online">Pipeline workspace online</div>
    </header>
    <div class="hero">
      <div>
        <p class="eyebrow">Faceless video production</p>
        <h1>Stories remembered.<br>Videos ready to publish.</h1>
        <p class="hero-copy">Turn researched nostalgia scripts into narrated, illustrated, quality-checked YouTube packages with one repeatable pipeline.</p>
      </div>
      <div class="command"><span>Render the sample episode</span><code>docker compose run --rm video --script scripts/ep01_vanished_places.md</code></div>
    </div>
    <div class="metrics">
      <div class="metric"><strong>{scripts_count}</strong><span>Scripts ready</span></div>
      <div class="metric"><strong>{total_segments}</strong><span>Visual scenes</span></div>
      <div class="metric"><strong>{total_words:,}</strong><span>Narration words</span></div>
    </div>
    <section>
      <div class="section-heading"><h2>Production slate</h2><span>Markdown scripts available to the pipeline</span></div>
      <div class="scripts">{scripts_markup}</div>
    </section>
    <section>
      <div class="section-heading"><h2>Recent renders</h2><span>Live from the output directory</span></div>
      <ul class="runs">{runs_markup}</ul>
    </section>
    <footer><span>Voiceover / archival visuals / subtitles / thumbnail / quality gates</span><span>Alloy development session</span></footer>
  </main>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/healthz":
            self._send("application/json", b'{"status":"ok"}')
            return
        if path == "/":
            self._send("text/html; charset=utf-8", render_dashboard().encode())
            return
        self._send("text/plain; charset=utf-8", b"Not found", status=404)

    def _send(self, content_type: str, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"dashboard: {format % args}", flush=True)


def main() -> None:
    host = os.environ.get("WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("WEB_PORT", "3000"))
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"Nostalgia Studio listening on http://{host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
