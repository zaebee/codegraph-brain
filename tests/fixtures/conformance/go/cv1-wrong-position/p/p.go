package p

import "time"

func tick() {}

func later() {}

// AfterFunc invokes its second argument only. The first is passed here only to
// check the position; this does not type-check as Go, and the extractor does not care.
func schedule() { time.AfterFunc(tick, later) }
