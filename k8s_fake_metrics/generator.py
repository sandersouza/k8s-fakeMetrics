"""Core logic responsible for synthesising Kubernetes metrics."""

from __future__ import annotations

import itertools
import logging
import random
import string
import time
from typing import Dict, List, Sequence

from prometheus_client import CollectorRegistry, start_http_server

from .config import Config
from .entities import (
    ClusterState,
    ContainerState,
    DaemonSetState,
    DeploymentState,
    IngressState,
    NODE_CONDITIONS,
    POD_PHASES,
    NodeState,
    PodState,
)
from .metrics import MetricSet

logger = logging.getLogger(__name__)

NAMESPACE_POOL: Sequence[str] = (
    "kube-system",
    "default",
    "observability",
    "payments",
    "frontend",
    "backend",
    "dev",
    "qa",
    "data",
    "messaging",
)

QOS_CLASSES: Sequence[str] = ("Guaranteed", "Burstable", "BestEffort")
CONTAINER_NAMES: Sequence[str] = (
    "istio-proxy",
    "app",
    "sidecar",
    "collector",
    "init",
    "adapter",
    "service",
)
NETWORK_INTERFACES: Sequence[str] = ("eth0", "eth1")
NODE_DISK_DEVICES: Sequence[str] = ("sda", "sdb")
NODE_SENSORS: Sequence[str] = ("cpu", "chassis")

APISERVER_VERBS: Sequence[str] = ("GET", "LIST", "WATCH", "CREATE", "PATCH", "DELETE")
APISERVER_RESOURCES: Sequence[str] = (
    "pods",
    "deployments",
    "nodes",
    "services",
    "events",
    "namespaces",
)
HTTP_STATUS_CODES: Sequence[str] = ("200", "201", "202", "400", "404", "429", "500")
SCHEDULER_RESULTS: Sequence[str] = ("scheduled", "error", "unschedulable")
CONTROLLER_RESULTS: Sequence[str] = ("success", "error", "requeue")

VOLUME_NAMES: Sequence[str] = ("config", "cache", "logs", "data")


