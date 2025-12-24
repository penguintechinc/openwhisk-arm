package types

import "time"

// Activation represents an OpenWhisk activation record
type Activation struct {
	ActivationID  string                 `json:"activationId"`
	Namespace     string                 `json:"namespace"`
	Name          string                 `json:"name"`
	Version       string                 `json:"version"`
	Subject       string                 `json:"subject"`
	Action        *Action                `json:"action"`
	Start         time.Time              `json:"start"`
	End           time.Time              `json:"end"`
	Duration      int64                  `json:"duration"`
	Response      *Response              `json:"response"`
	Logs          []string               `json:"logs"`
	Annotations   map[string]interface{} `json:"annotations"`
	StatusCode    int                    `json:"statusCode"`
	Published     bool                   `json:"published"`
	InvokeTime    int64                  `json:"invokeTime"`
}

// Action represents an OpenWhisk action
type Action struct {
	Name      string `json:"name"`
	Namespace string `json:"namespace"`
	Version   string `json:"version"`
	Path      string `json:"path"`
}

// InvocationRequest represents a request to invoke an action
type InvocationRequest struct {
	ActivationID string                 `json:"activationId"`
	Action       *Action                `json:"action"`
	Parameters   map[string]interface{} `json:"parameters"`
	AuthKey      string                 `json:"authKey"`
	TransID      string                 `json:"transId"`
}

// Response represents the response from an action invocation
type Response struct {
	Status     string                 `json:"status"`
	StatusCode int                    `json:"statusCode"`
	Result     map[string]interface{} `json:"result"`
	Error      string                 `json:"error"`
}
