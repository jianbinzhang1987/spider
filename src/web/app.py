"""FastAPI web application for the component price search system.

Endpoints:
- POST /api/upload — Upload Excel file, returns task_id
- GET /api/task/{task_id}/status — Get task progress (JSON or SSE)
- GET /api/task/{task_id}/download — Download result Excel
- GET / — Frontend page
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.scheduler import BatchScheduler, SearchItem, SearchResultRow, TaskProgress
from src.web.excel_handler import parse_upload_excel, generate_result_excel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="元器件比价系统", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory task store
tasks: dict[str, TaskProgress] = {}
task_results: dict[str, list[SearchResultRow]] = {}


@dataclass
class VerificationProgress:
    task_id: str
    adapter: str
    adapter_label: str
    mpn: str
    status: str = "pending"  # pending, running, completed, failed
    message: str = "准备打开浏览器"
    error: str | None = None
    session_path: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


verification_tasks: dict[str, VerificationProgress] = {}
verification_handles: dict[str, dict[str, Any]] = {}

VERIFY_ADAPTERS = {
    "icdeal": {"label": "百能云芯", "session": "data/sessions/icdeal.json"},
    "allchips": {"label": "硬之城", "session": "data/sessions/allchips.json"},
    "icgoo": {"label": "ICGOO", "session": "data/sessions/icgoo.json"},
    "icnet": {"label": "IC交易网", "session": "data/sessions/icnet.json"},
    "digikey": {"label": "Digi-Key", "session": "data/sessions/digikey.json"},
    "mouser": {"label": "Mouser", "session": "data/sessions/mouser.json"},
}

VERIFY_URLS = {
    "icdeal": "https://www.icdeal.com/s/{mpn}/",
    "allchips": "https://www.allchips.com/search?key={mpn}&sp=2",
    "icgoo": "https://www.icgoo.net/search/{mpn}/1",
    "icnet": "https://www.ic.net.cn/search/{mpn}.html",
    "digikey": "https://www.digikey.cn/zh/products/result?keywords={mpn}",
    "mouser": "https://www.mouser.cn/c/?q={mpn}",
}

# Output directory for generated Excel files
OUTPUT_DIR = Path("/tmp/spider_output")
OUTPUT_DIR.mkdir(exist_ok=True)

# Upload directory
UPLOAD_DIR = Path("/tmp/spider_uploads")
UPLOAD_DIR.mkdir(exist_ok=True)


@app.post("/api/upload")
async def upload_excel(file: UploadFile = File(...), adapters: str = Query(default="")):
    """Upload an Excel file and start batch search."""
    if not file.filename or not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(400, "请上传 .xlsx 或 .xls 格式的Excel文件")

    task_id = str(uuid.uuid4())[:8]
    upload_path = UPLOAD_DIR / f"{task_id}_{file.filename}"

    content = await file.read()
    upload_path.write_bytes(content)

    try:
        items = parse_upload_excel(upload_path)
    except Exception as e:
        upload_path.unlink(missing_ok=True)
        raise HTTPException(400, f"Excel解析失败: {e}")

    if not items:
        upload_path.unlink(missing_ok=True)
        raise HTTPException(400, "Excel中没有找到有效数据（需要包含'型号'列）")

    # Initialize progress
    adapter_list = [a.strip() for a in adapters.split(",") if a.strip()] if adapters else None
    progress = TaskProgress(total_items=len(items))
    tasks[task_id] = progress

    # Start background task
    asyncio.create_task(_run_search_task(task_id, items, adapter_list))

    return {
        "task_id": task_id,
        "total_items": len(items),
        "message": f"任务已创建，正在查询 {len(items)} 个型号",
    }


@app.get("/api/task/{task_id}/status")
async def get_task_status(task_id: str):
    """Get current task progress."""
    progress = tasks.get(task_id)
    if not progress:
        raise HTTPException(404, "任务不存在")

    return {
        "task_id": task_id,
        "status": progress.status,
        "total_items": progress.total_items,
        "completed_items": progress.completed_items,
        "total_queries": progress.total_queries,
        "completed_queries": progress.completed_queries,
        "current_item": progress.current_item,
        "progress_pct": round(
            progress.completed_items / max(progress.total_items, 1) * 100, 1
        ),
    }


@app.get("/api/task/{task_id}/download")
async def download_result(task_id: str):
    """Download the result Excel file."""
    progress = tasks.get(task_id)
    if not progress:
        raise HTTPException(404, "任务不存在")

    if progress.status != "completed":
        raise HTTPException(400, f"任务尚未完成，当前状态: {progress.status}")

    results = task_results.get(task_id)
    if not results:
        raise HTTPException(404, "没有查询结果")

    output_path = OUTPUT_DIR / f"比价结果_{task_id}.xlsx"
    generate_result_excel(results, output_path)

    from urllib.parse import quote
    encoded_filename = quote(f"比价结果_{task_id}.xlsx")
    download_name = f"比价结果_{task_id}.xlsx"
    return FileResponse(
        output_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=download_name,
        headers={
            "Content-Disposition": (
                f'attachment; filename="compare_result_{task_id}.xlsx"; '
                f"filename*=UTF-8''{encoded_filename}"
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/adapters")
async def list_adapters():
    """List all available adapters."""
    from src.adapters.registry import AdapterRegistry
    # Import all adapters to trigger registration
    from src.adapters import oneyac, hqew, wlxmall, cmalls, icgoo, icstk, icdeal, allchips, ichunt, icnet, vipmro  # noqa: F401
    from src.adapters import digikey, mouser, element14, lcsc, ickey  # noqa: F401

    return {"adapters": sorted(AdapterRegistry.list_adapters())}


@app.post("/api/verification/{adapter_name}")
async def start_site_verification(
    adapter_name: str,
    mpn: str = Query(default="RC0402FR-0710KL"),
):
    """Open a visible browser for captcha/login preflight and save session."""
    if adapter_name not in VERIFY_ADAPTERS:
        raise HTTPException(400, "该站点暂不支持Web验证入口")

    for task in verification_tasks.values():
        if task.adapter == adapter_name and task.status in {"pending", "running"}:
            return _verification_payload(task)

    task_id = str(uuid.uuid4())[:8]
    meta = VERIFY_ADAPTERS[adapter_name]
    progress = VerificationProgress(
        task_id=task_id,
        adapter=adapter_name,
        adapter_label=meta["label"],
        mpn=mpn,
        session_path=meta["session"],
    )
    verification_tasks[task_id] = progress
    asyncio.create_task(_run_verification_task(task_id))
    return _verification_payload(progress)


@app.get("/api/verification/{task_id}/status")
async def get_site_verification_status(task_id: str):
    """Get visible-browser verification progress."""
    progress = verification_tasks.get(task_id)
    if not progress:
        raise HTTPException(404, "验证任务不存在")
    return _verification_payload(progress)


@app.post("/api/verification/{task_id}/complete")
async def complete_site_verification(task_id: str):
    """User confirms the visible-browser verification is done; save session and close."""
    progress = verification_tasks.get(task_id)
    if not progress:
        raise HTTPException(404, "验证任务不存在")
    handle = verification_handles.get(task_id)
    if not handle:
        raise HTTPException(400, "验证浏览器已结束或尚未启动")
    event = handle.get("done_event")
    if event:
        event.set()
    progress.message = "正在保存 session 并关闭浏览器"
    return _verification_payload(progress)


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the frontend page."""
    html_path = Path(__file__).parent / "static" / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>元器件比价系统</h1><p>前端文件未找到</p>")


