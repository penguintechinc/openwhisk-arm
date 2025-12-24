package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"os/signal"
	"syscall"

	"github.com/docker/docker/client"
	"github.com/penguintechinc/penguinwhisk/invoker/internal/config"
	"github.com/penguintechinc/penguinwhisk/invoker/internal/container"
	"github.com/penguintechinc/penguinwhisk/invoker/internal/executor"
	"github.com/penguintechinc/penguinwhisk/invoker/internal/logs"
	"github.com/penguintechinc/penguinwhisk/invoker/internal/messaging"
	"github.com/penguintechinc/penguinwhisk/invoker/internal/proxy"
	"github.com/redis/go-redis/v9"
)

func main() {
	// Load configuration
	cfg, err := config.Load()
	if err != nil {
		log.Fatalf("Failed to load configuration: %v", err)
	}

	log.Printf("Starting invoker: %s", cfg.Invoker.ID)

	ctx := context.Background()

	// Connect to Redis
	redisClient := redis.NewClient(&redis.Options{
		Addr: fmt.Sprintf("%s:%d", cfg.Redis.Host, cfg.Redis.Port),
	})

	if err := redisClient.Ping(ctx).Err(); err != nil {
		log.Fatalf("Failed to connect to Redis: %v", err)
	}
	log.Println("Connected to Redis")

	// Create Docker client
	dockerClient, err := client.NewClientWithOpts(
		client.WithHost(cfg.Docker.Host),
		client.WithAPIVersionNegotiation(),
	)
	if err != nil {
		log.Fatalf("Failed to create Docker client: %v", err)
	}
	defer dockerClient.Close()
	log.Println("Connected to Docker daemon")

	// Create ContainerManager
	containerManager := container.NewManager(dockerClient, cfg.Docker.NetworkName, &container.ResourceLimits{
		MemoryMB:  cfg.Resources.MemoryMB,
		CPUShares: cfg.Resources.CPUShares,
	})

	// Create ContainerPool
	pool := container.NewPool(containerManager, cfg.Pool.MaxSize, cfg.Pool.IdleTimeout)

	// Create RuntimeProxy
	runtimeProxy := proxy.NewRuntimeProxy()

	// Create LogCollector
	logCollector := logs.NewLogCollector(dockerClient)

	// Create Publisher
	publisher := messaging.NewPublisher(redisClient)

	// Create Executor
	exec := executor.NewExecutor(pool, runtimeProxy, logCollector, publisher)

	// Create Consumer with Executor as handler
	consumer := messaging.NewConsumer(redisClient, cfg.Invoker.ID, exec)

	// Create HeartbeatPublisher
	heartbeat := messaging.NewHeartbeatPublisher(redisClient, cfg.Invoker.ID, cfg.Invoker.HeartbeatInterval)

	// Start heartbeat publisher
	heartbeat.Start(ctx)
	log.Println("Heartbeat publisher started")

	// Prewarm containers
	if len(cfg.Pool.Prewarm) > 0 {
		log.Printf("Prewarming containers: %v", cfg.Pool.Prewarm)
		for runtime, count := range cfg.Pool.Prewarm {
			for i := 0; i < count; i++ {
				if err := pool.Prewarm(ctx, runtime); err != nil {
					log.Printf("Failed to prewarm container for runtime %s: %v", runtime, err)
				}
			}
		}
		log.Println("Container prewarming complete")
	}

	// Start consumer in a goroutine
	consumerErrCh := make(chan error, 1)
	go func() {
		log.Println("Starting consumer...")
		if err := consumer.Start(ctx); err != nil {
			consumerErrCh <- err
		}
	}()

	// Graceful shutdown handling
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)

	// Wait for shutdown signal or consumer error
	select {
	case sig := <-sigChan:
		log.Printf("Received signal %v, shutting down...", sig)
	case err := <-consumerErrCh:
		log.Printf("Consumer error: %v, shutting down...", err)
	}

	// Cleanup
	log.Println("Stopping consumer...")
	consumer.Stop()

	log.Println("Stopping heartbeat publisher...")
	heartbeat.Stop()

	log.Println("Draining container pool...")
	pool.Drain(ctx)

	log.Println("Closing Redis connection...")
	if err := redisClient.Close(); err != nil {
		log.Printf("Error closing Redis connection: %v", err)
	}

	log.Println("Invoker shutdown complete")
}
