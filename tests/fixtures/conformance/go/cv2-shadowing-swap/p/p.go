package p

type C struct{}

func (c *C) a() {}
func (c *C) b() {}

func (c *C) run() {
	f := c.a
	g := c.b
	if true {
		f, g := g, f
		f()
		g()
	}
}