async def _run_search_task(
    task_id: str,
    items: list[SearchItem],
    adapter_names: list[str] | None,
) -> None:
    """Background task to run batch search."""
    progress = tasks[task_id]

    try:
        # Import adapters to trigger registration
        from src.adapters import oneyac, hqew, wlxmall, cmalls, icgoo, icstk, icdeal, allchips, ichunt, icnet, vipmro  # noqa: F401
        from src.adapters import digikey, mouser, element14, lcsc, ickey  # noqa: F401

        # Default to the implemented production candidates.
        if adapter_names is None:
            adapter_names = ["digikey", "mouser", "element14", "lcsc", "ickey",
                            "oneyac", "hqew", "wlxmall", "cmalls", "icgoo",
                            "icstk", "vipmro", "icdeal", "allchips", "icnet"]

        scheduler = BatchScheduler(adapter_names=adapter_names, max_concurrent=5)

        # Determine if browser adapters are needed
        from src.adapters.base import BrowserAdapter
        from src.adapters.registry import AdapterRegistry
        needs_browser = any(
            issubclass(AdapterRegistry.get(n), BrowserAdapter)
            for n in adapter_names
            if AdapterRegistry.get(n) is not None
        )

        results = await scheduler.run(items, progress, use_browser=needs_browser)
        task_results[task_id] = results
        progress.status = "completed"
        logger.info(f"Task {task_id} completed: {len(results)} results")
    except Exception as e:
        progress.status = "failed"
        logger.error(f"Task {task_id} failed: {e}")


