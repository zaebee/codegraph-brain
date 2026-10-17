package p

import "sync"

var once sync.Once

func a() {}
func b() {}

// A closure captures the variable, not its value at the closure's definition: only
// b runs here.
func Stale() {
	cb := a
	f := func() { once.Do(cb) }
	cb = b
	f()
}
