"""runtime — 运行期基础设施包。

包含：配置、事件总线、任务注册表、状态容器、数据访问层。
所有模块可独立 import，不产生 I/O 副作用（不建库、不起线程、不读配置）。
"""
from campus_ids.runtime.settings import Settings, get_settings
from campus_ids.runtime.events import EventBus
from campus_ids.runtime.tasks import TaskRegistry, TaskKind
from campus_ids.runtime.state import RuntimeState
from campus_ids.runtime.db import metadata, init_db, get_connection, get_engine, reset_engine

__all__ = [
    "Settings", "get_settings",
    "EventBus",
    "TaskRegistry", "TaskKind",
    "RuntimeState",
    "metadata", "init_db", "get_connection", "get_engine", "reset_engine",
]