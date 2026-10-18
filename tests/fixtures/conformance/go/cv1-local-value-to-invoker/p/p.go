package p

import "sync"

type C struct{ once sync.Once }

func (c *C) setup() {}

func (c *C) init() {
	cb := c.setup
	c.once.Do(cb)
}
