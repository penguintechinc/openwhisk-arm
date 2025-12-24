package executor

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/penguintechinc/penguinwhisk/invoker/internal/container"
	"github.com/penguintechinc/penguinwhisk/invoker/internal/logs"
	"github.com/penguintechinc/penguinwhisk/invoker/internal/messaging"
	"github.com/penguintechinc/penguinwhisk/invoker/internal/proxy"
)

// Executor handles invocation messages and executes actions in containers
type Executor struct {
	pool       *container.ContainerPool
	proxy      *proxy.RuntimeProxy
	logs       *logs.LogCollector
	publisher  *messaging.Publisher
	codeClient *http.Client
}

// NewExecutor creates a new executor instance
func NewExecutor(
	pool *container.ContainerPool,
	proxy *proxy.RuntimeProxy,
	logs *logs.LogCollector,
	publisher *messaging.Publisher,
) *Executor {
	return &Executor{
		pool:      pool,
		proxy:     proxy,
		logs:      logs,
		publisher: publisher,
		codeClient: &http.Client{
			Timeout: 30 * time.Second,
		},
	}
}

// HandleInvocation processes an invocation message and executes the action
func (e *Executor) HandleInvocation(ctx context.Context, msg *messaging.InvocationMessage) (*messaging.ActivationResult, error) {
	startTime := time.Now()

	// Get container from pool (warm or cold)
	cont, isColdStart, err := e.pool.Get(ctx, msg.Runtime)
	if err != nil {
		return nil, fmt.Errorf("failed to get container: %w", err)
	}

	// Ensure container is returned to pool or removed
	var returnToPool = true
	defer func() {
		if returnToPool {
			e.pool.Return(cont)
		} else {
			e.pool.Remove(cont.ID)
		}
	}()

	// Fetch action code from MinIO
	code, err := e.fetchCode(ctx, msg.CodeURL)
	if err != nil {
		returnToPool = false
		return nil, fmt.Errorf("failed to fetch code: %w", err)
	}

	// If cold start, initialize the container
	if isColdStart {
		initReq := &proxy.InitRequest{
			Code:   code,
			Binary: msg.Binary,
			Main:   msg.Main,
		}
		if err := e.proxy.Init(ctx, cont, initReq); err != nil {
			returnToPool = false
			return nil, fmt.Errorf("failed to initialize container: %w", err)
		}
	}

	// Run the action
	runReq := &proxy.RunRequest{
		Value: msg.Parameters,
	}
	runResp, err := e.proxy.Run(ctx, cont, runReq)
	if err != nil {
		returnToPool = false
		return nil, fmt.Errorf("failed to run action: %w", err)
	}

	// Collect logs from container
	containerLogs, err := e.logs.Collect(ctx, cont.ID)
	if err != nil {
		// Log collection failure shouldn't fail the activation
		containerLogs = []string{fmt.Sprintf("Failed to collect logs: %v", err)}
	}

	// Calculate duration
	endTime := time.Now()
	duration := endTime.Sub(startTime).Milliseconds()

	// Build activation result
	result := &messaging.ActivationResult{
		ActivationID: msg.ActivationID,
		Response: messaging.Response{
			StatusCode: runResp.StatusCode,
			Result:     runResp.Result,
		},
		Logs:      containerLogs,
		Start:     startTime.UnixMilli(),
		End:       endTime.UnixMilli(),
		Duration:  duration,
		Namespace: msg.Namespace,
		Action:    msg.Action,
	}

	// Publish result
	if err := e.publisher.PublishResult(ctx, result); err != nil {
		return result, fmt.Errorf("failed to publish result: %w", err)
	}

	return result, nil
}

// fetchCode retrieves action code from MinIO using a presigned URL
func (e *Executor) fetchCode(ctx context.Context, codeURL string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, codeURL, nil)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	resp, err := e.codeClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("failed to fetch code: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("unexpected status code: %d", resp.StatusCode)
	}

	code, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read response body: %w", err)
	}

	return code, nil
}

// InvocationHandler defines the interface for handling invocations
type InvocationHandler interface {
	HandleInvocation(ctx context.Context, msg *messaging.InvocationMessage) (*messaging.ActivationResult, error)
}
