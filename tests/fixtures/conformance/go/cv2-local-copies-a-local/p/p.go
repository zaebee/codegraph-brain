package p

type C struct{}

func (c *C) handle() {}

// A package function named like the local below: copying the local must not
// turn into a value naming this function.
func cb() {}

func (c *C) run() {
	cb := c.handle
	cb2 := cb
	cb2()
}
