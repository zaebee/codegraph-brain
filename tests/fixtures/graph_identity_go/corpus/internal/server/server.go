package server

import "example.com/shop/internal/store"

// New builds a Server.
func New(db *store.Store) *Server {
	return &Server{db: db}
}

func (s *Server) Start() {
	s.Log("starting")
	s.Serve()
	s.db.Save("order")
	validate()
}

func (s *Server) Serve() error {
	return nil
}
