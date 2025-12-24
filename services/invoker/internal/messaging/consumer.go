package messaging

import (
	"context"
	"encoding/json"
	"fmt"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"
	"github.com/rs/zerolog/log"
)

const (
	// StreamName is the Redis stream for invocation requests
	StreamName = "penguinwhisk:invocations"
	// GroupName is the consumer group for invokers
	GroupName = "invokers"
	// ActivationsStream is where results are published
	ActivationsStream = "penguinwhisk:activations"
	// BlockTimeout for XREADGROUP
	BlockTimeout = 2000 * time.Millisecond
	// MaxRetries for message processing
	MaxRetries = 3
)

// InvocationHandler processes invocation requests
type InvocationHandler interface {
	HandleInvocation(ctx context.Context, msg *InvocationMessage) (*ActivationResult, error)
}

// Consumer consumes invocation requests from Redis Streams
type Consumer struct {
	redisClient  *redis.Client
	invokerID    string
	streamName   string
	groupName    string
	consumerName string
	handler      InvocationHandler

	ctx    context.Context
	cancel context.CancelFunc
	wg     sync.WaitGroup
	mu     sync.Mutex
	active int
}

// InvocationMessage represents an invocation request
type InvocationMessage struct {
	ActivationID    string            `json:"activation_id"`
	Action          ActionSpec        `json:"action"`
	Params          map[string]any    `json:"params"`
	Blocking        bool              `json:"blocking"`
	ResponseChannel string            `json:"response_channel,omitempty"`
	Deadline        int64             `json:"deadline"`
	Context         InvocationContext `json:"context"`
}

// ActionSpec describes the action to invoke
type ActionSpec struct {
	Namespace  string         `json:"namespace"`
	Name       string         `json:"name"`
	Version    string         `json:"version"`
	Exec       ExecSpec       `json:"exec"`
	Limits     LimitsSpec     `json:"limits"`
	Parameters map[string]any `json:"parameters,omitempty"`
}

// ExecSpec describes action execution metadata
type ExecSpec struct {
	Kind       string `json:"kind"`
	Code       string `json:"code,omitempty"`
	Image      string `json:"image,omitempty"`
	Main       string `json:"main,omitempty"`
	Binary     bool   `json:"binary,omitempty"`
	Entrypoint string `json:"entrypoint,omitempty"`
}

// LimitsSpec defines resource limits
type LimitsSpec struct {
	Timeout     int `json:"timeout"`      // milliseconds
	Memory      int `json:"memory"`       // megabytes
	Concurrency int `json:"concurrency"`  // max concurrent activations
	Logs        int `json:"logs"`         // kilobytes
}

// InvocationContext provides invocation metadata
type InvocationContext struct {
	Namespace   string `json:"namespace"`
	ActionName  string `json:"action_name"`
	ActivationID string `json:"activation_id"`
	APIHost     string `json:"api_host"`
	APIKey      string `json:"api_key,omitempty"`
	Deadline    int64  `json:"deadline"`
}

// ActivationResult represents the result of an invocation
type ActivationResult struct {
	ActivationID string         `json:"activation_id"`
	Namespace    string         `json:"namespace"`
	Name         string         `json:"name"`
	Version      string         `json:"version"`
	Response     Response       `json:"response"`
	Start        int64          `json:"start"`
	End          int64          `json:"end"`
	Duration     int64          `json:"duration"`
	Annotations  []Annotation   `json:"annotations,omitempty"`
	Logs         []string       `json:"logs,omitempty"`
}

// Response contains activation result
type Response struct {
	StatusCode int            `json:"statusCode"`
	Success    bool           `json:"success"`
	Result     map[string]any `json:"result,omitempty"`
	Error      string         `json:"error,omitempty"`
}

// Annotation represents activation metadata
type Annotation struct {
	Key   string `json:"key"`
	Value any    `json:"value"`
}

