package p

func fn() {}
func g()  {}
func h()  {}

// fn, g and h are package functions; each local below only shares a name with one.
func Param(fn func()) {
	x := fn
	x()
}

func Range(fs []func()) {
	for _, g := range fs {
		x := g
		x()
	}
}

func Closure() {
	_ = func(h func()) {
		x := h
		x()
	}
}
