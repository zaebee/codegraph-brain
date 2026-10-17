package p

type C struct{}

func (c *C) handle() {}

func (c *C) run() {
	cb := c.handle
	cb()
}
