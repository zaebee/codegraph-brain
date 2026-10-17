package p

import "sync"

var once sync.Once

func hook() {}

// The parameter hook shadows the package function hook: the value passed is the
// parameter, which names no function the graph holds.
func init2(hook func()) { once.Do(hook) }
