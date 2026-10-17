package p

import "sync"

type Conn struct{}

func (c *Conn) close() {}

func shutdown(once *sync.Once, conn *Conn) { once.Do(conn.close) }
