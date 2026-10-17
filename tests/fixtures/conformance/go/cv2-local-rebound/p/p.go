package p

type C struct{ n int }

func (c *C) handle() {}

func (c *C) run(other func()) {
	cb := c.handle
	cb = other
	cb()
}
