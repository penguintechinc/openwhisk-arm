package runtime

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"time"

	"github.com/sirupsen/logrus"
)

// RuntimeProxy handles HTTP communication with action runtime containers
type RuntimeProxy struct {
	httpClient *http.Client
	timeout    time.Duration
	logger     *logrus.Logger
}

// InitPayload represents the initialization payload sent to runtime containers
type InitPayload struct {
	Name   string            `json:"name"`
	Main   string            `json:"main"`
	Code   string            `json:"code"`
	Binary bool              `json:"binary"`
	Env    map[string]string `json:"env"`
}

// RunPayload represents the execution payload sent to runtime containers
type RunPayload struct {
	Value         map[string]interface{} `json:"value"`
	Namespace     string                 `json:"namespace"`
	ActionName    string                 `json:"action_name"`
	ActivationID  string                 `json:"activation_id"`
	TransactionID string                 `json:"transaction_id"`
	Deadline      int64                  `json:"deadline"`
}

// RunResult represents the result of action execution
type RunResult struct {
	Result     map[string]interface{} `json:"result"`
	Error      string                 `json:"error"`
	StatusCode int                    `json:"statusCode"` // 0=success, 1=app error, 2=dev error
}

// Error types for runtime operations
type InitializationError struct {
	Message    string
	StatusCode int
	Body       string
}

func (e *InitializationError) Error() string {
	return fmt.Sprintf("initialization error: %s (status: %d, body: %s)", e.Message, e.StatusCode, e.Body)
}

type ExecutionError struct {
	Message    string
	StatusCode int
	Body       string
}

func (e *ExecutionError) Error() string {
	return fmt.Sprintf("execution error: %s (status: %d, body: %s)", e.Message, e.StatusCode, e.Body)
}

type TimeoutError struct {
	Message string
	Timeout time.Duration
}

func (e *TimeoutError) Error() string {
	return fmt.Sprintf("timeout error: %s (timeout: %v)", e.Message, e.Timeout)
}

type ContainerError struct {
	Message string
	Cause   error
}

func (e *ContainerError) Error() string {
	if e.Cause != nil {
		return fmt.Sprintf("container error: %s (cause: %v)", e.Message, e.Cause)
	}
	return fmt.Sprintf("container error: %s", e.Message)
}

// NewRuntimeProxy creates a new RuntimeProxy with the specified timeout
func NewRuntimeProxy(timeout time.Duration) *RuntimeProxy {
	logger := logrus.New()
	logger.SetLevel(logrus.InfoLevel)
	logger.SetFormatter(&logrus.JSONFormatter{})

	return &RuntimeProxy{
		httpClient: &http.Client{
			Timeout: timeout,
			Transport: &http.Transport{
				DisableKeepAlives: true, // Disable keep-alive for container isolation
				DialContext: (&net.Dialer{
					Timeout:   10 * time.Second,
					KeepAlive: 0,
				}).DialContext,
				MaxIdleConns:          0,
				MaxIdleConnsPerHost:   0,
				IdleConnTimeout:       0,
				TLSHandshakeTimeout:   10 * time.Second,
				ExpectContinueTimeout: 1 * time.Second,
			},
		},
		timeout: timeout,
		logger:  logger,
	}
}