// NewConsumer creates a new Redis Streams consumer
func NewConsumer(redisURL, invokerID string, handler InvocationHandler) (*Consumer, error) {
	opts, err := redis.ParseURL(redisURL)
	if err != nil {
		return nil, fmt.Errorf("parse redis URL: %w", err)
	}

	client := redis.NewClient(opts)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if err := client.Ping(ctx).Err(); err != nil {
		return nil, fmt.Errorf("connect to redis: %w", err)
	}

	c := &Consumer{
		redisClient:  client,
		invokerID:    invokerID,
		streamName:   StreamName,
		groupName:    GroupName,
		consumerName: fmt.Sprintf("invoker-%s", invokerID),
		handler:      handler,
	}

	if err := c.ensureConsumerGroup(ctx); err != nil {
		return nil, fmt.Errorf("ensure consumer group: %w", err)
	}

	log.Info().
		Str("invoker_id", invokerID).
		Str("stream", StreamName).
		Str("group", GroupName).
		Str("consumer", c.consumerName).
		Msg("Consumer initialized")

	return c, nil
}

// ensureConsumerGroup creates the consumer group if it doesn't exist
func (c *Consumer) ensureConsumerGroup(ctx context.Context) error {
	err := c.redisClient.XGroupCreateMkStream(ctx, c.streamName, c.groupName, "0").Err()
	if err != nil && err.Error() != "BUSYGROUP Consumer Group name already exists" {
		return fmt.Errorf("create consumer group: %w", err)
	}

	log.Debug().
		Str("stream", c.streamName).
		Str("group", c.groupName).
		Msg("Consumer group ready")

	return nil
}

// Start begins consuming messages from the stream
func (c *Consumer) Start(ctx context.Context) error {
	c.ctx, c.cancel = context.WithCancel(ctx)

	log.Info().
		Str("consumer", c.consumerName).
		Msg("Starting consumer")

	for {
		select {
		case <-c.ctx.Done():
			log.Info().Msg("Consumer shutdown requested")
			c.wg.Wait()
			return c.ctx.Err()
		default:
			if err := c.readMessages(); err != nil {
				log.Error().Err(err).Msg("Error reading messages")
				time.Sleep(time.Second)
			}
		}
	}
}

// readMessages reads and processes messages from the stream
func (c *Consumer) readMessages() error {
	streams, err := c.redisClient.XReadGroup(c.ctx, &redis.XReadGroupArgs{
		Group:    c.groupName,
		Consumer: c.consumerName,
		Streams:  []string{c.streamName, ">"},
		Count:    10,
		Block:    BlockTimeout,
	}).Result()

	if err != nil {
		if err == redis.Nil {
			return nil
		}
		return fmt.Errorf("xreadgroup: %w", err)
	}

	for _, stream := range streams {
		for _, message := range stream.Messages {
			c.wg.Add(1)
			c.incrementActive()

			go func(msg redis.XMessage) {
				defer c.wg.Done()
				defer c.decrementActive()
				c.processMessage(c.ctx, msg)
			}(message)
		}
	}

	return nil
}

// processMessage processes a single message
func (c *Consumer) processMessage(ctx context.Context, msg redis.XMessage) {
	log.Debug().
		Str("message_id", msg.ID).
		Interface("values", msg.Values).
		Msg("Processing message")

	// Parse invocation message
	invMsg, err := c.parseInvocationMessage(msg.Values)
	if err != nil {
		log.Error().
			Err(err).
			Str("message_id", msg.ID).
			Msg("Failed to parse invocation message")
		c.ackMessage(ctx, msg.ID)
		return
	}

	// Check deadline
	if time.Now().UnixMilli() > invMsg.Deadline {
		log.Warn().
			Str("activation_id", invMsg.ActivationID).
			Int64("deadline", invMsg.Deadline).
			Msg("Invocation already past deadline")
		c.ackMessage(ctx, msg.ID)
		c.publishErrorResult(ctx, invMsg, "Invocation deadline exceeded")
		return
	}

	// Create invocation context with timeout
	deadline := time.UnixMilli(invMsg.Deadline)
	invCtx, cancel := context.WithDeadline(ctx, deadline)
	defer cancel()

	// Handle invocation
	result, err := c.handler.HandleInvocation(invCtx, invMsg)
	if err != nil {
		log.Error().
			Err(err).
			Str("activation_id", invMsg.ActivationID).
			Msg("Invocation failed")

		result = &ActivationResult{
			ActivationID: invMsg.ActivationID,
			Namespace:    invMsg.Action.Namespace,
			Name:         invMsg.Action.Name,
			Version:      invMsg.Action.Version,
			Response: Response{
				StatusCode: 500,
				Success:    false,
				Error:      err.Error(),
			},
			Start: time.Now().UnixMilli(),
			End:   time.Now().UnixMilli(),
		}
	}

	// Publish result to activations stream
	if err := c.publishResult(ctx, result); err != nil {
		log.Error().
			Err(err).
			Str("activation_id", invMsg.ActivationID).
			Msg("Failed to publish result")
	}

	// Acknowledge message
	c.ackMessage(ctx, msg.ID)

	log.Info().
		Str("activation_id", invMsg.ActivationID).
		Bool("success", result.Response.Success).
		Int64("duration_ms", result.Duration).
		Msg("Invocation completed")
}

