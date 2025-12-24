package runtime

import (
	"bufio"
	"context"
	"encoding/binary"
	"fmt"
	"io"
	"strings"
	"time"

	"github.com/docker/docker/api/types/container"
	"github.com/docker/docker/client"
)

const (
	// LogMarker indicates the end of an activation's logs
	LogMarker = "XXX_THE_END_OF_A_WHISK_ACTIVATION_XXX"
	// DefaultMaxLogSize is the maximum size of logs in bytes
	DefaultMaxLogSize = 10 * 1024 * 1024 // 10 MB
)

// LogLine represents a single log line from a container
type LogLine struct {
	Timestamp time.Time
	Stream    string // "stdout" or "stderr"
	Message   string
}

// LogCollector handles collection and framing of container logs
type LogCollector struct {
	manager   *ContainerManager
	logMarker string
}

// NewLogCollector creates a new log collector
func NewLogCollector(manager *ContainerManager) *LogCollector {
	return &LogCollector{
		manager:   manager,
		logMarker: LogMarker,
	}
}

// CollectLogs retrieves logs from a container since the specified timestamp
func (lc *LogCollector) CollectLogs(ctx context.Context, containerID string, since time.Time) ([]LogLine, error) {
	opts := container.LogsOptions{
		ShowStdout: true,
		ShowStderr: true,
		Since:      since.Format(time.RFC3339Nano),
		Timestamps: true,
		Follow:     false,
	}

	logs, err := lc.manager.client.ContainerLogs(ctx, containerID, opts)
	if err != nil {
		return nil, fmt.Errorf("failed to get container logs: %w", err)
	}
	defer logs.Close()

	return lc.parseLogs(logs)
}

// StreamLogs streams logs from a container as they arrive
func (lc *LogCollector) StreamLogs(ctx context.Context, containerID string, since time.Time) (<-chan LogLine, error) {
	opts := container.LogsOptions{
		ShowStdout: true,
		ShowStderr: true,
		Since:      since.Format(time.RFC3339Nano),
		Timestamps: true,
		Follow:     true,
	}

	logs, err := lc.manager.client.ContainerLogs(ctx, containerID, opts)
	if err != nil {
		return nil, fmt.Errorf("failed to stream container logs: %w", err)
	}

	ch := make(chan LogLine, 100)
	go func() {
		defer close(ch)
		defer logs.Close()

		logLines, err := lc.parseLogs(logs)
		if err != nil {
			return
		}

		for _, line := range logLines {
			select {
			case ch <- line:
				if strings.Contains(line.Message, lc.logMarker) {
					return
				}
			case <-ctx.Done():
				return
			}
		}
	}()

	return ch, nil
}

// parseLogs parses Docker logs format into LogLine structs
func (lc *LogCollector) parseLogs(reader io.Reader) ([]LogLine, error) {
	var lines []LogLine
	header := make([]byte, 8)

	for {
		// Read 8-byte header
		n, err := io.ReadFull(reader, header)
		if err != nil {
			if err == io.EOF {
				break
			}
			if n == 0 {
				break
			}
			return nil, fmt.Errorf("failed to read log header: %w", err)
		}

		// Parse header
		streamType := header[0]
		size := binary.BigEndian.Uint32(header[4:8])

		// Read message
		message := make([]byte, size)
		_, err = io.ReadFull(reader, message)
		if err != nil {
			return nil, fmt.Errorf("failed to read log message: %w", err)
		}

		// Parse timestamp and message
		line := string(message)
		logLine, err := lc.parseLogLine(line, streamType)
		if err != nil {
			continue // Skip malformed lines
		}

		lines = append(lines, logLine)

		// Stop at marker
		if strings.Contains(logLine.Message, lc.logMarker) {
			break
		}
	}

	return lines, nil
}

// parseLogLine parses a single log line with timestamp
func (lc *LogCollector) parseLogLine(line string, streamType byte) (LogLine, error) {
	// Format: "2024-01-01T00:00:00.000000000Z message"
	parts := strings.SplitN(strings.TrimSpace(line), " ", 2)
	if len(parts) != 2 {
		return LogLine{}, fmt.Errorf("invalid log line format")
	}

	timestamp, err := time.Parse(time.RFC3339Nano, parts[0])
	if err != nil {
		return LogLine{}, fmt.Errorf("failed to parse timestamp: %w", err)
	}

	stream := "stdout"
	if streamType == 2 {
		stream = "stderr"
	}

	return LogLine{
		Timestamp: timestamp,
		Stream:    stream,
		Message:   parts[1],
	}, nil
}

// FormatLogs formats log lines into OpenWhisk log format
func (lc *LogCollector) FormatLogs(logs []LogLine) []string {
	formatted := make([]string, 0, len(logs))

	for _, line := range logs {
		// Skip the marker itself
		if strings.Contains(line.Message, lc.logMarker) {
			continue
		}

		// Format: "TIMESTAMP STREAM: MESSAGE"
		timestamp := line.Timestamp.Format(time.RFC3339Nano)
		formatted = append(formatted, fmt.Sprintf("%s %s: %s", timestamp, line.Stream, line.Message))
	}

	return formatted
}

// TruncateLogs truncates logs to the specified maximum size in bytes
func (lc *LogCollector) TruncateLogs(logs []string, maxSize int) []string {
	if maxSize <= 0 {
		maxSize = DefaultMaxLogSize
	}

	var totalSize int
	var truncated []string
	truncationMarker := "... (log truncated)"

	for _, line := range logs {
		lineSize := len(line) + 1 // +1 for newline
		if totalSize+lineSize > maxSize {
			truncated = append(truncated, truncationMarker)
			break
		}
		truncated = append(truncated, line)
		totalSize += lineSize
	}

	return truncated
}

// CollectAndFormatLogs is a convenience method that collects and formats logs
func (lc *LogCollector) CollectAndFormatLogs(ctx context.Context, containerID string, since time.Time, maxSize int) ([]string, error) {
	logs, err := lc.CollectLogs(ctx, containerID, since)
	if err != nil {
		return nil, err
	}

	formatted := lc.FormatLogs(logs)
	return lc.TruncateLogs(formatted, maxSize), nil
}
