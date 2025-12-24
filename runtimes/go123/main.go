package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

var (
	compiledBinary string
	actionEnv      map[string]string
	actionMu       sync.RWMutex
)

type InitRequest struct {
	Value struct {
		Code   string            `json:"code"`
		Binary bool              `json:"binary"`
		Main   string            `json:"main"`
		Env    map[string]string `json:"env"`
	} `json:"value"`
}

type RunRequest struct {
	Value      map[string]interface{} `json:"value"`
	Activation struct {
		ID          string `json:"activationId"`
		Namespace   string `json:"namespace"`
		ActionName  string `json:"action_name"`
		APIHost     string `json:"api_host"`
		APIKey      string `json:"api_key"`
		Deadline    int64  `json:"deadline"`
	} `json:"activation"`
}

type ErrorResponse struct {
	Error string `json:"error"`
}

func initHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req InitRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		fmt.Println("XXX_THE_END_OF_A_WHISK_ACTIVATION_XXX")
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(ErrorResponse{Error: "Invalid request: " + err.Error()})
		return
	}

	if req.Value.Code == "" {
		fmt.Println("XXX_THE_END_OF_A_WHISK_ACTIVATION_XXX")
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(ErrorResponse{Error: "Code is required"})
		return
	}

	// Create temp directory for compilation
	tmpDir, err := os.MkdirTemp("", "action-*")
	if err != nil {
		fmt.Println("XXX_THE_END_OF_A_WHISK_ACTIVATION_XXX")
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(ErrorResponse{Error: "Failed to create temp directory: " + err.Error()})
		return
	}

	// Write code to file
	srcFile := filepath.Join(tmpDir, "main.go")
	if err := os.WriteFile(srcFile, []byte(req.Value.Code), 0644); err != nil {
		os.RemoveAll(tmpDir)
		fmt.Println("XXX_THE_END_OF_A_WHISK_ACTIVATION_XXX")
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(ErrorResponse{Error: "Failed to write code: " + err.Error()})
		return
	}

	// Initialize go.mod
	modCmd := exec.Command("go", "mod", "init", "action")
	modCmd.Dir = tmpDir
	if err := modCmd.Run(); err != nil {
		os.RemoveAll(tmpDir)
		fmt.Println("XXX_THE_END_OF_A_WHISK_ACTIVATION_XXX")
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadGateway)
		json.NewEncoder(w).Encode(ErrorResponse{Error: "Failed to initialize module: " + err.Error()})
		return
	}

	// Compile the code
	binaryPath := filepath.Join(tmpDir, "action")
	var compileErr bytes.Buffer
	buildCmd := exec.Command("go", "build", "-o", binaryPath, srcFile)
	buildCmd.Dir = tmpDir
	buildCmd.Stderr = &compileErr

	if err := buildCmd.Run(); err != nil {
		os.RemoveAll(tmpDir)
		fmt.Println("XXX_THE_END_OF_A_WHISK_ACTIVATION_XXX")
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadGateway)
		errMsg := strings.TrimSpace(compileErr.String())
		if errMsg == "" {
			errMsg = err.Error()
		}
		json.NewEncoder(w).Encode(ErrorResponse{Error: "Compilation failed: " + errMsg})
		return
	}

	// Store compiled binary path and environment
	actionMu.Lock()
	compiledBinary = binaryPath
	actionEnv = req.Value.Env
	if actionEnv == nil {
		actionEnv = make(map[string]string)
	}
	actionMu.Unlock()

	fmt.Println("XXX_THE_END_OF_A_WHISK_ACTIVATION_XXX")
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]bool{"ok": true})
}

func runHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	actionMu.RLock()
	binary := compiledBinary
	env := actionEnv
	actionMu.RUnlock()

	if binary == "" {
		fmt.Println("XXX_THE_END_OF_A_WHISK_ACTIVATION_XXX")
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(ErrorResponse{Error: "Action not initialized"})
		return
	}

	var req RunRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		req.Value = make(map[string]interface{})
	}

	// Prepare parameters as JSON
	paramsJSON, err := json.Marshal(req.Value)
	if err != nil {
		fmt.Println("XXX_THE_END_OF_A_WHISK_ACTIVATION_XXX")
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(ErrorResponse{Error: "Failed to marshal params: " + err.Error()})
		return
	}

	// Set up command with environment variables
	cmd := exec.Command(binary)

	// Set action environment
	cmd.Env = os.Environ()
	for k, v := range env {
		cmd.Env = append(cmd.Env, fmt.Sprintf("%s=%s", k, v))
	}

	// Set OpenWhisk environment variables
	cmd.Env = append(cmd.Env, fmt.Sprintf("__OW_ACTIVATION_ID=%s", req.Activation.ID))
	cmd.Env = append(cmd.Env, fmt.Sprintf("__OW_NAMESPACE=%s", req.Activation.Namespace))
	cmd.Env = append(cmd.Env, fmt.Sprintf("__OW_ACTION_NAME=%s", req.Activation.ActionName))
	cmd.Env = append(cmd.Env, fmt.Sprintf("__OW_API_HOST=%s", req.Activation.APIHost))
	cmd.Env = append(cmd.Env, fmt.Sprintf("__OW_API_KEY=%s", req.Activation.APIKey))
	cmd.Env = append(cmd.Env, fmt.Sprintf("__OW_DEADLINE=%d", req.Activation.Deadline))
	cmd.Env = append(cmd.Env, fmt.Sprintf("__OW_ACTIVATION_BODY=%s", string(paramsJSON)))

	// Set stdin with parameters
	cmd.Stdin = bytes.NewReader(paramsJSON)

	// Capture stdout and stderr
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	// Set timeout (default 60 seconds if no deadline)
	timeout := 60 * time.Second
	if req.Activation.Deadline > 0 {
		deadline := time.Unix(req.Activation.Deadline/1000, 0)
		timeout = time.Until(deadline)
		if timeout <= 0 {
			timeout = 1 * time.Second
		}
	}

	// Run with timeout
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	cmd.SysProcAttr = nil

	errChan := make(chan error, 1)
	go func() {
		errChan <- cmd.Run()
	}()

	var runErr error
	select {
	case <-ctx.Done():
		cmd.Process.Kill()
		runErr = fmt.Errorf("action timed out after %v", timeout)
	case runErr = <-errChan:
	}

	// Print stderr as logs
	if stderr.Len() > 0 {
		fmt.Print(stderr.String())
	}

	// Print activation marker
	fmt.Println("XXX_THE_END_OF_A_WHISK_ACTIVATION_XXX")

	// Handle execution errors
	if runErr != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadGateway)
		json.NewEncoder(w).Encode(ErrorResponse{Error: "Action execution failed: " + runErr.Error()})
		return
	}

	// Parse stdout as JSON result
	var result map[string]interface{}
	stdoutStr := strings.TrimSpace(stdout.String())
	if stdoutStr != "" {
		if err := json.Unmarshal([]byte(stdoutStr), &result); err != nil {
			// If not valid JSON, wrap stdout as string result
			result = map[string]interface{}{
				"body": stdoutStr,
			}
		}
	} else {
		result = make(map[string]interface{})
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(result)
}

func healthHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

func main() {
	http.HandleFunc("/init", initHandler)
	http.HandleFunc("/run", runHandler)
	http.HandleFunc("/health", healthHandler)

	fmt.Println("OpenWhisk Go 1.23 runtime listening on port 8080")
	if err := http.ListenAndServe(":8080", nil); err != nil {
		fmt.Printf("Server error: %v\n", err)
	}
}
