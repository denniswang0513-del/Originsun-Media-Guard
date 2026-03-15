"""
report_generator.py
───────────────────
Renders a ReportManifest into a self-contained HTML file using Jinja2.
All CSS, Base64 thumbnails and data are embedded — no external resources required.
"""

import os
from datetime import datetime
from typing import Optional

# Lazy import so server can still start even if Jinja2 is missing.
# If missing, auto-install and retry — self-healing for new deployments.
try:
    from jinja2 import Environment, FileSystemLoader, select_autoescape  # type: ignore
    _JINJA2_AVAILABLE = True
except ImportError:
    try:
        import subprocess as _sp, sys as _sys
        _sp.run(
            [_sys.executable, "-m", "pip", "install", "Jinja2", "--quiet",
             "--no-warn-script-location"],
            capture_output=True, timeout=60
        )
        from jinja2 import Environment, FileSystemLoader, select_autoescape  # type: ignore
        _JINJA2_AVAILABLE = True
    except Exception:
        _JINJA2_AVAILABLE = False

try:
    from playwright.async_api import async_playwright  # type: ignore
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    try:
        import subprocess as _sp, sys as _sys
        _sp.run([_sys.executable, "-m", "pip", "install", "playwright", "--quiet", "--no-warn-script-location"], capture_output=True, timeout=90)
        _sp.run([_sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"], capture_output=True, timeout=300)
        from playwright.async_api import async_playwright  # type: ignore
        _PLAYWRIGHT_AVAILABLE = True
    except Exception:
        _PLAYWRIGHT_AVAILABLE = False


def _fmt_size(size_bytes: int) -> str:
    """Return a human-readable file size string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes //= 1024  # type: ignore
    return f"{size_bytes} TB"


def _fmt_clip_duration(seconds: float) -> str:
    """Format a duration in seconds → HH:MM:SS or MM:SS."""
    if not seconds or seconds <= 0:
        return "—"
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _fmt_duration(start: datetime, end: Optional[datetime]) -> str:
    if end is None:
        return "—"
    delta = end - start
    h, rem = divmod(int(delta.total_seconds()), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    elif m:
        return f"{m}m {s}s"
    return f"{s}s"


def generate_report(manifest) -> str:  # type: ignore
    """
    Render a ReportManifest into a self-contained HTML string.

    Parameters
    ----------
    manifest : ReportManifest
        The fully populated manifest object from core_engine.

    Returns
    -------
    str
        Complete HTML content ready to be written to disk.
    """
    if not _JINJA2_AVAILABLE:
        raise RuntimeError("Jinja2 is not installed. Run: pip install Jinja2")

    # Locate the templates directory relative to this file
    base_dir = os.path.dirname(os.path.abspath(__file__))
    template_dir = os.path.join(base_dir, "templates")

    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html"]),
    )

    # Attach helper filters
    env.filters["fmt_size"] = lambda b: _fmt_size(int(b))  # type: ignore
    env.filters["fmt_clip_dur"] = lambda s: _fmt_clip_duration(float(s or 0))  # type: ignore

    template = env.get_template("report.html")

    context = {
        "manifest": manifest,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed": _fmt_duration(manifest.start_time, manifest.end_time),
        "total_size_str": _fmt_size(manifest.total_bytes),
    }

    return template.render(**context)


def save_report(manifest, output_dir: str, custom_name: str = "") -> str:  # type: ignore
    """
    Generate and save the HTML report to `output_dir/Originsun_Reports/`.

    Parameters
    ----------
    custom_name : str, optional
        If provided, the file is saved as `{custom_name}.html`.
        Otherwise defaults to `Report_{project}_{timestamp}.html`.

    Returns
    -------
    str
        Absolute path of the saved HTML file.
    """
    html_content = generate_report(manifest)

    report_dir = os.path.join(output_dir, "Originsun_Reports")
    os.makedirs(report_dir, exist_ok=True)

    if custom_name:
        # Sanitise to avoid path-traversal characters
        safe_name = "".join(c for c in custom_name if c.isalnum() or c in " _-").strip()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{safe_name or 'Report'}_{timestamp}.html"
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"Report_{manifest.project_name}_{timestamp}.html"

    out_path = os.path.join(report_dir, filename)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return out_path


async def generate_pdf_from_html(local_html_path: str, output_pdf_path: str) -> bool:
    """
    Render a local HTML file to a PDF document using headless Chromium (Playwright).
    """
    if not _PLAYWRIGHT_AVAILABLE:
        print("[warn] Playwright is not available, skipping PDF generation.")
        return False
        
    try:
        file_url = f"file:///{local_html_path.replace(os.sep, '/')}"
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            # Wait for all resources (e.g. Base64 images) to fully load
            await page.goto(file_url, wait_until="networkidle")
            await page.pdf(path=output_pdf_path, print_background=True, format="A4")
            await browser.close()
        return True
    except Exception as e:
        print(f"[error] PDF Generation Failed: {e}")
        return False