class FakeMetricsGenerator:
    """Generate synthetic Prometheus metrics representing Kubernetes activity."""

    def __init__(self, config: Config, registry: CollectorRegistry | None = None) -> None:
        self.config = config
        self.registry = registry or CollectorRegistry()
        self.metrics = MetricSet(self.registry)
        self.random = random.Random(config.random_seed)
        self.clusters: List[ClusterState] = []
        self._create_environment()

    # ------------------------------------------------------------------
    # Environment construction
    # ------------------------------------------------------------------
    def _create_environment(self) -> None:
        for cluster_index in range(self.config.cluster_count):
            cluster_name = f"cluster-{cluster_index + 1}"
            namespaces = self._select_namespaces()
            nodes = self._create_nodes(cluster_name)
            deployments = self._create_deployments(cluster_name, namespaces)
            daemonsets = self._create_daemonsets(cluster_name, namespaces, nodes)
            ingresses = self._create_ingresses(cluster_name, namespaces)
            cluster = ClusterState(
                name=cluster_name,
                namespaces=namespaces,
                nodes=nodes,
                deployments=deployments,
                daemonsets=daemonsets,
                ingresses=ingresses,
            )
            self._populate_pods(cluster)
            self.clusters.append(cluster)
            logger.debug("Created cluster %s with %s nodes", cluster.name, len(cluster.nodes))

    def _select_namespaces(self) -> List[str]:
        namespaces = set(["kube-system", "default"])
        available = [ns for ns in NAMESPACE_POOL if ns not in namespaces]
        while len(namespaces) < max(self.config.namespaces_per_cluster, 2):
            namespaces.add(self.random.choice(available))
        return sorted(namespaces)

    def _create_nodes(self, cluster_name: str) -> List[NodeState]:
        nodes: List[NodeState] = []
        for node_index in range(self.config.nodes_per_cluster):
            cpu = self.random.choice([4, 8, 16])
            memory_gib = self.random.uniform(8, 64)
            storage_gib = self.random.uniform(100, 500)
            node = NodeState(
                name=f"{cluster_name}-node-{node_index + 1}",
                cluster=cluster_name,
                cpu_cores=cpu,
                memory_bytes=memory_gib * 1024**3,
                ephemeral_storage_bytes=storage_gib * 1024**3,
            )
            nodes.append(node)
        return nodes

    def _create_deployments(self, cluster_name: str, namespaces: Sequence[str]) -> List[DeploymentState]:
        deployments: List[DeploymentState] = []
        for namespace in namespaces:
            for index in range(self.config.deployments_per_namespace):
                desired = self.random.randint(2, max(self.config.pods_per_node, 2))
                deployments.append(
                    DeploymentState(
                        name=f"{namespace}-deploy-{index + 1}",
                        namespace=namespace,
                        desired_replicas=desired,
                        updated_replicas=desired,
                        ready_replicas=desired,
                        available_replicas=desired,
                    )
                )
        return deployments

    def _create_daemonsets(
        self, cluster_name: str, namespaces: Sequence[str], nodes: Sequence[NodeState]
    ) -> List[DaemonSetState]:
        daemonsets: List[DaemonSetState] = []
        for namespace in namespaces:
            if namespace == "default":
                continue
            if self.random.random() < 0.5:
                name = f"{namespace}-ds"
                daemonsets.append(
                    DaemonSetState(
                        name=name,
                        namespace=namespace,
                        desired_number_scheduled=len(nodes),
                        number_available=len(nodes),
                    )
                )
        if not daemonsets:
            daemonsets.append(
                DaemonSetState(
                    name=f"{cluster_name}-node-exporter",
                    namespace="kube-system",
                    desired_number_scheduled=len(nodes),
                    number_available=len(nodes),
                )
            )
        return daemonsets

    def _create_ingresses(self, cluster_name: str, namespaces: Sequence[str]) -> List[IngressState]:
        ingresses: List[IngressState] = []
        if not self.config.enable_ingress_controllers:
            return ingresses
        for namespace in namespaces:
            if namespace in {"kube-system", "default"}:
                continue
            if self.random.random() < 0.4:
                controller = "nginx"
                if self.config.enable_traefik_metrics and self.random.random() < 0.3:
                    controller = "traefik"
                ingresses.append(
                    IngressState(
                        name=f"{namespace}-ingress",
                        namespace=namespace,
                        controller=controller,
                    )
                )
        if not ingresses:
            ingresses.append(
                IngressState(name=f"{cluster_name}-ingress", namespace="default", controller="nginx")
            )
        return ingresses

    def _populate_pods(self, cluster: ClusterState) -> None:
        for node in cluster.nodes:
            pods: List[PodState] = []
            for _ in range(self.config.pods_per_node):
                namespace = self.random.choice(cluster.namespaces)
                deployment = self.random.choice(
                    [d for d in cluster.deployments if d.namespace == namespace]
                )
                pod_name = self._random_pod_name(namespace)
                containers = self._create_containers()
                pod = PodState(
                    name=pod_name,
                    namespace=namespace,
                    node_name=node.name,
                    deployment=deployment.name,
                    containers=containers,
                    phase="Running" if self.random.random() > 0.1 else "Pending",
                    qos_class=self.random.choice(QOS_CLASSES),
                )
                pod.update_ready_containers()
                pods.append(pod)
            node.pods = pods

    def _random_pod_name(self, namespace: str) -> str:
        suffix = "".join(self.random.choices(string.ascii_lowercase + string.digits, k=5))
        return f"{namespace}-{suffix}"

    def _create_containers(self) -> List[ContainerState]:
        containers: List[ContainerState] = []
        min_containers = max(1, self.config.containers_per_pod_min)
        max_containers = max(min_containers, self.config.containers_per_pod_max)
        container_count = self.random.randint(min_containers, max_containers)
        for index in range(container_count):
            name = self.random.choice(CONTAINER_NAMES)
            base_memory_limit_gib = self.random.uniform(0.25, 2.0)
            fs_limit_gib = self.random.uniform(1.0, 10.0)
            containers.append(
                ContainerState(
                    name=f"{name}-{index}",
                    cpu_seconds_total=self.random.uniform(0.0, 1000.0),
                    memory_usage_bytes=base_memory_limit_gib * 1024**3 * self.random.uniform(0.2, 0.9),
                    memory_limit_bytes=base_memory_limit_gib * 1024**3,
                    filesystem_usage_bytes=fs_limit_gib * 1024**3 * self.random.uniform(0.1, 0.8),
                    filesystem_limit_bytes=fs_limit_gib * 1024**3,
                    restarts=self.random.randint(0, 5),
                    network_rx_bytes_total=self.random.uniform(1e6, 5e9),
                    network_tx_bytes_total=self.random.uniform(1e6, 5e9),
                )
            )
        return containers

    # ------------------------------------------------------------------
    # Metrics update loop
    # ------------------------------------------------------------------
    def run(self, iterations: int | None = None) -> None:
        logger.info(
            "Starting fake metrics generator for %s clusters (port %s)",
            self.config.cluster_count,
            self.config.metrics_port,
        )
        start_http_server(self.config.metrics_port, addr=self.config.metrics_host, registry=self.registry)
        loop = itertools.count() if iterations is None else range(iterations)
        for iteration in loop:
            start = time.time()
            logger.debug("Updating metrics iteration %s", iteration)
            self.update_metrics()
            elapsed = time.time() - start
            sleep_time = max(self.config.update_interval_seconds - elapsed, 0.0)
            if iterations is None:
                time.sleep(sleep_time)
            else:
                if sleep_time > 0:
                    time.sleep(sleep_time)

    def update_metrics(self) -> None:
        for cluster in self.clusters:
            self._update_cluster_metrics(cluster)

    # ------------------------------------------------------------------
    # Cluster metric updates
    # ------------------------------------------------------------------
    def _update_cluster_metrics(self, cluster: ClusterState) -> None:
        self._update_namespace_metrics(cluster)
        self._update_node_metrics(cluster)
        self._update_pod_metrics(cluster)
        self._update_deployment_metrics(cluster)
        self._update_daemonset_metrics(cluster)
        self._update_control_plane_metrics(cluster)
        if self.config.enable_ingress_controllers:
            self._update_ingress_metrics(cluster)

    def _update_namespace_metrics(self, cluster: ClusterState) -> None:
        for namespace in cluster.namespaces:
            phase = "Active" if self.random.random() > 0.05 else "Terminating"
            self.metrics.kube_namespace_status_phase.labels(
                cluster=cluster.name, namespace=namespace, phase="Active"
            ).set(1 if phase == "Active" else 0)
            self.metrics.kube_namespace_status_phase.labels(
                cluster=cluster.name, namespace=namespace, phase="Terminating"
            ).set(1 if phase == "Terminating" else 0)
            quota_name = f"{namespace}-quota"
            cpu_used = self.random.uniform(0.1, 0.9) * len(cluster.nodes) * 2
            mem_used = self.random.uniform(0.1, 0.9) * len(cluster.nodes) * 4 * 1024**3
            self.metrics.cluster_resource_quota_cpu.labels(
                cluster=cluster.name, namespace=namespace, quota=quota_name
            ).set(cpu_used)
            self.metrics.cluster_resource_quota_memory.labels(
                cluster=cluster.name, namespace=namespace, quota=quota_name
            ).set(mem_used)

    def _update_node_metrics(self, cluster: ClusterState) -> None:
        for node in cluster.nodes:
            for mode in node.cpu_modes_seconds:
                delta = self.random.uniform(0.5, 5.0) * self.config.update_interval_seconds
                node.cpu_modes_seconds[mode] += delta
                self.metrics.node_cpu_seconds_total.labels(
                    cluster=cluster.name, node=node.name, mode=mode
                ).inc(delta)
            total_memory = node.memory_bytes
            usage = self.random.uniform(0.4, 0.95) * total_memory
            node.memory_available_bytes = max(total_memory - usage, total_memory * 0.05)
            self.metrics.node_memory_total_bytes.labels(cluster=cluster.name, node=node.name).set(
                total_memory
            )
            self.metrics.node_memory_available_bytes.labels(
                cluster=cluster.name, node=node.name
            ).set(node.memory_available_bytes)

            mountpoint = "/"
            device = "rootfs"
            filesystem_size = node.ephemeral_storage_bytes
            usage_ratio = self.random.uniform(0.3, 0.95)
            node.filesystem_usage_bytes = filesystem_size * usage_ratio
            free_bytes = filesystem_size - node.filesystem_usage_bytes
            avail_bytes = max(free_bytes * 0.9, 0)
            self.metrics.node_filesystem_size_bytes.labels(
                cluster=cluster.name, node=node.name, mountpoint=mountpoint, device=device
            ).set(filesystem_size)
            self.metrics.node_filesystem_free_bytes.labels(
                cluster=cluster.name, node=node.name, mountpoint=mountpoint, device=device
            ).set(free_bytes)
            self.metrics.node_filesystem_avail_bytes.labels(
                cluster=cluster.name, node=node.name, mountpoint=mountpoint, device=device
            ).set(avail_bytes)

            for interface in NETWORK_INTERFACES:
                rx_delta = self.random.uniform(1e5, 5e7)
                tx_delta = self.random.uniform(1e5, 5e7)
                node.network_rx_bytes_total += rx_delta
                node.network_tx_bytes_total += tx_delta
                self.metrics.node_network_receive_bytes_total.labels(
                    cluster=cluster.name, node=node.name, interface=interface
                ).inc(rx_delta)
                self.metrics.node_network_transmit_bytes_total.labels(
                    cluster=cluster.name, node=node.name, interface=interface
                ).inc(tx_delta)

            for device in NODE_DISK_DEVICES:
                read_delta = self.random.uniform(1e6, 5e8)
                write_delta = self.random.uniform(1e6, 5e8)
                node.disk_read_bytes_total += read_delta
                node.disk_written_bytes_total += write_delta
                self.metrics.node_disk_read_bytes_total.labels(
                    cluster=cluster.name, node=node.name, device=device
                ).inc(read_delta)
                self.metrics.node_disk_written_bytes_total.labels(
                    cluster=cluster.name, node=node.name, device=device
                ).inc(write_delta)

            for sensor in NODE_SENSORS:
                temp = self.random.uniform(40.0, 85.0)
                self.metrics.node_temperature_celsius.labels(
                    cluster=cluster.name, node=node.name, sensor=sensor
                ).set(temp)

            self.metrics.kube_node_status_capacity_cpu_cores.labels(
                cluster=cluster.name, node=node.name
            ).set(node.cpu_cores)
            self.metrics.kube_node_status_capacity_memory_bytes.labels(
                cluster=cluster.name, node=node.name
            ).set(node.memory_bytes)

            for condition in NODE_CONDITIONS:
                healthy = True if condition == "Ready" else self.random.random() > 0.1
                if condition == "Ready" and self.random.random() < 0.05:
                    healthy = False
                node.conditions[condition] = healthy
                self.metrics.kube_node_status_condition.labels(
                    cluster=cluster.name,
                    node=node.name,
                    condition=condition,
                    status="true",
                ).set(1 if healthy else 0)
                self.metrics.kube_node_status_condition.labels(
                    cluster=cluster.name,
                    node=node.name,
                    condition=condition,
                    status="false",
                ).set(0 if healthy else 1)
                self.metrics.kube_node_status_condition.labels(
                    cluster=cluster.name,
                    node=node.name,
                    condition=condition,
                    status="unknown",
                ).set(0)

    def _update_pod_metrics(self, cluster: ClusterState) -> None:
        for pod in cluster.iter_pods():
            # Update pod phase with small chance of change
            if pod.phase == "Running":
                if self.random.random() < 0.02:
                    pod.phase = self.random.choice(["Pending", "Failed", "Running"])
            else:
                if self.random.random() < 0.3:
                    pod.phase = "Running"

            pod.update_ready_containers()
            for phase in POD_PHASES:
                value = 1 if pod.phase == phase else 0
                self.metrics.kube_pod_status_phase.labels(
                    cluster=cluster.name,
                    namespace=pod.namespace,
                    pod=pod.name,
                    phase=phase,
                ).set(value)

            for container in pod.containers:
                cpu_delta = self.random.uniform(0.01, 0.2)
                container.update_cpu(cpu_delta)
                self.metrics.container_cpu_usage_seconds_total.labels(
                    cluster=cluster.name,
                    namespace=pod.namespace,
                    pod=pod.name,
                    container=container.name,
                ).inc(cpu_delta)

                memory_variation = container.memory_limit_bytes * self.random.uniform(-0.02, 0.03)
                container.set_memory_usage(container.memory_usage_bytes + memory_variation)
                self.metrics.container_memory_usage_bytes.labels(
                    cluster=cluster.name,
                    namespace=pod.namespace,
                    pod=pod.name,
                    container=container.name,
                ).set(container.memory_usage_bytes)

                fs_variation = container.filesystem_limit_bytes * self.random.uniform(-0.02, 0.05)
                container.set_filesystem_usage(container.filesystem_usage_bytes + fs_variation)
                self.metrics.container_fs_usage_bytes.labels(
                    cluster=cluster.name,
                    namespace=pod.namespace,
                    pod=pod.name,
                    container=container.name,
                ).set(container.filesystem_usage_bytes)

                if pod.phase != "Running" and self.random.random() < 0.2:
                    container.increment_restarts()
                restart_delta = container.restarts - container.reported_restarts
                if restart_delta > 0:
                    self.metrics.kube_pod_container_status_restarts_total.labels(
                        cluster=cluster.name,
                        namespace=pod.namespace,
                        pod=pod.name,
                        container=container.name,
                    ).inc(restart_delta)
                    container.reported_restarts = container.restarts

                ready_value = 1 if pod.phase == "Running" else 0
                self.metrics.kube_pod_container_status_ready.labels(
                    cluster=cluster.name,
                    namespace=pod.namespace,
                    pod=pod.name,
                    container=container.name,
                ).set(ready_value)

                rx_delta = self.random.uniform(1e4, 1e7)
                tx_delta = self.random.uniform(1e4, 1e7)
                container.add_network_traffic(rx_delta, tx_delta)
                self.metrics.container_network_receive_bytes_total.labels(
                    cluster=cluster.name,
                    namespace=pod.namespace,
                    pod=pod.name,
                    container=container.name,
                ).inc(rx_delta)
                self.metrics.container_network_transmit_bytes_total.labels(
                    cluster=cluster.name,
                    namespace=pod.namespace,
                    pod=pod.name,
                    container=container.name,
                ).inc(tx_delta)

                volume_name = self.random.choice(VOLUME_NAMES)
                volume_capacity = container.filesystem_limit_bytes * self.random.uniform(0.5, 1.0)
                volume_used = min(container.filesystem_usage_bytes, volume_capacity)
                self.metrics.kubelet_volume_stats_capacity_bytes.labels(
                    cluster=cluster.name,
                    namespace=pod.namespace,
                    pod=pod.name,
                    volume=volume_name,
                ).set(volume_capacity)
                self.metrics.kubelet_volume_stats_used_bytes.labels(
                    cluster=cluster.name,
                    namespace=pod.namespace,
                    pod=pod.name,
                    volume=volume_name,
                ).set(volume_used)

    def _update_deployment_metrics(self, cluster: ClusterState) -> None:
        pods_by_deployment: Dict[str, List[PodState]] = {}
        for pod in cluster.iter_pods():
            pods_by_deployment.setdefault(pod.deployment, []).append(pod)
        for deployment in cluster.deployments:
            pods = pods_by_deployment.get(deployment.name, [])
            ready = sum(1 for pod in pods if pod.phase == "Running")
            desired = max(deployment.desired_replicas + self.random.randint(-1, 1), 1)
            deployment.update_counts(ready=ready, desired=desired)
            for condition, value in (
                ("desired", deployment.desired_replicas),
                ("ready", deployment.ready_replicas),
                ("updated", deployment.updated_replicas),
                ("available", deployment.available_replicas),
            ):
                self.metrics.kube_deployment_status_replicas.labels(
                    cluster=cluster.name,
                    namespace=deployment.namespace,
                    deployment=deployment.name,
                    condition=condition,
                ).set(value)

    def _update_daemonset_metrics(self, cluster: ClusterState) -> None:
        ready_nodes = sum(1 for node in cluster.nodes if node.ready)
        for daemonset in cluster.daemonsets:
            desired = daemonset.desired_number_scheduled
            available = min(ready_nodes, desired)
            daemonset.update_availability(available)
            self.metrics.kube_daemonset_status_number_available.labels(
                cluster=cluster.name,
                namespace=daemonset.namespace,
                daemonset=daemonset.name,
            ).set(daemonset.number_available)

    def _update_control_plane_metrics(self, cluster: ClusterState) -> None:
        for _ in range(self.random.randint(50, 120)):
            verb = self.random.choice(APISERVER_VERBS)
            resource = self.random.choice(APISERVER_RESOURCES)
            code = self.random.choices(HTTP_STATUS_CODES, weights=[70, 5, 3, 5, 5, 5, 7])[0]
            self.metrics.apiserver_request_total.labels(
                cluster=cluster.name, verb=verb, resource=resource, code=code
            ).inc()
            latency = self.random.gammavariate(2.0, 0.02)
            self.metrics.apiserver_request_duration_seconds.labels(
                cluster=cluster.name, verb=verb, resource=resource
            ).observe(latency)
        for component in ("kube-controller-manager", "kube-scheduler", "kubelet"):
            code = self.random.choices(HTTP_STATUS_CODES, weights=[80, 3, 2, 3, 3, 3, 6])[0]
            self.metrics.rest_client_requests_total.labels(
                cluster=cluster.name, component=component, code=code
            ).inc(self.random.randint(5, 50))
        for result in SCHEDULER_RESULTS:
            increment = max(int(self.random.gammavariate(2.0, 3.0)), 1)
            self.metrics.scheduler_schedule_attempts_total.labels(
                cluster=cluster.name, result=result
            ).inc(increment if result != "error" else max(increment // 10, 1))
        for _ in range(self.random.randint(20, 60)):
            duration = self.random.gammavariate(2.0, 0.03)
            self.metrics.scheduler_e2e_scheduling_duration_seconds.labels(cluster=cluster.name).observe(
                duration
            )
        for controller in ("deployment", "statefulset", "daemonset", "cronjob"):
            result = self.random.choice(CONTROLLER_RESULTS)
            self.metrics.controller_manager_reconcile_total.labels(
                cluster=cluster.name, controller=controller, result=result
            ).inc(self.random.randint(1, 25))

    def _update_ingress_metrics(self, cluster: ClusterState) -> None:
        for ingress in cluster.ingresses:
            code = self.random.choices(HTTP_STATUS_CODES, weights=[85, 2, 1, 4, 4, 2, 2])[0]
            requests = self.random.randint(20, 200)
            ingress.requests_total += requests
            if ingress.controller == "nginx":
                self.metrics.nginx_ingress_controller_requests.labels(
                    cluster=cluster.name,
                    namespace=ingress.namespace,
                    ingress=ingress.name,
                    status=code,
                ).inc(requests)
                for _ in range(self.random.randint(5, 30)):
                    latency = self.random.gammavariate(2.0, 0.02)
                    self.metrics.nginx_ingress_controller_upstream_latency_seconds.labels(
                        cluster=cluster.name,
                        namespace=ingress.namespace,
                        ingress=ingress.name,
                    ).observe(latency)
            else:
                # Traefik controller metrics
                service = f"{ingress.namespace}-service"
                self.metrics.traefik_backend_requests_total.labels(
                    cluster=cluster.name, service=service, code=code
                ).inc(requests)


def build_generator(config: Config) -> FakeMetricsGenerator:
    """Helper to construct a generator with a dedicated CollectorRegistry."""

    registry = CollectorRegistry()
    return FakeMetricsGenerator(config=config, registry=registry)


def run_from_config(config: Config, iterations: int | None = None) -> None:
    """Run the generator with the provided configuration."""

    generator = FakeMetricsGenerator(config)
    generator.run(iterations=iterations)


__all__ = ["FakeMetricsGenerator", "build_generator", "run_from_config"]
