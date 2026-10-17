package p

import "sync"

var once sync.Once

func setup() {}

func init2(hook func()) { once.Do(hook) }
