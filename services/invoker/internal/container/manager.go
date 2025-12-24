package container

import (
	"context"
	"fmt"
	"io"
	"strings"
	"time"

	"github.com/docker/docker/api/types"
	"github.com/docker/docker/api/types/container"
	"github.com/docker/docker/api/types/filters"
	"github.com/docker/docker/api/types/image"
	"github.com/docker/docker/api/types/network"
	"github.com/docker/docker/client"
	"github.com/docker/go-connections/nat"
	"go.uber.org/zap"

	"openwhisk-invoker/internal/config"
)

// ContainerState represents the state of a container
type ContainerState string

const (
	ContainerStateCreated ContainerState = "created"
	ContainerStateRunning ContainerState = "running"
	ContainerStateStopped ContainerState = "stopped"
	ContainerStateExited  ContainerState = "exited"
)

// ResourceLimits defines resource constraints for containers
type ResourceLimits struct {
	MemoryMB    int64
	CPUShares   int64
	TimeoutSecs int
}

// ContainerSpec defines the specification for creating a container
type ContainerSpec struct {
	Image       string
	Memory      int64 // bytes
	Timeout     time.Duration
	Environment map[string]string
}

// Container represents a managed container instance
type Container struct {
	ID        string
	IP        string
	State     ContainerState
	Runtime   string
	CreatedAt time.Time
}

// ContainerManager manages Docker container lifecycle
type ContainerManager struct {
	dockerClient    *client.Client
	networkName     string
	containerPrefix string
	resourceLimits  ResourceLimits
	logger          *zap.Logger
}

// NewContainerManager creates a new container manager instance
func NewContainerManager(cfg *config.Config) (*ContainerManager, error) {
	logger, err := zap.NewProduction()
	if err != nil {
		return nil, fmt.Errorf("failed to create logger: %w", err)
	}

	// Create Docker client
	cli, err := client.NewClientWithOpts(client.FromEnv, client.WithAPIVersionNegotiation())
	if err != nil {
		logger.Error("failed to create Docker client", zap.Error(err))
		return nil, fmt.Errorf("failed to create Docker client: %w", err)
	}

	// Verify Docker connection
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	_, err = cli.Ping(ctx)
	if err != nil {
		logger.Error("failed to ping Docker daemon", zap.Error(err))
		return nil, fmt.Errorf("failed to connect to Docker daemon: %w", err)
	}

	manager := &ContainerManager{
		dockerClient:    cli,
		networkName:     cfg.Docker.Network,
		containerPrefix: cfg.Docker.ContainerPrefix,
		resourceLimits: ResourceLimits{
			MemoryMB:    int64(cfg.Docker.MemoryLimitMB),
			CPUShares:   int64(cfg.Docker.CPUShares),
			TimeoutSecs: cfg.Docker.TimeoutSeconds,
		},
		logger: logger,
	}

	// Ensure network exists
	if err := manager.ensureNetwork(context.Background()); err != nil {
		logger.Error("failed to ensure network exists", zap.Error(err))
		return nil, fmt.Errorf("failed to ensure network: %w", err)
	}

	logger.Info("container manager initialized",
		zap.String("network", manager.networkName),
		zap.String("prefix", manager.containerPrefix))

	return manager, nil
}

// ensureNetwork creates the Docker network if it doesn't exist
func (m *ContainerManager) ensureNetwork(ctx context.Context) error {
	// Check if network exists
	networks, err := m.dockerClient.NetworkList(ctx, types.NetworkListOptions{
		Filters: filters.NewArgs(filters.Arg("name", m.networkName)),
	})
	if err != nil {
		return fmt.Errorf("failed to list networks: %w", err)
	}

	if len(networks) > 0 {
		m.logger.Debug("network already exists", zap.String("network", m.networkName))
		return nil
	}

	// Create network
	_, err = m.dockerClient.NetworkCreate(ctx, m.networkName, types.NetworkCreate{
		Driver:     "bridge",
		Attachable: true,
		Labels: map[string]string{
			"project": "penguinwhisk",
			"managed": "true",
		},
	})
	if err != nil {
		return fmt.Errorf("failed to create network: %w", err)
	}

	m.logger.Info("created Docker network", zap.String("network", m.networkName))
	return nil
}