// parseInvocationMessage parses message values into InvocationMessage
func (c *Consumer) parseInvocationMessage(values map[string]any) (*InvocationMessage, error) {
	data, ok := values["data"].(string)
	if !ok {
		return nil, fmt.Errorf("missing or invalid 'data' field")
	}

	var msg InvocationMessage
	if err := json.Unmarshal([]byte(data), &msg); err != nil {
		return nil, fmt.Errorf("unmarshal invocation message: %w", err)
	}

	return &msg, nil
}

// publishResult publishes activation result to activations stream
func (c *Consumer) publishResult(ctx context.Context, result *ActivationResult) error {
	data, err := json.Marshal(result)
	if err != nil {
		return fmt.Errorf("marshal result: %w", err)
	}

	err = c.redisClient.XAdd(ctx, &redis.XAddArgs{
		Stream: ActivationsStream,
		Values: map[string]any{
			"activation_id": result.ActivationID,
			"namespace":     result.Namespace,
			"success":       result.Response.Success,
			"data":          string(data),
		},
	}).Err()

	if err != nil {
		return fmt.Errorf("xadd to activations stream: %w", err)
	}

	return nil
}

// publishErrorResult publishes an error result
func (c *Consumer) publishErrorResult(ctx context.Context, msg *InvocationMessage, errMsg string) {
	result := &ActivationResult{
		ActivationID: msg.ActivationID,
		Namespace:    msg.Action.Namespace,
		Name:         msg.Action.Name,
		Version:      msg.Action.Version,
		Response: Response{
			StatusCode: 500,
			Success:    false,
			Error:      errMsg,
		},
		Start: time.Now().UnixMilli(),
		End:   time.Now().UnixMilli(),
	}

	if err := c.publishResult(ctx, result); err != nil {
		log.Error().
			Err(err).
			Str("activation_id", msg.ActivationID).
			Msg("Failed to publish error result")
	}
}

// ackMessage acknowledges a message
func (c *Consumer) ackMessage(ctx context.Context, messageID string) {
	err := c.redisClient.XAck(ctx, c.streamName, c.groupName, messageID).Err()
	if err != nil {
		log.Error().
			Err(err).
			Str("message_id", messageID).
			Msg("Failed to acknowledge message")
	}
}

// Stop gracefully stops the consumer
func (c *Consumer) Stop() {
	log.Info().Msg("Stopping consumer")

	if c.cancel != nil {
		c.cancel()
	}

	c.wg.Wait()

	if c.redisClient != nil {
		if err := c.redisClient.Close(); err != nil {
			log.Error().Err(err).Msg("Error closing redis client")
		}
	}

	log.Info().Msg("Consumer stopped")
}

// GetActiveInvocations returns the count of active invocations
func (c *Consumer) GetActiveInvocations() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.active
}

// incrementActive increments active invocation counter
func (c *Consumer) incrementActive() {
	c.mu.Lock()
	c.active++
	c.mu.Unlock()
}

// decrementActive decrements active invocation counter
func (c *Consumer) decrementActive() {
	c.mu.Lock()
	c.active--
	c.mu.Unlock()
}
