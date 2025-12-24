"""
Scheduler Service - Load balancing and invoker management for OpenWhisk ARM controller.

Manages invoker registry, health monitoring, and load balancing decisions.
"""

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from .messaging import MessagingService

logger = logging.getLogger(__name__)


@dataclass
class InvokerCapacity:
    """Invoker capacity metrics."""

    total_memory: int
    available_memory: int
    warm_containers: int
    busy_containers: int
    prewarm_containers: int
    supported_runtimes: list[str] = field(default_factory=list)


@dataclass
class InvokerInfo:
    """Invoker information and status."""

    invoker_id: str
    last_heartbeat: datetime
    capacity: InvokerCapacity
    status: str  # healthy, unhealthy, draining


class SchedulerService:
    """
    Scheduler service for load balancing and invoker management.

    Tracks available invokers, monitors health via heartbeats, and selects
    optimal invokers for action execution.
    """

    def __init__(self, messaging: MessagingService) -> None:
        """
        Initialize scheduler service.

        Args:
            messaging: MessagingService instance for heartbeat monitoring
        """
        self.messaging = messaging
        self._invokers: dict[str, InvokerInfo] = {}
        self._lock = threading.RLock()
        self._monitoring_thread: Optional[threading.Thread] = None
        self._stop_monitoring = threading.Event()

        logger.info("SchedulerService initialized")

    def start_monitoring(self) -> None:
        """Start background heartbeat monitoring thread."""
        if self._monitoring_thread is not None and self._monitoring_thread.is_alive():
            logger.warning("Monitoring thread already running")
            return

        self._stop_monitoring.clear()
        self._monitoring_thread = threading.Thread(
            target=self._monitor_heartbeats,
            daemon=True,
            name="scheduler-monitor"
        )
        self._monitoring_thread.start()
        logger.info("Started heartbeat monitoring thread")

    def stop_monitoring(self) -> None:
        """Stop background heartbeat monitoring thread."""
        if self._monitoring_thread is None:
            return

        self._stop_monitoring.set()
        if self._monitoring_thread.is_alive():
            self._monitoring_thread.join(timeout=5.0)
        logger.info("Stopped heartbeat monitoring thread")

    def _monitor_heartbeats(self) -> None:
        """
        Background task to monitor heartbeat stream.

        Continuously listens for invoker heartbeats and updates registry.
        Marks invokers as unhealthy if no heartbeat received in 30 seconds.
        """
        logger.info("Heartbeat monitoring started")

        while not self._stop_monitoring.is_set():
            try:
                # Check for stale invokers every second
                self._check_stale_invokers()

                # Process heartbeat messages
                # In production, this would consume from messaging stream
                # For now, just sleep and check stale invokers
                self._stop_monitoring.wait(timeout=1.0)

            except Exception as e:
                logger.error(f"Error in heartbeat monitoring: {e}", exc_info=True)

        logger.info("Heartbeat monitoring stopped")

    def _check_stale_invokers(self) -> None:
        """Mark invokers as unhealthy if no heartbeat in 30 seconds."""
        stale_threshold = datetime.utcnow() - timedelta(seconds=30)

        with self._lock:
            for invoker_id, info in self._invokers.items():
                if info.status == "healthy" and info.last_heartbeat < stale_threshold:
                    info.status = "unhealthy"
                    logger.warning(
                        f"Invoker {invoker_id} marked unhealthy - "
                        f"last heartbeat: {info.last_heartbeat}"
                    )

    def update_invoker_status(self, heartbeat: dict) -> None:
        """
        Update invoker information from heartbeat message.

        Args:
            heartbeat: Heartbeat message containing invoker status
                Expected format:
                {
                    "invoker_id": str,
                    "timestamp": str (ISO format),
                    "status": str,
                    "capacity": {
                        "total_memory": int,
                        "available_memory": int,
                        "warm_containers": int,
                        "busy_containers": int,
                        "prewarm_containers": int,
                        "supported_runtimes": list[str]
                    }
                }
        """
        try:
            invoker_id = heartbeat["invoker_id"]
            timestamp = datetime.fromisoformat(heartbeat["timestamp"])
            status = heartbeat.get("status", "healthy")
            capacity_data = heartbeat.get("capacity", {})

            capacity = InvokerCapacity(
                total_memory=capacity_data.get("total_memory", 0),
                available_memory=capacity_data.get("available_memory", 0),
                warm_containers=capacity_data.get("warm_containers", 0),
                busy_containers=capacity_data.get("busy_containers", 0),
                prewarm_containers=capacity_data.get("prewarm_containers", 0),
                supported_runtimes=capacity_data.get("supported_runtimes", [])
            )

            with self._lock:
                self._invokers[invoker_id] = InvokerInfo(
                    invoker_id=invoker_id,
                    last_heartbeat=timestamp,
                    capacity=capacity,
                    status=status
                )

            logger.debug(
                f"Updated invoker {invoker_id}: status={status}, "
                f"available_memory={capacity.available_memory}MB"
            )

        except (KeyError, ValueError) as e:
            logger.error(f"Invalid heartbeat message: {e}", exc_info=True)

    def select_invoker(
        self,
        action_kind: str,
        memory_required: int
    ) -> Optional[str]:
        """
        Select optimal invoker for action execution.

        Selection criteria:
        1. Invoker must be healthy
        2. Invoker must have enough available memory
        3. Invoker must support required runtime
        4. Prefer invoker with warm container for this runtime

        Args:
            action_kind: Runtime identifier (e.g., "python:3.13", "nodejs:18")
            memory_required: Memory required in MB

        Returns:
            Invoker ID if suitable invoker found, None otherwise
        """
        with self._lock:
            healthy_invokers = [
                info for info in self._invokers.values()
                if info.status == "healthy"
                and info.capacity.available_memory >= memory_required
                and action_kind in info.capacity.supported_runtimes
            ]

            if not healthy_invokers:
                logger.warning(
                    f"No healthy invoker available for {action_kind} "
                    f"with {memory_required}MB memory"
                )
                return None

            # Prefer invoker with warm containers
            invokers_with_warm = [
                info for info in healthy_invokers
                if info.capacity.warm_containers > 0
            ]

            if invokers_with_warm:
                # Select invoker with most available memory
                selected = max(
                    invokers_with_warm,
                    key=lambda x: x.capacity.available_memory
                )
            else:
                # No warm containers, select invoker with most available memory
                selected = max(
                    healthy_invokers,
                    key=lambda x: x.capacity.available_memory
                )

            logger.info(
                f"Selected invoker {selected.invoker_id} for {action_kind}: "
                f"available_memory={selected.capacity.available_memory}MB, "
                f"warm_containers={selected.capacity.warm_containers}"
            )

            return selected.invoker_id

    def get_healthy_invokers(self) -> list[InvokerInfo]:
        """
        Get list of healthy invokers.

        Returns:
            List of InvokerInfo objects for healthy invokers
        """
        with self._lock:
            return [
                info for info in self._invokers.values()
                if info.status == "healthy"
            ]

    def get_cluster_capacity(self) -> dict:
        """
        Get aggregated capacity across all invokers.

        Returns:
            Dictionary with cluster-wide capacity metrics:
            {
                "total_invokers": int,
                "healthy_invokers": int,
                "total_memory": int,
                "available_memory": int,
                "total_containers": int,
                "warm_containers": int,
                "busy_containers": int,
                "prewarm_containers": int,
                "supported_runtimes": list[str]
            }
        """
        with self._lock:
            healthy = [
                info for info in self._invokers.values()
                if info.status == "healthy"
            ]

            total_memory = sum(info.capacity.total_memory for info in healthy)
            available_memory = sum(info.capacity.available_memory for info in healthy)
            warm_containers = sum(info.capacity.warm_containers for info in healthy)
            busy_containers = sum(info.capacity.busy_containers for info in healthy)
            prewarm_containers = sum(info.capacity.prewarm_containers for info in healthy)

            # Collect all unique supported runtimes
            all_runtimes = set()
            for info in healthy:
                all_runtimes.update(info.capacity.supported_runtimes)

            return {
                "total_invokers": len(self._invokers),
                "healthy_invokers": len(healthy),
                "total_memory": total_memory,
                "available_memory": available_memory,
                "total_containers": warm_containers + busy_containers + prewarm_containers,
                "warm_containers": warm_containers,
                "busy_containers": busy_containers,
                "prewarm_containers": prewarm_containers,
                "supported_runtimes": sorted(all_runtimes)
            }
