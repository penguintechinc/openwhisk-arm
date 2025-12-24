package types

// RuntimeKind constants for supported runtimes
const (
	RuntimeKindNodeJS   = "nodejs:20"
	RuntimeKindPython   = "python:3.12"
	RuntimeKindGo       = "go:1.23"
)

// Container represents a Docker container for action execution
type Container struct {
	ID             string
	Name           string
	Image          string
	Runtime        string
	State          ContainerState
	CreatedAt      int64
	ExitCode       int
	Error          string
}

// ContainerState represents the state of a container
type ContainerState struct {
	Running    bool
	Paused     bool
	Restarting bool
	OOMKilled  bool
	Dead       bool
	ExitCode   int
	Status     string
	Error      string
	StartedAt  int64
	FinishedAt int64
}
