package container

import (
	"context"
	"fmt"
	"sync"
	"time"
)

// PoolState represents the state of a pooled container
type PoolState string

const (
	PoolStateWarm   PoolState = "warm"
	PoolStateBusy   PoolState = "busy"
	PoolStatePaused PoolState = "paused"
)

// PooledContainer wraps a container with pooling metadata
type PooledContainer struct {
	Container         *Container
	Runtime           string
	State             PoolState
	LastUsed          time.Time
	InitializedAction string // empty if just prewarmed
}

// PoolConfig defines configuration for the container pool
type PoolConfig struct {
	MaxPoolSize       int
	PrewarmConfig     map[string]int // runtime -> prewarm count
	IdleTimeout       time.Duration
	CleanupInterval   time.Duration
}

// PoolStats provides statistics about the pool
type PoolStats struct {
	WarmContainers    map[string]int // runtime -> count
	BusyContainers    int
	PrewarmContainers map[string]int // runtime -> count
	TotalContainers   int
}

// ContainerPool manages a pool of warm containers for fast invocations
type ContainerPool struct {
	manager         *ContainerManager
	warmContainers  map[string][]*PooledContainer // runtime -> containers
	busyContainers  map[string]*PooledContainer   // containerID -> container
	prewarmConfig   map[string]int                // runtime -> count
	mu              sync.RWMutex
	maxPoolSize     int
	idleTimeout     time.Duration
	cleanupInterval time.Duration
	stopCleanup     chan struct{}
	cleanupWg       sync.WaitGroup
}

// NewContainerPool creates a new container pool
func NewContainerPool(manager *ContainerManager, config PoolConfig) *ContainerPool {
	pool := &ContainerPool{
		manager:         manager,
		warmContainers:  make(map[string][]*PooledContainer),
		busyContainers:  make(map[string]*PooledContainer),
		prewarmConfig:   config.PrewarmConfig,
		maxPoolSize:     config.MaxPoolSize,
		idleTimeout:     config.IdleTimeout,
		cleanupInterval: config.CleanupInterval,
		stopCleanup:     make(chan struct{}),
	}

	// Start cleanup goroutine
	pool.cleanupWg.Add(1)
	go pool.cleanupLoop()

	return pool
}

// GetContainer gets a container from the pool or creates a new one
// Selection priority:
// 1. Warm container initialized with same action (stem cell reuse)
// 2. Warm container with matching runtime (needs /init)
// 3. Create new container (cold start)
func (p *ContainerPool) GetContainer(ctx context.Context, runtime string, action string) (*PooledContainer, error) {
	p.mu.Lock()
	defer p.mu.Unlock()

	// First: check for warm container initialized with same action
	if containers, exists := p.warmContainers[runtime]; exists {
		for i, pc := range containers {
			if pc.InitializedAction == action && pc.State == PoolStateWarm {
				// Remove from warm pool
				p.warmContainers[runtime] = append(containers[:i], containers[i+1:]...)

				// Mark as busy
				pc.State = PoolStateBusy
				pc.LastUsed = time.Now()
				p.busyContainers[pc.Container.ID] = pc

				return pc, nil
			}
		}
	}

	// Second: check for warm container with matching runtime
	if containers, exists := p.warmContainers[runtime]; exists && len(containers) > 0 {
		// Take the most recently used container
		pc := containers[len(containers)-1]
		p.warmContainers[runtime] = containers[:len(containers)-1]

		// Mark as busy
		pc.State = PoolStateBusy
		pc.LastUsed = time.Now()
		pc.InitializedAction = action
		p.busyContainers[pc.Container.ID] = pc

		return pc, nil
	}

	// Third: create new container (cold start)
	container, err := p.manager.CreateContainer(ctx, runtime)
	if err != nil {
		return nil, fmt.Errorf("failed to create container: %w", err)
	}

	pc := &PooledContainer{
		Container:         container,
		Runtime:           runtime,
		State:             PoolStateBusy,
		LastUsed:          time.Now(),
		InitializedAction: action,
	}

	p.busyContainers[container.ID] = pc

	return pc, nil
}