async def _run_verification_task(task_id: str) -> None:
    """Run one visible-browser verification task."""
    progress = verification_tasks[task_id]
    progress.status = "running"
    progress.started_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    progress.message = "已打开可见浏览器，请完成验证码/登录后回到本页面点击“保存session”"

    pool = None
    page = None
    try:
        from src.core.browser_pool import BrowserPool
        from src.preflight_verification import _merge_browser_sessions

        session_path = progress.session_path or f"data/sessions/{progress.adapter}.json"
        before_mtime = _file_mtime(session_path)
        before_size = _file_size(session_path)
        target_tpl = VERIFY_URLS.get(progress.adapter)
        if not target_tpl:
            raise RuntimeError("未配置该站点的验证入口")
        target_url = target_tpl.format(mpn=quote(progress.mpn))

        pool = BrowserPool(max_pages=1, headless=False, storage_state_path=session_path)
        await asyncio.wait_for(pool.start(), timeout=30)
        page = await pool.acquire_page()
        verification_handles[task_id] = {
            "pool": pool,
            "page": page,
            "done_event": asyncio.Event(),
        }
        await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.bring_to_front()
        except Exception:
            pass

        done_event = verification_handles[task_id]["done_event"]
        try:
            await asyncio.wait_for(done_event.wait(), timeout=900)
        except asyncio.TimeoutError:
            progress.error = "验证窗口等待超过15分钟，已自动保存当前session并关闭"

        progress.message = "正在保存 session"
        if page:
            await asyncio.wait_for(pool.release_page(page), timeout=20)
            page = None
        if pool:
            await asyncio.wait_for(pool.stop(), timeout=15)
            pool = None
        _merge_browser_sessions()

        after_mtime = _file_mtime(session_path)
        after_size = _file_size(session_path)
        session_saved = after_size > 0 and (
            after_mtime != before_mtime or after_size != before_size
        )

        progress.completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        if session_saved:
            progress.status = "completed"
            progress.message = "session 已保存；后续批量查询会复用该验证状态"
        elif progress.error:
            progress.status = "failed"
            progress.message = "验证窗口已关闭，但未检测到新的 session 文件"
        else:
            progress.status = "failed"
            progress.error = "未检测到新的 session 文件，请确认验证码/登录是否完成"
    except Exception as e:
        progress.status = "failed"
        progress.error = str(e)
        progress.message = "验证任务失败"
        progress.completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        logger.exception("Verification task %s failed", task_id)
    finally:
        verification_handles.pop(task_id, None)
        if page is not None and pool is not None:
            try:
                await pool.release_page(page)
            except Exception:
                pass
        if pool is not None:
            try:
                await pool.stop()
            except Exception:
                pass


def _verification_payload(progress: VerificationProgress) -> dict[str, Any]:
    return {
        "task_id": progress.task_id,
        "adapter": progress.adapter,
        "adapter_label": progress.adapter_label,
        "mpn": progress.mpn,
        "status": progress.status,
        "message": progress.message,
        "error": progress.error,
        "session_path": progress.session_path,
        "started_at": progress.started_at,
        "completed_at": progress.completed_at,
    }


def _file_mtime(path: str) -> float | None:
    p = Path(path)
    return p.stat().st_mtime if p.exists() else None


def _file_size(path: str) -> int:
    p = Path(path)
    return p.stat().st_size if p.exists() else 0