// CreateContainer creates a new container from the given specification
func (m *ContainerManager) CreateContainer(ctx context.Context, spec ContainerSpec) (*Container, error) {
	m.logger.Debug("creating container", zap.String("image", spec.Image))

	// Pull image if not exists
	if err := m.pullImageIfNeeded(ctx, spec.Image); err != nil {
		return nil, fmt.Errorf("failed to pull image: %w", err)
	}

	// Build environment variables
	env := make([]string, 0, len(spec.Environment))
	for k, v := range spec.Environment {
		env = append(env, fmt.Sprintf("%s=%s", k, v))
	}

	// Container configuration
	containerConfig := &container.Config{
		Image: spec.Image,
		Env:   env,
		ExposedPorts: nat.PortSet{
			"8080/tcp": struct{}{},
		},
		Labels: map[string]string{
			"project": "penguinwhisk",
			"managed": "true",
			"prefix":  m.containerPrefix,
		},
		StopTimeout: func() *int { t := int(spec.Timeout.Seconds()); return &t }(),
	}

	// Host configuration with resource limits
	memoryBytes := spec.Memory
	if memoryBytes == 0 {
		memoryBytes = m.resourceLimits.MemoryMB * 1024 * 1024
	}

	hostConfig := &container.HostConfig{
		Resources: container.Resources{
			Memory:    memoryBytes,
			CPUShares: m.resourceLimits.CPUShares,
		},
		NetworkMode: container.NetworkMode(m.networkName),
		AutoRemove:  false, // We manage removal explicitly
	}

	// Network configuration
	networkConfig := &network.NetworkingConfig{
		EndpointsConfig: map[string]*network.EndpointSettings{
			m.networkName: {
				NetworkID: m.networkName,
			},
		},
	}

	// Generate container name
	containerName := fmt.Sprintf("%s-%d", m.containerPrefix, time.Now().UnixNano())

	// Create container
	resp, err := m.dockerClient.ContainerCreate(
		ctx,
		containerConfig,
		hostConfig,
		networkConfig,
		nil,
		containerName,
	)
	if err != nil {
		m.logger.Error("failed to create container",
			zap.String("image", spec.Image),
			zap.Error(err))
		return nil, fmt.Errorf("failed to create container: %w", err)
	}

	m.logger.Info("container created",
		zap.String("id", resp.ID[:12]),
		zap.String("name", containerName),
		zap.String("image", spec.Image))

	return &Container{
		ID:        resp.ID,
		IP:        "", // Will be populated after start
		State:     ContainerStateCreated,
		Runtime:   spec.Image,
		CreatedAt: time.Now(),
	}, nil
}

// pullImageIfNeeded pulls the Docker image if it doesn't exist locally
func (m *ContainerManager) pullImageIfNeeded(ctx context.Context, imageName string) error {
	// Check if image exists locally
	_, _, err := m.dockerClient.ImageInspectWithRaw(ctx, imageName)
	if err == nil {
		m.logger.Debug("image already exists locally", zap.String("image", imageName))
		return nil
	}

	m.logger.Info("pulling image", zap.String("image", imageName))

	// Pull image
	reader, err := m.dockerClient.ImagePull(ctx, imageName, image.PullOptions{})
	if err != nil {
		return fmt.Errorf("failed to pull image: %w", err)
	}
	defer reader.Close()

	// Wait for pull to complete
	_, err = io.Copy(io.Discard, reader)
	if err != nil {
		return fmt.Errorf("failed to read pull response: %w", err)
	}

	m.logger.Info("image pulled successfully", zap.String("image", imageName))
	return nil
}

// StartContainer starts a created container and waits for it to be healthy
func (m *ContainerManager) StartContainer(ctx context.Context, containerID string) error {
	m.logger.Debug("starting container", zap.String("id", containerID[:12]))

	// Start container
	if err := m.dockerClient.ContainerStart(ctx, containerID, container.StartOptions{}); err != nil {
		m.logger.Error("failed to start container",
			zap.String("id", containerID[:12]),
			zap.Error(err))
		return fmt.Errorf("failed to start container: %w", err)
	}

	// Wait for container to be running
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		inspect, err := m.dockerClient.ContainerInspect(ctx, containerID)
		if err != nil {
			return fmt.Errorf("failed to inspect container: %w", err)
		}

		if inspect.State.Running {
			m.logger.Info("container started",
				zap.String("id", containerID[:12]),
				zap.String("ip", inspect.NetworkSettings.Networks[m.networkName].IPAddress))
			return nil
		}

		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(100 * time.Millisecond):
			// Continue waiting
		}
	}

	return fmt.Errorf("container failed to start within timeout")
}

