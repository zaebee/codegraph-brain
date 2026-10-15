package store

import "sync"

// Base holds what every store shares.
type Base struct {
	mu sync.Mutex
}

func (b *Base) Lock() {
	b.mu.Lock()
}

// Store keeps orders.
type Store struct {
	Base
}

func (s *Store) Save(order string) {
	s.Lock()
	validate(order)
}

func validate(order string) bool {
	return order != ""
}
