package p

func a() {}
func b() {}

func run(n int) {
	cb := a
	for i := 0; i < n; i++ {
		cb()
		cb = b
	}
}