// ReturnContainer returns a container to the pool or removes it
func (p *ContainerPool) ReturnContainer(containerID string, reuse bool) error {
	p.mu.Lock()
	defer p.mu.Unlock()

	pc, exists := p.busyContainers[containerID]
	if !exists {
		return fmt.Errorf("container %s not found in busy pool", containerID)
	}

	// Remove from busy pool
	delete(p.busyContainers, containerID)

	if !reuse {
		// Remove container
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		return p.manager.RemoveContainer(ctx, containerID)
	}

	// Check pool size limit
	totalWarm := 0
	for _, containers := range p.warmContainers {
		totalWarm += len(containers)
	}

	if totalWarm >= p.maxPoolSize {
		// Pool is full, remove oldest container
		if err := p.removeOldestContainer(); err != nil {
			// If removal fails, just remove this container
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			return p.manager.RemoveContainer(ctx, containerID)
		}
	}

	// Mark as warm and add to pool
	pc.State = PoolStateWarm
	pc.LastUsed = time.Now()

	if p.warmContainers[pc.Runtime] == nil {
		p.warmContainers[pc.Runtime] = make([]*PooledContainer, 0)
	}
	p.warmContainers[pc.Runtime] = append(p.warmContainers[pc.Runtime], pc)

	return nil
}

// PrewarmContainers creates prewarm containers according to configuration
func (p *ContainerPool) PrewarmContainers(ctx context.Context) error {
	p.mu.Lock()
	defer p.mu.Unlock()

	for runtime, count := range p.prewarmConfig {
		// Check how many containers already exist for this runtime
		existing := 0
		if containers, exists := p.warmContainers[runtime]; exists {
			for _, pc := range containers {
				if pc.InitializedAction == "" {
					existing++
				}
			}
		}

		// Create additional containers if needed
		needed := count - existing
		for i := 0; i < needed; i++ {
			container, err := p.manager.CreateContainer(ctx, runtime)
			if err != nil {
				return fmt.Errorf("failed to prewarm container for runtime %s: %w", runtime, err)
			}

			pc := &PooledContainer{
				Container:         container,
				Runtime:           runtime,
				State:             PoolStateWarm,
				LastUsed:          time.Now(),
				InitializedAction: "",
			}

			if p.warmContainers[runtime] == nil {
				p.warmContainers[runtime] = make([]*PooledContainer, 0)
			}
			p.warmContainers[runtime] = append(p.warmContainers[runtime], pc)
		}
	}

	return nil
}

// ScalePool increases or decreases prewarm containers for a runtime
func (p *ContainerPool) ScalePool(ctx context.Context, runtime string, delta int) error {
	p.mu.Lock()
	defer p.mu.Unlock()

	if delta > 0 {
		// Add containers
		for i := 0; i < delta; i++ {
			container, err := p.manager.CreateContainer(ctx, runtime)
			if err != nil {
				return fmt.Errorf("failed to scale up pool: %w", err)
			}

			pc := &PooledContainer{
				Container:         container,
				Runtime:           runtime,
				State:             PoolStateWarm,
				LastUsed:          time.Now(),
				InitializedAction: "",
			}

			if p.warmContainers[runtime] == nil {
				p.warmContainers[runtime] = make([]*PooledContainer, 0)
			}
			p.warmContainers[runtime] = append(p.warmContainers[runtime], pc)
		}

		// Update prewarm config
		p.prewarmConfig[runtime] = p.prewarmConfig[runtime] + delta
	} else if delta < 0 {
		// Remove containers
		toRemove := -delta
		containers := p.warmContainers[runtime]

		for i := 0; i < toRemove && i < len(containers); i++ {
			pc := containers[i]
			if pc.State == PoolStateWarm && pc.InitializedAction == "" {
				removeCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
				if err := p.manager.RemoveContainer(removeCtx, pc.Container.ID); err != nil {
					cancel()
					return fmt.Errorf("failed to remove container: %w", err)
				}
				cancel()
			}
		}

		// Update warm pool
		remaining := make([]*PooledContainer, 0)
		removed := 0
		for _, pc := range containers {
			if removed < toRemove && pc.State == PoolStateWarm && pc.InitializedAction == "" {
				removed++
			} else {
				remaining = append(remaining, pc)
			}
		}
		p.warmContainers[runtime] = remaining

		// Update prewarm config
		newCount := p.prewarmConfig[runtime] + delta
		if newCount < 0 {
			newCount = 0
		}
		p.prewarmConfig[runtime] = newCount
	}

	return nil
}

