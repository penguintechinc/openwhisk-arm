package messaging

import (
	"context"
	"encoding/json"
	"fmt"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"
)

const (
	// Default stream configuration
	defaultActivationsStream = "penguinwhisk:activations"
	defaultMaxStreamLen      = 10000
	defaultChannelTTL        = 300 // 5 minutes
)

// ActivationResponse represents the response portion of an activation
type ActivationResponse struct {
	Success bool                   `json:"success"`
	Result  map[string]interface{} `json:"result"`
}

// ActivationResult represents the complete activation result
type ActivationResult struct {
	ActivationID  string                 `json:"activationId"`
	Namespace     string                 `json:"namespace"`
	ActionName    string                 `json:"name"`
	ActionVersion string                 `json:"version"`
	Subject       string                 `json:"subject"`
	Start         int64                  `json:"start"`
	End           int64                  `json:"end"`
	Duration      int                    `json:"duration"`
	StatusCode    int                    `json:"statusCode"` // 0=success, 1=app error, 2=dev error, 3=internal error
	Response      ActivationResponse     `json:"response"`
	Logs          []string               `json:"logs"`
	Annotations   map[string]interface{} `json:"annotations"`
	Cause         string                 `json:"cause,omitempty"` // for sequences
}

// Publisher handles publishing activation results to Redis
type Publisher struct {
	redisClient      *redis.Client
	activationsStream string
	maxStreamLen     int64
	channelTTL       time.Duration
}

// NewPublisher creates a new activation result publisher
func NewPublisher(redisClient *redis.Client) *Publisher {
	return &Publisher{
		redisClient:      redisClient,
		activationsStream: defaultActivationsStream,
		maxStreamLen:     defaultMaxStreamLen,
		channelTTL:       time.Duration(defaultChannelTTL) * time.Second,
	}
}

// PublishActivation publishes an activation result to the main activations stream
func (p *Publisher) PublishActivation(ctx context.Context, result *ActivationResult) error {
	if result == nil {
		return fmt.Errorf("activation result cannot be nil")
	}

	// Convert result to Redis hash fields
	fields, err := p.resultToFields(result)
	if err != nil {
		return fmt.Errorf("failed to convert result to fields: %w", err)
	}

	// Publish to stream with approximate maxlen trimming
	args := &redis.XAddArgs{
		Stream: p.activationsStream,
		MaxLen: p.maxStreamLen,
		Approx: true,
		Values: fields,
	}

	_, err = p.redisClient.XAdd(ctx, args).Result()
	if err != nil {
		return fmt.Errorf("failed to publish activation to stream: %w", err)
	}

	return nil
}

// PublishToChannel publishes an activation result to a specific response channel
// Used for blocking invocations where the controller is waiting for a response
func (p *Publisher) PublishToChannel(ctx context.Context, channel string, result *ActivationResult) error {
	if result == nil {
		return fmt.Errorf("activation result cannot be nil")
	}

	if channel == "" {
		return fmt.Errorf("channel cannot be empty")
	}

	// Convert result to Redis hash fields
	fields, err := p.resultToFields(result)
	if err != nil {
		return fmt.Errorf("failed to convert result to fields: %w", err)
	}

	// Publish to response channel stream
	args := &redis.XAddArgs{
		Stream: channel,
		MaxLen: 1, // Only keep the latest response
		Approx: false,
		Values: fields,
	}

	_, err = p.redisClient.XAdd(ctx, args).Result()
	if err != nil {
		return fmt.Errorf("failed to publish to channel: %w", err)
	}

	// Set TTL on the channel to auto-cleanup
	err = p.redisClient.Expire(ctx, channel, p.channelTTL).Err()
	if err != nil {
		return fmt.Errorf("failed to set TTL on channel: %w", err)
	}

	return nil
}

// resultToFields converts ActivationResult to Redis stream fields
func (p *Publisher) resultToFields(result *ActivationResult) (map[string]interface{}, error) {
	fields := make(map[string]interface{})

	// Basic fields
	fields["activationId"] = result.ActivationID
	fields["namespace"] = result.Namespace
	fields["name"] = result.ActionName
	fields["version"] = result.ActionVersion
	fields["subject"] = result.Subject
	fields["start"] = strconv.FormatInt(result.Start, 10)
	fields["end"] = strconv.FormatInt(result.End, 10)
	fields["duration"] = strconv.Itoa(result.Duration)
	fields["statusCode"] = strconv.Itoa(result.StatusCode)

	// Serialize response
	responseJSON, err := json.Marshal(result.Response)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal response: %w", err)
	}
	fields["response"] = string(responseJSON)

	// Serialize logs
	logsJSON, err := json.Marshal(result.Logs)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal logs: %w", err)
	}
	fields["logs"] = string(logsJSON)

	// Serialize annotations
	annotationsJSON, err := json.Marshal(result.Annotations)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal annotations: %w", err)
	}
	fields["annotations"] = string(annotationsJSON)

	// Optional cause field for sequences
	if result.Cause != "" {
		fields["cause"] = result.Cause
	}

	return fields, nil
}

// SetMaxStreamLen configures the maximum stream length
func (p *Publisher) SetMaxStreamLen(maxLen int64) {
	p.maxStreamLen = maxLen
}

// SetChannelTTL configures the TTL for response channels
func (p *Publisher) SetChannelTTL(ttl time.Duration) {
	p.channelTTL = ttl
}

// Close closes the publisher (currently a no-op, but included for future cleanup)
func (p *Publisher) Close() error {
	// No cleanup needed currently, but method exists for interface compatibility
	return nil
}
