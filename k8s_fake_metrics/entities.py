"""Domain entities representing Kubernetes resources in the fake environment."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional


POD_PHASES: tuple[str, ...] = ("Pending", "Running", "Succeeded", "Failed", "Unknown")
NODE_CONDITIONS: tuple[str, ...] = (
    "Ready",
    "DiskPressure",
    "MemoryPressure",
    "NetworkUnavailable",
)


@dataclass(slots=True)
class ContainerState:
    """Mutable state for a container inside a pod."""

    name: str
    cpu_seconds_total: float
    memory_usage_bytes: float
    memory_limit_bytes: float
    filesystem_usage_bytes: float
    filesystem_limit_bytes: float
    restarts: int
    network_rx_bytes_total: float
    network_tx_bytes_total: float
    reported_restarts: int = 0

    def update_cpu(self, delta: float) -> None:
        self.cpu_seconds_total += max(delta, 0.0)

    def set_memory_usage(self, usage: float) -> None:
        self.memory_usage_bytes = min(max(usage, 0.0), self.memory_limit_bytes)

    def set_filesystem_usage(self, usage: float) -> None:
        self.filesystem_usage_bytes = min(max(usage, 0.0), self.filesystem_limit_bytes)

    def increment_restarts(self, amount: int = 1) -> None:
        if amount > 0:
            self.restarts += amount

    def add_network_traffic(self, rx: float, tx: float) -> None:
        self.network_rx_bytes_total += max(rx, 0.0)
        self.network_tx_bytes_total += max(tx, 0.0)


@dataclass(slots=True)
class PodState:
    """Mutable state for a Kubernetes pod."""

    name: str
    namespace: str
    node_name: str
    deployment: str
    containers: List[ContainerState]
    phase: str = "Pending"
    qos_class: str = "Burstable"
    ready_containers: int = 0

    def set_phase(self, phase: str) -> None:
        if phase not in POD_PHASES:
            raise ValueError(f"Unsupported pod phase: {phase}")
        self.phase = phase

    @property
    def restart_count(self) -> int:
        return sum(container.restarts for container in self.containers)

    def update_ready_containers(self) -> None:
        self.ready_containers = sum(
            1 for container in self.containers if self.phase == "Running"
        )


@dataclass(slots=True)
class NodeState:
    """Mutable state for a Kubernetes node."""

    name: str
    cluster: str
    cpu_cores: int
    memory_bytes: float
    ephemeral_storage_bytes: float
    pods: List[PodState] = field(default_factory=list)
    cpu_modes_seconds: Dict[str, float] = field(
        default_factory=lambda: {"user": 0.0, "system": 0.0, "idle": 0.0, "iowait": 0.0}
    )
    memory_available_bytes: float = 0.0
    filesystem_usage_bytes: float = 0.0
    network_rx_bytes_total: float = 0.0
    network_tx_bytes_total: float = 0.0
    disk_read_bytes_total: float = 0.0
    disk_written_bytes_total: float = 0.0
    conditions: Dict[str, bool] = field(
        default_factory=lambda: {condition: True for condition in NODE_CONDITIONS}
    )

    def __post_init__(self) -> None:
        self.memory_available_bytes = self.memory_bytes * 0.6
        self.filesystem_usage_bytes = self.ephemeral_storage_bytes * 0.4

    @property
    def ready(self) -> bool:
        return self.conditions.get("Ready", False)


@dataclass(slots=True)
class DeploymentState:
    """Simplified representation of a deployment."""

    name: str
    namespace: str
    desired_replicas: int
    updated_replicas: int
    ready_replicas: int
    available_replicas: int

    def update_counts(self, ready: int, desired: Optional[int] = None) -> None:
        if desired is not None:
            self.desired_replicas = desired
        self.ready_replicas = ready
        self.available_replicas = max(min(ready, self.desired_replicas), 0)
        self.updated_replicas = min(self.desired_replicas, self.available_replicas)


@dataclass(slots=True)
class DaemonSetState:
    """Simplified representation of a daemonset."""

    name: str
    namespace: str
    desired_number_scheduled: int
    number_available: int

    def update_availability(self, available: int) -> None:
        self.number_available = max(min(available, self.desired_number_scheduled), 0)


@dataclass(slots=True)
class IngressState:
    """Representation for ingress controllers generating metrics."""

    name: str
    namespace: str
    controller: str = "nginx"
    requests_total: float = 0.0


@dataclass(slots=True)
class ClusterState:
    """Container for all simulated resources for a cluster."""

    name: str
    namespaces: List[str]
    nodes: List[NodeState]
    deployments: List[DeploymentState]
    daemonsets: List[DaemonSetState]
    ingresses: List[IngressState]

    def iter_pods(self) -> Iterable[PodState]:
        for node in self.nodes:
            yield from node.pods


__all__ = [
    "ClusterState",
    "ContainerState",
    "DaemonSetState",
    "DeploymentState",
    "IngressState",
    "NodeState",
    "POD_PHASES",
    "NODE_CONDITIONS",
    "PodState",
]