// StopContainer stops a running container with a grace period
func (m *ContainerManager) StopContainer(ctx context.Context, containerID string, timeout time.Duration) error {
	m.logger.Debug("stopping container",
		zap.String("id", containerID[:12]),
		zap.Duration("timeout", timeout))

	timeoutSeconds := int(timeout.Seconds())
	stopOptions := container.StopOptions{
		Timeout: &timeoutSeconds,
	}

	if err := m.dockerClient.ContainerStop(ctx, containerID, stopOptions); err != nil {
		m.logger.Error("failed to stop container",
			zap.String("id", containerID[:12]),
			zap.Error(err))
		return fmt.Errorf("failed to stop container: %w", err)
	}

	m.logger.Info("container stopped", zap.String("id", containerID[:12]))
	return nil
}

// RemoveContainer removes a container
func (m *ContainerManager) RemoveContainer(ctx context.Context, containerID string, force bool) error {
	m.logger.Debug("removing container",
		zap.String("id", containerID[:12]),
		zap.Bool("force", force))

	removeOptions := container.RemoveOptions{
		Force:         force,
		RemoveVolumes: true,
	}

	if err := m.dockerClient.ContainerRemove(ctx, containerID, removeOptions); err != nil {
		m.logger.Error("failed to remove container",
			zap.String("id", containerID[:12]),
			zap.Error(err))
		return fmt.Errorf("failed to remove container: %w", err)
	}

	m.logger.Info("container removed", zap.String("id", containerID[:12]))
	return nil
}

// GetContainerIP retrieves the IP address of a container on the managed network
func (m *ContainerManager) GetContainerIP(ctx context.Context, containerID string) (string, error) {
	inspect, err := m.dockerClient.ContainerInspect(ctx, containerID)
	if err != nil {
		return "", fmt.Errorf("failed to inspect container: %w", err)
	}

	if inspect.NetworkSettings == nil {
		return "", fmt.Errorf("container has no network settings")
	}

	endpoint, ok := inspect.NetworkSettings.Networks[m.networkName]
	if !ok {
		return "", fmt.Errorf("container not connected to network %s", m.networkName)
	}

	if endpoint.IPAddress == "" {
		return "", fmt.Errorf("container has no IP address")
	}

	return endpoint.IPAddress, nil
}

// GetContainerLogs retrieves container logs since a specific time
func (m *ContainerManager) GetContainerLogs(ctx context.Context, containerID string, since time.Time) ([]string, error) {
	options := container.LogsOptions{
		ShowStdout: true,
		ShowStderr: true,
		Since:      since.Format(time.RFC3339),
		Timestamps: true,
	}

	reader, err := m.dockerClient.ContainerLogs(ctx, containerID, options)
	if err != nil {
		return nil, fmt.Errorf("failed to get container logs: %w", err)
	}
	defer reader.Close()

	// Read all logs
	logBytes, err := io.ReadAll(reader)
	if err != nil {
		return nil, fmt.Errorf("failed to read logs: %w", err)
	}

	// Split into lines
	logStr := string(logBytes)
	lines := strings.Split(logStr, "\n")

	// Filter empty lines
	result := make([]string, 0, len(lines))
	for _, line := range lines {
		if trimmed := strings.TrimSpace(line); trimmed != "" {
			result = append(result, trimmed)
		}
	}

	return result, nil
}

// ListContainers lists containers matching the given filters
func (m *ContainerManager) ListContainers(ctx context.Context, filterMap map[string]string) ([]*Container, error) {
	// Build Docker filters
	dockerFilters := filters.NewArgs()
	dockerFilters.Add("label", "project=penguinwhisk")

	for k, v := range filterMap {
		dockerFilters.Add("label", fmt.Sprintf("%s=%s", k, v))
	}

	// List containers
	containers, err := m.dockerClient.ContainerList(ctx, container.ListOptions{
		All:     true,
		Filters: dockerFilters,
	})
	if err != nil {
		return nil, fmt.Errorf("failed to list containers: %w", err)
	}

	// Convert to Container objects
	result := make([]*Container, 0, len(containers))
	for _, c := range containers {
		state := ContainerStateExited
		if c.State == "running" {
			state = ContainerStateRunning
		} else if c.State == "created" {
			state = ContainerStateCreated
		} else if c.State == "exited" {
			state = ContainerStateExited
		}

		// Get IP address
		ip := ""
		if c.NetworkSettings != nil {
			if endpoint, ok := c.NetworkSettings.Networks[m.networkName]; ok {
				ip = endpoint.IPAddress
			}
		}

		result = append(result, &Container{
			ID:        c.ID,
			IP:        ip,
			State:     state,
			Runtime:   c.Image,
			CreatedAt: time.Unix(c.Created, 0),
		})
	}

	return result, nil
}

// Close closes the Docker client connection
func (m *ContainerManager) Close() error {
	if m.dockerClient != nil {
		return m.dockerClient.Close()
	}
	return nil
}
