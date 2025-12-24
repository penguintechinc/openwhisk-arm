package config

import (
	"time"

	"github.com/spf13/viper"
)

// Config holds the application configuration
type Config struct {
	Redis      RedisConfig
	Docker     DockerConfig
	Invoker    InvokerConfig
	Pool       PoolConfig
	MinIO      MinIOConfig
	Resources  ResourceConfig
}

// RedisConfig holds Redis connection settings
type RedisConfig struct {
	Host string
	Port int
	URL  string
}

// DockerConfig holds Docker daemon settings
type DockerConfig struct {
	Host        string
	APIVersion  string
	NetworkName string
}

// InvokerConfig holds invoker-specific settings
type InvokerConfig struct {
	ID               string
	Port             int
	MaxConcurrent    int
	ContainerTimeout int
	HeartbeatInterval time.Duration
}

// PoolConfig holds container pool settings
type PoolConfig struct {
	MaxSize     int
	IdleTimeout time.Duration
	Prewarm     map[string]int // runtime -> count
}

// MinIOConfig holds MinIO connection settings
type MinIOConfig struct {
	Endpoint  string
	AccessKey string
	SecretKey string
	UseSSL    bool
}

// ResourceConfig holds container resource limits
type ResourceConfig struct {
	MemoryMB int64
	CPUShares int64
}

// Load loads configuration from environment variables and config files
func Load() (*Config, error) {
	viper.SetEnvPrefix("INVOKER")
	viper.AutomaticEnv()

	// Set defaults
	viper.SetDefault("redis.host", "redis")
	viper.SetDefault("redis.port", 6379)
	viper.SetDefault("redis.url", "redis://redis:6379")
	viper.SetDefault("docker.host", "unix:///var/run/docker.sock")
	viper.SetDefault("docker.apiversion", "1.41")
	viper.SetDefault("docker.networkname", "openwhisk")
	viper.SetDefault("invoker.id", "invoker0")
	viper.SetDefault("invoker.port", 8085)
	viper.SetDefault("invoker.maxconcurrent", 10)
	viper.SetDefault("invoker.containertimeout", 300)
	viper.SetDefault("invoker.heartbeatinterval", "10s")
	viper.SetDefault("pool.maxsize", 100)
	viper.SetDefault("pool.idletimeout", "10m")
	viper.SetDefault("minio.endpoint", "minio:9000")
	viper.SetDefault("minio.accesskey", "minioadmin")
	viper.SetDefault("minio.secretkey", "minioadmin")
	viper.SetDefault("minio.usessl", false)
	viper.SetDefault("resources.memorymb", 256)
	viper.SetDefault("resources.cpushares", 1024)

	// Parse prewarm configuration
	prewarmMap := make(map[string]int)
	if viper.IsSet("pool.prewarm") {
		prewarmConfig := viper.GetStringMap("pool.prewarm")
		for runtime, count := range prewarmConfig {
			if c, ok := count.(int); ok {
				prewarmMap[runtime] = c
			}
		}
	}

	cfg := &Config{
		Redis: RedisConfig{
			Host: viper.GetString("redis.host"),
			Port: viper.GetInt("redis.port"),
			URL:  viper.GetString("redis.url"),
		},
		Docker: DockerConfig{
			Host:        viper.GetString("docker.host"),
			APIVersion:  viper.GetString("docker.apiversion"),
			NetworkName: viper.GetString("docker.networkname"),
		},
		Invoker: InvokerConfig{
			ID:                viper.GetString("invoker.id"),
			Port:              viper.GetInt("invoker.port"),
			MaxConcurrent:     viper.GetInt("invoker.maxconcurrent"),
			ContainerTimeout:  viper.GetInt("invoker.containertimeout"),
			HeartbeatInterval: viper.GetDuration("invoker.heartbeatinterval"),
		},
		Pool: PoolConfig{
			MaxSize:     viper.GetInt("pool.maxsize"),
			IdleTimeout: viper.GetDuration("pool.idletimeout"),
			Prewarm:     prewarmMap,
		},
		MinIO: MinIOConfig{
			Endpoint:  viper.GetString("minio.endpoint"),
			AccessKey: viper.GetString("minio.accesskey"),
			SecretKey: viper.GetString("minio.secretkey"),
			UseSSL:    viper.GetBool("minio.usessl"),
		},
		Resources: ResourceConfig{
			MemoryMB:  viper.GetInt64("resources.memorymb"),
			CPUShares: viper.GetInt64("resources.cpushares"),
		},
	}

	return cfg, nil
}
