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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

    return FileResponse(
        output_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"比价结果_{task_id}.xlsx",
    )


@app.get("/api/adapters")
async def list_adapters():
    """List all available adapters."""
    from src.adapters.registry import AdapterRegistry
    # Import all adapters to trigger registration
    from src.adapters import oneyac, hqew, wlxmall, cmalls, icgoo, icstk, icdeal, allchips, ichunt, icnet, vipmro  # noqa: F401
    from src.adapters import digikey, mouser, element14, lcsc, ickey  # noqa: F401

    return {"adapters": sorted(AdapterRegistry.list_adapters())}


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

        # Default to API-only adapters if none specified (faster, no browser needed)
        if adapter_names is None:
            adapter_names = ["digikey", "mouser", "element14", "lcsc", "ickey",
                            "oneyac", "hqew", "wlxmall", "cmalls", "icgoo"]

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
