"""web/deps.py — FastAPI 依赖注入工具。

R-06: 消除 7 处 service-getter 模板代码（getattr → None → 503 ApiError）。
R-07: 消除 tasks/scenarios 重复的任务结果→HTTP异常映射。
"""
from __future__ import annotations

from typing import Any

from fastapi import Request

from campus_ids.web.errors import AlreadyRunningError, ApiError


def get_service(request: Request, attr: str, label: str) -> Any:
    """从 app.state 获取服务实例；未初始化时抛 503。

    Parameters:
        request: FastAPI Request 对象
        attr: app.state 上的属性名（如 "alert_service"）
        label: 服务中文名（用于错误提示，如 "告警"）
    """
    service = getattr(request.app.state, attr, None)
    if service is None:
        raise ApiError(
            error_code="SERVICE_UNAVAILABLE",
            detail=f"{label}服务未初始化",
            status_code=503,
        )
    return service


def raise_for_task_result(
    result: dict[str, Any],
    resource_label: str,
    *,
    already_running_msg: str | None = None,
    error_code: str = "SERVICE_UNAVAILABLE",
) -> None:
    """检查任务/剧本启动结果，异常状态抛对应 HTTP 异常。

    Parameters:
        result: TaskRegistry.start() 或 ScenarioService.start_scenario() 返回的字典
        resource_label: 资源名（如任务名或剧本名），用于默认错误消息
        already_running_msg: 已运行时的自定义消息（默认 "任务 '{label}' 已在运行"）
        error_code: error 状态时的错误码（默认 SERVICE_UNAVAILABLE）
    """
    status = result.get("status")
    if status == "already_running":
        raise AlreadyRunningError(already_running_msg or resource_label)
    if status == "error":
        raise ApiError(
            error_code=error_code,
            detail=result.get("message", f"'{resource_label}' 暂不可用"),
            status_code=503,
        )