// CleanupIdleContainers removes containers idle longer than maxIdle
func (p *ContainerPool) CleanupIdleContainers(maxIdle time.Duration) error {
	p.mu.Lock()
	defer p.mu.Unlock()

	now := time.Now()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()

	for runtime, containers := range p.warmContainers {
		remaining := make([]*PooledContainer, 0)

		for _, pc := range containers {
			if pc.State == PoolStateWarm && now.Sub(pc.LastUsed) > maxIdle {
				// Remove idle container
				if err := p.manager.RemoveContainer(ctx, pc.Container.ID); err != nil {
					// Log error but continue cleanup
					fmt.Printf("Failed to remove idle container %s: %v\n", pc.Container.ID, err)
				}
			} else {
				remaining = append(remaining, pc)
			}
		}

		p.warmContainers[runtime] = remaining
	}

	return nil
}

// GetPoolStats returns statistics about the pool
func (p *ContainerPool) GetPoolStats() PoolStats {
	p.mu.RLock()
	defer p.mu.RUnlock()

	stats := PoolStats{
		WarmContainers:    make(map[string]int),
		BusyContainers:    len(p.busyContainers),
		PrewarmContainers: make(map[string]int),
		TotalContainers:   len(p.busyContainers),
	}

	for runtime, containers := range p.warmContainers {
		warmCount := 0
		prewarmCount := 0

		for _, pc := range containers {
			warmCount++
			if pc.InitializedAction == "" {
				prewarmCount++
			}
		}

		stats.WarmContainers[runtime] = warmCount
		stats.PrewarmContainers[runtime] = prewarmCount
		stats.TotalContainers += warmCount
	}

	return stats
}

// removeOldestContainer removes the oldest container from the pool
// Must be called with lock held
func (p *ContainerPool) removeOldestContainer() error {
	var oldestPC *PooledContainer
	var oldestRuntime string
	var oldestIndex int

	for runtime, containers := range p.warmContainers {
		for i, pc := range containers {
			if oldestPC == nil || pc.LastUsed.Before(oldestPC.LastUsed) {
				oldestPC = pc
				oldestRuntime = runtime
				oldestIndex = i
			}
		}
	}

	if oldestPC == nil {
		return fmt.Errorf("no containers to remove")
	}

	// Remove from pool
	containers := p.warmContainers[oldestRuntime]
	p.warmContainers[oldestRuntime] = append(containers[:oldestIndex], containers[oldestIndex+1:]...)

	// Remove container
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	return p.manager.RemoveContainer(ctx, oldestPC.Container.ID)
}

// cleanupLoop periodically cleans up idle containers
func (p *ContainerPool) cleanupLoop() {
	defer p.cleanupWg.Done()

	ticker := time.NewTicker(p.cleanupInterval)
	defer ticker.Stop()

	for {
		select {
		case <-ticker.C:
			if err := p.CleanupIdleContainers(p.idleTimeout); err != nil {
				fmt.Printf("Cleanup error: %v\n", err)
			}
		case <-p.stopCleanup:
			return
		}
	}
}

// Shutdown stops the pool and removes all containers
func (p *ContainerPool) Shutdown(ctx context.Context) error {
	// Stop cleanup goroutine
	close(p.stopCleanup)
	p.cleanupWg.Wait()

	p.mu.Lock()
	defer p.mu.Unlock()

	// Remove all warm containers
	for runtime, containers := range p.warmContainers {
		for _, pc := range containers {
			if err := p.manager.RemoveContainer(ctx, pc.Container.ID); err != nil {
				fmt.Printf("Failed to remove container %s during shutdown: %v\n", pc.Container.ID, err)
			}
		}
		delete(p.warmContainers, runtime)
	}

	// Remove all busy containers
	for id, pc := range p.busyContainers {
		if err := p.manager.RemoveContainer(ctx, pc.Container.ID); err != nil {
			fmt.Printf("Failed to remove container %s during shutdown: %v\n", pc.Container.ID, err)
		}
		delete(p.busyContainers, id)
	}

	return nil
}
