package server

import "example.com/shop/internal/store"

// Server serves orders from a store.
type Server struct {
	Logger
	db *store.Store
}

// Handler is anything that can serve.
type Handler interface {
	Serve() error
}

// Logger is embedded in Server.
type Logger struct{}

func (l *Logger) Log(msg string) {}
