"""攻击模拟共享状态与启动逻辑。

L5: 从 bp_admin.py 下沉，消除 bp_model.py 跨蓝图 import 私有函数的架构瑕疵。
"""
from __future__ import annotations

import logging
import threading
import time as _time

logger = logging.getLogger(__name__)


class AttackSimState:
    """攻击模拟状态管理（封装全局变量，消除 global 语句）。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.sim = None          # AttackSimulator 实例
        self.running = False
        self.type = ""
        self.start_time = 0.0
        self.duration = 0        # 攻击持续时长（秒），用于自动停止检测


sim_state = AttackSimState()
"""全局攻击模拟状态实例（供蓝图模块共享）。"""


def _attack_watchdog(duration: int) -> None:
    """攻击模拟看门狗：在 duration 到期后自动清理状态。"""
    _time.sleep(duration + 1)  # 等待攻击自然结束 + 1秒缓冲
    with sim_state.lock:
        if sim_state.running and sim_state.sim is not None:
            # 检查所有攻击线程是否已结束
            all_dead = all(not t.is_alive() for t in sim_state.sim._threads)
            if all_dead:
                sim_state.running = False
                sim_state.type = ""
                sim_state.duration = 0
                sim_state.sim = None
                logger.info("攻击模拟已自动结束（持续 %ds）", duration)


def start_attack_sim(attack_type: str, duration: int) -> tuple | None:
    """启动攻击模拟的公共逻辑。

    Returns:
        tuple: (error_response, status_code) 如果启动失败
        None: 如果启动成功
    """
    valid_types = ('syn_flood', 'port_scan', 'udp_flood', 'brute_force', 'lateral', 'all')
    if attack_type not in valid_types:
        return ({'status': 'failed', 'message': f'无效攻击类型，可选: {valid_types}'}), 400

    with sim_state.lock:
        if sim_state.running:
            return ({'status': 'failed', 'message': '攻击模拟正在运行中，请先停止'}), 409

        from campus_ids.demo.attack_sim import AttackSimulator
        sim_state.sim = AttackSimulator()
        sim_state.type = attack_type
        sim_state.start_time = _time.time()
        sim_state.duration = duration
        sim_state.running = True

        if attack_type == 'all':
            sim_state.sim.start_all(duration)
        else:
            sim_state.sim._running = True  # D2: 单类型攻击需手动置标志
            method_map = {
                'syn_flood': sim_state.sim.inject_syn_flood,
                'port_scan': sim_state.sim.inject_port_scan,
                'udp_flood': sim_state.sim.inject_udp_flood,
                'brute_force': sim_state.sim.inject_brute_force,
                'lateral': sim_state.sim.inject_lateral_movement,
            }
            t = threading.Thread(target=method_map[attack_type], args=(duration,), daemon=True)
            t.start()
            sim_state.sim._threads.append(t)

        watchdog = threading.Thread(target=_attack_watchdog, args=(duration,), daemon=True)
        watchdog.start()

    logger.info("攻击模拟已启动: type=%s, duration=%ds", attack_type, duration)
    return None