// Init initializes a runtime container with action code
func (rp *RuntimeProxy) Init(ctx context.Context, containerIP string, initPayload *InitPayload) error {
	url := fmt.Sprintf("http://%s:8080/init", containerIP)

	rp.logger.WithFields(logrus.Fields{
		"url":        url,
		"actionName": initPayload.Name,
		"main":       initPayload.Main,
		"binary":     initPayload.Binary,
	}).Info("Initializing runtime container")

	// Create request payload
	payload := map[string]interface{}{
		"value": map[string]interface{}{
			"name":   initPayload.Name,
			"main":   initPayload.Main,
			"code":   initPayload.Code,
			"binary": initPayload.Binary,
			"env":    initPayload.Env,
		},
	}

	payloadBytes, err := json.Marshal(payload)
	if err != nil {
		return &InitializationError{
			Message: "failed to marshal init payload",
		}
	}

	// Create HTTP request
	req, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(payloadBytes))
	if err != nil {
		return &InitializationError{
			Message: "failed to create init request",
		}
	}
	req.Header.Set("Content-Type", "application/json")

	// Send request
	resp, err := rp.httpClient.Do(req)
	if err != nil {
		// Check for timeout
		if ctx.Err() == context.DeadlineExceeded {
			return &TimeoutError{
				Message: "init request timed out",
				Timeout: rp.timeout,
			}
		}
		return &ContainerError{
			Message: "failed to connect to runtime container",
			Cause:   err,
		}
	}
	defer resp.Body.Close()

	// Read response body
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		rp.logger.WithError(err).Warn("Failed to read init response body")
		body = []byte{}
	}

	// Check status code
	if resp.StatusCode != http.StatusOK {
		rp.logger.WithFields(logrus.Fields{
			"statusCode": resp.StatusCode,
			"body":       string(body),
		}).Error("Init request failed")

		return &InitializationError{
			Message:    "init request returned non-200 status",
			StatusCode: resp.StatusCode,
			Body:       string(body),
		}
	}

	rp.logger.WithFields(logrus.Fields{
		"actionName": initPayload.Name,
		"statusCode": resp.StatusCode,
	}).Info("Runtime container initialized successfully")

	return nil
}

// Run executes an action in a runtime container
func (rp *RuntimeProxy) Run(ctx context.Context, containerIP string, runPayload *RunPayload) (*RunResult, error) {
	url := fmt.Sprintf("http://%s:8080/run", containerIP)

	rp.logger.WithFields(logrus.Fields{
		"url":           url,
		"namespace":     runPayload.Namespace,
		"actionName":    runPayload.ActionName,
		"activationID":  runPayload.ActivationID,
		"transactionID": runPayload.TransactionID,
		"deadline":      runPayload.Deadline,
	}).Info("Executing action in runtime container")

	// Create request payload
	payloadBytes, err := json.Marshal(runPayload)
	if err != nil {
		return nil, &ExecutionError{
			Message: "failed to marshal run payload",
		}
	}

	// Create HTTP request
	req, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(payloadBytes))
	if err != nil {
		return nil, &ExecutionError{
			Message: "failed to create run request",
		}
	}
	req.Header.Set("Content-Type", "application/json")

	// Send request
	resp, err := rp.httpClient.Do(req)
	if err != nil {
		// Check for timeout
		if ctx.Err() == context.DeadlineExceeded {
			return nil, &TimeoutError{
				Message: "run request timed out",
				Timeout: rp.timeout,
			}
		}
		return nil, &ContainerError{
			Message: "failed to connect to runtime container",
			Cause:   err,
		}
	}
	defer resp.Body.Close()

	// Read response body
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		rp.logger.WithError(err).Error("Failed to read run response body")
		return nil, &ExecutionError{
			Message: "failed to read run response",
		}
	}

	// Check status code
	if resp.StatusCode != http.StatusOK {
		rp.logger.WithFields(logrus.Fields{
			"statusCode": resp.StatusCode,
			"body":       string(body),
		}).Error("Run request failed")

		return nil, &ExecutionError{
			Message:    "run request returned non-200 status",
			StatusCode: resp.StatusCode,
			Body:       string(body),
		}
	}

	// Parse response
	var result RunResult
	if err := json.Unmarshal(body, &result); err != nil {
		rp.logger.WithError(err).WithField("body", string(body)).Error("Failed to parse run response")
		return nil, &ExecutionError{
			Message: "failed to parse run response",
			Body:    string(body),
		}
	}

	rp.logger.WithFields(logrus.Fields{
		"activationID": runPayload.ActivationID,
		"statusCode":   result.StatusCode,
		"hasError":     result.Error != "",
	}).Info("Action execution completed")

	return &result, nil
}

// SetLogger allows setting a custom logger
func (rp *RuntimeProxy) SetLogger(logger *logrus.Logger) {
	rp.logger = logger
}
